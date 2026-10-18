"""Start a pinned TEI container and check its encoding endpoints."""

import logging
import math
import os
import re
import socket
import subprocess
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from uuid import uuid4

import hydra
from hydra.utils import instantiate
from omegaconf import DictConfig, OmegaConf

from src.components.embedding.encoder import TextEmbedder

OmegaConf.register_new_resolver(
    "run_id", lambda: uuid4().hex, use_cache=True, replace=True
)


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def build_command(cfg: DictConfig, embedder: TextEmbedder) -> list[str]:
    """Validate the launch boundary and render Docker arguments without secrets."""
    runtime = cfg.embedding.runtime
    if runtime.version != "1.9.4":
        raise ValueError("Unsupported TEI runtime version")
    image = runtime.image
    if not isinstance(image, str) or not re.fullmatch(
        r"ghcr\.io/huggingface/text-embeddings-inference:"
        r"(?:turing-|86-|89-|hopper-|100-|120-)?1\.9\.4@sha256:[0-9a-f]{64}",
        image,
    ):
        raise ValueError("TEI image must pin the supported CUDA release and digest")
    gpu = runtime.gpu_uuid
    if not isinstance(gpu, str) or not re.fullmatch(
        r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", gpu
    ):
        raise ValueError("A single complete GPU UUID is required")
    name = runtime.container_name
    if not isinstance(name, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name
    ):
        raise ValueError("Invalid TEI container name")
    cache_value = runtime.cache_dir
    if not isinstance(cache_value, str) or any(c in cache_value for c in ',"\r\n'):
        raise ValueError("Invalid cache path for a Docker bind mount")
    cache = Path(cache_value)
    resolved_cache = cache.resolve()
    if (
        not cache.is_absolute()
        or any(c in str(resolved_cache) for c in ',"\r\n')
        or not resolved_cache.is_dir()
        or not os.access(resolved_cache, os.W_OK | os.X_OK)
    ):
        raise ValueError("TEI cache must be an existing writable absolute directory")
    port = _positive_integer(runtime.host_port, "host_port")
    if port > 65535 or cfg.embedding.client.url.rstrip("/") != (
        f"http://127.0.0.1:{port}"
    ):
        raise ValueError("TEI client URL must match the published loopback port")
    token_batch = _positive_integer(runtime.max_batch_tokens, "max_batch_tokens")
    client_batch = _positive_integer(
        runtime.max_client_batch_size, "max_client_batch_size"
    )
    concurrent = _positive_integer(
        runtime.max_concurrent_requests, "max_concurrent_requests"
    )
    if (
        token_batch < embedder.profile.max_tokens * embedder.limits["batch_inputs"]
        or client_batch < embedder.limits["batch_inputs"]
        or concurrent < client_batch
        or runtime.auto_truncate is not False
    ):
        raise ValueError("TEI capacity and truncation must match the encoding contract")
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--gpus",
        f"device={gpu}",
        "--publish",
        f"127.0.0.1:{port}:80",
        "--mount",
        f"type=bind,source={resolved_cache},target=/data",
        "--restart",
        "unless-stopped",
        "--log-driver",
        "local",
    ]
    if cfg.embedding.client.api_key is not None:
        command.extend(("--env", "API_KEY"))
    profile = embedder.profile
    command.extend(
        (
            image,
            "--model-id",
            profile.model_id,
            "--revision",
            profile.revision,
            "--pooling",
            profile.pooling,
            "--dtype",
            profile.inference_dtype,
            "--auto-truncate",
            "false",
            "--max-batch-tokens",
            str(token_batch),
            "--max-client-batch-size",
            str(client_batch),
            "--max-concurrent-requests",
            str(concurrent),
            "--hostname",
            "0.0.0.0",
            "--port",
            "80",
        )
    )
    return command


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("TEI startup deadline exceeded")
    return remaining


def _docker(
    arguments: list[str], deadline: float, *, env: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=_remaining(deadline),
        env=env,
    )
    return result.stdout.strip()


def launch(cfg: DictConfig) -> str:
    """Start a new container and verify health, model info and encoding probes."""
    runtime = cfg.embedding.runtime
    duration = runtime.startup_deadline
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("startup_deadline must be positive and finite")
    deadline = time.monotonic() + duration
    client = instantiate(cfg.embedding.client, deadline=deadline, _convert_="all")
    embedder: TextEmbedder = instantiate(
        cfg.embedding.encoder, integration=client, _convert_="all"
    )
    command = build_command(cfg, embedder)
    name = runtime.container_name
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
        port_probe.bind(("127.0.0.1", runtime.host_port))
    existing = _docker(
        [
            "docker",
            "container",
            "ls",
            "--all",
            "--filter",
            f"name=^/{re.escape(name)}$",
            "--format",
            "{{.ID}}",
        ],
        deadline,
    )
    if existing:
        raise ValueError("TEI container name is already in use")
    environment = os.environ.copy()
    if cfg.embedding.client.api_key is not None:
        environment["API_KEY"] = cfg.embedding.client.api_key
    logging.getLogger(__name__).info("Starting TEI container %s", name)
    container_id = _docker(command, deadline, env=environment)
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise ValueError("Docker returned an invalid container identity")
    image_id = _docker(
        [
            "docker",
            "image",
            "inspect",
            runtime.image,
            "--format",
            "{{.Id}}",
        ],
        deadline,
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Docker returned an invalid image identity")
    logging.getLogger(__name__).info("Waiting for TEI HTTP health: %s", name)
    while True:
        status = _docker(
            [
                "docker",
                "container",
                "inspect",
                container_id,
                "--format",
                "{{.Image}} {{.State.Status}}",
            ],
            deadline,
        )
        if status != f"{image_id} running":
            raise RuntimeError("TEI container is not running the pinned image")
        try:
            client.health()
            break
        except RuntimeError as exc:
            cause = exc.__cause__
            if isinstance(cause, HTTPError):
                if cause.code != 503:
                    raise
            elif not isinstance(cause, (URLError, OSError, HTTPException)):
                raise
        time.sleep(min(1.0, _remaining(deadline)))
    logging.getLogger(__name__).info("Checking TEI profile and probes: %s", name)
    info = embedder.check_runtime()
    for key in (
        "max_batch_tokens",
        "max_client_batch_size",
        "max_concurrent_requests",
    ):
        if type(info.get(key)) is not int or info[key] != runtime[key]:
            raise ValueError("TEI reported a different runtime capacity")
    if info.get("auto_truncate") is not False:
        raise ValueError("TEI automatic truncation must be disabled")
    embedder.encode_query("Проверка поиска вакансий")
    embedder.encode_documents(("Проверка документа вакансии",))
    _remaining(deadline)
    logging.getLogger(__name__).info(
        "TEI HTTP contract ready: %s; selected GPU execution requires live evidence",
        name,
    )
    return container_id


@hydra.main(config_path="../../../configs", config_name="vector", version_base="1.3")
def main(cfg: DictConfig) -> None:
    try:
        launch(cfg)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted during TEI launch")
        raise SystemExit(130) from None
    except Exception as exc:
        logging.getLogger(__name__).error(
            "Failed during TEI launch (%s)", type(exc).__name__
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
