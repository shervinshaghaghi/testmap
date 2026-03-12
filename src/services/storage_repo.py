from __future__ import annotations

from pathlib import Path

from .supabase_client import get_supabase


def upload_bytes(
    bucket: str,
    path_in_bucket: str,
    data: bytes,
    content_type: str | None = None,
    upsert: bool = False,
) -> dict:
    client = get_supabase()
    storage = client.storage.from_(bucket)

    if upsert:
        try:
            storage.remove([path_in_bucket])
        except Exception:
            pass

    file_options: dict[str, str] = {}
    if content_type:
        file_options["content-type"] = content_type

    return storage.upload(
        path=path_in_bucket,
        file=data,
        file_options=file_options or None,
    )


def upload_file(
    bucket: str,
    path_in_bucket: str,
    local_path: str | Path,
    content_type: str | None = None,
    upsert: bool = False,
) -> dict:
    local_path = Path(local_path)
    data = local_path.read_bytes()
    return upload_bytes(
        bucket,
        path_in_bucket,
        data,
        content_type=content_type,
        upsert=upsert,
    )


def download_file(bucket: str, path_in_bucket: str, local_path: str | Path) -> Path:
    client = get_supabase()
    local_path = Path(local_path)

    data = client.storage.from_(bucket).download(path_in_bucket)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(data)
    return local_path


def delete_file(bucket: str, path_in_bucket: str) -> list:
    client = get_supabase()
    return client.storage.from_(bucket).remove([path_in_bucket])