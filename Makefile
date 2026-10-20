.PHONY: check native-install-smoke

check:
	uv run --locked python -c 'import os, sys; actual = f"{sys.version_info.major}.{sys.version_info.minor}"; expected = os.getenv("EXPECTED_PYTHON_VERSION"); assert sys.version_info >= (3, 11) and (expected is None or actual == expected), (expected, actual); print(f"checking with Python {actual}")'
	uv run --locked pytest -q

native-install-smoke:
	sudo "$(shell command -v uv)" run --locked python scripts/native_install_smoke.py --run
