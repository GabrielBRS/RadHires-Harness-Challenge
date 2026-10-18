# MAX + Qwen inside the checkout

## What is downloaded

- **Engine/source:** [Modular MAX 26.6.0 / Mojo 1.1.0](https://github.com/modular/modular/releases/tag/max%2Fv26.6.0),
  commit `c6fa49f712fe15c99d0461ad2baaf2a15cbc58ce`, under `inference/source/`.
- **Engine/runtime:** `max[serve]==26.6.0` from the [official Modular package on PyPI](https://pypi.org/project/max/26.6.0/),
  installed into `inference/.venv/`, including the runtime/compiler dependencies.
- **Model:** [Qwen/Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B/tree/1cfa9a7208912126459214e8b04321603b3df60c),
  revision `1cfa9a7208912126459214e8b04321603b3df60c`, Safetensors/BF16, under `models/Qwen3-4B/`.

The manifest `runtime.lock.json` records these references and model file hashes. `build.py`
checks existing files and resumes incomplete downloads. `run.py` uses the local model path,
with Hugging Face offline mode enabled. Installation needs internet; invoice inference stays
local. No Hugging Face account or API key is required for this public model.

The official [Qwen implementation in the selected MAX tag](https://github.com/modular/modular/blob/c6fa49f712fe15c99d0461ad2baaf2a15cbc58ce/max/python/max/pipelines/architectures/qwen3/model_config.py)
supports BF16. We use the smaller 4B model to target a 16 GB GPU class. Its 8.06 GB download
is **not a VRAM measurement**: weights, activations, KV cache and kernels consume memory.
The profile uses context 8192, output cap 2048, batch 1, two sequential roles, and device memory
utilization 0.85. Confirm actual allocation on your GPU; no sub-16 GB peak has been measured here.
CPU uses float32 and requires substantially more RAM; 32 GB total is not a guarantee if other
applications consume it. On Apple Silicon the memory is shared with the OS.

## Linux

Use Ubuntu 22.04/24.04 or another compatible Linux with glibc 2.34+, a supported CPU, and a
[MAX-compatible GPU/driver](https://max.modular.com/packages/#gpu-compatibility) for GPU mode.
This setup does not install or alter GPU drivers.

```bash
sudo apt update
sudo apt install -y build-essential git curl python3 python3-venv pkg-config unzip zip patch
# Install Rust from its official installer, then reopen the shell or source its environment.
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh
sh /tmp/rustup-init.sh
. "$HOME/.cargo/env"
rustup toolchain install 1.97.1 --profile minimal --component rustfmt --component clippy
```

Install [Node.js 24](https://nodejs.org/en/download) using its official Linux instructions.
Verify `node --version`, `npm --version`, `cargo --version`, `python3 --version`, and `git --version`.
Then from this checkout:

```bash
python3 build.py
python3 run.py
```

For CPU inference explicitly use `python3 run.py --device cpu`. Slow generation can exceed
our 180-second per-role timeout; it is not an equivalent performance profile to a supported GPU.

## Windows

MAX is [not natively supported on Windows](https://max.modular.com/packages/#system-requirements).
Use [WSL2 with Ubuntu](https://learn.microsoft.com/windows/wsl/install):

```powershell
# Administrator PowerShell; restart Windows if requested.
wsl --install -d Ubuntu-24.04
```

Open Ubuntu, complete its first-run setup and follow the **Linux** instructions above, including
Node and Rust installation **inside WSL**. Clone the repository into your WSL home directory,
then run `python3 build.py` and `python3 run.py` there. Access the UI from Windows at
http://localhost:3000 via WSL localhost forwarding. GPU access requires a compatible Windows
GPU driver with WSL support; use Microsoft's/vendor's official instructions.

If the repository is on a Windows filesystem and [Python for Windows](https://www.python.org/downloads/windows/)
is installed, `py build.py` and `py run.py` detect Windows, translate the script path with
`wslpath`, and invoke Linux Python in the default WSL distribution. They do not compile a
Windows MAX executable or install Linux prerequisites for you. All source/model paths remain
inside that same checkout. Avoid mixing native Windows and WSL node_modules/target directories.

## macOS

MAX requires **Apple Silicon and macOS 15+**. Install [Xcode Command Line Tools](https://developer.apple.com/xcode/resources/)
16+ and the Metal toolchain:

```bash
xcode-select --install
xcodebuild -downloadComponent MetalToolchain
```

Install [Node.js 24 for macOS](https://nodejs.org/en/download),
[Python 3.10–3.14 for macOS](https://www.python.org/downloads/macos/), and
[Rust via rustup](https://rustup.rs/). Git is included with the command-line tools.
Then run the same build/run commands from the checkout. The scripts detect macOS automatically.

**Qwen on Metal is not validated here.** MAX describes Apple GPU model support as evolving;
some architectures fail at graph compilation when a kernel is unavailable. Try the explicit
CPU mode with sufficient RAM, or run the app-only demo. Intel Macs can use the Rust/React demo;
this project's MAX setup checks and rejects unsupported Intel/macOS combinations.

## Source builds and compilation

`python3 build.py --inference-only --max-from-source` invokes the documented
[Bazel source workflow](https://github.com/modular/modular/blob/c6fa49f712fe15c99d0461ad2baaf2a15cbc58ce/max/docs/development.md).
The selected release requires `--config=prebuilt-mojo`, which the script supplies.
The source includes public Mojo kernels and Python serving code; the build consumes vendor
compiler/runtime components. This is separate from compiling the Qwen computation graph at startup.

Both tested source tags encountered an upstream LLVM archive checksum mismatch in this
environment. The source mode reports the error and leaves the runtime unmarked. Do not change
checksums blindly. The standard `build.py` uses the official compiled MAX release instead.
All generated runtime/compiler/cache files are local under `inference/`; Rust/Node may also
use their normal package/toolchain caches. The top-level MAX version is pinned, while its
package's transitive dependency resolution follows the vendor's declared requirements.

## Diagnostics and agent use

```bash
ai-backend/target/release/invoice-check agents doctor
ai-backend/target/release/invoice-check agents call check_invoice --input-file examples/check-invoice.json
ai-backend/target/release/invoice-check eval --live
```

Use these after MAX is ready. If ports are occupied, select all three explicitly with `run.py`.
Direct CLI calls to a different inference port need `MAX_URL=http://127.0.0.1:PORT` (export it in
the shell). `run.py` sets this automatically for its backend. `.env.example` is documentation;
the scripts do not silently load environment files. CLI `--max-url` and `--model` are also available.

Inspect `inference/logs/max.log`, `backend.log` and `frontend.log`. Model failures stay in the
review queue, never fall back to fixture answers. Other agents should follow [AGENTS.md](AGENTS.md).
For offline UI work use `run.py --demo` explicitly; its banner identifies that no AI is running.

The supervisor assigns MAX's internal metrics listener an ephemeral port so it cannot collide
with the configured inference/API/UI ports. Readiness requires a valid `/v1/models` response
containing the pinned Qwen name, not merely any HTTP 200 response.

Validated locally: official MAX 26.6.0 startup, Qwen GPU inference, and both actual agent roles.
The 17-fixture live evaluation matched 10 expected decisions with zero unsafe approvals; seven
clean invoices were flagged unnecessarily. See `evals/results/summary.json` before judging quality.
