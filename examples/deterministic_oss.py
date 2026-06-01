#!/usr/bin/env python3
"""The bitwise moat as a first-class feature (local, no GPU — Ollama llama3.2:3b).

`rewind.Deterministic` makes replay/fork re-run a self-hosted model with a pinned
seed, so:
  1. det.verify_replay(artifact) re-runs each recorded boundary and confirms the
     canonical response matches -> the recording replays BIT-FOR-BIT.
  2. fork(..., inference=det) auto-runs the divergent branch deterministically, so
     the counterfactual is REPRODUCIBLE — fork it twice and the frontier is identical.
     Its divergence from the recorded prefix is provably your edit, not noise.

    ollama serve
    python examples/deterministic_oss.py
"""

from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

import rewind
import rewind_native

ROOT = Path(__file__).resolve().parents[1]
OSS = "http://localhost:11434/v1"
MODEL = "llama3.2:3b"
TICKET = "I was charged twice this month and now I'm locked out of my account."

det = rewind.Deterministic(seed=42)


def client() -> OpenAI:
    return OpenAI(api_key="local", base_url=OSS)


def ask(c: OpenAI, system: str, user: str, max_tokens: int) -> str:
    # SEEDED -> the recording is bitwise-reproducible.
    r = c.chat.completions.create(
        model=MODEL, seed=42, temperature=1.0, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (r.choices[0].message.content or "").strip()


def agent(c: OpenAI) -> tuple[str, str]:
    category = ask(c, "Classify in ONE word: billing, technical, account, or other. Only the word.", TICKET, 16)
    reply = ask(c, f"You are support. Category is '{category}'. Reply in one short sentence.", TICKET, 60)
    return category, reply


def _resp_canon(artifact: str, seq: int) -> str:
    ev = next(e for e in rewind_native.load_events(artifact) if e["seq"] == seq)
    blob = json.loads((Path(artifact) / "objects" / f"b3-{ev['raw_cid']}.bin").read_bytes())
    return det.canon(blob["response"]["body"])


def _swap_to(artifact: str, category: str) -> bytes:
    ev = next(e for e in rewind_native.load_events(artifact) if e["seq"] == 0)
    blob = json.loads((Path(artifact) / "objects" / f"b3-{ev['raw_cid']}.bin").read_bytes())
    completion = json.loads(blob["response"]["body"])
    completion["choices"][0]["message"]["content"] = category
    return json.dumps(completion).encode()


def main() -> None:
    base = str(ROOT / "runs" / "det-base.rewind")
    import shutil
    shutil.rmtree(base, ignore_errors=True)

    print("1) record a SEEDED self-hosted run:")
    with rewind.record("det-base", out_dir=base) as rec:
        cat, reply = agent(client())
    print(f"   classified={cat!r}  reply={reply!r}  ({len(rec.events)} boundaries)\n")

    print("2) det.verify_replay — re-run each boundary, confirm bit-for-bit:")
    for r in det.verify_replay(base):
        print(f"   seq {r['seq']}: {'bitwise ✓' if r['bitwise'] else 'DRIFTED ✗'}")

    print("\n3) fork TWICE with inference=det (frontier asked live, pinned seed):")
    swap = _swap_to(base, "technical")
    forks = []
    for tag in ("a", "b"):
        out = str(ROOT / "runs" / f"det-fork-{tag}.rewind")
        shutil.rmtree(out, ignore_errors=True)
        with rewind.fork(base, at=0, swap_response=(200, swap), inference=det, record_to=out):
            agent(client())
        forks.append(out)
    fa, fb = (_resp_canon(f, 1) for f in forks)
    print(f"   fork-a frontier canon = {fa}")
    print(f"   fork-b frontier canon = {fb}")
    print(f"   => counterfactual is {'REPRODUCIBLE ✓ (identical)' if fa == fb else 'non-deterministic ✗'}")
    print("\n   The prefix is bit-for-bit and the counterfactual is reproducible, so the")
    print("   divergence is PROVABLY the edit — the bitwise moat, automated.")


if __name__ == "__main__":
    main()
