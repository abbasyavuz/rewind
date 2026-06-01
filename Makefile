# Rewind — one-command dev setup.
#
# `make dev` builds everything a contributor needs, including the PyO3 native module
# (rewind_native) — without it, `import rewind` fails. `pip install` alone is NOT
# enough because the native module is a separate maturin crate (crates/rewind-py).

VENV := python/rewind/.venv
PY := $(VENV)/bin/python

.PHONY: dev build native test clean

## dev: build the Rust CLI + native module + Python SDK into a venv (run this first)
dev: build
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -U pip maturin
	$(PY) -m pip install -q -e "python/rewind[dev,examples]"
	$(MAKE) native
	@echo "✓ dev setup complete. Try:  make test"

## build: the Rust workspace (rewind-core + the `rewind` CLI)
build:
	cargo build --release

## native: build the PyO3 wheel and install it into the venv (build+install is more
## robust than `maturin develop`, which needs an activated VIRTUAL_ENV).
native:
	PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 $(VENV)/bin/maturin build --release \
	  -m crates/rewind-py/Cargo.toml -i $(PY) --out dist
	$(PY) -m pip install -q --force-reinstall dist/rewind_native-*.whl

## test: cargo test + pytest
test:
	cargo test
	cd python/rewind && ../../$(VENV)/bin/python -m pytest -q

## clean: remove build artifacts and the venv
clean:
	cargo clean
	rm -rf $(VENV)
