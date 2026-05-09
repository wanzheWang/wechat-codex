from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    kind: str
    project_key: str | None = None
    mode: str | None = None
    instruction: str | None = None
    task_id: str | None = None
    task_name: str | None = None
    category: str | None = None
    page: int | None = None


def parse_command(text: str) -> ParsedCommand:
    normalized = " ".join(text.strip().split())
    if not normalized:
        return ParsedCommand(kind="help")

    parts = normalized.split(" ", 3)
    if parts[0] == "项目" and len(parts) >= 4:
        project_key = parts[1]
        mode_word = parts[2]
        instruction = parts[3].strip()
        if mode_word == "只读":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="read",
                instruction=instruction,
            )
        if mode_word == "修改":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="write",
                instruction=instruction,
            )

    named_category_parts = normalized.split(" ", 7)
    if (
        len(named_category_parts) >= 8
        and named_category_parts[0] == "任务"
        and named_category_parts[2] == "分类"
        and named_category_parts[4] == "项目"
    ):
        task_name = named_category_parts[1].strip()
        category = named_category_parts[3].strip()
        project_key = named_category_parts[5]
        mode_word = named_category_parts[6]
        instruction = named_category_parts[7].strip()
        if mode_word == "只读":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="read",
                instruction=instruction,
                task_name=task_name,
                category=category,
            )
        if mode_word == "修改":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="write",
                instruction=instruction,
                task_name=task_name,
                category=category,
            )

    named_parts = normalized.split(" ", 5)
    if (
        len(named_parts) >= 6
        and named_parts[0] == "任务"
        and named_parts[2] == "项目"
    ):
        task_name = named_parts[1].strip()
        project_key = named_parts[3]
        mode_word = named_parts[4]
        instruction = named_parts[5].strip()
        if mode_word == "只读":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="read",
                instruction=instruction,
                task_name=task_name,
            )
        if mode_word == "修改":
            return ParsedCommand(
                kind="create_task",
                project_key=project_key,
                mode="write",
                instruction=instruction,
                task_name=task_name,
            )

    short_parts = normalized.split(" ", 1)
    if normalized in {"最近", "列表", "任务列表"}:
        return ParsedCommand(kind="list")
    if normalized == "分类列表":
        return ParsedCommand(kind="category_list")
    if short_parts[0] == "分类" and len(short_parts) == 2:
        return ParsedCommand(kind="category_tasks", category=short_parts[1].strip())
    if short_parts[0] == "归类" and len(short_parts) == 2:
        args = short_parts[1].split()
        if len(args) == 2:
            return ParsedCommand(kind="set_category", task_id=args[0].strip(), category=args[1].strip())
    if short_parts[0] == "取消分类" and len(short_parts) == 2:
        return ParsedCommand(kind="clear_category", task_id=short_parts[1].strip())
    if short_parts[0] == "状态" and len(short_parts) == 2:
        return ParsedCommand(kind="status", task_id=short_parts[1].strip())
    if short_parts[0] == "完整" and len(short_parts) == 2:
        return ParsedCommand(kind="full", task_id=short_parts[1].strip())
    if short_parts[0] == "文件" and len(short_parts) == 2:
        return ParsedCommand(kind="file", task_id=short_parts[1].strip())
    if short_parts[0] == "全文" and len(short_parts) == 2:
        args = short_parts[1].split()
        if len(args) == 1:
            return ParsedCommand(kind="full_text", task_id=args[0].strip(), page=1)
        if len(args) == 2 and args[1].isdigit():
            return ParsedCommand(kind="full_text", task_id=args[0].strip(), page=int(args[1]))
    if short_parts[0] == "批准" and len(short_parts) == 2:
        return ParsedCommand(kind="approve", task_id=short_parts[1].strip())
    if short_parts[0] == "取消" and len(short_parts) == 2:
        return ParsedCommand(kind="cancel", task_id=short_parts[1].strip())
    if short_parts[0] == "命名" and len(short_parts) == 2:
        args = short_parts[1].split()
        if len(args) == 2:
            return ParsedCommand(kind="rename", task_id=args[0].strip(), task_name=args[1].strip())
    return ParsedCommand(kind="help")
