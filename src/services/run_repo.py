from __future__ import annotations

from typing import Any

from .supabase_client import get_supabase


def create_run(
    *,
    task_id: str | None,
    task_name_snapshot: str,
    spec_path_snapshot: str,
    prompt_path_snapshot: str,
    student_name: str,
    input_path: str,
    backend: str,
    model: str,
    status: str = "queued",
) -> dict[str, Any]:
    sb = get_supabase()
    payload = {
        "task_id": task_id,
        "task_name_snapshot": task_name_snapshot,
        "spec_path_snapshot": spec_path_snapshot,
        "prompt_path_snapshot": prompt_path_snapshot,
        "student_name": student_name,
        "input_path": input_path,
        "backend": backend,
        "model": model,
        "status": status,
    }
    res = sb.table("mapping_runs").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise RuntimeError("Failed to create mapping run.")
    return rows[0]


def update_run_status(
    run_id: str,
    *,
    status: str,
    output_docx_path: str | None = None,
    output_xlsx_path: str | None = None,
    error_message: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    sb = get_supabase()

    payload: dict[str, Any] = {
        "status": status,
    }

    if output_docx_path is not None:
        payload["output_docx_path"] = output_docx_path
    if output_xlsx_path is not None:
        payload["output_xlsx_path"] = output_xlsx_path
    if error_message is not None:
        payload["error_message"] = error_message
    if finished_at is not None:
        payload["finished_at"] = finished_at

    res = (
        sb.table("mapping_runs")
        .update(payload)
        .eq("id", run_id)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise RuntimeError(f"Failed to update mapping run {run_id}.")
    return rows[0]