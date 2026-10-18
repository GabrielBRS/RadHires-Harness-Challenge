#!/usr/bin/env python3
"""Cross-platform build/download orchestration. No invoice logic lives in Python."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import venv

ROOT = Path(__file__).resolve().parent
INFERENCE = ROOT / "inference"
SOURCE = INFERENCE / "source"
LOCK = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
MODEL = ROOT / LOCK["model"]["directory"]
TARGET = LOCK["max"]["target"]


def windows_handoff(script: str) -> None:
    if platform.system() != "Windows":
        return
    if not shutil.which("wsl.exe"):
        raise RuntimeError("MAX requires WSL2 on Windows. Run 'wsl --install -d Ubuntu-24.04', then follow README.md.")
    converted = subprocess.check_output(["wsl.exe", "--exec", "wslpath", "-a", str(ROOT / script)], text=True).strip()
    print("Running the Linux toolchain inside WSL2. Install Rust, Node and build tools in that distribution.", flush=True)
    raise SystemExit(subprocess.call(["wsl.exe", "--exec", "python3", converted, *sys.argv[1:]]))


def environment() -> dict[str, str]:
    env = os.environ.copy()
    # Keep inference/model/tool caches in the checkout, regardless of the caller's cwd.
    paths = {"HF_HOME": INFERENCE / "cache/huggingface", "XDG_CACHE_HOME": INFERENCE / "cache",
             "BAZELISK_HOME": INFERENCE / "cache/bazelisk", "MODULAR_CACHE_DIR": INFERENCE / "cache/modular",
             "UV_CACHE_DIR": INFERENCE / "cache/uv", "npm_config_cache": ROOT / "frontend/.npm-cache"}
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env["MAX_SERVE_HOST"] = "127.0.0.1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def command(args: list[str], cwd: Path = ROOT, env: dict | None = None) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, cwd=cwd, env=env or environment(), check=True)


def require(*names: str) -> None:
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        raise RuntimeError(f"Missing tools: {', '.join(missing)}. See README.md installation steps for your OS.")


def check_platform() -> None:
    system = platform.system()
    if system == "Darwin":
        if platform.machine() != "arm64" or int(platform.mac_ver()[0].split('.')[0]) < 15:
            raise RuntimeError("MAX requires Apple Silicon and macOS 15+. The app-only demo can run without MAX.")
    elif system == "Linux":
        libc, version = platform.libc_ver()
        if libc != "glibc" or tuple(map(int, version.split('.')[:2])) < (2, 34):
            raise RuntimeError("MAX requires glibc 2.34+. Use Ubuntu 22.04/24.04 or a compatible distribution.")
    else:
        raise RuntimeError(f"Unsupported MAX platform: {system}. Windows must use WSL2.")


def source_ready() -> None:
    actual = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if actual != LOCK["max"]["commit"]:
        raise RuntimeError(f"MAX source revision differs from runtime.lock.json: {actual}. Keep a separate checkout for experiments.")
    dirty = subprocess.check_output(["git", "-C", str(SOURCE), "status", "--porcelain", "--untracked-files=no"], text=True)
    if dirty.strip():
        raise RuntimeError("MAX source has local modifications. Restore them or keep a separate checkout before a reproducible build.")


def download_source() -> None:
    if not SOURCE.exists():
        command(["git", "clone", "--depth", "1", "--branch", LOCK["max"]["tag"], LOCK["max"]["repository"], str(SOURCE)])
    source_ready()


def matches(path: Path, item: dict) -> bool:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return False
    digest = hashlib.sha256() if item["sha256"] else hashlib.sha1()
    if not item["sha256"]:
        digest.update(f"blob {item['size']}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == (item["sha256"] or item["git_sha1"])


def download_model() -> None:
    MODEL.mkdir(parents=True, exist_ok=True)
    model = LOCK["model"]
    for item in model["files"]:
        path = MODEL / item["path"]
        if matches(path, item):
            print("Verified:", path.relative_to(ROOT), flush=True)
            continue
        url = f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{item['path']}"
        partial = path.with_name(path.name + ".part")
        for attempt in range(3):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= item["size"]:
                    partial.unlink()
                    offset = 0
                request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
                print(f"Downloading {item['path']} ({item['size'] / 1e9:.2f} GB), attempt {attempt + 1}", flush=True)
                with urllib.request.urlopen(request, timeout=60) as response:
                    resume = offset > 0 and response.status == 206
                    with partial.open("ab" if resume else "wb") as output:
                        shutil.copyfileobj(response, output, length=4 * 1024 * 1024)
                if not matches(partial, item):
                    partial.unlink(missing_ok=True)
                    raise RuntimeError(f"Checksum mismatch: {item['path']}")
                partial.replace(path)
                break
            except (OSError, RuntimeError) as error:
                if attempt == 2:
                    raise RuntimeError(f"Model download failed: {error}. Re-run build.py to resume.") from error
                time.sleep(2)
    (MODEL / "REVISION").write_text(model["revision"] + "\n", encoding="utf-8")
    # Override only the serving template; never modify the pinned model files.
    template = json.loads((MODEL / "tokenizer_config.json").read_text(encoding="utf-8"))["chat_template"]
    (INFERENCE / "qwen-no-thinking.jinja").write_text("{% set enable_thinking = false %}\n" + template, encoding="utf-8")


def bazel_args(action: str) -> list[str]:
    return [str(SOURCE / "bazelw"), f"--output_user_root={INFERENCE / 'cache/bazel'}", action,
            f"--disk_cache={INFERENCE / 'cache/bazel-disk'}", "--config=prebuilt-mojo"]


def main() -> None:
    windows_handoff("build.py")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-only", action="store_true", help="Build React and Rust only; no model/MAX download")
    parser.add_argument("--inference-only", action="store_true", help="Download and build MAX/Qwen only")
    parser.add_argument("--download-only", action="store_true", help="Download pinned MAX source and Qwen; do not compile")
    parser.add_argument("--max-from-source", action="store_true", help="Build MAX serving code and Mojo kernels with Bazel instead of the official compiled distribution")
    parser.add_argument("--jobs", type=int, default=4, help="Maximum Bazel/Cargo compilation jobs")
    args = parser.parse_args()
    if sum([args.app_only, args.inference_only, args.download_only]) > 1 or args.jobs < 1:
        parser.error("Choose one scope option and a positive --jobs value")
    print(f"Project: {ROOT}\nPlatform: {platform.system()} / {platform.machine()}", flush=True)
    if not args.app_only:
        check_platform()
        require("git", "bash", "curl", "cc")
    if not args.inference_only and not args.download_only:
        require("node", "npm", "cargo")
        command(["npm", "ci", "--no-audit", "--no-fund"], ROOT / "frontend")
        command(["npm", "run", "build"], ROOT / "frontend")
        command(["cargo", "build", "--release", "--locked", "--jobs", str(args.jobs), "--manifest-path", str(ROOT / "ai-backend/Cargo.toml")])
    if not args.app_only:
        download_source()
        download_model()
        if not args.download_only:
            marker = INFERENCE / "build.json"
            marker.unlink(missing_ok=True)
            if args.max_from_source:
                command([*bazel_args("build"), f"--jobs={args.jobs}", TARGET], SOURCE)
                runtime = "source"
            else:
                runtime = "package"
                runtime_env = INFERENCE / ".venv"
                if not (runtime_env / "bin/python").is_file():
                    venv.EnvBuilder(with_pip=True).create(runtime_env)
                command([str(runtime_env / "bin/python"), "-m", "pip", "install",
                         LOCK["max"]["package"], "--index-url", LOCK["max"]["index"],
                         "--no-input", "--disable-pip-version-check"], env=environment())
                command([str(runtime_env / "bin/max"), "--version"])
                subprocess.run([str(runtime_env / "bin/max"), "serve", "--help"],
                               env=environment(), stdout=subprocess.DEVNULL, check=True)
                installed = subprocess.check_output([str(runtime_env / "bin/python"), "-m", "pip", "freeze"], text=True)
                (INFERENCE / "installed-runtime.txt").write_text(installed, encoding="utf-8")
            # Only a successful installation/build can mark the runtime ready.
            marker.write_text(json.dumps({"commit": LOCK["max"]["commit"], "target": TARGET,
                "runtime": runtime, "package": LOCK["max"]["package"],
                "platform": platform.system(), "machine": platform.machine()}, indent=2), encoding="utf-8")
    print("Complete. " + ("Run: python3 run.py --demo" if args.app_only else ("Downloads verified. Run build.py to prepare the runtime." if args.download_only else "Run: python3 run.py")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Build interrupted. Downloads can be resumed.", file=sys.stderr)
        sys.exit(130)
