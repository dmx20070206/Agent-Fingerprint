"""Local provider contracts without downloading weights or allocating GPUs."""
import argparse
import base64
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from agent_fingerprint.cli import vllm
from agent_fingerprint.cli.experiment import build_plan
from agent_fingerprint.collection.gateway.litellm_gateway import GatewayConfig
from agent_fingerprint.paths import PROJECT_ROOT


def test_catalog_matches_gateway_and_experiment():
    catalog = vllm.load_catalog(vllm.DEFAULT_CONFIG)
    routes = GatewayConfig.load(PROJECT_ROOT / "configs/litellm.config.yaml").routes
    experiment = yaml.safe_load((PROJECT_ROOT / "configs/experiments/default.yaml").read_text())
    assert len(catalog["models"]) == 6
    ports = set()
    for alias, model in catalog["models"].items():
        route = routes[alias]
        assert route.model == "openai/" + alias
        assert route.api_base == f"http://127.0.0.1:{model['port']}/v1"
        assert route.api_key_env == "VLLM_API_KEY"
        assert model["port"] not in ports
        ports.add(model["port"])
        plan = build_plan(experiment, agents=["webvoyager"], models=[alias], tasks=["shop"], repeats=1)
        assert plan[0]["model"] == alias
        assert "--model" in plan[0]["argv"]
    assert experiment["matrix"]["models"] == ["chat-gpt", "claude", "gemini", "deepseek-chat"]


def test_phi_vision_adapter_and_path_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    catalog = vllm.load_catalog(vllm.DEFAULT_CONFIG)
    args = argparse.Namespace(executable="/env/bin/vllm")
    command = vllm.serve_command(catalog, "phi-4-multimodal", args)
    assert Path(command[2]).is_absolute()
    adapters = json.loads(command[command.index("--default-mm-loras") + 1])
    assert adapters["image"] == str(PROJECT_ROOT / "models/Phi-4-multimodal-instruct/vision-lora")
    assert command[command.index("--max-lora-rank") + 1] == "320"


def test_serve_passes_credentials_only_in_environment(tmp_path, monkeypatch, capsys):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").touch()
    monkeypatch.setattr(vllm, "model_path", lambda *args: tmp_path)
    monkeypatch.setenv("VLLM_API_KEY", "sk-test-private-value")
    monkeypatch.setattr(vllm.shutil, "which", lambda name: "/bin/vllm")
    calls = []
    monkeypatch.setattr(vllm.os, "execvpe", lambda *args: calls.append(args))
    vllm.main(["serve", "qwen3.5-9b", "--gpus", "2,3", "--tensor-parallel-size", "2"])
    executable, command, env = calls[0]
    assert executable == "vllm"
    assert env["CUDA_VISIBLE_DEVICES"] == "2,3"
    assert env["VLLM_API_KEY"] == "sk-test-private-value"
    assert "sk-test-private-value" not in " ".join(command)
    assert "sk-test-private-value" not in capsys.readouterr().out
    assert command[command.index("--tensor-parallel-size") + 1] == "2"


def test_missing_key_or_invalid_gpu_selection_never_starts(monkeypatch, capsys):
    monkeypatch.setattr(vllm, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    monkeypatch.setattr(vllm.os, "execvpe", lambda *args: pytest.fail("must not start"))
    assert vllm.main(["serve", "qwen3.5-9b"]) == 1
    assert "VLLM_API_KEY" in capsys.readouterr().err
    assert vllm.main(["serve", "qwen3.5-27b", "--gpus", "1", "--dry-run"]) == 1
    assert "tensor parallel size is 4" in capsys.readouterr().err
    assert vllm.main(["serve", "qwen3.5-9b", "--dry-run"]) == 0


def test_missing_shards_and_phi_adapter_are_rejected(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model-1.safetensors").touch()
    (tmp_path / "model.safetensors.index.json").write_text(json.dumps({
        "weight_map": {"one": "model-1.safetensors", "two": "model-2.safetensors"},
    }))
    with pytest.raises(ValueError, match="Incomplete model download"):
        vllm.validate_weights(tmp_path, {})
    (tmp_path / "model-2.safetensors").touch()
    with pytest.raises(ValueError, match="Missing Phi vision adapter"):
        vllm.validate_weights(tmp_path, {"vision_lora": "vision-lora"})
    vllm.validate_weights(tmp_path, {})


@pytest.fixture
def endpoint():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer sk-test":
                self.send_error(401)
                return
            requests.append((self.path, None))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"qwen3.5-9b"}]}')

        def do_POST(self):
            assert self.headers.get("Authorization") == "Bearer sk-test"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_authenticated_text_and_image_wire_format(endpoint, tmp_path):
    url, requests = endpoint
    kwargs = {"timeout": 2, "max_tokens": 20}
    with pytest.raises(urllib.error.HTTPError) as error:
        vllm.check_endpoint(url, "qwen3.5-9b", "wrong", image=None, **kwargs)
    assert error.value.code == 401
    result = vllm.check_endpoint(url, "qwen3.5-9b", "sk-test", image=None, **kwargs)
    assert result["message"]["content"] == "OK"
    with pytest.raises(ValueError, match="does not advertise"):
        vllm.check_endpoint(url, "wrong-alias", "sk-test", image=None, **kwargs)
    image = tmp_path / "screenshot.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    ))
    vllm.check_endpoint(url, "qwen3.5-9b", "sk-test", image=image, **kwargs)
    path, body = requests[-1]
    assert path == "/v1/chat/completions"
    assert body["model"] == "qwen3.5-9b"
    image_url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    assert base64.b64decode(image_url.split(",", 1)[1]) == image.read_bytes()
