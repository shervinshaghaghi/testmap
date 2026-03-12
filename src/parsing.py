# src/parsing.py
import json
import re
from pathlib import Path
from typing import Any

from .config import LLMConfig
from .llm import chat
from .loaders import clamp


REQ_ID_RE = re.compile(
    r"^(R\d{1,2}|OOB|MIX:\s*R\d{1,2}(\s*\+\s*R\d{1,2})+)$",
    re.IGNORECASE,
)


def normalize_case_id(cid: str) -> str:
    c = (cid or "").strip().upper()
    c = re.sub(r"\s+", "", c)
    c = c.replace(":", "-").replace("_", "-")

    m = re.fullmatch(r"TC-?(\d+)", c)
    if m:
        return f"TC-{int(m.group(1)):03d}"

    return c


def extract_expected_case_ids(student_text: str) -> list[str]:
    out: list[str] = []
    seen = set()

    for m in re.finditer(r"(?im)^\s*ID\s*:\s*([A-Za-z0-9_-]+)\s*$", student_text):
        cid = normalize_case_id(m.group(1))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)

    for m in re.finditer(r"\bTC[-_][A-Za-z0-9_-]+\b", student_text, flags=re.IGNORECASE):
        cid = normalize_case_id(m.group(0))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)

    for m in re.finditer(r"\bTC\s*[:#-]?\s*\d+\b", student_text, flags=re.IGNORECASE):
        cid = normalize_case_id(m.group(0))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)

    return out


def extract_json_object(text: str) -> dict:
    if text is None:
        return {}

    t = text.strip()

    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL | re.IGNORECASE)
    if m:
        t = m.group(1).strip()

    start = t.find("{")
    if start == -1:
        return {}

    depth = 0
    end = -1
    in_string = False
    escape = False

    for i, ch in enumerate(t[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return {}

    try:
        obj = json.loads(t[start:end])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def extract_case_ids_via_llm(student_text: str, out_dir: Path, cfg: LLMConfig) -> list[str]:
    system = (
        "Extract test case identifiers from the provided text.\n"
        "Return ONLY JSON in exactly this format:\n"
        "{\"case_ids\":[\"...\"]}\n"
        "Rules:\n"
        "- case_ids must be copied from the text (do not invent).\n"
        "- keep order of appearance.\n"
        "- unique only.\n"
        "- if none, return {\"case_ids\":[]}."
    )
    user = "TEXT:\n" + clamp(student_text, 12000)

    raw = chat(system=system, user=user, cfg=cfg, force_json=True, debug_dir=out_dir)
    (out_dir / "debug_caseids_raw.txt").write_text(raw or "", encoding="utf-8", errors="ignore")

    obj = extract_json_object(raw)
    ids = obj.get("case_ids", [])
    if not isinstance(ids, list):
        ids = []

    out: list[str] = []
    seen = set()
    for x in ids:
        if not isinstance(x, str):
            continue
        cid = normalize_case_id(x)
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)

    (out_dir / "debug_caseids_list.txt").write_text("\n".join(out), encoding="utf-8", errors="ignore")
    return out


def label_spec_requirements(spec_text: str, max_r: int = 19) -> tuple[str, list[str]]:
    lines = (spec_text or "").splitlines()
    labeled_lines = []
    r_count = 0

    for line in lines:
        m = re.match(r"^\s*(\d+)\s*[-–]\s*(.+?)\s*$", line)
        if m and r_count < max_r:
            r_count += 1
            labeled_lines.append(f"R{r_count}: {m.group(2).strip()}")
        else:
            labeled_lines.append(line)

    allowed = [f"R{i}" for i in range(1, r_count + 1)]
    return "\n".join(labeled_lines), allowed


def sanitize_req_id(req: str, allowed_rids: list[str]) -> str:
    req = (req or "").strip()
    if not req:
        return "OOB"

    req_u = req.upper().strip()
    req_u = re.sub(r"\s+", " ", req_u).strip()
    req_u = req_u.replace("MIX :", "MIX:").replace("MIX:  ", "MIX: ")

    req_u = re.sub(r"\bR(\d{1,2})\.\d+\b", r"R\1", req_u)

    rids = re.findall(r"\bR\d{1,2}\b", req_u)
    if len(rids) >= 2 and not req_u.startswith("MIX:") and req_u != "OOB":
        req_u = "MIX: " + "+".join(rids)

    if req_u.startswith("MIX:"):
        req_u = re.sub(r"\s*\+\s*", "+", req_u)
        req_u = req_u.replace("MIX: ", "MIX: ")

    allowed_set = set(x.upper() for x in allowed_rids)

    if REQ_ID_RE.match(req_u):
        if req_u.startswith("R"):
            return req_u if req_u in allowed_set else "OOB"
        if req_u == "OOB":
            return "OOB"
        if req_u.startswith("MIX:"):
            rnums = re.findall(r"R(\d{1,2})", req_u)
            if rnums and all(f"R{int(n)}" in allowed_set for n in rnums):
                return req_u
            return "OOB"

    if req_u in allowed_set:
        return req_u

    return "OOB"


def is_expected_mapping_header_line(line: str) -> bool:
    if not line or "|" not in line:
        return False
    low = line.lower()
    return ("student" in low) and ("caseid" in low or "case id" in low) and (
        "requirementid" in low or "requirement id" in low
    )


def has_expected_mapping_header(text: str) -> bool:
    for l in (text or "").splitlines():
        if is_expected_mapping_header_line(l.strip()):
            return True
    return False


def build_user_for_table(student: str, labeled_spec: str, student_text: str, case_ids: list[str]) -> str:
    return (
        f"Student: {student}\n\n"
        f"ALLOWED CASEIDS:\n{', '.join(case_ids)}\n\n"
        f"SPECIFICATION (R1..R19):\n{clamp(labeled_spec, 7000)}\n\n"
        f"STUDENT TEST CASES:\n{clamp(student_text, 9000)}\n"
    )


def build_retry_message(user_payload: str, attempt_no: int) -> str:
    if attempt_no == 1:
        return (
            "You returned the wrong format.\n"
            "Return ONLY a markdown pipe table with EXACTLY these columns:\n"
            "| Student | CaseID | RequirementID | Rationale (short) |\n"
            "RequirementID must be exactly one of: R1..R19, OOB, or MIX: R1+R2+...\n"
            "Do NOT output Goal/Inputs/Expected Outputs.\n"
            "Do NOT use decimals like R1.1.\n"
            "Rationale must not contain the '|' character.\n\n"
            + user_payload
        )
    if attempt_no == 2:
        return (
            "STOP. Your output is still not the required mapping table.\n"
            "Return ONLY this table header, then one row per allowed CaseID:\n"
            "| Student | CaseID | RequirementID | Rationale (short) |\n"
            "| --- | --- | --- | --- |\n"
            "No other text.\n"
            "RequirementID must be: R1..R19 or OOB or MIX: R1+R2.\n"
            "Rationale must not contain the '|' character.\n\n"
            + user_payload
        )
    return (
        "FINAL TRY: output ONLY the mapping table. No other tables.\n"
        "Columns must be exactly: Student, CaseID, RequirementID, Rationale (short).\n"
        "If you output ID/Goal/Inputs/Expected Outputs again, it will be rejected.\n"
        "Rationale must not contain the '|' character.\n\n"
        + user_payload
    )


def parse_llm_table_output(
    raw: str, student_name: str, expected_case_ids: list[str], allowed_rids: list[str]
) -> dict:
    lines = [l.rstrip() for l in (raw or "").splitlines()]

    header_i = -1
    for i, l in enumerate(lines):
        if is_expected_mapping_header_line((l or "").strip()):
            header_i = i
            break

    if header_i == -1:
        return {
            "student": student_name,
            "rows": [
                {"case_id": cid, "requirement_id": "OOB", "rationale": "unparsed LLM output"}
                for cid in expected_case_ids
            ],
            "notes": {"oob_or_mix": [], "problematic_students": "unparsed LLM output"},
        }

    table_lines = []
    stop_i = len(lines)
    for i in range(header_i, len(lines)):
        l = lines[i]
        low = (l or "").lower().strip()

        if "oob (and mix) details" in low or low.startswith("oob (and mix)"):
            stop_i = i
            break
        if "problematic students" in low:
            stop_i = i
            break

        if "|" in (l or ""):
            table_lines.append(l)

    parsed = []
    expected_set = set(expected_case_ids)

    for l in table_lines[1:]:
        if re.match(r"^\s*\|?\s*[-: ]+\|", (l or "")):
            continue

        cells = [c.strip() for c in (l or "").split("|")]
        cells = [c for c in cells if c != ""]
        if len(cells) < 4:
            continue

        cid_n = normalize_case_id(cells[1])
        if cid_n not in expected_set:
            continue

        rid_s = sanitize_req_id(cells[2], allowed_rids)
        rat_s = " ".join((" ".join(cells[3:]) or "").split()).strip() or "No rationale."
        if len(rat_s.split()) > 18:
            rat_s = " ".join(rat_s.split()[:18])

        parsed.append({"case_id": cid_n, "requirement_id": rid_s, "rationale": rat_s})

    by = {r["case_id"]: r for r in parsed}
    ordered = [
        by.get(cid, {"case_id": cid, "requirement_id": "OOB", "rationale": "missing from LLM table"})
        for cid in expected_case_ids
    ]

    tail = "\n".join(lines[stop_i:]).strip()
    oob_or_mix = []
    problematic = "None"

    for m in re.finditer(r"(?m)^\s*[-*•]\s*([A-Za-z0-9_.:-]+)\s*[—-]\s*(.+?)\s*$", tail):
        cid = normalize_case_id(m.group(1))
        msg = (m.group(2) or "").strip()
        if cid and msg:
            oob_or_mix.append({"case_id": cid, "detail": msg})

    m = re.search(r"(?im)problematic students.*?:\s*(.+?)\s*\.?\s*$", tail)
    if m:
        problematic = (m.group(1) or "").strip()

    return {
        "student": student_name,
        "rows": ordered,
        "notes": {"oob_or_mix": oob_or_mix, "problematic_students": problematic},
    }