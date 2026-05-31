"""Event schema — the cross-language contract with rewind-core.

These dataclasses mirror the Rust `EventRecord` (see crates/rewind-core/src/log.rs
and spec/rewind-format-v0.1-DRAFT.md). The canonical encoding lives in Rust; this
is the Python-side view used during capture before handing bytes to rewind-core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import blake3


def cid(data: bytes) -> bytes:
    """BLAKE3-256 content id (32 raw bytes). Matches rewind-core's `Cid::of`."""
    return blake3.blake3(data).digest()


class BoundaryKind(str, Enum):
    MODEL_CALL = "ModelCall"
    TOOL_CALL = "ToolCall"
    RETRIEVAL = "Retrieval"
    CLOCK = "Clock"
    RNG = "Rng"
    HTTP = "Http"
    OPAQUE_TOOL = "OpaqueTool"


class CaptureSurface(str, Enum):
    SDK_HTTPX = "SdkHttpx"
    GATEWAY = "Gateway"  # provider wire NOT seen -> envelope auto-INDETERMINATE
    OPAQUE = "Opaque"


@dataclass(frozen=True)
class Hlc:
    """Hybrid logical clock — log order / audit timestamp only (NOT part of the
    causal boundary id, so replay stays deterministic)."""

    wall_ms: int
    counter: int
    node: int


@dataclass
class EventRecord:
    seq: int
    lamport: int
    hlc: Hlc
    prev_hash: bytes
    causal_boundary_id: bytes
    kind: BoundaryKind
    capture_surface: CaptureSurface
    raw_cid: bytes
    redacted_cid: bytes | None = None
    redaction_transform_cid: bytes | None = None
    meta: dict[str, str] = field(default_factory=dict)


def derive_causal_boundary_id(parent: bytes, semantic_request_hash: bytes) -> bytes:
    """blake3(parent || semantic_request_hash) — the anti-swap primitive and replay
    match key (technical plan §3.1). NO clock, so it reproduces on replay.

    Byte-for-byte identical to rewind-core::log::causal_boundary_id (guarded by a
    parity test). Do not change one side without the other.
    """
    assert len(parent) == 32 and len(semantic_request_hash) == 32
    return cid(parent + semantic_request_hash)


ZERO_CID = b"\x00" * 32
