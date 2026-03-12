import argparse
from pathlib import Path

from src.config import LLMConfig
from src.mapper import run_mapping


def main() -> None:
    ap = argparse.ArgumentParser()

    ap.add_argument("--student", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", required=True)

    ap.add_argument("--task", default="fileclass_v1")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--prompt", default=None)

    # ---- backend selection ----
    ap.add_argument("--backend", choices=["llama_cpp", "ollama", "gemini"], default="llama_cpp")

    # ---- shared / API-model option ----
    ap.add_argument("--model", default="llama3.1:latest", help="Model name for Ollama or Gemini.")

    # ---- llama-cpp-python (GGUF) options ----
    ap.add_argument("--model-path", default=None, help="Path to GGUF model (required for llama_cpp).")
    ap.add_argument("--n-ctx", type=int, default=4096)
    ap.add_argument("--n-gpu-layers", type=int, default=0)
    ap.add_argument("--n-threads", type=int, default=4)

    # ---- ollama options ----
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")

    # ---- gemini options ----
    ap.add_argument("--api-key", default=None, help="API key for Gemini backend.")

    # ---- shared timeout ----
    ap.add_argument("--timeout", type=int, default=900)

    args = ap.parse_args()

    task_dir = Path("tasks") / args.task
    spec_path = Path(args.spec) if args.spec else (task_dir / "spec.docx")
    prompt_path = Path(args.prompt) if args.prompt else (task_dir / "prompt.docx")

    cfg = LLMConfig(
        backend=args.backend,
        model=args.model,
        model_path=args.model_path,
        n_ctx=args.n_ctx,
        n_gpu_layers=args.n_gpu_layers,
        n_threads=args.n_threads,
        url=args.ollama_url,
        timeout_sec=args.timeout,
        api_key=args.api_key,
    )

    _, out_docx, out_xlsx = run_mapping(
        student=args.student,
        student_file=Path(args.file),
        spec_path=spec_path,
        prompt_path=prompt_path,
        out_dir=Path(args.out),
        llm_cfg=cfg,
    )

    print(f"Wrote: {out_docx}")
    print(f"Wrote: {out_xlsx}")


if __name__ == "__main__":
    main()