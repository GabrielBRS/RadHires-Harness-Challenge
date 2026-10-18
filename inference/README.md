# Project-local MAX runtime

`python3 build.py` downloads Modular's source to `inference/source`, verifies its exact tag
and commit from `runtime.lock.json`, and installs the official MAX 26.6.0 release inside
`inference/.venv`. Nothing is installed into the system Python environment.

`python3 build.py --inference-only --max-from-source` instead builds
`//max/python/max/_entrypoints:pipelines` with Bazel and `--config=prebuilt-mojo`.
This builds public serving code/Mojo kernels using the vendor compiler; it is not a bootstrap
of the entire compiler. The observed upstream LLVM checksum failure is documented in README.md.

Bazel, Hugging Face, pip, UV and MAX caches are redirected beneath `inference/cache`.
`run.py` starts either the installed MAX command or the built Bazel target with its runfiles,
on loopback port 8000. It passes the **absolute project-local model path**, never a remote
model ID as the weights location. The served API model name remains `Qwen/Qwen3-4B`.

The model graph is compiled at first startup. `build.json` is written only after successful
installation/build. Source, runtime, caches, logs and generated chat template are ignored by
Git. Preserve upstream licenses. See [the setup guide](../docs/LOCAL_AI.md).
