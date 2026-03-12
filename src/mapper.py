import re
from pathlib import Path
from typing import Tuple, Dict, Any, List

from .config import LLMConfig
from .llm import chat
from .loaders import load_docx_text, load_student_text
from .parsing import (
    extract_case_ids_via_llm,
    extract_expected_case_ids,
    label_spec_requirements,
    build_user_for_table,
    build_retry_message,
    has_expected_mapping_header,
    parse_llm_table_output,
    extract_json_object,
    normalize_case_id,
    sanitize_req_id,
)
from .writers.word_writer import write_result_docx
from .writers.excel_writer import write_student_xlsx


def json_fallback_mapping(
    student: str,
    labeled_spec: str,
    student_text: str,
    case_ids: List[str],
    allowed_rids: List[str],
    out_dir: Path,
    cfg: LLMConfig,
) -> Dict[str, Any]:
    system = (
        "You map student test cases to exactly ONE RequirementID.\n"
        "Return ONLY valid JSON in this exact format:\n"
        "{\"rows\":[{\"case_id\":\"TC-001\",\"requirement_id\":\"R1\",\"rationale\":\"...\"}]}\n"
        "Rules:\n"
        "- requirement_id must be exactly R1..R19, OOB, or MIX: R1+R2+...\n"
        "- rationale max 12 words.\n"
        "- one row for every provided case_id.\n"
        "- do not invent case_ids.\n"
        "- do not omit any provided case_id.\n"
        "- output JSON only.\n"
    )

    user = build_user_for_table(student, labeled_spec, student_text, case_ids)

    raw = chat(
        system=system,
        user=user,
        cfg=cfg,
        force_json=True,
        num_predict=4096,
        debug_dir=out_dir,
    )

    obj = extract_json_object(raw)
    rows = obj.get("rows", [])
    if not isinstance(rows, list):
        rows = []

    by: Dict[str, Dict[str, str]] = {}
    case_set = set(case_ids)

    for r in rows:
        if not isinstance(r, dict):
            continue

        cid = normalize_case_id(r.get("case_id", ""))
        if cid not in case_set:
            continue

        rid = sanitize_req_id(str(r.get("requirement_id", "")), allowed_rids)

        rat = str(r.get("rationale", "") or "").strip()
        rat = " ".join(rat.split())
        if not rat:
            rat = "No rationale."
        if len(rat.split()) > 12:
            rat = " ".join(rat.split()[:12])

        by[cid] = {
            "case_id": cid,
            "requirement_id": rid,
            "rationale": rat,
        }

    ordered = [
        by.get(
            cid,
            {
                "case_id": cid,
                "requirement_id": "OOB",
                "rationale": "missing from json output",
            },
        )
        for cid in case_ids
    ]

    return {
        "student": student,
        "rows": ordered,
        "notes": {"oob_or_mix": [], "problematic_students": "None"},
    }


def run_mapping(
    student: str,
    student_file: Path,
    spec_path: Path,
    prompt_path: Path,
    out_dir: Path,
    llm_cfg: LLMConfig,
) -> Tuple[Dict[str, Any], Path, Path]:
    prompt_path = Path(prompt_path)
    spec_path = Path(spec_path)
    student_file = Path(student_file)
    out_dir = Path(out_dir)

    if not prompt_path.exists():
        raise FileNotFoundError(f"Missing prompt file: {prompt_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"Missing spec file: {spec_path}")
    if not student_file.exists():
        raise FileNotFoundError(f"Missing student file: {student_file}")

    out_dir.mkdir(parents=True, exist_ok=True)

    prompt_text = load_docx_text(prompt_path)
    spec_text = load_docx_text(spec_path)
    student_text = load_student_text(student_file)

    labeled_spec, allowed_rids = label_spec_requirements(spec_text, max_r=19)
    if not allowed_rids:
        allowed_rids = [f"R{i}" for i in range(1, 20)]
        labeled_spec = spec_text

    # CaseIDs: merge llm + regex
    case_ids_llm = extract_case_ids_via_llm(student_text, out_dir, cfg=llm_cfg)
    case_ids_re = extract_expected_case_ids(student_text)

    case_ids: List[str] = []
    seen = set()
    for cid in case_ids_llm + case_ids_re:
        cidn = normalize_case_id(cid)
        if cidn and cidn not in seen:
            seen.add(cidn)
            case_ids.append(cidn)

    if not case_ids:
        raise RuntimeError("No CaseIDs found.")

    backend = (llm_cfg.backend or "").lower()

    # Gemini should use JSON directly, not markdown table mode
    if backend == "gemini":
        result = json_fallback_mapping(
            student=student,
            labeled_spec=labeled_spec,
            student_text=student_text,
            case_ids=case_ids,
            allowed_rids=allowed_rids,
            out_dir=out_dir,
            cfg=llm_cfg,
        )
    else:
        system = prompt_text
        user = build_user_for_table(student, labeled_spec, student_text, case_ids)

        raw = ""
        for attempt in range(0, 3):
            if attempt == 0:
                raw = chat(
                    system=system,
                    user=user,
                    cfg=llm_cfg,
                    force_json=False,
                    num_predict=2048,
                    debug_dir=out_dir,
                )
            else:
                retry_user = build_retry_message(user, attempt)
                raw = chat(
                    system=system,
                    user=retry_user,
                    cfg=llm_cfg,
                    force_json=False,
                    num_predict=2048,
                    debug_dir=out_dir,
                )

            if has_expected_mapping_header(raw):
                break

        if has_expected_mapping_header(raw):
            result = parse_llm_table_output(raw, student, case_ids, allowed_rids)
        else:
            result = json_fallback_mapping(
                student=student,
                labeled_spec=labeled_spec,
                student_text=student_text,
                case_ids=case_ids,
                allowed_rids=allowed_rids,
                out_dir=out_dir,
                cfg=llm_cfg,
            )

    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", student).strip("_") or "STUDENT"

    out_docx = out_dir / f"{safe}.docx"
    write_result_docx(result, out_docx)

    out_xlsx = out_dir / f"{safe}.xlsx"
    write_student_xlsx(result, out_xlsx, max_r=19)

    return result, out_docx, out_xlsx