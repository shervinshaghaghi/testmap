from __future__ import annotations

from pathlib import Path

from src.services.storage_repo import upload_file
from src.services.task_repo import create_task

TASKS_DIR = Path("tasks")
BUCKET = "task-assets"


def seed_task(task_key: str, title: str | None = None) -> None:
    task_dir = TASKS_DIR / task_key
    spec_path = task_dir / "spec.docx"
    prompt_path = task_dir / "prompt.docx"

    if not spec_path.exists():
        raise FileNotFoundError(f"Missing spec file: {spec_path}")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")

    spec_bucket_path = f"{task_key}/spec.docx"
    prompt_bucket_path = f"{task_key}/prompt.docx"

    print(f"Uploading spec for {task_key}...")
    upload_file(
        BUCKET,
        spec_bucket_path,
        spec_path,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        upsert=True,
    )

    print(f"Uploading prompt for {task_key}...")
    upload_file(
        BUCKET,
        prompt_bucket_path,
        prompt_path,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        upsert=True,
    )

    print(f"Creating DB row for {task_key}...")
    create_task(
        task_key=task_key,
        task_name=title or task_key.replace("_", " ").replace("-", " ").title(),
        spec_path=spec_bucket_path,
        prompt_path=prompt_bucket_path,
        source_type="builtin",
        is_active=True,
    )

    print(f"Done: {task_key}")


def main() -> None:
    builtins = [
        ("fileclass_v1", "File Class V1"),
    ]

    for task_key, title in builtins:
        seed_task(task_key, title)


if __name__ == "__main__":
    main()