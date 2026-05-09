from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.commands import parse_command
from shared.task_store import TaskStore, UNCATEGORIZED, normalize_category
from shared.wechat import parse_text_message, text_reply, verify_signature


HELP_TEXT = "\n".join(
    [
        "可用指令：",
        "任务 工程结构 项目 artical 只读 总结当前工程结构",
        "任务 工程结构 分类 微信接入 项目 artical 只读 总结当前工程结构",
        "任务 修复按钮 项目 artical 修改 修复按钮样式",
        "任务列表",
        "分类列表",
        "分类 <分类名>",
        "归类 <任务名或ID> <分类名>",
        "取消分类 <任务名或ID>",
        "项目 artical 只读 总结当前工程结构",
        "项目 artical 修改 修复某个问题",
        "状态 <任务名或ID>",
        "全文 <任务名或ID>",
        "全文 <任务名或ID> 2",
        "文件 <任务名或ID>",
        "完整 <任务名或ID>",
        "批准 <任务名或ID>",
        "取消 <任务名或ID>",
        "命名 <任务名或ID> <新任务名>",
    ]
)


class GatewayConfig:
    def __init__(self) -> None:
        self.host = os.getenv("GATEWAY_HOST", "127.0.0.1")
        self.port = int(os.getenv("GATEWAY_PORT", "3000"))
        self.wechat_token = os.getenv("WECHAT_TOKEN", "dev-token")
        self.runner_secret = os.getenv("RUNNER_SHARED_SECRET", "dev-runner-secret")
        self.allowed_openids = {
            item.strip()
            for item in os.getenv("ALLOWED_WECHAT_OPENIDS", "").split(",")
            if item.strip()
        }
        self.store_path = os.getenv("TASK_DATABASE_PATH")
        self.max_reply_chars = int(os.getenv("WECHAT_MAX_REPLY_CHARS", "1400"))
        self.full_chunk_chars = int(os.getenv("WECHAT_FULL_CHUNK_CHARS", "1000"))
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


CONFIG = GatewayConfig()
STORE = TaskStore(CONFIG.store_path)


class Handler(BaseHTTPRequestHandler):
    server_version = "WechatCodexGateway/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if parsed.path == "/wechat/callback":
            self._handle_wechat_verify(parsed.query)
            return
        if parsed.path.startswith("/results/") and parsed.path.endswith(".md"):
            self._handle_result_file(parsed.path, parsed.query)
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/wechat/callback":
            self._handle_wechat_message()
            return
        if parsed.path == "/runner/tasks/next":
            if not self._check_runner_secret():
                return
            task = STORE.claim_next()
            self._send_json(200, {"task": task})
            return
        if parsed.path.endswith("/heartbeat") and parsed.path.startswith("/runner/tasks/"):
            if not self._check_runner_secret():
                return
            task_id = parsed.path.split("/")[3]
            task = STORE.heartbeat(task_id)
            self._send_json(200 if task else 404, {"task": task})
            return
        if parsed.path.endswith("/result") and parsed.path.startswith("/runner/tasks/"):
            if not self._check_runner_secret():
                return
            self._handle_runner_result(parsed.path.split("/")[3])
            return
        self._send_json(404, {"error": "not_found"})

    def _handle_wechat_verify(self, query: str) -> None:
        params = parse_qs(query)
        signature = first(params, "signature")
        timestamp = first(params, "timestamp")
        nonce = first(params, "nonce")
        echostr = first(params, "echostr")
        if not all([signature, timestamp, nonce, echostr]):
            self._send_text(400, "missing verification params")
            return
        if verify_signature(CONFIG.wechat_token, signature, timestamp, nonce):
            self._send_text(200, echostr)
            return
        self._send_text(403, "signature mismatch")

    def _handle_wechat_message(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            message = parse_text_message(body)
            reply = handle_user_text(message.from_user, message.content)
            reply = truncate_reply(reply, CONFIG.max_reply_chars)
            payload = text_reply(
                to_user=message.from_user,
                from_user=message.to_user,
                content=reply,
            )
            self._send_xml(200, payload)
        except Exception as exc:
            self._send_xml(
                200,
                text_reply(to_user="", from_user="", content=f"处理失败：{exc}"),
            )

    def _handle_runner_result(self, task_id: str) -> None:
        payload = self._read_json_body()
        status = payload.get("status")
        if status not in {"succeeded", "failed"}:
            self._send_json(400, {"error": "invalid status"})
            return
        task = STORE.complete(
            task_id,
            status=status,
            final_message=payload.get("final_message"),
            log_path=payload.get("log_path"),
            result_path=payload.get("result_path"),
            error_message=payload.get("error_message"),
        )
        self._send_json(200 if task else 404, {"task": task})

    def _handle_result_file(self, path: str, query: str) -> None:
        task_id = Path(path).name.removesuffix(".md")
        params = parse_qs(query)
        token = first(params, "token")
        if not hmac.compare_digest(token, result_token(task_id)):
            self._send_text(403, "invalid token")
            return
        task = STORE.get(task_id)
        if not task or task.get("status") != "succeeded":
            self._send_text(404, "result not found")
            return
        result_path = task.get("result_path")
        if not result_path:
            self._send_text(404, "result file not found")
            return
        file_path = Path(result_path)
        try:
            data = file_path.read_bytes()
        except FileNotFoundError:
            self._send_text(404, "result file not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", f'inline; filename="{task_id}.md"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check_runner_secret(self) -> bool:
        header = self.headers.get("X-Runner-Secret", "")
        if header != CONFIG.runner_secret:
            self._send_json(401, {"error": "unauthorized"})
            return False
        return True

    def _read_json_body(self) -> dict:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_xml(self, status: int, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def handle_user_text(openid: str, text: str) -> str:
    if CONFIG.allowed_openids and openid not in CONFIG.allowed_openids:
        return "你不在允许使用 Codex 的微信用户白名单中。"

    command = parse_command(text)
    if command.kind == "create_task":
        assert command.project_key and command.instruction and command.mode
        approval_required = command.mode == "write"
        if command.task_name and STORE.name_exists_for_user(command.task_name, openid):
            return f"任务名已存在：{command.task_name}。请换一个名字。"
        task = STORE.create(
            source_user_id=openid,
            project_key=command.project_key,
            instruction=command.instruction,
            mode=command.mode,
            approval_required=approval_required,
            task_name=command.task_name,
            category=command.category,
        )
        task_ref = display_task_ref(task)
        if approval_required:
            return (
                f"写入任务已创建：{task_ref}。\n"
                f"发送「批准 {task_ref}」后才会执行。"
            )
        return f"任务已入队：{task_ref}。稍后发送「状态 {task_ref}」查看结果。"

    if command.kind == "status":
        assert command.task_id
        task = STORE.get_for_user_by_ref(command.task_id, openid)
        if not task:
            return "未找到这个任务。"
        return format_task_status(task)

    if command.kind == "list":
        tasks = STORE.recent_for_user(openid)
        if not tasks:
            return "暂无任务。"
        return format_task_list(tasks)

    if command.kind == "category_list":
        categories = STORE.categories_for_user(openid)
        if not categories:
            return "暂无分类。"
        return format_category_list(categories)

    if command.kind == "category_tasks":
        assert command.category
        tasks = STORE.tasks_for_user_category(openid, command.category)
        if not tasks:
            return f"分类「{command.category}」下暂无任务。"
        return format_task_list(tasks, title=f"分类「{command.category}」")

    if command.kind == "full_text":
        assert command.task_id and command.page
        task = STORE.get_for_user_by_ref(command.task_id, openid)
        if not task:
            return "未找到这个任务。"
        return format_full_text_chunk(task, command.page)

    if command.kind == "file":
        assert command.task_id
        task = STORE.get_for_user_by_ref(command.task_id, openid)
        if not task:
            return "未找到这个任务。"
        return format_mobile_file_link(task)

    if command.kind == "full":
        assert command.task_id
        task = STORE.get_for_user_by_ref(command.task_id, openid)
        if not task:
            return "未找到这个任务。"
        return format_full_result_location(task)

    if command.kind == "approve":
        assert command.task_id
        task = STORE.approve(command.task_id, openid)
        if not task:
            return "未找到可批准的任务。"
        if task["status"] == "queued":
            return f"任务 {display_task_ref(task)} 已批准并入队。"
        return format_task_status(task)

    if command.kind == "cancel":
        assert command.task_id
        task = STORE.cancel(command.task_id, openid)
        if not task:
            return "未找到可取消的任务。"
        return f"任务 {display_task_ref(task)} 当前状态：{task['status']}。"

    if command.kind == "rename":
        assert command.task_id and command.task_name
        try:
            task = STORE.rename(command.task_id, openid, command.task_name)
        except ValueError as exc:
            return str(exc)
        if not task:
            return "未找到可命名的任务。"
        return f"已命名：{task['name']}。\n后续可发送「状态 {task['name']}」。"

    if command.kind == "set_category":
        assert command.task_id and command.category
        task = STORE.set_category(command.task_id, openid, command.category)
        if not task:
            return "未找到可归类的任务。"
        return (
            f"已归类：{display_task_ref(task)} -> {display_category(task)}。\n"
            f"查看该分类：分类 {display_category(task)}"
        )

    if command.kind == "clear_category":
        assert command.task_id
        task = STORE.clear_category(command.task_id, openid)
        if not task:
            return "未找到可取消分类的任务。"
        return f"已取消分类：{display_task_ref(task)}。"

    return HELP_TEXT


def format_task_status(task: dict, compact: bool = False) -> str:
    head = f"任务 {display_task_ref(task)}：{task['status']}"
    meta = (
        f"{head}\n分类：{display_category(task)}\n"
        f"项目：{task['project_key']} / 模式：{task['mode']}\n"
        f"指令：{task['instruction']}"
    )
    if compact:
        return meta
    if task["status"] == "succeeded":
        return f"{meta}\n\n结果：\n{task.get('final_message') or '(无最终消息)'}"
    if task["status"] == "failed":
        return f"{meta}\n\n错误：\n{task.get('error_message') or '(无错误详情)'}"
    return meta


def format_task_list(tasks: list[dict], title: str = "最近任务") -> str:
    lines = [f"{title}："]
    for index, task in enumerate(tasks, start=1):
        lines.extend(
            [
                "",
                f"{index}. {display_task_ref(task)}",
                f"状态：{task['status']}",
                f"分类：{display_category(task)}",
                f"项目：{task['project_key']} / 模式：{task['mode']}",
                f"指令：{task['instruction']}",
            ]
        )
    lines.extend(
        [
            "",
            "可用：状态/全文/文件/批准/取消 <任务名或ID>",
            "分类：归类 <任务名或ID> <分类名>",
            "命名旧任务：命名 <任务ID> <新任务名>",
        ]
    )
    return "\n".join(lines)


def format_category_list(categories: list[dict]) -> str:
    lines = ["分类列表："]
    for index, item in enumerate(categories, start=1):
        detail = f"{item['count']} 个任务"
        if item.get("running"):
            detail += f"，运行中 {item['running']}"
        if item.get("waiting"):
            detail += f"，待处理 {item['waiting']}"
        lines.append(f"{index}. {item['category']}：{detail}")
    lines.extend(["", "查看分类：分类 <分类名>"])
    return "\n".join(lines)


def display_task_ref(task: dict) -> str:
    task_name = task.get("name")
    if task_name:
        return task_name
    return task["id"]


def display_category(task: dict) -> str:
    return normalize_category(task.get("category")) or UNCATEGORIZED


def format_full_result_location(task: dict) -> str:
    if task["status"] != "succeeded":
        return format_task_status(task)
    result_path = task.get("result_path")
    log_path = task.get("log_path")
    lines = [
        f"任务 {display_task_ref(task)} 的完整回答保存在本机：",
        "",
    ]
    if result_path:
        lines.append(result_path)
    elif log_path:
        lines.append(log_path)
    else:
        lines.append("当前任务没有记录完整结果路径。")
    lines.extend(
        [
            "",
            "在电脑终端可用以下命令查看：",
            f"cat {result_path or log_path}",
        ]
    )
    return "\n".join(lines)


def format_full_text_chunk(task: dict, page: int) -> str:
    if task["status"] != "succeeded":
        return format_task_status(task)
    if page < 1:
        page = 1
    text = task.get("final_message") or "(无最终消息)"
    chunk_size = max(200, CONFIG.full_chunk_chars)
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]
    total = len(chunks)
    if page > total:
        return f"任务 {display_task_ref(task)} 只有 {total} 段。"
    current = chunks[page - 1]
    footer = f"\n\n第 {page}/{total} 段"
    task_ref = display_task_ref(task)
    if page < total:
        footer += f"\n继续发送：全文 {task_ref} {page + 1}"
    else:
        footer += "\n已到最后一段。"
    if CONFIG.public_base_url and task.get("result_path"):
        footer += f"\n手机文件链接可发送：文件 {task_ref}"
    return current + footer


def format_mobile_file_link(task: dict) -> str:
    if task["status"] != "succeeded":
        return format_task_status(task)
    if not task.get("result_path"):
        return "这个任务还没有完整结果文件。"
    if not CONFIG.public_base_url:
        return (
            "当前 gateway 没有配置 PUBLIC_BASE_URL，无法生成手机可打开链接。\n\n"
            f"本机文件：\n{task['result_path']}"
        )
    url = f"{CONFIG.public_base_url}/results/{task['id']}.md?token={result_token(task['id'])}"
    return (
        f"任务 {display_task_ref(task)} 的完整回答文件：\n\n"
        f"{url}\n\n"
        "这个链接带访问 token，请不要转发给无关人员。"
    )


def result_token(task_id: str) -> str:
    return hmac.new(
        CONFIG.runner_secret.encode("utf-8"),
        task_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def truncate_reply(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    suffix = "\n\n结果较长，已截断。完整内容保存在本机任务日志中。"
    return text[: max_chars - len(suffix)] + suffix


def first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key)
    return values[0] if values else ""


def main() -> None:
    server = ThreadingHTTPServer((CONFIG.host, CONFIG.port), Handler)
    print(f"gateway listening on http://{CONFIG.host}:{CONFIG.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
