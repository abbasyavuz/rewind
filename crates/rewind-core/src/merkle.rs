//! Binary Merkle tree over event `record_hash`es, in sequence order.
//!
//! The root is committed in the manifest and signed. An odd node is paired with
//! itself (duplicate-last), a known second-preimage caveat acceptable for v0
//! integrity (NOT confidentiality — see technical plan §3.6).

use crate::cid::Cid;

/// Compute the Merkle root over an ordered slice of leaf CIDs.
/// Empty input yields `Cid::ZERO`.
pub fn merkle_root(leaves: &[Cid]) -> Cid {
    if leaves.is_empty() {
        return Cid::ZERO;
    }
    let mut level: Vec<Cid> = leaves.to_vec();
    while level.len() > 1 {
        let mut next = Vec::with_capacity(level.len().div_ceil(2));
        for pair in level.chunks(2) {
            let left = pair[0];
            let right = if pair.len() == 2 { pair[1] } else { pair[0] };
            let mut buf = [0u8; 64];
            buf[..32].copy_from_slice(&left.0);
            buf[32..].copy_from_slice(&right.0);
            next.push(Cid::of(&buf));
        }
        level = next;
    }
    level[0]
}
