"""Counterfactual fork tests — the time-travel 'what if?' Run: pytest."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import rewind_native

import rewind

URL = "https://api.anthropic.com/v1/messages"


def _agent(client: httpx.Client) -> str:
    """A tiny branching agent: its first decision steers which tool it calls."""
    decision = client.post(URL, json={"step": "ask"}).json()
    if decision["tool"] == "search":
        out = client.post(URL, json={"step": "search"}).json()
    else:
        out = client.post(URL, json={"step": "calc"}).json()
    return out["result"]


def _record_baseline(out: str) -> None:
    # ask -> {tool: search}; search -> {result: S}.  The recorded ("real") run.
    with rewind.record("base", out_dir=out) as rec:
        for body, resp in (
            ({"step": "ask"}, b'{"tool":"search"}'),
            ({"step": "search"}, b'{"result":"S"}'),
        ):
            req = httpx.Request("POST", URL, json=body)
            rec.record_boundary(
                kind=rewind.BoundaryKind.MODEL_CALL,
                surface=rewind.CaptureSurface.SDK_HTTPX,
                request=req,
                req_body=req.content,
                resp_status=200,
                resp_body=resp,
                meta={},
            )


def test_replay_reproduces_the_baseline(tmp_path) -> None:
    out = str(tmp_path / "b.rewind")
    _record_baseline(out)
    with rewind.replay(out):
        assert _agent(httpx.Client()) == "S"  # faithful re-execution, no network


def test_fork_one_response_changes_the_whole_trajectory(tmp_path) -> None:
    out = str(tmp_path / "b.rewind")
    _record_baseline(out)

    def frontier(request: httpx.Request, body: bytes) -> httpx.Response:
        # The calc branch's request was never recorded -> serve the counterfactual.
        return httpx.Response(200, content=b'{"result":"C"}', request=request)

    # "What if boundary 0 had returned tool=calc instead of tool=search?"
    with rewind.fork(out, at=0, swap_response=(200, b'{"tool":"calc"}'), on_frontier=frontier) as fk:
        result = _agent(httpx.Client())

    assert result == "C"  # one swapped response -> a different downstream outcome
    rep = fk.report()
    assert rep["forked"] is True
    assert rep["frontier_hits"] == 1


def test_fork_without_frontier_handler_fails_loud(tmp_path) -> None:
    out = str(tmp_path / "b.rewind")
    _record_baseline(out)
    # Swap to a branch that diverges, but provide no frontier handler.
    with rewind.fork(out, at=0, swap_response=(200, b'{"tool":"calc"}')) as _fk:
        with pytest.raises(rewind.ReplayMismatch, match="frontier"):
            _agent(httpx.Client())


def test_fork_invalid_point_rejected(tmp_path) -> None:
    out = str(tmp_path / "b.rewind")
    _record_baseline(out)
    with pytest.raises(ValueError, match="fork point"):
        with rewind.fork(out, at=99, swap_response=(200, b"{}")):
            pass


def test_fork_record_to_writes_verifiable_diffable_artifact(tmp_path) -> None:
    base = str(tmp_path / "base.rewind")
    forked = str(tmp_path / "forked.rewind")
    _record_baseline(base)

    def frontier(request: httpx.Request, body: bytes) -> httpx.Response:
        return httpx.Response(200, content=b'{"result":"C"}', request=request)

    with rewind.fork(
        base, at=0, swap_response=(200, b'{"tool":"calc"}'), on_frontier=frontier, record_to=forked
    ) as fk:
        assert _agent(httpx.Client()) == "C"
    assert fk.report()["forked_artifact"] == forked

    # The forked run is a real, signed, offline-verifiable artifact.
    pub = (Path(forked) / "run-key.pub").read_text()
    assert rewind_native.verify(forked, pub)["ok"] is True

    # Its prefix carries the SAME causal boundary id as the original at the fork
    # point (request unchanged), so `rewind diff` aligns them exactly.
    base_ids = [e["causal_boundary_id"] for e in rewind_native.load_events(base)]
    fork_ids = [e["causal_boundary_id"] for e in rewind_native.load_events(forked)]
    assert fork_ids[0] == base_ids[0]
    assert fork_ids[1] != base_ids[1]  # diverged past the fork


def test_fork_status_only_swap_is_a_real_divergence(tmp_path) -> None:
    """A swap that changes ONLY the HTTP status (same body) is still a real
    divergence: same causal id (request unchanged) but different committed bytes
    (raw_cid). This is what `rewind diff` must align on, not the decoded body."""
    base = str(tmp_path / "base.rewind")
    forked = str(tmp_path / "forked.rewind")
    _record_baseline(base)  # boundary 0: ask -> 200 {"tool":"search"}

    # Same body the agent branches on -> it follows the SAME path (re-converges on
    # boundary 1); only the status differs.
    with rewind.fork(
        base, at=0, swap_response=(503, b'{"tool":"search"}'), record_to=forked
    ):
        assert _agent(httpx.Client()) == "S"

    base0 = next(e for e in rewind_native.load_events(base) if e["seq"] == 0)
    fork0 = next(e for e in rewind_native.load_events(forked) if e["seq"] == 0)
    assert fork0["causal_boundary_id"] == base0["causal_boundary_id"]  # request unchanged
    assert fork0["raw_cid"] != base0["raw_cid"]  # committed bytes (status) differ
