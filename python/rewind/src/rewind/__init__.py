"""Rewind capture SDK (v0).

Typical use::

    import rewind

    with rewind.record("incident-123", out_dir="./incident-123.rewind"):
        run_my_agent()          # OpenAI/Anthropic SDK calls are captured below the framework

    # then, offline, with the Rust CLI:
    #   rewind verify ./incident-123.rewind --pubkey key.pub

Status: v0 scaffolding. Capture (httpx chokepoint), causal boundary ids, the
deny-by-default guard, and forensic commitment are wired; the signed `.rewind`
manifest is produced by `rewind-core` (PyO3 binding TODO(phase-1)). Replay and
counterfactual fork are Phase-2 (see docs/rewind-technical-plan.md).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator

from . import capture, context
from .capture import Recorder, install, uninstall
from .events import BoundaryKind, CaptureSurface, EventRecord
from .guard import CoverageReport, Guard, UncoveredNondeterminismError

__version__ = "0.0.1"

__all__ = [
    "record",
    "install",
    "uninstall",
    "Recorder",
    "BoundaryKind",
    "CaptureSurface",
    "EventRecord",
    "Guard",
    "CoverageReport",
    "UncoveredNondeterminismError",
]


@contextlib.contextmanager
def record(run_id: str, out_dir: str | os.PathLike[str], strict: bool = True) -> Iterator[Recorder]:
    """Record all boundaries within the block into a `.rewind` artifact directory."""
    install(strict=strict)
    recorder = Recorder(run_id=run_id, out_dir=out_dir, strict=strict)
    ctx = context.RunContext(run_id=run_id, recorder=recorder)
    token = context.set_current(ctx)
    try:
        yield recorder
    finally:
        recorder.finalize()
        context.reset_current(token)
        if not recorder.guard.report.is_clean():
            # Surface uncovered nondeterminism even in non-strict mode.
            print(recorder.guard.report.render())
