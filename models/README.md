# Project-local Qwen weights

`python3 build.py` downloads `Qwen/Qwen3-4B` Safetensors into `models/Qwen3-4B/`.
The repository/revision and every file size/hash are pinned in `runtime.lock.json`.
Large weight files use SHA-256; small Git files use their Git blob SHA-1.
Downloads are checked before atomic rename and resumed after interrupted transfers.
Re-running the build checks existing hashes. `REVISION` is written only after all files verify.

The 12 source files occupy 8,060,925,056 bytes (~7.51 GiB); runtime memory is larger.
No model files are committed. Do not move this folder to a global cache: `run.py`
resolves it relative to its own project root, even when launched from another directory.
The Qwen license is included in the downloaded snapshot.
