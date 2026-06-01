# Changelog

All notable changes to Rewind are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses [SemVer](https://semver.org/).

## [Unreleased]

Initial development toward the first release. The core loop works end to end and is validated against
real models (`minimax/minimax-m3` via OpenRouter; local `llama3.2:3b` via Ollama).

### Added
- **`.rewind` artifact engine** (`rewind-core`): BLAKE3 content addressing, hash-chained HLC event log,
  Merkle root, dCBOR manifest, Ed25519 attestation, and a standalone **offline verifier**.
- **`rewind` CLI**: `verify`, `inspect`, `keygen`, `demo`, and the time-travel debugger commands
  `log`, `show`, `diff` (`--json` on `log`/`inspect`/`verify`).
- **Capture SDK** (Python): below-framework capture at the httpx transport (zero SDK changes),
  causal boundary ids (anti-swap), a deny-by-default nondeterminism guard, and forensic commitment
  with auditable redaction. **Streaming/SSE** responses are teed incrementally (TTFT preserved).
- **Deterministic replay**: match each boundary by causal id; **FAIL LOUD** on divergence/ambiguity;
  rewind-core refuses to sign a recording with colliding causal ids.
- **Counterfactual fork**: deterministic prefix, swap one boundary, live frontier; `record_to` writes
  a second signed artifact so `rewind diff` shows the divergence.
- **`Deterministic` inference profile** (the bitwise moat): `verify_replay` proves a recording replays
  bit-for-bit, and `fork(inference=…)` re-runs the divergent branch with a pinned seed → reproducible
  counterfactual.
- Examples (real OpenRouter + Ollama agents) and the Spike-1 (divergence-envelope) and bitwise-OSS
  measurement harnesses + findings.

### Notes
- Closed-API divergence triage is provisional (pilot on `minimax-m3`; binding Claude measurement pending).
- The local bitwise tier is *canonical*-bitwise + signed; raw-byte batch-invariance is the GPU/vLLM tier.
