import json
import logging
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from hydra import compose, initialize_config_dir
from hydra.errors import InstantiationException
from omegaconf import OmegaConf

from src.domain.embedding import EmbeddingProfile
from src.integrations.embedding import launch as launcher
from src.integrations.embedding.client import TEIIntegration

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "ghcr.io/huggingface/text-embeddings-inference:turing-1.9.4@sha256:"
    "f78d17832f00f59f7d52e4b3b8495264257de3c18b773357a00ee341b2af852d"
)
GPU = "GPU-12345678-1234-1234-1234-123456789abc"
CONTAINER = "c" * 64
IMAGE_ID = "sha256:" + "d" * 64


@pytest.fixture
def launch_site(tmp_path, monkeypatch):
    profile = EmbeddingProfile()
    state: dict[str, Any] = {
        "existing": "",
        "container": CONTAINER,
        "image": IMAGE_ID,
        "status": IMAGE_ID + " running",
        "health": [200],
        "info": {
            "model_id": profile.model_id,
            "model_sha": profile.revision,
            "model_dtype": "float16",
            "model_type": {"embedding": {"pooling": "mean"}},
            "version": "1.9.4",
            "max_input_length": 512,
            "max_batch_tokens": 4096,
            "max_client_batch_size": 8,
            "max_concurrent_requests": 32,
            "auto_truncate": False,
        },
        "vector": [1.0] + [0.0] * 767,
    }
    requests, commands = [], []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            requests.append((self.path, body, self.headers.get("Authorization")))
            status = 200
            if self.path == "/health":
                statuses = state["health"]
                status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
                content = b""
            elif self.path == "/info":
                content = json.dumps(state["info"]).encode()
            else:
                assert self.path == "/embed"
                assert body is not None
                content = json.dumps([state["vector"] for _ in body["inputs"]]).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = respond

        def log_message(self, format, *args):
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler, bind_and_activate=False)
    worker = None

    def docker(arguments, **kwargs):
        nonlocal worker
        commands.append((arguments, kwargs))
        if arguments[1:3] == ["container", "ls"]:
            output = state["existing"]
        elif arguments[1] == "run":
            server.server_bind()
            server.server_activate()
            worker = Thread(
                target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
            )
            worker.start()
            output = state["container"]
        elif arguments[1:3] == ["image", "inspect"]:
            output = state["image"]
        else:
            assert arguments[1:3] == ["container", "inspect"]
            output = state["status"]
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", docker)
    monkeypatch.delenv("TEI_API_KEY", raising=False)
    with initialize_config_dir(config_dir=str(ROOT / "configs"), version_base="1.3"):
        cfg = compose(
            config_name="vector",
            overrides=[
                f"embedding.runtime.image={IMAGE}",
                f"embedding.runtime.gpu_uuid={GPU}",
                f"embedding.runtime.cache_dir={tmp_path}",
                f"embedding.runtime.host_port={port}",
            ],
        )
    try:
        yield cfg, state, requests, commands
    finally:
        if worker is not None:
            server.shutdown()
            worker.join(timeout=2)
        server.server_close()


def test_launcher_composed_http_probes_safe_argv_and_secret_environment(
    launch_site, monkeypatch, caplog
):
    cfg, _, requests, commands = launch_site
    monkeypatch.setenv("TEI_API_KEY", "launch-secret")
    caplog.set_level(logging.INFO)
    assert launcher.launch(cfg) == CONTAINER
    run, options = commands[1]
    assert run[:5] == ["docker", "run", "--detach", "--name", "recsys-tei"]
    assert run[run.index("--gpus") + 1] == f"device={GPU}"
    assert run[run.index("--publish") + 1] == (
        f"127.0.0.1:{cfg.embedding.runtime.host_port}:80"
    )
    assert run[run.index("--mount") + 1].endswith(",target=/data")
    assert run[run.index("--hostname") + 1] == "0.0.0.0"
    assert run[run.index("--auto-truncate") + 1] == "false"
    assert run[run.index("--env") + 1] == "API_KEY"
    assert options["env"]["API_KEY"] == "launch-secret"
    assert "launch-secret" not in " ".join(run) + caplog.text
    assert all(options["check"] and "shell" not in options for _, options in commands)
    assert all(0 < options["timeout"] <= 600 for _, options in commands)
    assert [request[0] for request in requests] == [
        "/health",
        "/info",
        "/info",
        "/embed",
        "/info",
        "/embed",
    ]
    bodies = [body for path, body, _ in requests if path == "/embed"]
    assert bodies == [
        {
            "inputs": ["query: Проверка поиска вакансий"],
            "normalize": True,
            "truncate": False,
            "truncation_direction": "right",
        },
        {
            "inputs": ["passage: Проверка документа вакансии"],
            "normalize": True,
            "truncate": True,
            "truncation_direction": "right",
        },
    ]
    assert all(auth == "Bearer launch-secret" for _, _, auth in requests)
    assert "HTTP contract ready" in caplog.text
    assert "requires live evidence" in caplog.text


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("embedding.runtime.image", "tei:latest"),
        ("embedding.runtime.gpu_uuid", "all"),
        ("embedding.runtime.gpu_uuid", GPU + ",GPU-23456789"),
        ("embedding.runtime.container_name", "bad/name"),
        ("embedding.runtime.cache_dir", "relative"),
        ("embedding.runtime.cache_dir", "/not/a/cache"),
        ("embedding.runtime.host_port", True),
        ("embedding.runtime.host_port", 65536),
        ("embedding.client.url", "http://127.0.0.1:1"),
        ("embedding.runtime.max_batch_tokens", 512),
        ("embedding.runtime.max_client_batch_size", 7),
        ("embedding.runtime.max_concurrent_requests", 1),
        ("embedding.runtime.auto_truncate", True),
        ("embedding.runtime.startup_deadline", 0),
        ("embedding.runtime.startup_deadline", float("nan")),
        ("embedding.runtime.startup_deadline", True),
        ("embedding.profile.revision", "main"),
        ("embedding.client.api_key", "bad\nheader"),
    ],
)
def test_invalid_launch_configuration_fails_before_docker(launch_site, path, value):
    cfg, _, requests, commands = launch_site
    OmegaConf.update(cfg, path, value)
    with pytest.raises((ValueError, InstantiationException)):
        launcher.launch(cfg)
    assert commands == requests == []


def test_cache_must_be_directory_and_safe_mount_path(launch_site, tmp_path):
    cfg, _, requests, commands = launch_site
    file = tmp_path / "cache-file"
    file.write_text("preserve")
    for path in (file, tmp_path / "cache,option"):
        cfg.embedding.runtime.cache_dir = str(path)
        with pytest.raises(ValueError):
            launcher.launch(cfg)
    assert commands == requests == []
    assert file.read_text() == "preserve"


def test_cache_symlink_cannot_inject_mount_options(launch_site, tmp_path):
    cfg, _, requests, commands = launch_site
    target = tmp_path / "cache,readonly"
    target.mkdir()
    link = tmp_path / "cache-link"
    link.symlink_to(target, target_is_directory=True)
    cfg.embedding.runtime.cache_dir = str(link)
    with pytest.raises(ValueError):
        launcher.launch(cfg)
    assert commands == requests == []


def test_occupied_port_fails_before_docker(launch_site):
    cfg, _, requests, commands = launch_site
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", cfg.embedding.runtime.host_port))
        occupied.listen()
        with pytest.raises(OSError):
            launcher.launch(cfg)
    assert commands == requests == []


def test_existing_even_stopped_container_is_not_adopted_or_removed(launch_site):
    cfg, state, requests, commands = launch_site
    state["existing"] = "existing-container-id"
    with pytest.raises(ValueError, match="already in use"):
        launcher.launch(cfg)
    assert len(commands) == 1
    assert "--all" in commands[0][0]
    assert requests == []


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("container", "bad-id", ValueError),
        ("image", "bad-image", ValueError),
        ("status", "sha256:" + "e" * 64 + " running", RuntimeError),
        ("status", IMAGE_ID + " exited", RuntimeError),
    ],
)
def test_bad_container_evidence_never_reaches_http_or_cleanup(
    launch_site, key, value, error
):
    cfg, state, requests, _ = launch_site
    state[key] = value
    with pytest.raises(error):
        launcher.launch(cfg)
    assert requests == []


def test_health_503_retries_but_other_status_does_not(launch_site, monkeypatch):
    cfg, state, requests, _ = launch_site
    state["health"] = [503, 200]
    monkeypatch.setattr(launcher.time, "sleep", lambda duration: None)
    assert launcher.launch(cfg) == CONTAINER
    assert [path for path, _, _ in requests].count("/health") == 2


def test_unauthorized_health_fails_without_inference_or_cleanup(launch_site):
    cfg, state, requests, _ = launch_site
    state["health"] = [401]
    with pytest.raises(RuntimeError, match="401") as error:
        launcher.launch(cfg)
    assert error.value.__cause__ is not None
    assert [path for path, _, _ in requests] == ["/health"]


@pytest.mark.parametrize("failure", ["profile", "capacity", "vector"])
def test_wrong_runtime_or_probe_never_announces_ready(launch_site, caplog, failure):
    cfg, state, requests, _ = launch_site
    if failure == "profile":
        state["info"]["model_sha"] = "e" * 40
    elif failure == "capacity":
        state["info"]["max_batch_tokens"] = 8192
    else:
        state["vector"] = [0.0] * 768
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError):
        launcher.launch(cfg)
    assert "HTTP contract ready" not in caplog.text
    assert sum(path == "/embed" for path, _, _ in requests) == (failure == "vector")


def test_startup_deadline_stops_polls_without_removing_container(
    launch_site, monkeypatch
):
    cfg, state, requests, commands = launch_site
    cfg.embedding.runtime.startup_deadline = 2
    state["health"] = [503]
    now = [100.0]
    monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr("src.integrations.embedding.client.monotonic", lambda: now[0])
    monkeypatch.setattr(
        launcher.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration)
    )
    with pytest.raises(TimeoutError, match="deadline"):
        launcher.launch(cfg)
    assert [path for path, _, _ in requests] == ["/health", "/health"]
    assert [options["timeout"] for _, options in commands][-2:] == [2.0, 1.0]
    assert not any("rm" in command or "stop" in command for command, _ in commands)


def test_startup_deadline_is_checked_between_info_and_embed(launch_site, monkeypatch):
    cfg, _, requests, commands = launch_site
    cfg.embedding.runtime.startup_deadline = 2
    now = [100.0]
    monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
    monkeypatch.setattr("src.integrations.embedding.client.monotonic", lambda: now[0])
    original_info = TEIIntegration.info
    calls = 0

    def info(client):
        nonlocal calls
        assert client.deadline == 102.0
        assert client.timeout == 60.0
        result = original_info(client)
        calls += 1
        if calls == 2:
            now[0] = 103.0
        return result

    monkeypatch.setattr(TEIIntegration, "info", info)
    with pytest.raises(TimeoutError, match="deadline"):
        launcher.launch(cfg)
    assert [path for path, _, _ in requests] == ["/health", "/info", "/info"]
    assert len(commands) == 4


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("private"),
        subprocess.CalledProcessError(1, ["docker"], stderr="private"),
    ],
)
def test_docker_failure_preserves_original_exception(launch_site, monkeypatch, error):
    cfg, _, requests, _ = launch_site

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(launcher.subprocess, "run", fail)
    with pytest.raises(type(error)) as caught:
        launcher.launch(cfg)
    assert caught.value is error
    assert requests == []


@pytest.mark.parametrize(
    ("error", "exit_code"), [(ValueError("secret body"), 1), (KeyboardInterrupt(), 130)]
)
def test_cli_errors_are_safe_and_use_existing_exit_codes(
    monkeypatch, caplog, error, exit_code
):
    def fail(cfg):
        raise error

    monkeypatch.setattr(launcher, "launch", fail)
    with pytest.raises(SystemExit) as caught:
        launcher.main.__wrapped__(OmegaConf.create({}))
    assert caught.value.code == exit_code
    assert "secret body" not in caplog.text
