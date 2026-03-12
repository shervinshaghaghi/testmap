from __future__ import annotations

from typing import Any

from .supabase_client import get_supabase


def create_task(
    *,
    task_key: str,
    task_name: str,
    spec_path: str,
    prompt_path: str,
    source_type: str = "builtin",
    is_active: bool = True,
) -> dict[str, Any]:
    client = get_supabase()

    payload = {
        "task_key": task_key,
        "task_name": task_name,
        "spec_path": spec_path,
        "prompt_path": prompt_path,
        "source_type": source_type,
        "is_active": is_active,
    }

    result = client.table("tasks").upsert(payload, on_conflict="task_key").execute()
    return result.data[0]


def list_tasks() -> list[dict[str, Any]]:
    client = get_supabase()
    result = (
        client.table("tasks")
        .select("*")
        .eq("is_active", True)
        .order("task_name")
        .execute()
    )
    return result.data