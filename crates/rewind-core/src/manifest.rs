//! The artifact manifest — the canonical root that gets signed.

use crate::cid::Cid;
use crate::error::Result;
use crate::hlc::Hlc;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const FORMAT_VERSION: &str = "0.1-DRAFT";

/// Capability flag: which profile this artifact was captured under.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Profile {
    /// Compliance / Article-12 buyer: raw + Merkle + signature. No replay guarantee.
    RecordOnly,
    /// SRE buyer: RecordOnly + side-channel shims + determinism manifest.
    Replayable,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Manifest {
    pub format_version: String,
    pub profile: Profile,
    pub run_id: String,
    pub created_hlc: Hlc,
    pub event_count: u64,
    pub head_seq: u64,
    /// `record_hash` of the last event ("last verified head").
    pub head_hash: Cid,
    /// Merkle root over all event record hashes.
    pub merkle_root: Cid,
    /// Only present for `Replayable`: inference pins, shim coverage, etc.
    pub determinism: Option<BTreeMap<String, String>>,
}

impl Manifest {
    pub fn to_cbor(&self) -> Result<Vec<u8>> {
        crate::cbor::to_vec(self)
    }

    pub fn from_cbor(bytes: &[u8]) -> Result<Self> {
        crate::cbor::from_slice(bytes)
    }
}
