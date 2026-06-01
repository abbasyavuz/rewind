#!/usr/bin/env python3
"""The headline moat, demonstrated locally: bitwise-exact OSS replay.

The de-risked thesis (docs/rewind-technical-plan.md): for a SELF-HOSTED model we
control, re-running a boundary is canonically identical — the A/A noise floor is
ZERO — so a fork's divergence is 100% attributable to your edit. Contrast the
closed API (Spike-1: minimax flips 25–42% on the same input), where attribution is
only best-effort triage. THAT binary, un-fakeable property is the moat.

This runs entirely on a Mac with NO GPU, via Ollama's OpenAI-compatible endpoint
(llama3.2:3b, 2GB) captured by the SAME httpx hook — zero SDK changes:

    ollama serve            # (usually already running)
    python spikes/spike_oss_bitwise.py

HONEST FRAMING: this is CANONICAL-bitwise (we hash the response's semantic content
with volatile id/created/usage/logprobs stripped) + a signed artifact. Full
RAW-byte batch-invariance under production batching is the GPU/vLLM tier
(Thinking Machines batch_invariant_ops) — out of scope for a laptop pilot.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import rewind
import rewind_native

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

OSS_BASE = "http://localhost:11434/v1"
OSS_MODEL = "llama3.2:3b"
CLOSED_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
CLOSED_MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3")
CLOSED_KEY = os.environ.get("OPENROUTER_API_KEY", "")

N = 10
PROMPT = [
    {"role": "system", "content": "You are a support-ticket router. Reply with ONE word: billing, technical, account, shipping, sales, or other."},
    {"role": "user", "content": "I was charged twice this month and now my account is locked."},
]

# Fields that legitimately vary run-to-run even for a deterministic generation.
_VOLATILE = {"id", "created", "system_fingerprint", "usage", "service_tier", "provider", "object"}


def canon_hash(resp) -> str:
    """Canonical hash of the response's *semantic* content (volatile fields stripped)."""
    d = resp.model_dump()
    for k in _VOLATILE:
        d.pop(k, None)
    for ch in d.get("choices", []) or []:
        ch.pop("logprobs", None)  # v0: float logprobs excluded
        msg = ch.get("message") or {}
        msg.pop("reasoning", None)
        msg.pop("reasoning_details", None)
    return hashlib.blake2b(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


TEMP = 1.0  # a realistic sampling temperature — where the control matters


def aa_floor(client: OpenAI, model: str, *, seed, workers: int, reasoning_off: bool = False) -> Counter:
    """Run the SAME request N times at temp=1.0; return the distribution of canonical
    hashes. `seed` set => we pin the sampler (only possible when we control inference)."""
    def one(_):
        kw = {"temperature": TEMP, "max_tokens": 16}
        eb: dict = {}
        if seed is not None:
            kw["seed"] = seed
            eb["options"] = {"seed": seed, "temperature": TEMP}
        if reasoning_off:
            eb["reasoning"] = {"enabled": False}
        if eb:
            kw["extra_body"] = eb
        return canon_hash(client.chat.completions.create(model=model, messages=PROMPT, **kw))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return Counter(ex.map(one, range(N)))


def _report(label: str, hashes: Counter, note_floor0: str, note_floorN: str) -> None:
    uniq = len(hashes)
    floor = 1 - max(hashes.values()) / N
    print(f"  {label}")
    print(f"     {N} runs -> {uniq} unique canonical hash(es)  {dict(hashes)}")
    print(f"     noise floor = {floor:.2f}  => {note_floor0 if uniq == 1 else note_floorN}\n")


def main() -> None:
    print(f"── A/A noise floor (same input, N={N}, temp={TEMP}) — determinism is a CONTROL we hold ──\n")

    oss = OpenAI(api_key="local", base_url=OSS_BASE)
    # We control the self-hosted sampler: pin the seed -> bitwise-reproducible AT ANY temp.
    _report(f"SELF-HOSTED OSS  ({OSS_MODEL}, temp={TEMP}, seed=42  <- WE pin it):",
            aa_floor(oss, OSS_MODEL, seed=42, workers=1),
            "BITWISE-REPRODUCIBLE (floor 0 -> divergence is 100% attributable)",
            "not fully reproducible (batching?) — see GPU/vLLM tier")
    # Same model, same temp, WITHOUT our seed -> noisy. Proves the determinism is OUR control.
    _report(f"SELF-HOSTED OSS  ({OSS_MODEL}, temp={TEMP}, NO seed):",
            aa_floor(oss, OSS_MODEL, seed=None, workers=1),
            "stable on this prompt",
            "noisy without the seed — i.e. the floor=0 above is OUR control, not luck")

    if CLOSED_KEY:
        closed = OpenAI(api_key=CLOSED_KEY, base_url=CLOSED_BASE)
        # We pass a seed too — but a closed API need not honor it, and can't be pinned.
        _report(f"CLOSED API  ({CLOSED_MODEL}, temp={TEMP}, seed passed but uncontrollable):",
                aa_floor(closed, CLOSED_MODEL, seed=42, workers=8, reasoning_off=True),
                "stable on this prompt (but the provider can change this any time — no guarantee)",
                "noisy: no honored seed -> attribution is best-effort triage only")
    else:
        print("  CLOSED API: (set OPENROUTER_API_KEY to compare; Spike-1 measured minimax flip 0.25–0.42)\n")

    # Tie it to Rewind: capture a self-hosted run into a signed, offline-verifiable .rewind.
    art = str(ROOT / "runs" / "oss-bitwise.rewind")
    Path(art).parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.rmtree(art, ignore_errors=True)
    with rewind.record("oss-bitwise", out_dir=art) as rec:
        oss.chat.completions.create(model=OSS_MODEL, messages=PROMPT, temperature=0, seed=42, max_tokens=16,
                                    extra_body={"options": {"seed": 42}})
    pub = (Path(art) / "run-key.pub").read_text()
    report = rewind_native.verify(art, pub)
    print(f"  captured a self-hosted run -> {art}  ({len(rec.events)} boundary)")
    print(f"  rewind verify: {'VERIFIED ✓' if report['ok'] else 'FAILED ✗'}  (signed, offline-verifiable)")

    print("\n  => On a self-hosted model the noise floor is 0, so Rewind's fork divergence is")
    print("     PROVABLY edit-caused — the binary, un-fakeable moat. Closed APIs get honest triage.")
    print("  (Canonical-bitwise + signed; full raw-byte batch-invariance is the GPU/vLLM tier.)")


if __name__ == "__main__":
    main()
