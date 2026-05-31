"""Run context + causal-parent boundary id propagation.

Propagates the run id and the causal-parent boundary id across async tasks and
threads via contextvars. Each spawned task must `copy_context` so siblings don't
share mutable lineage (technical plan §3.1).

The HLC tick and the causal-id derivation themselves live in rewind-core (one
source of truth for the anti-swap primitive); Python only carries the parent id
between boundaries and hands it to the native writer.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from .events import ZERO_CID


@dataclass
class RunContext:
    run_id: str
    recorder: object  # rewind.capture.Recorder (avoid import cycle)


# The active run context (None when not recording).
_current: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "rewind_run", default=None
)
# The causal-parent boundary id for the current task.
_parent_boundary: contextvars.ContextVar[bytes] = contextvars.ContextVar(
    "rewind_parent_boundary", default=ZERO_CID
)


def current() -> RunContext | None:
    return _current.get()


def set_current(ctx: RunContext | None) -> contextvars.Token:
    return _current.set(ctx)


def reset_current(token: contextvars.Token) -> None:
    _current.reset(token)


def get_parent_boundary() -> bytes:
    return _parent_boundary.get()


def set_parent_boundary(boundary_id: bytes) -> contextvars.Token:
    """Call after appending an event so child calls chain off it. Use copy_context
    per spawned task to keep siblings independent."""
    return _parent_boundary.set(boundary_id)
