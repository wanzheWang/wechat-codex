from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
UNCATEGORIZED = "未分类"


def default_store_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "tasks.json"


class TaskStore:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else default_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"tasks": []})

    def all(self) -> list[dict[str, Any]]:
        return list(self._read()["tasks"])

    def create(
        self,
        *,
        source_user_id: str,
        project_key: str,
        instruction: str,
        mode: str,
        approval_required: bool,
        task_name: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        now = now_ts()
        task = {
            "id": new_task_id(),
            "name": task_name,
            "category": normalize_category(category),
            "source_platform": "wechat_official_account",
            "source_user_id": source_user_id,
            "project_key": project_key,
            "instruction": instruction,
            "mode": mode,
            "status": "waiting_approval" if approval_required else "queued",
            "approval_required": approval_required,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "heartbeat_at": None,
            "final_message": None,
            "log_path": None,
            "error_message": None,
        }
        data["tasks"].append(task)
        self._write(data)
        return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        for task in self._read()["tasks"]:
            if task["id"] == task_id:
                return task
        return None

    def get_for_user_by_ref(self, task_ref: str, source_user_id: str) -> dict[str, Any] | None:
        tasks = self._read()["tasks"]
        for task in reversed(tasks):
            if task.get("source_user_id") != source_user_id:
                continue
            if task.get("id") == task_ref or task.get("name") == task_ref:
                return task
        return None

    def name_exists_for_user(
        self,
        task_name: str,
        source_user_id: str,
        *,
        exclude_task_id: str | None = None,
    ) -> bool:
        for task in self._read()["tasks"]:
            if task.get("source_user_id") != source_user_id:
                continue
            if exclude_task_id and task.get("id") == exclude_task_id:
                continue
            if task.get("name") == task_name:
                return True
        return False

    def recent_for_user(self, source_user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        tasks = [
            task
            for task in self._read()["tasks"]
            if task.get("source_user_id") == source_user_id
        ]
        return list(reversed(tasks[-limit:]))

    def tasks_for_user_category(
        self,
        source_user_id: str,
        category: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        normalized = normalize_category(category)
        tasks = [
            task
            for task in self._read()["tasks"]
            if task.get("source_user_id") == source_user_id
            and normalize_category(task.get("category")) == normalized
        ]
        return list(reversed(tasks[-limit:]))

    def categories_for_user(self, source_user_id: str) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"category": UNCATEGORIZED, "count": 0, "running": 0, "waiting": 0}
        )
        for task in self._read()["tasks"]:
            if task.get("source_user_id") != source_user_id:
                continue
            category = normalize_category(task.get("category")) or UNCATEGORIZED
            counts[category]["category"] = category
            counts[category]["count"] += 1
            if task.get("status") == "running":
                counts[category]["running"] += 1
            if task.get("status") in {"queued", "waiting_approval"}:
                counts[category]["waiting"] += 1
        return sorted(counts.values(), key=lambda item: item["category"])

    def approve(self, task_ref: str, source_user_id: str) -> dict[str, Any] | None:
        data = self._read()
        task = find_task_by_ref(data["tasks"], task_ref, source_user_id)
        if task:
            if task["status"] == "waiting_approval":
                task["status"] = "queued"
                task["approval_required"] = False
                self._write(data)
            return task
        return None

    def cancel(self, task_ref: str, source_user_id: str) -> dict[str, Any] | None:
        data = self._read()
        task = find_task_by_ref(data["tasks"], task_ref, source_user_id)
        if task:
            if task["status"] not in TERMINAL_STATUSES:
                task["status"] = "cancelled"
                task["finished_at"] = now_ts()
                self._write(data)
            return task
        return None

    def rename(
        self,
        task_ref: str,
        source_user_id: str,
        task_name: str,
    ) -> dict[str, Any] | None:
        data = self._read()
        task = find_task_by_ref(data["tasks"], task_ref, source_user_id)
        if not task:
            return None
        if self.name_exists_for_user(task_name, source_user_id, exclude_task_id=task["id"]):
            raise ValueError(f"任务名已存在：{task_name}")
        task["name"] = task_name
        self._write(data)
        return task

    def set_category(
        self,
        task_ref: str,
        source_user_id: str,
        category: str,
    ) -> dict[str, Any] | None:
        data = self._read()
        task = find_task_by_ref(data["tasks"], task_ref, source_user_id)
        if not task:
            return None
        task["category"] = normalize_category(category)
        self._write(data)
        return task

    def clear_category(
        self,
        task_ref: str,
        source_user_id: str,
    ) -> dict[str, Any] | None:
        data = self._read()
        task = find_task_by_ref(data["tasks"], task_ref, source_user_id)
        if not task:
            return None
        task["category"] = None
        self._write(data)
        return task

    def claim_next(self) -> dict[str, Any] | None:
        data = self._read()
        for task in data["tasks"]:
            if task["status"] == "queued":
                task["status"] = "running"
                task["started_at"] = now_ts()
                task["heartbeat_at"] = now_ts()
                self._write(data)
                return task
        return None

    def heartbeat(self, task_id: str) -> dict[str, Any] | None:
        return self.update(task_id, {"heartbeat_at": now_ts()})

    def complete(
        self,
        task_id: str,
        *,
        status: str,
        final_message: str | None = None,
        log_path: str | None = None,
        result_path: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        return self.update(
            task_id,
            {
                "status": status,
                "finished_at": now_ts(),
                "final_message": final_message,
                "log_path": log_path,
                "result_path": result_path,
                "error_message": error_message,
            },
        )

    def update(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        data = self._read()
        for task in data["tasks"]:
            if task["id"] == task_id:
                task.update(patch)
                self._write(data)
                return task
        return None

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            data = {"tasks": []}
        if "tasks" not in data or not isinstance(data["tasks"], list):
            raise ValueError(f"invalid task store: {self.path}")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            tmp_name = handle.name
        os.replace(tmp_name, self.path)


def now_ts() -> int:
    return int(time.time())


def new_task_id() -> str:
    return time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]


def normalize_category(category: Any) -> str | None:
    if category is None:
        return None
    value = str(category).strip()
    if not value or value == UNCATEGORIZED:
        return None
    return value


def find_task_by_ref(
    tasks: list[dict[str, Any]],
    task_ref: str,
    source_user_id: str,
) -> dict[str, Any] | None:
    for task in reversed(tasks):
        if task.get("source_user_id") != source_user_id:
            continue
        if task.get("id") == task_ref or task.get("name") == task_ref:
            return task
    return None
