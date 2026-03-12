from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMConfig:
    """LLM runtime configuration.

    Supported backends:
      - backend="llama_cpp": run a local GGUF model via llama-cpp-python
      - backend="ollama": call an Ollama server over HTTP
      - backend="gemini": call the Gemini API over HTTP
    """

    # Which runtime to use
    backend: str = "llama_cpp"

    # Shared settings
    temperature: float = 0.0
    seed: int = 0
    timeout_sec: int = 900

    # ---- llama-cpp-python backend ----
    model_path: Optional[str] = None
    n_ctx: int = 4096
    n_gpu_layers: int = 0
    n_threads: int = 4

    # ---- HTTP backends (Ollama / Gemini) ----
    url: str = "http://127.0.0.1:11434"
    model: str = "llama3.1:latest"

    # ---- Gemini backend ----
    api_key: Optional[str] = None