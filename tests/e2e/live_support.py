"""A real server process for the tests that need their own settings.

The in-process app of the suite reads its settings once, at import. A test about a tiny
idempotency TTL or a strict rate limit starts a second `uvicorn` with its own environment
(same throw-away database) and talks to it over a socket: still black-box HTTP, no backend
import. `X-Forwarded-For` is trusted in that server so a test can pick the client IP.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tests.e2e.conftest import ROOT, Api


class RemoteApi(Api):
    """`Api` over a real socket: same helpers, other transport."""

    def __init__(self, base_url: str, ip: str | None = None, token: str | None = None):
        super().__init__(None, ip, token)
        self.base_url = base_url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, headers={"X-Forwarded-For": self.ip}, timeout=30
        )

    def as_token(self, token: str) -> "RemoteApi":
        return RemoteApi(self.base_url, self.ip, token)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(env: dict[str, str]) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env={**os.environ, "TRUST_FORWARDED_FOR": "true", **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("test server exited at startup")
        try:
            if httpx.get(f"{base_url}/health", timeout=1).status_code == 200:
                return process, base_url
        except httpx.HTTPError:
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("test server did not come up")


@pytest.fixture(scope="session")
def live_server(database):
    """`live_server(NAME=value, ...)` -> RemoteApi factory bound to a new server."""
    started: list[subprocess.Popen] = []

    def start(**env: str):
        process, base_url = start_server(env)
        started.append(process)
        return lambda token=None, ip=None: RemoteApi(base_url, ip, token)

    yield start
    for process in started:
        process.terminate()
        process.wait(timeout=10)


def run_cli(module: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    """Run a maintenance script (`python -m app.scripts.x`) like cron would, with its own env."""
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )
