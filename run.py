#!/usr/bin/env python3
"""Supervise compiled React, Rust and the project-local MAX runtime."""
from __future__ import annotations
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from build import ROOT, INFERENCE, SOURCE, MODEL, LOCK, TARGET, environment, windows_handoff, source_ready, bazel_args, require, check_platform


def available(port: int) -> None:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"Port {port} is occupied. Stop that process or select another port.") from error


def ready(url: str, process: subprocess.Popen, timeout: int, model: str | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited with code {process.returncode}; inspect inference/logs/.")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    if model is not None:
                        payload = json.load(response)
                        if isinstance(payload, dict) and any(item.get("id") == model for item in payload.get("data", []) if isinstance(item, dict)):
                            return
                    else:
                        return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Startup timed out after {timeout}s: {url}. Inspect inference/logs/.")


def preload_samples(port: int) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/samples",
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 202:
                raise RuntimeError(
                    f"Sample preload returned HTTP {response.status}."
                )
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise RuntimeError(f"Could not preload sample invoices: {error}") from error
    if not isinstance(payload, list):
        raise RuntimeError("Sample preload returned an invalid response.")
    return len(payload)


def main() -> None:
    windows_handoff("run.py")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Explicit PDF-regex demo; no MAX or AI calls")
    parser.add_argument("--device", choices=["gpu", "cpu"], default="gpu", help="MAX device; CPU is slower and uses float32")
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--backend-port", type=int, default=8080)
    parser.add_argument("--inference-port", type=int, default=8000)
    parser.add_argument("--startup-timeout", type=int, default=900, help="Seconds allowed for first MAX graph compilation")
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Start with an empty session instead of preloading the 17 sample invoices",
    )
    args = parser.parse_args()
    ports = [args.frontend_port, args.backend_port] + ([] if args.demo else [args.inference_port])
    if len(set(ports)) != len(ports) or any(p < 1024 or p > 65535 for p in ports) or args.startup_timeout < 1:
        parser.error("Use distinct ports between 1024 and 65535 and a positive timeout")
    for port in ports:
        available(port)
    require("node")
    binary = ROOT / "ai-backend/target/release/invoice-check"
    if not binary.is_file() or not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("Application build missing. Run python3 build.py --app-only (demo) or python3 build.py (full).")
    env = environment()
    env.update({"FRONTEND_PORT": str(args.frontend_port), "BACKEND_PORT": str(args.backend_port),
                "MAX_URL": f"http://127.0.0.1:{args.inference_port}", "MAX_MODEL": LOCK["model"]["repository"],
                "INVOICE_ROOT": str(ROOT), "MAX_SERVE_METRICS_ENDPOINT_PORT": "0", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    processes: list[subprocess.Popen] = []
    logs = []

    def launch(name: str, command: list[str], cwd=ROOT) -> subprocess.Popen:
        path = INFERENCE / "logs" / f"{name}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        log = path.open("w", encoding="utf-8")
        logs.append(log)
        print(f"Starting {name}; log: {path}", flush=True)
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(proc)
        return proc

    def stop(_signum=None, _frame=None):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if not args.demo:
            check_platform()
            require("git", "bash")
            marker = INFERENCE / "build.json"
            if not marker.is_file() or json.loads(marker.read_text())["commit"] != LOCK["max"]["commit"]:
                raise RuntimeError("MAX runtime is missing or stale. Run python3 build.py --inference-only.")
            source_ready()
            if not (MODEL / "REVISION").is_file() or (MODEL / "REVISION").read_text().strip() != LOCK["model"]["revision"]:
                raise RuntimeError("Pinned Qwen model is missing. Run python3 build.py --inference-only.")
            for item in LOCK["model"]["files"]:
                path = MODEL / item["path"]
                if not path.is_file() or path.stat().st_size != item["size"]:
                    raise RuntimeError(f"Model file missing or incomplete: {path}. Re-run build.py to verify checksums.")
            runtime = json.loads(marker.read_text())["runtime"]
            if runtime == "source":
                launcher = [*bazel_args("run"), "--jobs=4", TARGET, "--"]
            else:
                executable = INFERENCE / ".venv/bin/max"
                if not executable.is_file():
                    raise RuntimeError("MAX executable missing. Run build.py --inference-only.")
                launcher = [str(executable)]
            engine = launch("max", [*launcher, "serve", "--model", str(MODEL),
                "--served-model-name", LOCK["model"]["repository"], "--devices", args.device,
                "--quantization-encoding", "bfloat16" if args.device == "gpu" else "float32",
                "--max-length", "8192", "--max-batch-size", "1", "--device-memory-utilization", "0.85",
                "--chat-template", str(INFERENCE / "qwen-no-thinking.jinja"), "--port", str(args.inference_port)], SOURCE)
            print("Waiting for MAX to load Qwen and compile its computation graph…", flush=True)
            ready(f"http://127.0.0.1:{args.inference_port}/v1/models", engine, args.startup_timeout, LOCK["model"]["repository"])
        backend = launch("backend", [str(binary), "serve", "--port", str(args.backend_port), *(["--demo"] if args.demo else [])])
        ready(f"http://127.0.0.1:{args.backend_port}/api/health", backend, 30)
        if not args.empty:
            sample_count = preload_samples(args.backend_port)
            print(
                f"Preloaded {sample_count} sample invoices; processing continues in the background.",
                flush=True,
            )
        frontend = launch("frontend", ["node", str(ROOT / "frontend/server.mjs")])
        ready(f"http://127.0.0.1:{args.frontend_port}", frontend, 30)
        print(f"\nOpen http://localhost:{args.frontend_port}\nMode: {'DEMO — no AI' if args.demo else 'MAX + Qwen — local AI'}\nCtrl+C stops all services and clears the invoice session.", flush=True)
        while all(proc.poll() is None for proc in processes):
            time.sleep(0.5)
        raise RuntimeError("A service exited. The other services have been stopped; inspect inference/logs/.")
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # Entire process groups include MAX model workers, not just the launchers.
        for proc in reversed(processes):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 8
        for proc in processes:
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for log in logs:
            log.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. The invoice session has been cleared.")
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Startup failed: {error}", file=sys.stderr)
        sys.exit(1)
