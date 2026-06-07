#!/usr/bin/env python3
"""A multi-step TOOL-USE agent, captured by Rewind.

Works against any OpenAI-compatible provider — pick yours in .env (see .env.example);
Rewind ships no default model id. The model is given tools, decides which to call,
we run them locally and feed the results back, and it loops until it answers — so
each model call is a boundary and the timeline gets long. The whole tool-use
conversation (tool_calls + results) lives inside the captured request/response
bodies, so `rewind show` on a late boundary shows the full reasoning trail. Tools
are deterministic, so replay is exact.

    python examples/tooluse_agent.py record   # live tool loop against your provider (needs key + model)
    python examples/tooluse_agent.py replay     # reproduce offline (no key, no network)

Inspect:
    cargo run -q -p rewind-cli -- log  runs/tooluse.rewind
    cargo run -q -p rewind-cli -- show runs/tooluse.rewind 3
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import rewind

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among env vars (new REWIND_* names, then the legacy
    OPENROUTER_* ones for backward compatibility)."""
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return default


API_KEY = _env("REWIND_API_KEY", "OPENROUTER_API_KEY")
MODEL = _env("REWIND_MODEL", "OPENROUTER_MODEL")  # no default — you pick the model
BASE_URL = _env("REWIND_BASE_URL", "OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
ARTIFACT = str(ROOT / "runs" / "tooluse.rewind")
MAX_STEPS = 6

# --- deterministic local tools (fixed data -> replay is exact) ---
_ORDERS = {
    "A-2291": {"item": "Wireless Headphones", "unit_price": 42.00, "charges": 2, "ordered": "2026-05-20"},
    "A-3310": {"item": "USB-C Cable", "unit_price": 12.00, "charges": 1, "ordered": "2026-05-22"},
}


def get_order(order_id: str) -> dict:
    return _ORDERS.get(order_id, {"error": f"order {order_id} not found"})


def get_refund_policy(reason: str) -> dict:
    return {"reason": reason, "window_days": 30, "auto_approve_under_usd": 100.0, "eligible": True}


def issue_refund(order_id: str, amount: float) -> dict:
    return {"order_id": order_id, "refunded_usd": amount, "eta_days": "3-5", "reference": f"RF-{order_id}"}


TOOLS = {"get_order": get_order, "get_refund_policy": get_refund_policy, "issue_refund": issue_refund}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "get_order", "description": "Look up an order by its id.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "get_refund_policy", "description": "Get the refund policy for a reason, e.g. 'duplicate_charge'.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}},
    {"type": "function", "function": {
        "name": "issue_refund", "description": "Issue a refund of `amount` USD for an order.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "amount": {"type": "number"}}, "required": ["order_id", "amount"]}}},
]

SYSTEM = (
    "You are an order-support agent. Resolve the ticket by USING THE TOOLS: first look up the order, "
    "then check the refund policy, then issue the refund if the customer was over-charged. "
    "When done, reply to the customer in one short sentence. Call one tool at a time."
)
TICKET = "I was charged twice for order #A-2291 and want a refund for the duplicate charge."


def run_agent(client: OpenAI, trace: bool) -> str:
    messages: list = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": TICKET}]
    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS_SPEC, tool_choice="auto", temperature=0
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or ""
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = TOOLS[tc.function.name](**args)
            if trace:
                print(f"    ↳ tool {tc.function.name}({args}) -> {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return "(max steps reached)"


def make_client(live: bool) -> OpenAI:
    if live and not API_KEY:
        sys.exit("Set REWIND_API_KEY in .env first (see .env.example).")
    if live and not MODEL:
        sys.exit("Set REWIND_MODEL in .env first — Rewind ships no default model (see .env.example).")
    return OpenAI(api_key=(API_KEY if live else "offline-replay"), base_url=BASE_URL)


def cmd_record() -> None:
    Path(ARTIFACT).parent.mkdir(parents=True, exist_ok=True)
    print(f"recording a multi-step `{MODEL}` tool-use run…\n")
    with rewind.record("tooluse-live", out_dir=ARTIFACT) as rec:
        answer = run_agent(make_client(True), trace=True)
    print(f"\n  final answer : {answer}")
    print(f"\n✓ captured {len(rec.events)} model boundaries -> {ARTIFACT}")
    print(f"  log:  cargo run -q -p rewind-cli -- log {ARTIFACT}")
    print(f"  show: cargo run -q -p rewind-cli -- show {ARTIFACT} {max(0, len(rec.events) - 1)}")


def cmd_replay() -> None:
    with rewind.replay(ARTIFACT) as rep:
        answer = run_agent(make_client(False), trace=True)
    print(f"\n  final answer : {answer}")
    print("  coverage:", rep.report())


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "record"
    {"record": cmd_record, "replay": cmd_replay}.get(
        mode, lambda: sys.exit(f"unknown mode '{mode}' — use: record | replay")
    )()


if __name__ == "__main__":
    main()
