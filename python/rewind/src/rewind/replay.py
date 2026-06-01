"""Deterministic replay + counterfactual fork.

Replay re-executes an agent against a recorded `.rewind` artifact, serving every
boundary from the recording instead of hitting the network. A live request is
matched to its recorded boundary by the causal boundary id
`blake3(parent || cid(canonical_request))` — the same key the recorder used. Two
failure modes FAIL LOUD rather than serve a wrong cassette:

  * divergence — the live request has no matching recorded boundary;
  * ambiguity  — two live calls map to the same recorded boundary.

Fork is the "wow": serve the deterministic prefix from the recording, perturb ONE
boundary's response at the fork point, then let the agent diverge — subsequent
requests that aren't in the recording hit the `on_frontier` handler (a real call,
a canned response, or a raise). This is the counterfactual "what if this boundary
had returned X?" that powers the time-travel debugger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import rewind_native

from . import context
from .capture import _semantic_request_canon, frame_blob
from .commitment import commit
from .events import ZERO_CID, cid


class ReplayMismatch(RuntimeError):
    """The live run diverged from the recording in a way the engine cannot honestly
    serve. Failing loud beats a confidently-wrong reconstruction."""


def _match_id(parent: bytes, request: httpx.Request, req_body: bytes) -> str:
    """The causal boundary id for a live request — the recording match key."""
    return rewind_native.causal_id_hex(parent, cid(_semantic_request_canon(request, req_body)))


def _recorded_response(objects: Path, event: dict, request: httpx.Request) -> httpx.Response:
    raw = (objects / f"b3-{event['raw_cid']}.bin").read_bytes()
    resp = json.loads(raw)["response"]
    body = resp["body"]
    # Detect a recorded SSE stream so a streaming consumer (e.g. openai stream=True)
    # parses it; otherwise serve as JSON. SSE always begins with an event line; JSON
    # always begins with { or [ — so this start-anchored test has no false positives.
    head = body.lstrip()[:8]
    is_sse = head.startswith("data:") or head.startswith("event:")
    ctype = "text/event-stream" if is_sse else "application/json"
    return httpx.Response(
        status_code=resp["status"],
        headers={"content-type": ctype},
        content=body.encode("utf-8"),
        request=request,
    )


class _Session:
    """Shared loading + bookkeeping for replay and fork."""

    mode = "replay"  # both ride the transport hook's replay branch

    def __init__(self, artifact_dir: str) -> None:
        self.dir = Path(artifact_dir)
        self.objects = self.dir / "objects"
        self._events = rewind_native.load_events(str(self.dir))
        # Don't silently collapse colliding causal ids into a dict — a collision is
        # un-replayable concurrent siblings, and serving an arbitrary one would
        # violate the FAIL-LOUD invariant. (rewind-core also refuses these at record
        # time and verify flags them; this is defense-in-depth for old/tampered logs.)
        self._by_id: dict[str, dict] = {}
        for e in self._events:
            key = e["causal_boundary_id"]
            if key in self._by_id:
                raise ReplayMismatch(
                    f"un-replayable recording: boundaries seq {self._by_id[key]['seq']} and "
                    f"{e['seq']} share causal id {key[:16]}… (concurrent siblings)."
                )
            self._by_id[key] = e
        self._by_seq: dict[int, dict] = {e["seq"]: e for e in self._events}
        self._consumed: set[str] = set()


class Replayer(_Session):
    """Serves recorded boundary responses for a faithfully re-executing agent."""

    def serve(self, request: httpx.Request, req_body: bytes) -> httpx.Response:
        parent = context.get_parent_boundary()
        cbid = _match_id(parent, request, req_body)
        event = self._by_id.get(cbid)
        if event is None:
            raise ReplayMismatch(
                f"replay divergence at boundary {cbid[:16]}…: the agent issued a request "
                f"with no matching recorded boundary (different prompt/tool/order)."
            )
        if cbid in self._consumed:
            raise ReplayMismatch(
                f"ambiguous replay at boundary {cbid[:16]}…: two live calls map to one "
                f"recorded boundary (concurrent or duplicate siblings sharing a parent)."
            )
        self._consumed.add(cbid)
        context.set_parent_boundary(bytes.fromhex(cbid))
        return _recorded_response(self.objects, event, request)

    def report(self) -> dict[str, int]:
        total = len(self._events)
        served = len(self._consumed)
        return {"recorded": total, "served": served, "unused": total - served}


class Forker(_Session):
    """Counterfactual fork: deterministic prefix, one perturbed response, then a
    live frontier past the perturbation.

    at:            seq of the recorded boundary to perturb.
    swap_response: (status, body_bytes) to serve at the fork point instead of the
                   recorded response — the agent's request there is unchanged, so
                   the prefix lineage still matches.
    on_frontier:   callable(request, req_body) -> httpx.Response for boundaries that
                   diverge past the fork (not in the recording). None -> FAIL LOUD.
    """

    def __init__(
        self,
        artifact_dir: str,
        *,
        at: int,
        swap_response: tuple[int, bytes],
        on_frontier=None,
        record_to: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(artifact_dir)
        if at not in self._by_seq:
            raise ValueError(f"fork point seq={at} is not in the recording (0..{len(self._by_seq) - 1})")
        self._fork_id = self._by_seq[at]["causal_boundary_id"]
        self._fork_seq = at
        self._swap = swap_response
        self._on_frontier = on_frontier
        self._forked = False
        self._frontier_hits = 0

        # Optional: tee the counterfactual run into a second signed .rewind so
        # `rewind diff original forked` is real (a first-class, verifiable artifact).
        self._record_to = str(record_to) if record_to is not None else None
        self._rec = None
        if self._record_to is not None:
            self._base_run_id = rewind_native.verify(str(self.dir)).get("run_id", "original")
            self._rec = rewind_native.Writer(
                self._record_to, run_id or f"{self._base_run_id}-fork", "record-only", 1
            )
            self._secret = bytes.fromhex(rewind_native.generate_secret_key())
            self._disclosure_key = os.urandom(32)

    def _tee(
        self,
        parent: bytes,
        request: httpx.Request,
        req_body: bytes,
        response: httpx.Response,
        source: str,
        kind: str,
        surface: str,
    ) -> None:
        if self._rec is None:
            return
        blob = frame_blob(request, req_body, response.status_code, response.content)
        c = commit(blob, self._disclosure_key)
        meta = {"source": source}
        if source == "swap":
            meta["forked_from"] = self._base_run_id
            meta["swap_at"] = str(self._fork_seq)
        # Same parent + canon the live call matched on -> the forked prefix carries
        # the SAME causal boundary ids as the original, so `diff` aligns exactly.
        self._rec.append(
            kind, surface, parent, _semantic_request_canon(request, req_body),
            blob, c.redacted, c.transform_desc, meta, 0,
        )

    def serve(self, request: httpx.Request, req_body: bytes) -> httpx.Response:
        parent = context.get_parent_boundary()
        cbid = _match_id(parent, request, req_body)
        event = self._by_id.get(cbid)

        if event is not None and cbid not in self._consumed:
            # In the recording: the deterministic prefix, the fork point, or a
            # post-fork re-convergence.
            self._consumed.add(cbid)
            context.set_parent_boundary(bytes.fromhex(cbid))
            if cbid == self._fork_id and not self._forked:
                self._forked = True
                status, body = self._swap
                resp = httpx.Response(
                    status_code=status,
                    headers={"content-type": "application/json"},
                    content=body,
                    request=request,
                )
                self._tee(parent, request, req_body, resp, "swap", event["kind"], event["surface"])
                return resp
            resp = _recorded_response(self.objects, event, request)
            self._tee(parent, request, req_body, resp, "replay", event["kind"], event["surface"])
            return resp

        # Not in the recording (or already consumed): a divergence.
        if not self._forked:
            raise ReplayMismatch(
                f"prefix divergence before the fork point at {cbid[:16]}…: the recording "
                f"does not reproduce up to the perturbation (seq={self._fork_seq})."
            )
        self._frontier_hits += 1
        context.set_parent_boundary(bytes.fromhex(cbid))
        if self._on_frontier is None:
            raise ReplayMismatch(
                f"fork frontier at {cbid[:16]}…: the agent diverged past the perturbation "
                f"and no on_frontier handler was provided (offline fork stops here)."
            )
        resp = self._on_frontier(request, req_body)
        self._tee(parent, request, req_body, resp, "frontier", "ModelCall", "SdkHttpx")
        return resp

    def finalize_fork(self) -> str | None:
        """Sign and write the forked artifact (if record_to was set). Returns its path."""
        if self._rec is None:
            return None
        self._rec.set_determinism({"fork_of": self._base_run_id, "fork_seq": str(self._fork_seq)})
        pub = self._rec.finalize(self._secret)
        Path(self._record_to).joinpath("run-key.pub").write_text(pub)
        return self._record_to

    def report(self) -> dict:
        r = {
            "recorded": len(self._events),
            "served": len(self._consumed),
            "forked": self._forked,
            "fork_seq": self._fork_seq,
            "frontier_hits": self._frontier_hits,
        }
        if self._record_to is not None:
            r["forked_artifact"] = self._record_to
        return r


def reset_to_start() -> None:
    """Reset the causal-parent lineage to the root (used when re-entering a run)."""
    context.set_parent_boundary(ZERO_CID)
