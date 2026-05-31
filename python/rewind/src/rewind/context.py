"""Active session + causal-parent boundary id propagation.

One contextvar holds the active session (a Recorder in record mode, a Replayer in
replay mode — both expose `.mode`); another carries the causal-parent boundary id
across boundaries. Each spawned task must `copy_context` so siblings don't share
mutable lineage (technical plan §3.1).

The HLC tick and causal-id derivation live in rewind-core (one source of truth);
Python only carries the parent id between boundaries.
"""

from __future__ import annotations

import contextvars

from .events import ZERO_CID

# The active session (Recorder | Replayer | None). `.mode` is "record" | "replay".
_current: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "rewind_session", default=None
)
# The causal-parent boundary id for the current task.
_parent_boundary: contextvars.ContextVar[bytes] = contextvars.ContextVar(
    "rewind_parent_boundary", default=ZERO_CID
)


def current() -> object | None:
    return _current.get()


def set_current(session: object | None) -> contextvars.Token:
    return _current.set(session)


def reset_current(token: contextvars.Token) -> None:
    _current.reset(token)


def get_parent_boundary() -> bytes:
    return _parent_boundary.get()


def set_parent_boundary(boundary_id: bytes) -> contextvars.Token:
    """Call after a boundary so child calls chain off it. Use copy_context per
    spawned task to keep siblings independent."""
    return _parent_boundary.set(boundary_id)


def reset_parent_boundary(token: contextvars.Token) -> None:
    _parent_boundary.reset(token)
