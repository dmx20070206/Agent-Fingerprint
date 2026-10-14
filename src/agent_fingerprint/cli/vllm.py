"""Download, serve and check local models behind the existing LiteLLM gateway."""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shlex
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent_fingerprint.paths import PROJECT_ROOT

DEFAULT_CONFIG = PROJECT_ROOT / "configs/vllm.models.yaml"


def load_catalog(path: Path) -> dict:
    catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or catalog.get("schema") != "agent-fingerprint-vllm/v1":
        raise ValueError("Unsupported vLLM model catalog schema")
    if not isinstance(catalog.get("models"), dict) or not catalog["models"]:
        raise ValueError("vLLM catalog requires a non-empty models mapping")
    return catalog


def model_path(catalog: dict, model: dict) -> Path:
    root = Path(catalog.get("models_root", "models")).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return (root / model["directory"]).resolve()


def validate_weights(path: Path, model: dict) -> None:
    if not path.is_dir() or not any((path / f).is_file() for f in ("config.json", "params.json")):
        raise ValueError(f"Missing local model configuration in {path}; run 'af vllm download <alias>' first")
    if not any(path.glob("*.safetensors")):
        raise ValueError(f"No safetensors weights in {path}")
    if index := next(path.glob("*.safetensors.index.json"), None):
        shards = set(json.loads(index.read_text())["weight_map"].values())
        missing = sorted(name for name in shards if not (path / name).is_file())
        if missing:
            raise ValueError(f"Incomplete model download: missing {', '.join(missing)}")
    if model.get("vision_lora"):
        adapter = path / model["vision_lora"]
        if not (adapter / "adapter_config.json").is_file() or not any(adapter.glob("*.safetensors")):
            raise ValueError(f"Missing Phi vision adapter files in {adapter}")


def serve_command(catalog: dict, alias: str, args: argparse.Namespace) -> list[str]:
    model = catalog["models"][alias]
    settings = {**catalog.get("defaults", {}), **model}
    path = model_path(catalog, model)
    command = [args.executable, "serve", str(path), "--served-model-name", alias]
    for name in ("host", "port", "tensor_parallel_size", "max_model_len",
                 "gpu_memory_utilization", "max_num_seqs"):
        value = getattr(args, name, None)
        if value is None:
            value = settings[name]
        if name in {"port", "tensor_parallel_size", "max_model_len", "max_num_seqs"} and (
            type(value) is not int or value < 1 or (name == "port" and value > 65535)
        ):
            raise ValueError(f"Invalid {name}: {value}")
        if name == "gpu_memory_utilization" and not 0 < float(value) <= 1:
            raise ValueError("gpu_memory_utilization must be in (0, 1]")
        command.extend(["--" + name.replace("_", "-"), str(value)])
    command.extend(str(value) for value in model.get("serve_args", []))
    if model.get("vision_lora"):
        command.extend(["--default-mm-loras", json.dumps({"image": str(path / model["vision_lora"])})])
    return command


def check_endpoint(base_url: str, alias: str, key: str, *, image: Path | None,
                   timeout: float, max_tokens: int) -> dict:
    # Local inference must not be sent through a machine's HTTP(S) proxy.
    host = urllib.parse.urlsplit(base_url).hostname
    opener = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({})] if host in {"localhost", "127.0.0.1", "::1"} else [])
    )

    def request(resource, body=None):
        req = urllib.request.Request(
            base_url.rstrip("/") + resource,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)

    available = request("/models")
    if alias not in {item.get("id") for item in available.get("data", [])}:
        raise ValueError(f"Endpoint does not advertise model {alias!r}")
    content = [{"type": "text", "text": "Describe the image briefly." if image else "Reply with OK."}]
    if image:
        mime = mimetypes.guess_type(image.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("--image must be a PNG, JPEG or WebP file")
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    result = request("/chat/completions", {
        "model": alias, "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    })
    choices = result.get("choices") or []
    if not choices or not choices[0].get("message", {}).get("content"):
        raise ValueError("No visible chat response; inspect server logs or increase --max-tokens for reasoning models")
    if choices[0].get("finish_reason") == "length":
        raise ValueError("Chat response was truncated; increase --max-tokens")
    return {"model": alias, "base_url": base_url, "image": str(image) if image else None,
            "message": choices[0]["message"], "usage": result.get("usage")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List aliases, local paths and ports")
    download = sub.add_parser("download", help="Download a complete Hugging Face snapshot")
    download.add_argument("model", help="Model alias, or all")
    download.add_argument("--revision", help="Pin a Hugging Face commit/tag (single model only)")
    serve = sub.add_parser("serve", help="Run one vLLM server in the foreground; Ctrl-C stops it")
    serve.add_argument("model")
    serve.add_argument("--executable", default="vllm", help="vLLM executable, including one in a separate environment")
    serve.add_argument("--gpus", help="CUDA_VISIBLE_DEVICES, e.g. 1 or 1,2,3,4")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--tensor-parallel-size", type=int)
    serve.add_argument("--max-model-len", type=int)
    serve.add_argument("--max-num-seqs", type=int)
    serve.add_argument("--gpu-memory-utilization", type=float)
    serve.add_argument("--dry-run", action="store_true", help="Print the command without requiring weights, a key or vLLM")
    check = sub.add_parser("check", help="Check authenticated model discovery and chat, optionally with an image")
    check.add_argument("model")
    check.add_argument("--base-url", help="Override the default vLLM URL, e.g. http://127.0.0.1:4000/v1")
    check.add_argument("--key-env", default="VLLM_API_KEY")
    check.add_argument("--image", type=Path)
    check.add_argument("--timeout", type=float, default=180)
    check.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    try:
        catalog = load_catalog(args.config)
        models = catalog["models"]
        if args.action == "list":
            for alias, model in models.items():
                path = model_path(catalog, model)
                print(f"{alias}\t:{model['port']}\t{model['repo_id']}\t{path}\t"
                      f"{'directory exists' if path.is_dir() else 'not downloaded'}")
            return 0
        if args.model not in models and not (args.action == "download" and args.model == "all"):
            raise ValueError(f"Unknown model {args.model!r}; use 'af vllm list'")
        if args.action == "download":
            if args.model == "all" and args.revision:
                raise ValueError("--revision requires a single model alias")
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                raise ValueError("Install download support: python -m pip install huggingface_hub") from None
            for alias in models if args.model == "all" else [args.model]:
                model = models[alias]
                path = model_path(catalog, model)
                print(f"Downloading {model['repo_id']} -> {path}", flush=True)
                snapshot_download(repo_id=model["repo_id"], local_dir=str(path), revision=args.revision)
                validate_weights(path, model)
            return 0
        model = models[args.model]
        if args.action == "check":
            key = os.environ.get(args.key_env, "").strip()
            if not key:
                raise ValueError(f"Set {args.key_env} in .env or the environment")
            if args.timeout <= 0 or args.max_tokens <= 0:
                raise ValueError("--timeout and --max-tokens must be positive")
            base = args.base_url or f"http://127.0.0.1:{model['port']}/v1"
            result = check_endpoint(base, args.model, key, image=args.image,
                                    timeout=args.timeout, max_tokens=args.max_tokens)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        command = serve_command(catalog, args.model, args)
        env = os.environ.copy()
        if args.gpus is not None:
            devices = [device.strip() for device in args.gpus.split(",")]
            if any(not device for device in devices) or len(set(devices)) != len(devices):
                raise ValueError("--gpus must contain distinct, non-empty device identifiers")
            tp = int(command[command.index("--tensor-parallel-size") + 1])
            if len(devices) < tp:
                raise ValueError(f"Selected {len(devices)} GPUs but tensor parallel size is {tp}")
            env["CUDA_VISIBLE_DEVICES"] = ",".join(devices)
        if args.dry_run:
            print("Authentication: VLLM_API_KEY environment variable (value omitted)")
            if "CUDA_VISIBLE_DEVICES" in env:
                print("CUDA_VISIBLE_DEVICES=" + shlex.quote(env["CUDA_VISIBLE_DEVICES"]))
            print(shlex.join(command))
            return 0
        if not env.get("VLLM_API_KEY", "").strip():
            raise ValueError("Set VLLM_API_KEY in .env or the environment before serving")
        validate_weights(model_path(catalog, model), model)
        if shutil.which(args.executable) is None:
            raise ValueError("vLLM executable not found; install it in a dedicated environment (docs/reference/local-models.md)")
        # Replace this process so Ctrl-C/signals reach vLLM directly. The key
        # stays in the environment, never in argv or printed commands.
        os.execvpe(command[0], command, env)
    except (ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        print(f"vLLM: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
