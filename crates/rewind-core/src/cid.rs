//! Content identifiers: BLAKE3-256 over canonical bytes.
//!
//! `Cid` is the spine of the artifact — every blob, every event record, and the
//! Merkle root are addressed by their BLAKE3 hash. Tamper-evidence is free:
//! change one byte, every CID up the chain changes.

use serde::{Deserialize, Serialize};
use std::fmt;

/// A BLAKE3-256 content identifier. Rendered as `b3:<hex>`.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Cid(pub [u8; 32]);

impl Cid {
    /// The all-zero CID, used as the `prev_hash` of the first event in a log.
    pub const ZERO: Cid = Cid([0u8; 32]);

    /// Compute the CID of an arbitrary byte slice.
    pub fn of(bytes: &[u8]) -> Cid {
        Cid(*blake3::hash(bytes).as_bytes())
    }

    /// Hex (no prefix).
    pub fn to_hex(&self) -> String {
        hex::encode(self.0)
    }

    /// `b3:<hex>` canonical string form.
    pub fn to_prefixed(&self) -> String {
        format!("b3:{}", self.to_hex())
    }

    /// On-disk object filename: `b3-<hex>.bin`.
    pub fn object_filename(&self) -> String {
        format!("b3-{}.bin", self.to_hex())
    }

    pub fn is_zero(&self) -> bool {
        self.0 == [0u8; 32]
    }
}

impl fmt::Display for Cid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.to_prefixed())
    }
}

impl fmt::Debug for Cid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Cid({})", self.to_prefixed())
    }
}
