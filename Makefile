.PHONY: build demo run check
build:
	python3 build.py
run:
	python3 run.py
demo:
	python3 build.py --app-only
	python3 run.py --demo
check:
	cargo fmt --manifest-path ai-backend/Cargo.toml --check
	cargo test --manifest-path ai-backend/Cargo.toml --locked
	npm run build --prefix frontend
