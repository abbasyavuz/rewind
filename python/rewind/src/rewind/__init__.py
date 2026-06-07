"""Rewind capture + replay SDK (v0).

Record::

    import rewind
    with rewind.record("incident-123", out_dir="./incident-123.rewind"):
        run_my_agent()          # OpenAI/Anthropic SDK calls captured below the framework

    # then, offline, with the Rust CLI:
    #   rewind verify ./incident-123.rewind --pubkey key.pub

Replay (deterministic re-execution, no network)::

    with rewind.replay("./incident-123.rewind") as rep:
        run_my_agent()          # each boundary served from the recording
    print(rep.report())         # {recorded, served, unused}

Status: v0. Capture, deterministic replay (match by causal boundary id, FAIL LOUD on
divergence/ambiguity), counterfactual fork, and the `Deterministic` (bitwise) profile
are all wired; the signed `.rewind` is produced by rewind-core (PyO3). The only
deferred piece is an interactive time-travel debugger UI/TUI (v0.5; today the Rust
`rewind log|show|diff` CLI covers it). See the project README for the roadmap.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator

from collections.abc import Callable

from . import context
from .capture import Recorder, install, uninstall
from .events import ZERO_CID, BoundaryKind, CaptureSurface, EventRecord
from .guard import CoverageReport, Guard, UncoveredNondeterminismError
from .inference import Deterministic
from .replay import Forker, Replayer, ReplayMismatch

__version__ = "0.0.1"

__all__ = [
    "record",
    "replay",
    "fork",
    "install",
    "uninstall",
    "Recorder",
    "Replayer",
    "Forker",
    "Deterministic",
    "ReplayMismatch",
    "BoundaryKind",
    "CaptureSurface",
    "EventRecord",
    "Guard",
    "CoverageReport",
    "UncoveredNondeterminismError",
]


@contextlib.contextmanager
def record(run_id: str, out_dir: str | os.PathLike[str], strict: bool = True) -> Iterator[Recorder]:
    """Record all boundaries within the block into a signed `.rewind` artifact."""
    install(strict=strict)
    recorder = Recorder(run_id=run_id, out_dir=str(out_dir), strict=strict)
    session = context.set_current(recorder)
    parent = context.set_parent_boundary(ZERO_CID)
    try:
        yield recorder
    finally:
        recorder.finalize()
        context.reset_parent_boundary(parent)
        context.reset_current(session)
        if not recorder.guard.report.is_clean():
            # Surface uncovered nondeterminism even in non-strict mode.
            print(recorder.guard.report.render())


@contextlib.contextmanager
def replay(artifact_dir: str | os.PathLike[str]) -> Iterator[Replayer]:
    """Re-execute within the block against a recorded artifact, serving each
    boundary from the recording (no network). FAILs LOUD on divergence/ambiguity."""
    install()
    replayer = Replayer(str(artifact_dir))
    session = context.set_current(replayer)
    parent = context.set_parent_boundary(ZERO_CID)
    try:
        yield replayer
    finally:
        context.reset_parent_boundary(parent)
        context.reset_current(session)


@contextlib.contextmanager
def fork(
    artifact_dir: str | os.PathLike[str],
    *,
    at: int,
    swap_response: tuple[int, bytes],
    on_frontier: Callable[[object, bytes], object] | None = None,
    inference: Deterministic | None = None,
    record_to: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
) -> Iterator[Forker]:
    """Counterfactual fork: serve the deterministic prefix from the recording, swap
    one boundary's response at `at` (seq), then let the agent diverge — post-fork
    requests not in the recording go to `on_frontier` (a live call / canned response
    / None to FAIL LOUD). The "what if this boundary had returned X?" question.

    Pass `inference=Deterministic(...)` to auto-run the divergent branch against a
    self-hosted model with a pinned seed — the counterfactual becomes reproducible
    and its divergence is provably your edit (the bitwise moat). With `record_to`,
    the counterfactual is written to a second signed `.rewind` for `rewind diff`."""
    if on_frontier is None and inference is not None:
        on_frontier = lambda req, body: inference.reissue(req, body, inject=True)  # noqa: E731
    install()
    forker = Forker(
        str(artifact_dir),
        at=at,
        swap_response=swap_response,
        on_frontier=on_frontier,
        record_to=None if record_to is None else str(record_to),
        run_id=run_id,
    )
    session = context.set_current(forker)
    parent = context.set_parent_boundary(ZERO_CID)
    try:
        yield forker
    finally:
        context.reset_parent_boundary(parent)
        context.reset_current(session)
        forker.finalize_fork()
