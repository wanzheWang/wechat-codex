from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


class RunnerConfig:
    def __init__(self) -> None:
        self.gateway_url = os.getenv("GATEWAY_URL", "http://127.0.0.1:3000").rstrip("/")
        self.runner_secret = os.getenv("RUNNER_SHARED_SECRET", "dev-runner-secret")
        self.poll_interval = float(os.getenv("RUNNER_POLL_INTERVAL_SECONDS", "5"))
        self.timeout_seconds = int(os.getenv("CODEX_TASK_TIMEOUT_SECONDS", "1800"))
        self.default_sandbox = os.getenv("CODEX_DEFAULT_SANDBOX", "read-only")
        self.write_sandbox = os.getenv("CODEX_WRITE_SANDBOX", "workspace-write")
        self.log_dir = Path(
            os.getenv(
                "CODEX_RUNNER_LOG_DIR",
                str(Path(__file__).resolve().parents[1] / "data" / "logs"),
            )
        )
        self.result_dir = Path(
            os.getenv(
                "CODEX_RUNNER_RESULT_DIR",
                str(Path(__file__).resolve().parents[1] / "data" / "results"),
            )
        )


CONFIG = RunnerConfig()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Codex tasks from the gateway.")
    parser.add_argument("--once", action="store_true", help="Poll and run at most one task.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Codex; return a fake result.")
    args = parser.parse_args()

    CONFIG.log_dir.mkdir(parents=True, exist_ok=True)
    CONFIG.result_dir.mkdir(parents=True, exist_ok=True)
    while True:
        task = fetch_next_task()
        if task:
            run_task(task, dry_run=args.dry_run)
        elif args.once:
            print("no queued task")
            return

        if args.once:
            return
        time.sleep(CONFIG.poll_interval)


def fetch_next_task() -> dict[str, Any] | None:
    response = post_json("/runner/tasks/next", {})
    return response.get("task")


def run_task(task: dict[str, Any], *, dry_run: bool) -> None:
    task_id = task["id"]
    try:
        project_path = project_path_for(task["project_key"])
        sandbox = sandbox_for(task["mode"])
        prompt = build_prompt(task)
        log_path = CONFIG.log_dir / f"{task_id}.jsonl"

        if dry_run:
            final_message = (
                f"DRY_RUN: 已模拟执行任务 {task_id}。\n"
                f"名称：{task.get('name') or task_id}\n"
                f"分类：{task.get('category') or '未分类'}\n"
                f"项目：{task['project_key']}\n"
                f"路径：{project_path}\n"
                f"模式：{task['mode']}\n"
                f"指令：{task['instruction']}"
            )
            log_path.write_text(final_message + "\n", encoding="utf-8")
            result_path = write_result_file(task, final_message)
            submit_result(
                task_id,
                status="succeeded",
                final_message=final_message,
                log_path=str(log_path),
                result_path=str(result_path),
            )
            print(f"completed dry-run task {task_id}")
            return

        final_message = run_codex(
            project_path=project_path,
            sandbox=sandbox,
            prompt=prompt,
            log_path=log_path,
        )
        result_path = write_result_file(task, final_message)
        submit_result(
            task_id,
            status="succeeded",
            final_message=final_message,
            log_path=str(log_path),
            result_path=str(result_path),
        )
        print(f"completed task {task_id}")
    except Exception as exc:
        submit_result(task_id, status="failed", error_message=str(exc))
        print(f"failed task {task_id}: {exc}", file=sys.stderr)


def run_codex(*, project_path: Path, sandbox: str, prompt: str, log_path: Path) -> str:
    command = [
        "codex",
        "exec",
        "-C",
        str(project_path),
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--json",
        prompt,
    ]
    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=CONFIG.timeout_seconds,
        check=False,
    )
    log_path.write_text(process.stdout, encoding="utf-8")

    if process.returncode != 0:
        stderr = process.stderr.strip()
        raise RuntimeError(f"codex exec failed with code {process.returncode}: {stderr}")

    final_message = extract_final_message(process.stdout)
    return final_message or "(Codex 未返回最终文本)"


def extract_final_message(jsonl: str) -> str | None:
    final_message: str | None = None
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                final_message = item["text"]
    return final_message


def build_prompt(task: dict[str, Any]) -> str:
    if task["mode"] == "write":
        return (
            "你正在处理一条来自微信的 Codex 写入任务。\n"
            "要求：保持改动最小；不要修改无关文件；完成后总结修改文件和验证结果。\n\n"
            f"任务指令：{task['instruction']}"
        )
    return (
        "你正在处理一条来自微信的 Codex 只读任务。\n"
        "要求：不要修改文件；只阅读、分析并给出结论。\n\n"
        f"任务指令：{task['instruction']}"
    )


def project_path_for(project_key: str) -> Path:
    env_name = "PROJECT_" + project_key.upper().replace("-", "_")
    value = os.getenv(env_name)
    if not value:
        raise RuntimeError(f"missing project mapping env: {env_name}")
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"project path does not exist: {path}")
    return path


def sandbox_for(mode: str) -> str:
    if mode == "write":
        return CONFIG.write_sandbox
    return CONFIG.default_sandbox


def submit_result(
    task_id: str,
    *,
    status: str,
    final_message: str | None = None,
    log_path: str | None = None,
    result_path: str | None = None,
    error_message: str | None = None,
) -> None:
    payload = {
        "status": status,
        "final_message": final_message,
        "log_path": log_path,
        "result_path": result_path,
        "error_message": error_message,
    }
    post_json(f"/runner/tasks/{task_id}/result", payload)


def write_result_file(task: dict[str, Any], final_message: str) -> Path:
    result_path = CONFIG.result_dir / f"{task['id']}.md"
    result_path.write_text(
        "\n".join(
            [
                f"# Codex Task {task['id']}",
                "",
                f"- Name: `{task.get('name') or task['id']}`",
                f"- Category: `{task.get('category') or '未分类'}`",
                f"- Project: `{task['project_key']}`",
                f"- Mode: `{task['mode']}`",
                f"- Instruction: {task['instruction']}",
                "",
                "## Result",
                "",
                final_message,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return result_path


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        CONFIG.gateway_url + path,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Runner-Secret": CONFIG.runner_secret,
        },
    )
    with request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


if __name__ == "__main__":
    main()
