from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import string
import subprocess
import sys
import time
from typing import Callable
from urllib.parse import quote


_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_STOP_REQUESTED = False


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = _validated_config(args)
        _run(config)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": type(exc).__name__},
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an isolated, expiring Yuque noVNC login proof of concept."
    )
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--chrome-executable", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", type=int, default=8781)
    parser.add_argument("--display", type=int, default=100)
    parser.add_argument("--rfb-port", type=int, default=5901)
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--target-url", default="https://tc-aiot.yuque.com/")
    return parser


def _validated_config(args: argparse.Namespace) -> dict:
    runtime_dir = _absolute_path(args.runtime_dir, "runtime directory")
    chrome = _existing_file(args.chrome_executable, "Chromium executable")
    tls_cert = _existing_file(args.tls_cert, "TLS certificate")
    tls_key = _existing_file(args.tls_key, "TLS private key")
    listen_address = ipaddress.ip_address(args.listen_host)
    if (
        not listen_address.is_private
        or listen_address.is_loopback
        or listen_address.is_unspecified
        or listen_address.is_link_local
        or listen_address.is_multicast
    ):
        raise ValueError("listen host must be a non-loopback private IP address")
    if not 1024 <= args.listen_port <= 65535:
        raise ValueError("listen port is outside the allowed range")
    if not 1 <= args.display <= 999:
        raise ValueError("display number is outside the allowed range")
    if not 1024 <= args.rfb_port <= 65535:
        raise ValueError("RFB port is outside the allowed range")
    if not 60 <= args.duration_seconds <= 1800:
        raise ValueError("PoC duration must be between 60 and 1800 seconds")
    if not str(args.target_url).startswith("https://tc-aiot.yuque.com/"):
        raise ValueError("target URL must remain inside the registered Yuque origin")
    for command in ("Xvfb", "x11vnc", "websockify", "xauth"):
        if shutil.which(command) is None:
            raise RuntimeError(f"required command is unavailable: {command}")
    return {
        "runtime_dir": runtime_dir,
        "chrome": chrome,
        "tls_cert": tls_cert,
        "tls_key": tls_key,
        "listen_host": str(listen_address),
        "listen_port": args.listen_port,
        "display": args.display,
        "rfb_port": args.rfb_port,
        "duration_seconds": args.duration_seconds,
        "target_url": str(args.target_url),
    }


def _run(config: dict) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = False
    runtime_dir: Path = config["runtime_dir"]
    if runtime_dir.exists():
        raise RuntimeError("PoC runtime directory already exists")
    runtime_dir.mkdir(parents=True, mode=0o700)
    runtime_dir.chmod(0o700)
    profile_dir = runtime_dir / "profile"
    profile_dir.mkdir(mode=0o700)
    authority_path = runtime_dir / "Xauthority"
    password_path = runtime_dir / "vnc-password"
    status_path = runtime_dir / "status.json"
    password = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(8))
    password_path.write_text(password + "\n", encoding="ascii")
    password_path.chmod(0o600)
    cookie = secrets.token_hex(16)
    subprocess.run(
        ["xauth", "-f", str(authority_path), "add", f":{config['display']}", ".", cookie],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    authority_path.chmod(0o600)
    environment = dict(os.environ)
    environment.update(
        {
            "DISPLAY": f":{config['display']}",
            "XAUTHORITY": str(authority_path),
            "HOME": str(runtime_dir),
        }
    )
    processes: list[subprocess.Popen] = []
    previous_handlers = _install_signal_handlers()
    try:
        processes.append(
            _start(
                [
                    "Xvfb",
                    f":{config['display']}",
                    "-screen",
                    "0",
                    "900x1100x24",
                    "-nolisten",
                    "tcp",
                    "-noreset",
                    "-auth",
                    str(authority_path),
                ],
                environment,
            )
        )
        _wait_until(
            lambda: _x_display_ready(config["display"]),
            processes,
            "Xvfb did not become ready",
        )
        processes.append(
            _start(
                [
                    "x11vnc",
                    "-display",
                    f":{config['display']}",
                    "-auth",
                    str(authority_path),
                    "-rfbport",
                    str(config["rfb_port"]),
                    "-localhost",
                    "-forever",
                    "-shared",
                    "-passwdfile",
                    str(password_path),
                    "-noxdamage",
                    "-quiet",
                ],
                environment,
            )
        )
        _wait_until(
            lambda: _port_ready("127.0.0.1", config["rfb_port"]),
            processes,
            "x11vnc did not become ready",
        )
        processes.append(
            _start(
                [
                    "websockify",
                    "--web=/usr/share/novnc",
                    f"--cert={config['tls_cert']}",
                    f"--key={config['tls_key']}",
                    "--ssl-only",
                    "--heartbeat=30",
                    f"{config['listen_host']}:{config['listen_port']}",
                    f"127.0.0.1:{config['rfb_port']}",
                ],
                environment,
            )
        )
        _wait_until(
            lambda: _port_ready(config["listen_host"], config["listen_port"]),
            processes,
            "websockify did not become ready",
        )
        processes.append(
            _start(
                [
                    str(config["chrome"]),
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-sync",
                    "--disable-extensions",
                    "--password-store=basic",
                    "--use-mock-keychain",
                    "--no-sandbox",
                    "--window-position=0,0",
                    "--window-size=900,1100",
                    config["target_url"],
                ],
                environment,
            )
        )
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=config["duration_seconds"]
        )
        url = (
            f"https://{config['listen_host']}:{config['listen_port']}/"
            f"vnc_lite.html?scale=true#password={quote(password, safe='')}"
        )
        _write_json_atomic(
            status_path,
            {
                "status": "ready",
                "url": url,
                "expiresAt": expires_at.isoformat(),
                "display": config["display"],
                "listenPort": config["listen_port"],
                "rfbLoopbackOnly": True,
                "browserAutomation": False,
            },
        )
        print(json.dumps({"status": "ready", "expiresAt": expires_at.isoformat()}))
        deadline = time.monotonic() + config["duration_seconds"]
        while not _STOP_REQUESTED and time.monotonic() < deadline:
            _require_processes_alive(processes)
            time.sleep(0.5)
    finally:
        _restore_signal_handlers(previous_handlers)
        for process in reversed(processes):
            _terminate(process)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _start(command: list[str], environment: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_until(
    predicate: Callable[[], bool],
    processes: list[subprocess.Popen],
    message: str,
    *,
    timeout_seconds: float = 12,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _require_processes_alive(processes)
        if predicate():
            return
        time.sleep(0.1)
    raise RuntimeError(message)


def _require_processes_alive(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is not None:
            raise RuntimeError("a PoC subprocess exited unexpectedly")


def _x_display_ready(display: int) -> bool:
    return Path(f"/tmp/.X11-unix/X{display}").exists()


def _port_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _absolute_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path


def _existing_file(value: str, label: str) -> Path:
    path = _absolute_path(value, label)
    if not path.is_file():
        raise ValueError(f"{label} does not exist")
    return path


def _install_signal_handlers() -> dict[int, object]:
    previous = {}
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        previous[signal_number] = signal.getsignal(signal_number)
        signal.signal(signal_number, _request_stop)
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for signal_number, handler in previous.items():
        signal.signal(signal_number, handler)


def _request_stop(_signal_number, _frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
