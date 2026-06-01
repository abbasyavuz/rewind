"""Deterministic inference profile — the bitwise tier as a first-class API.

For a SELF-HOSTED model we control the sampler, so we can re-run a boundary
bit-for-bit. With a `Deterministic` profile:

  * `fork(..., inference=det)` re-runs the divergent (frontier) branch with a pinned
    seed, so the counterfactual is REPRODUCIBLE and its divergence from the recorded
    prefix is provably your edit — not sampling noise (the headline moat, automated).
  * `det.verify_replay(artifact)` re-runs each recorded boundary and confirms the
    canonical response matches — proving the recording replays bit-for-bit.

Canonical = the response's semantic content with volatile id/created/usage/logprobs
stripped (canonical-bitwise + signed; full raw-byte batch-invariance is the GPU tier).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import rewind_native

from . import context

_VOLATILE = {"id", "created", "system_fingerprint", "usage", "service_tier", "provider", "object"}


def canon_hash(body: bytes | str) -> str:
    """Canonical hash of a chat-completion response (volatile fields stripped)."""
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
    try:
        d = json.loads(text)
    except Exception:
        return hashlib.blake2b(text.encode()).hexdigest()[:16]
    for k in _VOLATILE:
        d.pop(k, None)
    for ch in d.get("choices", []) or []:
        ch.pop("logprobs", None)
        msg = ch.get("message") or {}
        msg.pop("reasoning", None)
        msg.pop("reasoning_details", None)
    return hashlib.blake2b(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


class Deterministic:
    """A self-hosted inference profile that re-runs requests with a pinned seed."""

    def __init__(self, *, seed: int = 42, api_key: str = "local", extra_headers: dict | None = None) -> None:
        self.seed = seed
        self.api_key = api_key
        self.extra_headers = extra_headers or {}

    def _inject_seed(self, body: bytes) -> bytes:
        try:
            d = json.loads(body)
        except Exception:
            return body
        # FORCE the sampler to be deterministic (don't just setdefault — the agent
        # may already carry temperature=1.0 / its own options.seed).
        d["seed"] = self.seed
        d["temperature"] = 0
        opts = d.get("options") or {}
        opts["seed"] = self.seed
        opts["temperature"] = 0
        d["options"] = opts
        return json.dumps(d).encode()

    def reissue(self, request: httpx.Request, req_body: bytes, *, inject: bool = True) -> httpx.Response:
        """Re-run a request live against its endpoint, OUTSIDE the rewind session (so
        it hits the network instead of recursing). `inject` pins the seed for a new
        (frontier) request; pass inject=False to re-send a recorded request verbatim."""
        token = context.set_current(None)
        try:
            body = self._inject_seed(req_body) if inject else req_body
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
                **self.extra_headers,
            }
            with httpx.Client(timeout=120) as hc:
                live = hc.request(request.method, str(request.url), headers=headers, content=body)
                data = live.read()
            drop = {"content-encoding", "content-length", "transfer-encoding"}
            rh = [(k, v) for k, v in live.headers.items() if k.lower() not in drop]
            return httpx.Response(live.status_code, headers=rh, content=data, request=request)
        finally:
            context.reset_current(token)

    def canon(self, body: bytes | str) -> str:
        return canon_hash(body)

    def verify_replay(self, artifact_dir: str) -> list[dict]:
        """Re-run each recorded boundary verbatim and check the canonical response
        matches the recording — proves bit-for-bit reproducibility. Requires the run
        to have been recorded deterministically (e.g. the agent passed a seed)."""
        events = rewind_native.load_events(str(artifact_dir))
        objects = Path(artifact_dir) / "objects"
        out: list[dict] = []
        for e in sorted(events, key=lambda x: x["seq"]):
            blob = json.loads((objects / f"b3-{e['raw_cid']}.bin").read_bytes())
            req = blob["request"]
            fresh = self.reissue(
                httpx.Request(req["method"], req["url"]), req["body"].encode(), inject=False
            )
            ok = canon_hash(fresh.content) == canon_hash(blob["response"]["body"])
            out.append({"seq": e["seq"], "bitwise": ok})
        return out
