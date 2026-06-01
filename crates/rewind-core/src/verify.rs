//! Offline verification of a `.rewind` artifact.
//!
//! This is the trust primitive: a third party, with nothing but this code and an
//! optional trusted public key, re-derives the chain, the Merkle root, and the
//! signature, and confirms every raw blob matches its CID.

use crate::attest::Attestation;
use crate::cid::Cid;
use crate::error::{Error, Result};
use crate::log::EventRecord;
use crate::manifest::Manifest;
use crate::merkle::merkle_root;
use ed25519_dalek::VerifyingKey;
use std::collections::HashSet;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct VerifyReport {
    pub run_id: String,
    pub event_count: u64,
    /// Every record's prev_hash links correctly and head matches the manifest.
    pub chain_ok: bool,
    /// Recomputed Merkle root equals the manifest's.
    pub merkle_ok: bool,
    /// Some(true/false) if a trusted key was supplied; None if signature unchecked.
    pub signature_ok: Option<bool>,
    /// Every raw_cid (and redacted/transform, if present) exists and hashes correctly.
    pub raw_objects_ok: bool,
    /// Every redacted record also carries a redaction transform descriptor.
    pub redaction_auditable: bool,
    /// No two boundaries share a causal id (a collision = un-replayable concurrent siblings).
    pub cbids_unique: bool,
}

impl VerifyReport {
    /// Overall pass: integrity holds and, if a key was supplied, the signature is valid.
    pub fn ok(&self) -> bool {
        self.chain_ok
            && self.merkle_ok
            && self.raw_objects_ok
            && self.redaction_auditable
            && self.cbids_unique
            && self.signature_ok.unwrap_or(true)
    }
}

fn read(path: &Path) -> Result<Vec<u8>> {
    fs::read(path).map_err(|e| Error::Io(format!("{}: {e}", path.display())))
}

/// Load and CBOR-decode the event log for replay. This does NOT check integrity —
/// pair it with [`verify_artifact`] when trust matters.
pub fn load_log(dir: &Path) -> Result<Vec<EventRecord>> {
    crate::cbor::from_slice(&read(&dir.join("log.cbor"))?)
}

pub fn verify_artifact(dir: &Path, trusted: Option<&VerifyingKey>) -> Result<VerifyReport> {
    let manifest = Manifest::from_cbor(&read(&dir.join("manifest.cbor"))?)?;
    let attestation = Attestation::from_cbor(&read(&dir.join("attestation.cbor"))?)?;
    let records: Vec<EventRecord> = crate::cbor::from_slice(&read(&dir.join("log.cbor"))?)?;

    // 1. Walk the hash chain (and check causal-id uniqueness).
    let mut prev = Cid::ZERO;
    let mut hashes = Vec::with_capacity(records.len());
    let mut chain_ok = true;
    let mut seen_cbids = HashSet::with_capacity(records.len());
    let mut cbids_unique = true;
    for (i, rec) in records.iter().enumerate() {
        if rec.prev_hash != prev || rec.seq != i as u64 {
            chain_ok = false;
        }
        if !seen_cbids.insert(rec.causal_boundary_id) {
            cbids_unique = false;
        }
        let h = rec.record_hash()?;
        hashes.push(h);
        prev = h;
    }
    if prev != manifest.head_hash || manifest.event_count != records.len() as u64 {
        chain_ok = false;
    }

    // 2. Merkle root.
    let merkle_ok = merkle_root(&hashes) == manifest.merkle_root;

    // 3. Signature (only if we were given a key to trust).
    let signature_ok = match trusted {
        Some(vk) => {
            let payload_matches = attestation.payload == manifest.to_cbor()?;
            Some(payload_matches && attestation.verify(vk)?)
        }
        None => None,
    };

    // 4. Raw object integrity + redaction auditability.
    let objects_dir = dir.join("objects");
    let mut raw_objects_ok = true;
    let mut redaction_auditable = true;
    let check = |cid: &Cid, ok: &mut bool| -> Result<()> {
        let path = objects_dir.join(cid.object_filename());
        match fs::read(&path) {
            Ok(bytes) => {
                if Cid::of(&bytes) != *cid {
                    *ok = false;
                }
            }
            Err(_) => *ok = false,
        }
        Ok(())
    };
    for rec in &records {
        check(&rec.raw_cid, &mut raw_objects_ok)?;
        if let Some(c) = &rec.redacted_cid {
            check(c, &mut raw_objects_ok)?;
            if rec.redaction_transform_cid.is_none() {
                redaction_auditable = false;
            }
        }
        if let Some(c) = &rec.redaction_transform_cid {
            check(c, &mut raw_objects_ok)?;
        }
    }

    Ok(VerifyReport {
        run_id: manifest.run_id,
        event_count: records.len() as u64,
        chain_ok,
        merkle_ok,
        signature_ok,
        raw_objects_ok,
        redaction_auditable,
        cbids_unique,
    })
}
