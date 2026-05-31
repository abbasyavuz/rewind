# rewind (Python capture SDK)

Below-framework capture of an AI agent's non-deterministic boundaries, written to a
content-addressed `.rewind` artifact that the Rust `rewind` CLI verifies offline.

```bash
pip install -e ".[dev]"
# build the native artifact engine (PyO3 -> rewind-core) into the same venv:
pip install maturin
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop --release \
  -m ../../crates/rewind-py/Cargo.toml
```

```python
import rewind

with rewind.record("incident-123", out_dir="./incident-123.rewind"):
    run_my_agent()   # OpenAI / Anthropic SDK calls captured at the httpx transport

# offline, no Python needed:
#   rewind verify ./incident-123.rewind --pubkey key.pub
```

## v0 scope (honest)

| Wired | TODO(phase-1+) |
|---|---|
| httpx transport chokepoint (OpenAI/Anthropic SDK) | Bedrock/Vertex/gateway (non-httpx) interceptors |
| Causal boundary ids (anti-swap) | Concurrent-replay determinism (Spike-2) |
| Deny-by-default nondeterminism guard + coverage report | Actual per-source shims (RNG/clock/vector store) |
| Forensic commitment + auditable regex redaction | Presidio backend; streaming/SSE tee |
| **PyO3 binding → real signed `.rewind`** (chain/Merkle/Ed25519 via rewind-core) | — |
| **Deterministic replay** (match by causal id, serve from recording, FAIL LOUD on divergence/ambiguity) | — |
| **Counterfactual fork** (deterministic prefix, swap one boundary's response, live-frontier past it) | Time-travel debugger UI; prompt-edit perturbations |

See [`../../docs/rewind-technical-plan.md`](../../docs/rewind-technical-plan.md).
