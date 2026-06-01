#!/usr/bin/env python3
"""Capture a REAL OpenRouter agent run with Rewind, then replay/fork it offline.

OpenRouter is OpenAI-compatible, and the OpenAI SDK talks to it over httpx — which
is exactly where Rewind's capture hook lives. So wrapping the agent in
`rewind.record(...)` captures every real model call into a signed `.rewind`, with
NO changes to the agent or the SDK.

Setup (once):
    cd python/rewind && pip install -e ".[dev,examples]"
    # then paste your key into .env at the repo root:  OPENROUTER_API_KEY=sk-or-...

Run:
    python examples/openrouter_agent.py record   # REAL minimax/minimax-m3 run (needs key)
    python examples/openrouter_agent.py replay    # reproduce offline (no key, no network)
    python examples/openrouter_agent.py fork       # counterfactual: swap the classification

Inspect with the Rust CLI (offline, no Python):
    cargo run -q -p rewind-cli -- log  runs/support.rewind
    cargo run -q -p rewind-cli -- show runs/support.rewind 0
    cargo run -q -p rewind-cli -- diff runs/support.rewind runs/support-fixed.rewind --verify \
        --pubkey-a runs/support.rewind/run-key.pub --pubkey-b runs/support-fixed.rewind/run-key.pub
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import rewind_native
from dotenv import load_dotenv
from openai import OpenAI

import rewind

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("OPENROUTER_MODEL", "minimax/minimax-m3")
BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

ARTIFACT = str(ROOT / "runs" / "support.rewind")
FORKED = str(ROOT / "runs" / "support-fixed.rewind")

TICKET = "Hi — I was charged twice for order #A-2291. Please refund the duplicate charge."


def live_client() -> OpenAI:
    if not API_KEY:
        sys.exit("Set OPENROUTER_API_KEY in .env first (see .env.example).")
    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        default_headers={"HTTP-Referer": "https://github.com/rewind-dev/rewind", "X-Title": "Rewind"},
    )


def offline_client() -> OpenAI:
    # Replay/fork never reach the network (the hook serves from the recording), so a
    # dummy key is fine — canonicalization ignores headers, only method+url+body match.
    return OpenAI(api_key="offline-replay", base_url=BASE_URL)


def ask(client: OpenAI, system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def agent(client: OpenAI) -> tuple[str, str]:
    """A 2-step support agent: classify the ticket, then draft a reply."""
    category = ask(
        client,
        "Classify this support ticket as exactly one word: billing, technical, or other. "
        "Reply with ONLY the word.",
        TICKET,
    )
    reply = ask(
        client,
        f"You are a concise support agent. The ticket category is '{category}'. "
        "Write a one-sentence empathetic reply telling the customer the next step.",
        TICKET,
    )
    return category, reply


def _completion_template(seq: int) -> dict:
    """Read a recorded boundary's response (a real chat completion) so swapped/frontier
    responses keep a valid OpenAI schema — we only change the message content."""
    events = rewind_native.load_events(ARTIFACT)
    raw_cid = next(e["raw_cid"] for e in events if e["seq"] == seq)
    blob = json.loads((Path(ARTIFACT) / "objects" / f"b3-{raw_cid}.bin").read_bytes())
    return json.loads(blob["response"]["body"])


def _with_content(template: dict, content: str) -> bytes:
    out = json.loads(json.dumps(template))  # deep copy
    out["choices"][0]["message"]["content"] = content
    return json.dumps(out).encode()


def cmd_record() -> None:
    client = live_client()
    Path(ARTIFACT).parent.mkdir(parents=True, exist_ok=True)
    print(f"recording a real `{MODEL}` run via OpenRouter…\n")
    with rewind.record("support-live", out_dir=ARTIFACT) as rec:
        category, reply = agent(client)
    print(f"  classified as : {category}")
    print(f"  agent reply   : {reply}")
    print(f"\n✓ captured {len(rec.events)} boundaries -> {ARTIFACT}")
    print(f"  verify:  cargo run -q -p rewind-cli -- verify {ARTIFACT} --pubkey {ARTIFACT}/run-key.pub")
    print(f"  log:     cargo run -q -p rewind-cli -- log {ARTIFACT}")


def cmd_replay() -> None:
    with rewind.replay(ARTIFACT) as rep:
        category, reply = agent(offline_client())
    print("reproduced OFFLINE (no network, no key):")
    print(f"  classified as : {category}")
    print(f"  agent reply   : {reply}")
    print("  coverage:", rep.report())


def cmd_fork() -> None:
    swap_to = os.environ.get("SWAP_CATEGORY", "technical")
    swap_body = _with_content(_completion_template(0), swap_to)
    reply_template = _completion_template(1)

    def frontier(request: httpx.Request, req_body: bytes) -> httpx.Response:
        # The reply request diverges (new category in the prompt). In production this
        # would be a LIVE model call; offline we synthesize a valid completion.
        content = f"(counterfactual) Routing this as '{swap_to}' — escalating to the right team now."
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_with_content(reply_template, content),
            request=request,
        )

    print(f"counterfactual: what if the classifier returned '{swap_to}' at boundary 0?\n")
    with rewind.fork(
        ARTIFACT, at=0, swap_response=(200, swap_body), on_frontier=frontier, record_to=FORKED
    ) as fk:
        category, reply = agent(offline_client())
    print(f"  classified as : {category}")
    print(f"  agent reply   : {reply}")
    print("  report:", fk.report())
    print(f"\n✓ forked run -> {FORKED}")
    print(f"  diff:  cargo run -q -p rewind-cli -- diff {ARTIFACT} {FORKED} --verify \\")
    print(f"           --pubkey-a {ARTIFACT}/run-key.pub --pubkey-b {FORKED}/run-key.pub")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "record"
    {"record": cmd_record, "replay": cmd_replay, "fork": cmd_fork}.get(
        mode, lambda: sys.exit(f"unknown mode '{mode}' — use: record | replay | fork")
    )()


if __name__ == "__main__":
    main()
