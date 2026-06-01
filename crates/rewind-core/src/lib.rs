//! # rewind-core
//!
//! The `.rewind` artifact engine: content-addressed (BLAKE3), hash-chained,
//! Merkle-committed, Ed25519-attested, and **offline-verifiable**.
//!
//! This crate is the "names" half of Rewind (the nouns): it knows nothing about
//! agents, HTTP, or replay. The Python SDK (the verbs: capture/replay/fork) calls
//! into it across the format contract in `spec/`.
//!
//! Status: v0 scaffolding. The primitives here are real; the end-to-end
//! capture→replay→fork pipeline is built in Phases 1-2 (see docs/rewind-technical-plan.md).

pub mod attest;
pub mod cbor;
pub mod cid;
pub mod error;
pub mod hlc;
pub mod log;
pub mod manifest;
pub mod merkle;
pub mod verify;
pub mod writer;

pub use attest::{Attestation, Keypair};
pub use cid::Cid;
pub use error::{Error, Result};
pub use hlc::Hlc;
pub use log::{causal_boundary_id, BoundaryKind, CaptureSurface, EventRecord};
pub use manifest::{Manifest, Profile};
pub use merkle::merkle_root;
pub use verify::{load_log, verify_artifact, VerifyReport};
pub use writer::ArtifactWriter;

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    /// Round-trip: write a tiny artifact, then verify it passes (including signature).
    #[test]
    fn write_then_verify_roundtrip() {
        let tmp = std::env::temp_dir().join(format!("rewind-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let kp = Keypair::generate();

        let mut w = ArtifactWriter::create(&tmp, "run-test", Profile::RecordOnly, 1).unwrap();
        let mut parent = Cid::ZERO;
        for i in 0..5u64 {
            let raw = format!("raw-bytes-{i}").into_bytes();
            let (_h, cbid) = w
                .append_boundary(
                    BoundaryKind::ModelCall,
                    CaptureSurface::SdkHttpx,
                    parent,
                    format!("req-{i}").as_bytes(),
                    &raw,
                    None,
                    None,
                    BTreeMap::new(),
                    1000 + i,
                )
                .unwrap();
            parent = cbid;
        }
        w.finalize(&kp).unwrap();

        let report = verify_artifact(&tmp, Some(&kp.verifying_key())).unwrap();
        assert!(report.chain_ok, "chain must verify");
        assert!(report.merkle_ok, "merkle must verify");
        assert_eq!(report.signature_ok, Some(true), "signature must verify");
        assert!(report.raw_objects_ok, "raw objects must verify");
        assert!(report.ok(), "overall must pass");
        assert_eq!(report.event_count, 5);

        let _ = std::fs::remove_dir_all(&tmp);
    }

    /// Tamper: flip a raw object and confirm verification fails.
    #[test]
    fn tamper_is_detected() {
        let tmp = std::env::temp_dir().join(format!("rewind-tamper-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let kp = Keypair::generate();

        let mut w = ArtifactWriter::create(&tmp, "run-tamper", Profile::RecordOnly, 1).unwrap();
        w.append_boundary(
            BoundaryKind::ToolCall,
            CaptureSurface::SdkHttpx,
            Cid::ZERO,
            b"req",
            b"original",
            None,
            None,
            BTreeMap::new(),
            1000,
        )
        .unwrap();
        w.finalize(&kp).unwrap();

        // Overwrite the object's bytes (CID no longer matches).
        let raw_cid = Cid::of(b"original");
        std::fs::write(
            tmp.join("objects").join(raw_cid.object_filename()),
            b"TAMPERED",
        )
        .unwrap();

        let report = verify_artifact(&tmp, Some(&kp.verifying_key())).unwrap();
        assert!(!report.raw_objects_ok, "tamper must be detected");
        assert!(!report.ok());

        let _ = std::fs::remove_dir_all(&tmp);
    }

    /// A boundary carrying BOTH redacted bytes and a transform descriptor must verify
    /// as redaction-auditable (the consistent case; guards the LB-6 branch's happy path).
    #[test]
    fn redacted_boundary_is_auditable() {
        let tmp = std::env::temp_dir().join(format!("rewind-redact-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let kp = Keypair::generate();

        let mut w = ArtifactWriter::create(&tmp, "run-redact", Profile::RecordOnly, 1).unwrap();
        w.append_boundary(
            BoundaryKind::ModelCall,
            CaptureSurface::SdkHttpx,
            Cid::ZERO,
            b"req",
            b"raw with secret sk-abc",
            Some(b"raw with secret <redacted>"),
            Some(br#"{"redactor":"regex-v0","spans":[]}"#),
            BTreeMap::new(),
            1000,
        )
        .unwrap();
        w.finalize(&kp).unwrap();

        let report = verify_artifact(&tmp, Some(&kp.verifying_key())).unwrap();
        assert!(report.redaction_auditable, "redacted+transform must be auditable");
        assert!(report.ok());

        let _ = std::fs::remove_dir_all(&tmp);
    }

    /// The seq counter increments one per boundary and the verify invariant (seq == i)
    /// holds across many boundaries — regression cover for the saturating_add change. (CB-2)
    #[test]
    fn seq_increments_monotonically() {
        let tmp = std::env::temp_dir().join(format!("rewind-seq-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        let kp = Keypair::generate();

        let mut w = ArtifactWriter::create(&tmp, "run-seq", Profile::RecordOnly, 1).unwrap();
        let mut parent = Cid::ZERO;
        for i in 0..50u64 {
            let (_h, cbid) = w
                .append_boundary(
                    BoundaryKind::ModelCall,
                    CaptureSurface::SdkHttpx,
                    parent,
                    format!("req-{i}").as_bytes(),
                    format!("raw-{i}").as_bytes(),
                    None,
                    None,
                    BTreeMap::new(),
                    1000 + i,
                )
                .unwrap();
            parent = cbid;
        }
        w.finalize(&kp).unwrap();

        let report = verify_artifact(&tmp, Some(&kp.verifying_key())).unwrap();
        assert_eq!(report.event_count, 50);
        assert!(report.chain_ok && report.merkle_ok, "seq/chain invariant must hold");
        assert!(report.ok());

        let _ = std::fs::remove_dir_all(&tmp);
    }
}

    /// Test if ciborium encoding is deterministic with the same struct serialized twice.
    #[test]
    fn cbor_determinism_test() {
        use std::collections::BTreeMap;
        use crate::manifest::{Manifest, Profile};
        use crate::cid::Cid;
        use crate::hlc::Hlc;
        
        let mut determinism = BTreeMap::new();
        determinism.insert("key_z".to_string(), "value_z".to_string());
        determinism.insert("key_a".to_string(), "value_a".to_string());
        
        let manifest1 = Manifest {
            format_version: "0.1-DRAFT".to_string(),
            profile: Profile::RecordOnly,
            run_id: "test-run".to_string(),
            created_hlc: Hlc::zero(1),
            event_count: 5,
            head_seq: 4,
            head_hash: Cid::ZERO,
            merkle_root: Cid::ZERO,
            determinism: Some(determinism.clone()),
        };
        
        // Encode the same manifest twice
        let bytes1 = manifest1.to_cbor().unwrap();
        let bytes2 = manifest1.to_cbor().unwrap();
        
        // Both should be identical
        assert_eq!(bytes1, bytes2, "CBOR encoding should be deterministic");
        
        // Now create a second manifest with the same data but BTreeMap inserted in different order
        let mut determinism2 = BTreeMap::new();
        determinism2.insert("key_a".to_string(), "value_a".to_string());
        determinism2.insert("key_z".to_string(), "value_z".to_string());
        
        let manifest2 = Manifest {
            format_version: "0.1-DRAFT".to_string(),
            profile: Profile::RecordOnly,
            run_id: "test-run".to_string(),
            created_hlc: Hlc::zero(1),
            event_count: 5,
            head_seq: 4,
            head_hash: Cid::ZERO,
            merkle_root: Cid::ZERO,
            determinism: Some(determinism2),
        };
        
        let bytes3 = manifest2.to_cbor().unwrap();
        
        // Both should be identical because BTreeMap already sorts keys
        assert_eq!(bytes1, bytes3, "CBOR encoding should be identical regardless of BTreeMap insertion order");
    }
