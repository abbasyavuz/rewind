//! The append-only, hash-chained event log.
//!
//! Each `EventRecord` captures one non-deterministic boundary. `prev_hash` links
//! to the preceding record's `record_hash`, forming a tamper-evident chain.
//!
//! `causal_boundary_id` is the fix for the silent-divergence engine (technical
//! plan §3.1): it is derived from causal lineage, NOT a wall-clock ordinal, so
//! concurrent calls cannot have their cassette responses swapped on replay.

use crate::cid::Cid;
use crate::error::Result;
use crate::hlc::Hlc;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum BoundaryKind {
    ModelCall,
    ToolCall,
    Retrieval,
    Clock,
    Rng,
    Http,
    /// A tool we cannot deterministically capture (e.g. MCP-over-stdio in v0).
    /// Recorded best-effort; explicitly OUT of the deterministic-replay scope.
    OpaqueTool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum CaptureSurface {
    /// Captured at the httpx transport (OpenAI/Anthropic SDK). Provider wire seen.
    SdkHttpx,
    /// Behind a gateway (LiteLLM/Portkey/egress proxy): provider wire NOT seen.
    /// Auto-INDETERMINATE for the divergence envelope (noise-floor poisoned).
    Gateway,
    /// Opaque boundary, best-effort recorded I/O only.
    Opaque,
}

/// One captured non-deterministic boundary.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EventRecord {
    pub seq: u64,
    pub lamport: u64,
    pub hlc: Hlc,
    /// `record_hash` of the preceding event (Cid::ZERO for the first).
    pub prev_hash: Cid,
    /// blake3(parent_boundary_id || semantic_request_hash) — NO clock, so it
    /// reproduces on replay. See `causal_boundary_id()`. The HLC lives in `hlc`.
    pub causal_boundary_id: Cid,
    pub kind: BoundaryKind,
    pub capture_surface: CaptureSurface,
    /// CID of the pre-redaction raw bytes (forensic commitment).
    pub raw_cid: Cid,
    /// CID of the redacted bytes, if redaction was applied.
    pub redacted_cid: Option<Cid>,
    /// CID of the redaction transform descriptor (makes redaction auditable).
    pub redaction_transform_cid: Option<Cid>,
    /// Small, already-redacted metadata (provider, model, timing buckets).
    pub meta: BTreeMap<String, String>,
}

impl EventRecord {
    /// Canonical hash of this record (over its deterministic CBOR encoding).
    pub fn record_hash(&self) -> Result<Cid> {
        Ok(Cid::of(&crate::cbor::to_vec(self)?))
    }
}

/// Derive a causal boundary id from lineage + request content. This is the
/// anti-swap primitive AND the replay match key.
///
/// Deliberately NO clock: the id is a pure function of `(parent, semantic)`, so it
/// reproduces bit-for-bit on replay. Because the parent advances after every
/// boundary, sequential repeats of an identical request get distinct ids; only
/// true concurrent siblings sharing one parent with identical requests collide —
/// which the replay engine treats as ambiguous and FAILs LOUD (never serves an
/// arbitrary cassette). The HLC stays in `EventRecord` for log order/audit only.
pub fn causal_boundary_id(parent: Cid, semantic_request_hash: Cid) -> Cid {
    let mut buf = [0u8; 64];
    buf[..32].copy_from_slice(&parent.0);
    buf[32..].copy_from_slice(&semantic_request_hash.0);
    Cid::of(&buf)
}
