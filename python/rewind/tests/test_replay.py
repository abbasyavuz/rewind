"""Deterministic replay engine tests. Run: pytest (from python/rewind)."""

from __future__ import annotations

import httpx
import pytest

import rewind
from rewind import context as rctx
from rewind.events import ZERO_CID

_CALLS = [
    ("https://api.anthropic.com/v1/messages", {"i": 0}, b'{"r":"zero"}'),
    ("https://api.anthropic.com/v1/messages", {"i": 1}, b'{"r":"one"}'),
    ("https://api.openai.com/v1/chat", {"q": "x"}, b'{"r":"two"}'),
]


def _record(out: str, calls) -> None:
    with rewind.record("rep", out_dir=out) as rec:
        for url, body, resp in calls:
            req = httpx.Request("POST", url, json=body)
            rec.record_boundary(
                kind=rewind.BoundaryKind.MODEL_CALL,
                surface=rewind.CaptureSurface.SDK_HTTPX,
                request=req,
                req_body=req.content,
                resp_status=200,
                resp_body=resp,
                meta={},
            )


def test_replay_serves_recorded_responses(tmp_path) -> None:
    out = str(tmp_path / "run.rewind")
    _record(out, _CALLS)

    with rewind.replay(out) as rep:
        for url, body, resp in _CALLS:
            req = httpx.Request("POST", url, json=body)
            served = rep.serve(req, req.content)
            assert served.status_code == 200
            assert served.content == resp
        assert rep.report() == {"recorded": 3, "served": 3, "unused": 0}


def test_replay_divergence_fails_loud(tmp_path) -> None:
    out = str(tmp_path / "d.rewind")
    _record(out, _CALLS[:1])

    with rewind.replay(out) as rep:
        # A request that was never recorded -> no matching boundary -> FAIL LOUD.
        bad = httpx.Request("POST", "https://api.anthropic.com/v1/messages", json={"i": 999})
        with pytest.raises(rewind.ReplayMismatch, match="divergence"):
            rep.serve(bad, bad.content)


def test_replay_ambiguity_fails_loud(tmp_path) -> None:
    out = str(tmp_path / "a.rewind")
    _record(out, _CALLS[:1])

    with rewind.replay(out) as rep:
        url, body, _ = _CALLS[0]
        req = httpx.Request("POST", url, json=body)
        rep.serve(req, req.content)  # consumes the boundary; parent advances
        # Simulate a second sibling sharing the same (root) parent -> same id, consumed.
        rctx.set_parent_boundary(ZERO_CID)
        with pytest.raises(rewind.ReplayMismatch, match="ambiguous"):
            rep.serve(req, req.content)
