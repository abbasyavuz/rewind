"""Offline tests for the deterministic inference profile."""

from __future__ import annotations

import json

from rewind.inference import Deterministic, canon_hash


def test_canon_hash_strips_volatile_fields() -> None:
    a = '{"id":"x1","created":1,"object":"chat.completion","usage":{"t":5},"choices":[{"message":{"content":"hi"}}]}'
    b = '{"id":"x2","created":2,"object":"chat.completion","usage":{"t":9},"choices":[{"message":{"content":"hi"}}]}'
    # same semantic content, different volatile fields -> identical canonical hash
    assert canon_hash(a) == canon_hash(b)
    c = '{"id":"x3","choices":[{"message":{"content":"DIFFERENT"}}]}'
    assert canon_hash(a) != canon_hash(c)


def test_canon_hash_ignores_reasoning_and_logprobs() -> None:
    a = '{"choices":[{"logprobs":{"x":-0.1},"message":{"content":"ok","reasoning":"thought A"}}]}'
    b = '{"choices":[{"logprobs":{"x":-0.9},"message":{"content":"ok","reasoning":"thought B"}}]}'
    assert canon_hash(a) == canon_hash(b)


def test_seed_injection_forces_determinism() -> None:
    det = Deterministic(seed=7)
    out = json.loads(det._inject_seed(b'{"model":"m","messages":[]}'))
    assert out["seed"] == 7
    assert out["options"]["seed"] == 7
    assert out["temperature"] == 0
    # FORCE even when the request already carries a temperature / its own options.seed.
    out2 = json.loads(det._inject_seed(b'{"temperature":1.0,"options":{"seed":99},"messages":[]}'))
    assert out2["temperature"] == 0
    assert out2["seed"] == 7 and out2["options"]["seed"] == 7
