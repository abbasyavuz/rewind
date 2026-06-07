# Rewind

English | [Türkçe](README.tr.md)

**A flight recorder and time-travel debugger for AI agents.**

When an agent misbehaves in production you usually can't reproduce it — the model is
non-deterministic and the trail is gone. Rewind captures every non-deterministic boundary of an
agent run (model calls, tool results, retrieval, clock, RNG, HTTP) into a **signed,
content-addressed, offline-verifiable `.rewind` artifact**, then lets you **deterministically
replay** it, **scrub the timeline**, and ask the counterfactual: *"what if this one boundary had
returned X?"* — rewind, change one thing, and watch the trajectory diverge.

It hooks in **below the framework** (at the httpx transport), so any client built on `httpx` is
recorded with **zero code changes**. Verified end-to-end against the OpenAI and Anthropic SDKs;
frameworks that delegate to those SDKs / `httpx` (LangGraph, CrewAI, …) are captured by the same
hook — coverage we expect but don't yet ship an example for.

> ### The moat — the provable part
> For **self-hosted / OSS models** we control the sampler, so a boundary re-runs **canonical
> bit-for-bit** — the response's semantic content, with volatile `id`/`created`/`usage`/`logprobs`
> stripped (noise floor = 0); full raw-byte batch-invariance is the GPU/vLLM tier. A fork's
> divergence is then provably *your edit*, not sampling noise. For
> **closed APIs** (Claude / GPT / Gemini) Rewind is honest: **best-effort divergence triage with a
> first-class `INDETERMINATE`**, never a forensic certificate. We measured why — see [Evidence](#evidence).

Apache-2.0 · open-core, OpenTelemetry-style · validated against a real model (`minimax/minimax-m3`)
and a local OSS model (Ollama).

---

## See it work

A support agent picked a flaky tool, timed out, and told the customer "I can't check that right
now." Rewind reproduced the incident offline, then we changed **one** model decision and asked
*what would have happened* — and proved the fix, as a git-like, independently verifiable diff:

```console
$ rewind diff runs/support.rewind runs/support-fixed.rewind --verify \
    --pubkey-a runs/support.rewind/run-key.pub --pubkey-b runs/support-fixed.rewind/run-key.pub
trust: runs/support.rewind VERIFIED ✓   runs/support-fixed.rewind VERIFIED ✓
prefix: 1 identical · diverged at seq 1 · forked at seq 1 · frontier: +2 −2

@@ seq 1 (c718f8c10af5) — same request, response changed @@
  {
-   "tool": "legacy_billing"
+   "tool": "billing_v2"
  }

@@ frontier (unaligned — the path not taken vs the counterfactual branch) @@
- seq 2 POST -> {"out":"ERROR: upstream timeout"}
- seq 3 POST -> {"answer":"Sorry, I can't check that right now."}
+ seq 2 POST -> {"out":"refund $42.00 sent on 2026-05-28"}
+ seq 3 POST -> {"answer":"Your $42.00 refund was sent on 2026-05-28."}
```

The deterministic prefix is byte-identical; only the perturbed branch diverged. `exit 1` lets CI
assert "this fix changed the trajectory."

## The bitwise moat, automated

On a model you self-host, Rewind makes replay/fork **reproducible by construction** — a first-class
`Deterministic` profile pins the sampler:

```console
$ python examples/deterministic_oss.py        # Ollama llama3.2:3b, no GPU
det.verify_replay → seq 0: bitwise ✓   seq 1: bitwise ✓      # the recording replays bit-for-bit
fork ×2 (inference=det) → frontier canon identical → counterfactual REPRODUCIBLE ✓
```

Why this is the moat, not luck — a representative A/A run of the *same* model (same input,
N=10, temp=1.0; exact numbers vary run to run):

| Setting | Noise floor | |
|---|---|---|
| self-hosted, **seed pinned by us** | **0.00** | bitwise-reproducible → divergence 100% attributable |
| self-hosted, no seed | ~0.6 | proves the determinism is **our control**, not the model |
| closed API (`minimax-m3`) | uncontrollable | stable on easy prompts; [Spike-1](#evidence) returned **INCONCLUSIVE** (no near-boundary cases surfaced at temp=1.0) — the near-decision-boundary noise floor is *unmeasurable* here, and you can't pin it anyway |

## Quick start

```bash
make dev      # builds the Rust CLI + the PyO3 native module + the Python SDK into a venv
make test     # cargo test + pytest
```

`import rewind` needs the native module (`rewind_native`), which is a separate maturin crate —
`pip install` **alone is not enough**, so use `make dev` (or, manually):

```bash
cargo build --release                                  # the `rewind` CLI
python3 -m venv python/rewind/.venv && . python/rewind/.venv/bin/activate
pip install -U maturin && pip install -e "python/rewind[dev,examples]"
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop --release -m crates/rewind-py/Cargo.toml
```

```python
import rewind

with rewind.record("incident", out_dir="./incident.rewind"):
    run_my_agent()                      # OpenAI/Anthropic/LangGraph/… captured below the framework

# Deterministic replay — no network, no key: each boundary served from the recording.
with rewind.replay("./incident.rewind") as rep:
    run_my_agent()
    print(rep.report())                 # {recorded, served, unused}; divergence/ambiguity → FAIL LOUD

# Counterfactual fork — change one boundary, watch the trajectory diverge.
with rewind.fork("./incident.rewind", at=1, swap_response=(200, b'{"tool":"billing_v2"}'),
                 inference=rewind.Deterministic(seed=42),   # the branch re-runs the OSS model, reproducibly
                 record_to="./fixed.rewind"):
    run_my_agent()
```

## Time-travel debugger (Rust CLI — offline, no Python)

```bash
rewind log    incident.rewind                # timeline: one row per boundary (pipe to `less -R`)
rewind show   incident.rewind 3              # one boundary's request + response (by seq or causal-id)
rewind diff   a.rewind b.rewind --verify     # prefix · divergence · frontier; refuses a tampered side
rewind verify incident.rewind --pubkey k.pub # anyone can confirm integrity + signature, offline
rewind log    x.rewind --json | jq …         # log, inspect, and verify have a --json surface
```

The `rewind` binary is self-contained: it re-derives the BLAKE3 hash chain, the Merkle root, and the
Ed25519 signature with no trust in us — so a `.rewind` is independently verifiable and tamper-evident.

## Examples

Each runs a **real** agent (`minimax/minimax-m3` via OpenRouter, or local `llama3.2:3b` via Ollama),
captured with zero SDK changes. See [`examples/`](examples/):

- [`openrouter_agent.py`](examples/openrouter_agent.py) — record · replay · fork (canned **or `--live`** frontier).
- [`tooluse_agent.py`](examples/tooluse_agent.py) — a multi-step tool-use agent; the full reasoning + tool trail is captured and reproduced offline.
- [`deterministic_oss.py`](examples/deterministic_oss.py) — the bitwise tier: `verify_replay` + reproducible fork.

## Evidence

We don't just claim the moat — we pre-registered thresholds and measured it with runnable harnesses:

- **[`spikes/spike1_envelope.py`](spikes/spike1_envelope.py)** (divergence-envelope) — closed-API
  single-sample attribution is easy when the model is confident and **poor/unmeasurable near the
  decision boundary** (the identifiability wall). This is *why* the headline moat is OSS-bitwise, not
  closed-API forensics.
- **[`spikes/spike_oss_bitwise.py`](spikes/spike_oss_bitwise.py)** (bitwise-OSS replay) — on a
  self-hosted model the noise floor is 0 *because we hold the seed*, so fork divergence is provably
  edit-caused.

## How it works

```
your agent ──(httpx transport hook)──► capture ──► rewind-core (Rust): BLAKE3 CID · hash-chained
                                          │           HLC log · Merkle · Ed25519 ─► signed .rewind
   replay / fork  ◄── causal-id match (blake3(parent ‖ request)) · FAIL LOUD on divergence/ambiguity
   rewind CLI     ◄── log · show · diff · verify   (static binary, offline, no Python)
```

Causal boundary ids chain on lineage + request content (no clock), so they reproduce on replay; the
parent advances each step so sequential repeats stay distinct and only true concurrent collisions are
refused.

## Status

**v0, and the core loop works end to end, validated against real models.** `cargo test` + `pytest`
green; `record → replay → fork → debugger CLI` all run offline and cross-tool verify.

Honest scope: closed-API triage is provisional (pilot on `minimax-m3`; the binding Claude measurement
is pending). The local bitwise tier is *canonical*-bitwise + signed; full **raw-byte** batch-invariance
under production batching is the GPU/vLLM tier. Streaming is supported; gateway/Bedrock and
MCP-over-stdio interceptors are fast-follows. `# TODO(phase-N)` markers map to the technical plan.

## Roadmap

- **Phase 0 (current):** ship the signed artifact engine, deterministic replay, counterfactual fork,
  and the offline Rust debugger CLI as a small, honest v0. This is the code that works today.
- **Phase 1 (current `# TODO(phase-1)` markers):** tighten correctness and capture coverage without
  pretending new surfaces are done. The six live markers in the codebase sit here: request
  canonicalization, moving redaction off the synchronous hot path, actual shims for planned
  nondeterminism sources (`time` / RNG / `uuid`), and a better semantic diff than today's line-level
  JSON view.
- **Phase 2 (after the Phase 1 floor is in):** broaden capture surfaces and production ergonomics:
  non-`httpx` interceptors (gateway/Bedrock/Vertex/MCP-style boundaries), richer debugger UX, and
  the GPU/vLLM tier for raw-byte batch-invariance.

The rule is simple: Rewind only claims a boundary or determinism property when the verifier and tests
already back it up. Everything else stays explicitly phased.

## Pre-publish checklist

Before opening the repository publicly, check these once:

- Replace the placeholder GitHub URL (`rewind-dev/rewind`) in the Cargo metadata with the real repo URL.
- Re-run the local gates: `cargo fmt --all --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test`, and `python -m pytest -q`.
- Confirm `.env`, `.rewind`, and any local run artifacts stay out of Git history.
- Verify `SECURITY.md`, issue templates, PR template, and `CODE_OF_CONDUCT.md` match the repo's actual
  support workflow.
- Sanity-check the examples and README commands against a clean clone.

## Repo layout

```
crates/    rewind-core (the .rewind engine) · rewind-cli (verify · log · show · diff)
python/    the capture / replay / fork / Deterministic SDK
examples/  real agents (OpenRouter + Ollama)
spikes/    the measurement harnesses (envelope + bitwise-OSS)
```

## License

[Apache-2.0](LICENSE).
