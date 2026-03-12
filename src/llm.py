from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from .config import LLMConfig

# ---- llama-cpp-python (GGUF) singleton cache ----
_LLAMA: Dict[Tuple[str, int, int, int], Any] = {}


def _safe_write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text or "", encoding="utf-8", errors="ignore")
    except Exception:
        pass


def _safe_write_json(path: Path, obj: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(obj, indent=2, ensure_ascii=False),
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        pass


def _ollama_chat(
    system: str,
    user: str,
    cfg: LLMConfig,
    force_json: bool = False,
    *,
    num_predict: int = 2048,
) -> str:
    url = f"{cfg.url.rstrip('/')}/api/chat"
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": float(cfg.temperature),
            "seed": int(cfg.seed),
            "num_predict": int(num_predict),
        },
    }
    if force_json:
        payload["format"] = "json"

    try:
        r = requests.post(url, json=payload, timeout=cfg.timeout_sec)
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to reach Ollama at {url}: {e}") from e

    if not r.ok:
        body = (r.text or "").strip()
        if len(body) > 2000:
            body = body[:2000] + " ...[truncated]"
        raise RuntimeError(f"Ollama error {r.status_code} from {url}: {body}")

    data = r.json()
    msg = data.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]
    if isinstance(data.get("response"), str):
        return data["response"]
    return ""


def _gemini_chat(
    system: str,
    user: str,
    cfg: LLMConfig,
    force_json: bool = False,
    *,
    num_predict: int = 4096,
) -> Tuple[str, Dict[str, Any]]:
    if not cfg.api_key:
        raise ValueError("LLMConfig.api_key is required when backend='gemini'.")

    model = (cfg.model or "").strip() or "gemini-2.5-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={cfg.api_key}"
    )

    guard = "Respond with final answer only. Do not output hidden reasoning."
    sys_final = ((system or "").strip() + "\n\n" + guard).strip()

    if force_json:
        sys_final += (
            "\n\nReturn ONLY valid JSON."
            "\nDo not use markdown."
            "\nDo not use code fences."
            "\nDo not include explanations before or after the JSON."
        )

    payload = {
        "systemInstruction": {
            "parts": [{"text": sys_final}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user}],
            }
        ],
        "generationConfig": {
            "temperature": float(cfg.temperature),
            "maxOutputTokens": int(num_predict),
        },
    }

    t0 = time.time()
    try:
        r = requests.post(url, json=payload, timeout=cfg.timeout_sec)
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to reach Gemini API: {e}") from e
    t1 = time.time()

    if not r.ok:
        body = (r.text or "").strip()
        if len(body) > 2000:
            body = body[:2000] + " ...[truncated]"
        raise RuntimeError(f"Gemini error {r.status_code}: {body}")

    data = r.json()

    text = ""
    candidates = data.get("candidates") or []
    if candidates:
        content = (candidates[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        texts = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        text = "\n".join(texts).strip()

    meta = {
        "backend": "gemini",
        "model": model,
        "temperature": float(cfg.temperature),
        "max_tokens": int(num_predict),
        "elapsed_sec": round(t1 - t0, 4),
    }
    return text, meta


def _get_llama(cfg: LLMConfig):
    if not cfg.model_path:
        raise ValueError("LLMConfig.model_path is required when backend='llama_cpp'.")

    key = (
        str(cfg.model_path),
        int(cfg.n_ctx),
        int(cfg.n_gpu_layers),
        int(cfg.n_threads),
    )
    if key in _LLAMA:
        return _LLAMA[key]

    from llama_cpp import Llama  # type: ignore

    llama = Llama(
        model_path=str(cfg.model_path),
        n_ctx=int(cfg.n_ctx),
        n_gpu_layers=int(cfg.n_gpu_layers),
        n_threads=int(cfg.n_threads),
        seed=int(cfg.seed),
        verbose=False,
    )
    _LLAMA[key] = llama
    return llama


def _llama_cpp_chat(
    system: str,
    user: str,
    cfg: LLMConfig,
    force_json: bool = False,
    *,
    num_predict: int = 2048,
) -> Tuple[str, Dict[str, Any]]:
    llama = _get_llama(cfg)

    guard = "Respond with final answer only. Do not output hidden reasoning."
    sys_final = ((system or "").strip() + "\n\n" + guard).strip()
    if force_json:
        sys_final += "\n\nReturn ONLY valid JSON. No markdown."

    messages = [
        {"role": "system", "content": sys_final},
        {"role": "user", "content": user},
    ]

    t0 = time.time()
    resp = llama.create_chat_completion(
        messages=messages,
        temperature=float(cfg.temperature),
        max_tokens=int(num_predict),
    )
    t1 = time.time()

    text = ""
    choices = resp.get("choices") or []
    if choices:
        msg = (choices[0] or {}).get("message") or {}
        if isinstance(msg, dict):
            text = str(msg.get("content") or "")

    usage = resp.get("usage") or {}
    meta = {
        "backend": "llama_cpp",
        "model_path": str(cfg.model_path),
        "n_ctx": int(cfg.n_ctx),
        "n_gpu_layers": int(cfg.n_gpu_layers),
        "n_threads": int(cfg.n_threads),
        "temperature": float(cfg.temperature),
        "max_tokens": int(num_predict),
        "elapsed_sec": round(t1 - t0, 4),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "finish_reason": ((resp.get("choices") or [{}])[0] or {}).get("finish_reason"),
    }
    return text, meta


def chat(
    system: str,
    user: str,
    cfg: LLMConfig,
    force_json: bool = False,
    *,
    num_predict: Optional[int] = None,
    debug_dir: Optional[Path] = None,
) -> str:
    backend = (cfg.backend or "").lower()

    if num_predict is None:
        if backend == "gemini":
            num_predict = 4096
        else:
            num_predict = 4096 if force_json else 2048

    if backend == "ollama":
        txt = _ollama_chat(
            system,
            user,
            cfg,
            force_json=force_json,
            num_predict=int(num_predict),
        )
        meta = {
            "backend": "ollama",
            "host": cfg.url,
            "model": cfg.model,
            "force_json": bool(force_json),
            "num_predict": int(num_predict),
        }

    elif backend == "gemini":
        force_json = True
        txt, meta = _gemini_chat(
            system,
            user,
            cfg,
            force_json=force_json,
            num_predict=int(num_predict),
        )
        meta["force_json"] = bool(force_json)

    else:
        txt, meta = _llama_cpp_chat(
            system,
            user,
            cfg,
            force_json=force_json,
            num_predict=int(num_predict),
        )
        meta["force_json"] = bool(force_json)

    if debug_dir is not None:
        raw_name = "debug_llm_json_raw.txt" if force_json else "debug_llm_raw.txt"
        meta_name = "debug_llm_json_meta.json" if force_json else "debug_llm_meta.json"
        _safe_write_text(debug_dir / raw_name, txt)
        _safe_write_json(debug_dir / meta_name, meta)

    return txt