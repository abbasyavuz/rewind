# Contributing to Rewind

Thanks for your interest! Rewind is a Rust workspace (the `.rewind` engine + CLI) plus a Python SDK
(capture / replay / fork) bound to Rust via PyO3.

## Setup

```bash
make dev      # builds the Rust CLI + the PyO3 native module + the Python SDK into a venv
make test     # cargo test + pytest
```

> `import rewind` needs the compiled native module (`rewind_native`, from `crates/rewind-py`).
> `pip install` alone does **not** build it — always use `make dev` (or the manual steps in the README).

## Before you open a PR

- **Rust:** `cargo fmt --all`, `cargo clippy --all-targets -- -D warnings`, `cargo test` — all clean.
- **Python:** `python -m pytest -q` green; keep it `ruff`-clean (`ruff check`).
- Add a test for any behavior change. Capture/replay/fork changes should preserve the **FAIL-LOUD**
  invariant (never silently serve a wrong/ambiguous cassette) and the cross-language BLAKE3 / causal-id
  parity (there are tests for both).
- Don't break the `.rewind` format casually — it's `v0.1-DRAFT` but consumers verify it offline. If you
  must, bump the format version and note it in `CHANGELOG.md`.
- Keep claims honest: if you add a capability, update the README/docs; if it's partial, mark the scope.

## Layout

- `crates/rewind-core` — the artifact engine (CID, HLC log, Merkle, Ed25519, verify). No agent/HTTP code.
- `crates/rewind-cli` — `rewind verify|inspect|log|show|diff`.
- `crates/rewind-py` — PyO3 bindings → `rewind_native`.
- `python/rewind` — the capture/replay/fork/`Deterministic` SDK.
- `examples/`, `spikes/` — runnable real-agent demos and the measurement harnesses.

By contributing you agree your contributions are licensed under [Apache-2.0](LICENSE).
