.PHONY: check native-install-smoke

check:
	uv run --locked pytest -q

native-install-smoke:
	sudo "$(shell command -v uv)" run --locked python scripts/native_install_smoke.py --run
