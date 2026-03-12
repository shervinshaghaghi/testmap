import sys
import re
import io
import os
from pathlib import Path
from datetime import datetime, timezone
import zipfile
import time

import streamlit as st
from dotenv import load_dotenv

# --- load .env ---
load_dotenv()

# --- make repo root importable (fix: No module named 'src') ---
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.config import LLMConfig
from src.mapper import run_mapping
from src.services.task_repo import list_tasks
from src.services.storage_repo import download_file, upload_file
from src.services.run_repo import create_run, update_run_status


# ---------- Paths ----------
UI_DIR = Path(__file__).resolve().parent
STUDENTS_DIR = BASE_DIR / "students"
OUT_BASE = BASE_DIR / "out_ui"   # separate from cli out/

TASK_ASSETS_BUCKET = "task-assets"
STUDENT_INPUTS_BUCKET = "student-inputs"
MAPPING_OUTPUTS_BUCKET = "mapping-outputs"

# ---------- Default LLM settings ----------
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_BACKEND = "ollama"
DEFAULT_MODEL_BY_BACKEND = {
    "ollama": "llama3.1:latest",
    "gemini": "gemini-2.5-flash",
}
DEFAULT_TIMEOUT_SEC = 900


# ---------- Helpers ----------
def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip()).strip("_") or "STUDENT"


def display_student(s: str) -> str:
    s = (s or "").strip()
    return s if s else "STUDENT"


def normalize_student_from_filename(filename: str) -> str:
    base = Path(filename).stem
    base = base.replace("_", " ").strip()
    return base if base else "STUDENT"


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_zip_student_docs(uploaded_zip) -> list[tuple[str, bytes, str]]:
    """
    Expects ZIP like:
      STUDENT_FOLDER_1/random.docx
      STUDENT_FOLDER_1/other.pdf
      STUDENT_FOLDER_2/something.docx

    Returns list of (student_name, file_bytes, original_filename)
    """
    z = zipfile.ZipFile(io.BytesIO(uploaded_zip.getvalue()))
    items: list[tuple[str, bytes, str]] = []

    for info in z.infolist():
        if info.is_dir():
            continue

        if info.filename.startswith("__MACOSX/"):
            continue
        if Path(info.filename).name.startswith("._"):
            continue

        parts = Path(info.filename).parts
        if len(parts) < 2:
            continue

        student_folder = parts[0]
        if student_folder == "__MACOSX":
            continue

        fname = parts[-1]
        if Path(fname).name.startswith("._"):
            continue

        ext = Path(fname).suffix.lower()
        if ext not in [".docx", ".pdf", ".txt"]:
            continue

        student_name = student_folder.replace("_", " ").strip() or "STUDENT"
        content = z.read(info.filename)
        items.append((student_name, content, fname))

    return items


def _validate_common(
    use_default_spec: bool,
    use_default_prompt: bool,
    spec_up,
    prompt_up,
) -> None:
    if (not use_default_spec) and (spec_up is None):
        st.error("Please upload spec.docx (or enable task default).")
        st.stop()

    if (not use_default_prompt) and (prompt_up is None):
        st.error("Please upload prompt.docx (or enable task default).")
        st.stop()


def _resolve_spec_prompt(
    run_out: Path,
    spec_default: Path,
    prompt_default: Path,
    use_default_spec: bool,
    use_default_prompt: bool,
    spec_up,
    prompt_up,
) -> tuple[Path, Path]:
    uploads_dir = run_out / "_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    if use_default_spec:
        spec_path = spec_default
    else:
        spec_path = uploads_dir / "spec.docx"
        spec_path.write_bytes(spec_up.getvalue())

    if use_default_prompt:
        prompt_path = prompt_default
    else:
        prompt_path = uploads_dir / "prompt.docx"
        prompt_path.write_bytes(prompt_up.getvalue())

    return spec_path, prompt_path


def _save_student_bytes(student_name: str, file_bytes: bytes, original_name: str, ts: str) -> Path:
    STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_student = safe_name(student_name)
    in_path = STUDENTS_DIR / f"{safe_student}_{ts}_{safe_name(original_name)}"
    in_path.write_bytes(file_bytes)
    return in_path


def _build_llm_cfg(backend: str, model_name: str) -> LLMConfig:
    backend = (backend or "").strip().lower()
    model_name = (model_name or "").strip()

    if backend not in {"ollama", "gemini"}:
        raise ValueError(f"Unsupported UI backend: {backend}")

    if not model_name:
        raise ValueError("Model name is required.")

    api_key = os.getenv("GEMINI_API_KEY")

    if backend == "gemini" and not api_key:
        raise ValueError("GEMINI_API_KEY is missing in .env")

    return LLMConfig(
        backend=backend,
        model=model_name,
        url=OLLAMA_URL,
        timeout_sec=int(DEFAULT_TIMEOUT_SEC),
        api_key=api_key,
    )


def _download_builtin_assets(task_row: dict, base_out_dir: Path) -> tuple[Path, Path]:
    builtin_dir = base_out_dir / "_builtin_task"
    builtin_dir.mkdir(parents=True, exist_ok=True)

    spec_local = builtin_dir / "spec.docx"
    prompt_local = builtin_dir / "prompt.docx"

    download_file(TASK_ASSETS_BUCKET, task_row["spec_path"], spec_local)
    download_file(TASK_ASSETS_BUCKET, task_row["prompt_path"], prompt_local)

    return spec_local, prompt_local


def _storage_student_input_path(task_key: str, ts: str, student_name: str, original_name: str) -> str:
    return f"{task_key}/{ts}/{safe_name(student_name)}/{safe_name(original_name)}"


def _storage_output_docx_path(task_key: str, ts: str, student_name: str) -> str:
    return f"{task_key}/{ts}/{safe_name(student_name)}/report.docx"


def _storage_output_xlsx_path(task_key: str, ts: str, student_name: str) -> str:
    return f"{task_key}/{ts}/{safe_name(student_name)}/mapping.xlsx"


def _spec_snapshot_value(selected_task: dict, use_default_spec: bool) -> str:
    if use_default_spec:
        return selected_task["spec_path"]
    return "custom-upload/spec.docx"


def _prompt_snapshot_value(selected_task: dict, use_default_prompt: bool) -> str:
    if use_default_prompt:
        return selected_task["prompt_path"]
    return "custom-upload/prompt.docx"


def _run_one(
    student_name: str,
    file_bytes: bytes,
    original_name: str,
    run_out: Path,
    spec_default: Path,
    prompt_default: Path,
    use_default_spec: bool,
    use_default_prompt: bool,
    spec_up,
    prompt_up,
    llm_cfg: LLMConfig,
) -> dict:
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    run_out.mkdir(parents=True, exist_ok=True)

    ts = run_out.name
    in_path = _save_student_bytes(student_name, file_bytes, original_name, ts)

    spec_path, prompt_path = _resolve_spec_prompt(
        run_out,
        spec_default,
        prompt_default,
        use_default_spec,
        use_default_prompt,
        spec_up,
        prompt_up,
    )

    try:
        result, out_docx, out_xlsx = run_mapping(
            student=student_name,
            student_file=in_path,
            spec_path=spec_path,
            prompt_path=prompt_path,
            out_dir=run_out,
            llm_cfg=llm_cfg,
        )
        return {
            "ok": True,
            "student": student_name,
            "safe_student": safe_name(student_name),
            "run_out": run_out,
            "docx": out_docx,
            "xlsx": out_xlsx,
            "result": result,
            "error": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "student": student_name,
            "safe_student": safe_name(student_name),
            "run_out": run_out,
            "docx": None,
            "xlsx": None,
            "result": None,
            "error": repr(e),
        }


@st.cache_data(ttl=60)
def _load_active_tasks() -> list[dict]:
    tasks = list_tasks()
    return tasks or []


# ---------- UI ----------
st.title("TestMap")

with st.sidebar:
    st.header("LLM settings")

    backend = st.selectbox(
        "Backend",
        options=["ollama", "gemini"],
        index=0 if DEFAULT_BACKEND == "ollama" else 1,
    )

    default_model = DEFAULT_MODEL_BY_BACKEND.get(backend, "")
    model_name = st.text_input("Model name", value=default_model)

    st.caption(f"Ollama URL: {OLLAMA_URL}")
    st.caption(f"Timeout (sec): {DEFAULT_TIMEOUT_SEC}")

try:
    tasks = _load_active_tasks()
except Exception as e:
    st.error(f"Could not load tasks from Supabase: {e}")
    st.stop()

if not tasks:
    st.error("No active tasks found in Supabase.")
    st.stop()

task_names = [t["task_name"] for t in tasks]
task_label = st.selectbox("Task", task_names, index=0)
selected_task = next(t for t in tasks if t["task_name"] == task_label)

task_key = selected_task["task_key"]
task_row_id = selected_task["id"]

mode = st.radio("Mode", ["Single", "Batch"], horizontal=True, index=0)

col1, col2 = st.columns(2)
with col1:
    use_default_spec = st.checkbox(f"Use default spec for {task_label}", value=True)
with col2:
    use_default_prompt = st.checkbox(f"Use default prompt for {task_label}", value=True)

spec_up = None
prompt_up = None
if not use_default_spec:
    spec_up = st.file_uploader("Upload spec.docx", type=["docx"], key="specu")
if not use_default_prompt:
    prompt_up = st.file_uploader("Upload prompt.docx", type=["docx"], key="promptu")

run_btn = None

if mode == "Single":
    student = st.text_input("Student name", placeholder='e.g., "John Cena"')
    uploaded = st.file_uploader(
        "Upload student test-case file (DOCX/PDF/TXT)",
        type=["docx", "pdf", "txt"],
    )
    run_btn = st.button("Run mapping", type="primary")
else:
    batch_source = st.radio(
        "Batch input",
        ["Multiple files", "ZIP of student folders"],
        horizontal=True,
        index=1,
    )

    uploaded_files = None
    uploaded_zip = None
    batch_names: list[str] = []

    if batch_source == "Multiple files":
        uploaded_files = st.file_uploader(
            "Upload multiple student files (DOCX/PDF/TXT)",
            type=["docx", "pdf", "txt"],
            accept_multiple_files=True,
        )

        if uploaded_files:
            st.markdown("### Student names")
            batch_names = []
            for idx, up in enumerate(uploaded_files):
                default_name = normalize_student_from_filename(up.name)
                name = st.text_input(
                    label=up.name,
                    value=default_name,
                    key=f"batch_student_{idx}_{up.name}",
                )
                batch_names.append(name)

    else:
        uploaded_zip = st.file_uploader(
            "Upload ZIP (folders per student, files inside can have any name)",
            type=["zip"],
        )

    run_btn = st.button("Run mapping", type="primary")


if run_btn:
    _validate_common(
        use_default_spec,
        use_default_prompt,
        spec_up,
        prompt_up,
    )

    try:
        llm_cfg = _build_llm_cfg(backend=backend, model_name=model_name)
    except Exception as e:
        st.error(str(e))
        st.stop()

    if mode == "Single":
        if not student.strip():
            st.error("Student name is required.")
            st.stop()

        if uploaded is None:
            st.error("Please upload a student file.")
            st.stop()

        safe_student = safe_name(student)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_out = OUT_BASE / task_key / safe_student / ts

        spec_default = run_out / "_builtin_task" / "spec.docx"
        prompt_default = run_out / "_builtin_task" / "prompt.docx"

        if use_default_spec or use_default_prompt:
            try:
                downloaded_spec, downloaded_prompt = _download_builtin_assets(selected_task, run_out)
                spec_default = downloaded_spec
                prompt_default = downloaded_prompt
            except Exception as e:
                st.error(f"Could not download built-in task assets from Supabase: {e}")
                st.stop()

        # Save local input first so it can be uploaded to Supabase and later used by mapper.
        local_input_path = _save_student_bytes(
            student_name=display_student(student),
            file_bytes=uploaded.getvalue(),
            original_name=uploaded.name,
            ts=ts,
        )

        input_storage_path = _storage_student_input_path(
            task_key=task_key,
            ts=ts,
            student_name=display_student(student),
            original_name=uploaded.name,
        )

        try:
            upload_file(
                STUDENT_INPUTS_BUCKET,
                input_storage_path,
                local_input_path,
                upsert=True,
            )
        except Exception as e:
            st.error(f"Could not upload student input to Supabase: {e}")
            st.stop()

        try:
            run_row = create_run(
                task_id=task_row_id,
                task_name_snapshot=selected_task["task_name"],
                spec_path_snapshot=_spec_snapshot_value(selected_task, use_default_spec),
                prompt_path_snapshot=_prompt_snapshot_value(selected_task, use_default_prompt),
                student_name=display_student(student),
                input_path=input_storage_path,
                backend=llm_cfg.backend,
                model=llm_cfg.model,
                status="running",
            )
        except Exception as e:
            st.error(f"Could not create mapping run record: {e}")
            st.stop()

        with st.spinner(f"Running TestMap for task: {task_label} ..."):
            t0 = time.time()
            res = _run_one(
                student_name=display_student(student),
                file_bytes=uploaded.getvalue(),
                original_name=uploaded.name,
                run_out=run_out,
                spec_default=spec_default,
                prompt_default=prompt_default,
                use_default_spec=use_default_spec,
                use_default_prompt=use_default_prompt,
                spec_up=spec_up,
                prompt_up=prompt_up,
                llm_cfg=llm_cfg,
            )
            elapsed = time.time() - t0

        if not res["ok"]:
            try:
                update_run_status(
                    run_id=run_row["id"],
                    status="failed",
                    error_message=res["error"],
                    finished_at=now_iso_utc(),
                )
            except Exception:
                pass

            st.error(f"Run failed: {res['error']}")
            st.write(f"Elapsed: {elapsed:.1f}s")
            st.subheader("Debug files in this run folder")
            for f in sorted(Path(res["run_out"]).glob("debug_*")):
                st.write(f"- {f.name}")
            st.stop()

        output_docx_path = _storage_output_docx_path(
            task_key=task_key,
            ts=ts,
            student_name=display_student(student),
        )
        output_xlsx_path = _storage_output_xlsx_path(
            task_key=task_key,
            ts=ts,
            student_name=display_student(student),
        )

        try:
            upload_file(
                MAPPING_OUTPUTS_BUCKET,
                output_docx_path,
                res["docx"],
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                upsert=True,
            )
            upload_file(
                MAPPING_OUTPUTS_BUCKET,
                output_xlsx_path,
                res["xlsx"],
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                upsert=True,
            )

            update_run_status(
                run_id=run_row["id"],
                status="completed",
                output_docx_path=output_docx_path,
                output_xlsx_path=output_xlsx_path,
                finished_at=now_iso_utc(),
            )
        except Exception as e:
            try:
                update_run_status(
                    run_id=run_row["id"],
                    status="failed",
                    error_message=f"Output upload/update failed: {e}",
                    finished_at=now_iso_utc(),
                )
            except Exception:
                pass

            st.error(f"Run finished locally, but saving outputs to Supabase failed: {e}")
            st.write(f"Elapsed: {elapsed:.1f}s")
            st.subheader("Local output files")
            st.write(f"- DOCX: {res['docx']}")
            st.write(f"- XLSX: {res['xlsx']}")
            st.stop()

        st.success(f"Done. Elapsed: {elapsed:.1f}s. Download results:")
        st.download_button(
            "Download DOCX",
            data=Path(res["docx"]).read_bytes(),
            file_name=Path(res["docx"]).name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        st.download_button(
            "Download XLSX",
            data=Path(res["xlsx"]).read_bytes(),
            file_name=Path(res["xlsx"]).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.subheader("Debug files in this run folder")
        for f in sorted(Path(res["run_out"]).glob("debug_*")):
            st.write(f"- {f.name}")

        st.caption(f"Run saved in mapping_runs: {run_row['id']}")
        st.caption(f"Input stored at: {input_storage_path}")
        st.caption(f"DOCX stored at: {output_docx_path}")
        st.caption(f"XLSX stored at: {output_xlsx_path}")

    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_out = OUT_BASE / task_key / f"_batch_{ts}"
        batch_out.mkdir(parents=True, exist_ok=True)

        spec_default = batch_out / "_builtin_task" / "spec.docx"
        prompt_default = batch_out / "_builtin_task" / "prompt.docx"

        if use_default_spec or use_default_prompt:
            try:
                downloaded_spec, downloaded_prompt = _download_builtin_assets(selected_task, batch_out)
                spec_default = downloaded_spec
                prompt_default = downloaded_prompt
            except Exception as e:
                st.error(f"Could not download built-in task assets from Supabase: {e}")
                st.stop()

        items: list[tuple[str, bytes, str]] = []
        if uploaded_files:
            for up, name in zip(uploaded_files, batch_names):
                student_name = display_student(name)
                items.append((student_name, up.getvalue(), up.name))
        elif uploaded_zip is not None:
            items = iter_zip_student_docs(uploaded_zip)
        else:
            st.error("Please provide batch input (files or zip).")
            st.stop()

        if not items:
            st.error("No usable DOCX/PDF/TXT files found in your batch input.")
            st.stop()

        results = []
        status_rows = []

        progress = st.progress(0.0)
        status_placeholder = st.empty()
        msg_placeholder = st.empty()

        total = len(items)
        t0_batch = time.time()

        with st.spinner(f"Running batch for task: {task_label} ..."):
            for i, (student_name, file_bytes, original_name) in enumerate(items, start=1):
                safe_student = safe_name(student_name)
                run_out = batch_out / safe_student
                run_out.mkdir(parents=True, exist_ok=True)

                t0 = time.time()
                msg_placeholder.write(f"Running {i}/{total}: {student_name} ({original_name})")

                res = _run_one(
                    student_name=display_student(student_name),
                    file_bytes=file_bytes,
                    original_name=original_name,
                    run_out=run_out,
                    spec_default=spec_default,
                    prompt_default=prompt_default,
                    use_default_spec=use_default_spec,
                    use_default_prompt=use_default_prompt,
                    spec_up=spec_up,
                    prompt_up=prompt_up,
                    llm_cfg=llm_cfg,
                )

                elapsed = time.time() - t0
                results.append(res)

                status_rows.append({
                    "i": i,
                    "student": student_name,
                    "file": original_name,
                    "status": "OK" if res["ok"] else "ERROR",
                    "sec": round(elapsed, 1),
                    "error": "" if res["ok"] else res["error"],
                    "out_dir": str(run_out),
                })

                progress.progress(i / total)
                status_placeholder.dataframe(status_rows, use_container_width=True)
                msg_placeholder.write(
                    f"Finished {i}/{total}: {student_name} -> "
                    f"{status_rows[-1]['status']} ({elapsed:.1f}s)"
                )

        batch_elapsed = time.time() - t0_batch

        st.subheader(f"Batch status (final) — elapsed {batch_elapsed:.1f}s")
        st.dataframe(status_rows, use_container_width=True)

        zip_path = batch_out / "outputs.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for r in results:
                student_folder = r["safe_student"]
                run_folder = Path(r["run_out"])

                if r["docx"]:
                    z.write(r["docx"], arcname=f"{student_folder}/{Path(r['docx']).name}")
                if r["xlsx"]:
                    z.write(r["xlsx"], arcname=f"{student_folder}/{Path(r['xlsx']).name}")

                for f in sorted(run_folder.glob("debug_*")):
                    z.write(f, arcname=f"{student_folder}/{f.name}")

                if (not r["ok"]) and r["error"]:
                    err_path = run_folder / "error.txt"
                    err_path.write_text(r["error"], encoding="utf-8")
                    z.write(err_path, arcname=f"{student_folder}/error.txt")

        st.success("Batch done. Download all outputs:")
        st.download_button(
            "Download ZIP (all students)",
            data=zip_path.read_bytes(),
            file_name=zip_path.name,
            mime="application/zip",
        )