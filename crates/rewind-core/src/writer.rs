//! Builds a `.rewind` artifact directory on disk.
//!
//! v0 on-disk layout (see spec/rewind-format-v0.1-DRAFT.md):
//!   <dir>/manifest.cbor, attestation.cbor, log.cbor, objects/b3-<hex>.bin

use crate::attest::{Attestation, Keypair};
use crate::cid::Cid;
use crate::error::Result;
use crate::hlc::Hlc;
use crate::log::{BoundaryKind, CaptureSurface, EventRecord};
use crate::manifest::{Manifest, Profile, FORMAT_VERSION};
use crate::merkle::merkle_root;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

pub struct ArtifactWriter {
    dir: PathBuf,
    objects_dir: PathBuf,
    run_id: String,
    profile: Profile,
    hlc: Hlc,
    seq: u64,
    lamport: u64,
    prev_hash: Cid,
    created_hlc: Hlc,
    records: Vec<EventRecord>,
    record_hashes: Vec<Cid>,
    determinism: Option<BTreeMap<String, String>>,
}

impl ArtifactWriter {
    pub fn create<P: AsRef<Path>>(dir: P, run_id: &str, profile: Profile, node: u64) -> Result<Self> {
        let dir = dir.as_ref().to_path_buf();
        let objects_dir = dir.join("objects");
        fs::create_dir_all(&objects_dir)?;
        Ok(ArtifactWriter {
            dir,
            objects_dir,
            run_id: run_id.to_string(),
            profile,
            hlc: Hlc::zero(node),
            seq: 0,
            lamport: 0,
            prev_hash: Cid::ZERO,
            created_hlc: Hlc::zero(node),
            records: Vec::new(),
            record_hashes: Vec::new(),
            determinism: None,
        })
    }

    pub fn set_determinism(&mut self, d: BTreeMap<String, String>) {
        self.determinism = Some(d);
    }

    /// Content-address and persist a blob; returns its CID (idempotent).
    pub fn put_object(&self, bytes: &[u8]) -> Result<Cid> {
        let cid = Cid::of(bytes);
        let path = self.objects_dir.join(cid.object_filename());
        if !path.exists() {
            fs::write(&path, bytes)?;
        }
        Ok(cid)
    }

    /// High-level append: derive the causal boundary id, content-address the raw
    /// (and optional redacted/transform) blobs, and chain the record — all in one
    /// place so Python (via PyO3) never re-implements the hashing. Returns
    /// `(record_hash, causal_boundary_id)`.
    #[allow(clippy::too_many_arguments)]
    pub fn append_boundary(
        &mut self,
        kind: BoundaryKind,
        surface: CaptureSurface,
        parent: Cid,
        semantic_request: &[u8],
        raw: &[u8],
        redacted: Option<&[u8]>,
        transform: Option<&[u8]>,
        meta: BTreeMap<String, String>,
        physical_ms: u64,
    ) -> Result<(Cid, Cid)> {
        let hlc = self.hlc.tick(physical_ms);
        if self.seq == 0 {
            self.created_hlc = hlc;
        }
        let semantic_cid = Cid::of(semantic_request);
        let cbid = crate::log::causal_boundary_id(parent, hlc, semantic_cid);

        let raw_cid = self.put_object(raw)?;
        let redacted_cid = match redacted {
            Some(b) => Some(self.put_object(b)?),
            None => None,
        };
        let redaction_transform_cid = match transform {
            Some(b) => Some(self.put_object(b)?),
            None => None,
        };

        self.lamport += 1;
        let rec = EventRecord {
            seq: self.seq,
            lamport: self.lamport,
            hlc,
            prev_hash: self.prev_hash,
            causal_boundary_id: cbid,
            kind,
            capture_surface: surface,
            raw_cid,
            redacted_cid,
            redaction_transform_cid,
            meta,
        };
        let h = rec.record_hash()?;
        self.records.push(rec);
        self.record_hashes.push(h);
        self.prev_hash = h;
        self.seq += 1;
        Ok((h, cbid))
    }

    /// Write log.cbor, manifest.cbor and a signed attestation.cbor. Returns the manifest.
    pub fn finalize(self, kp: &Keypair) -> Result<Manifest> {
        // log.cbor : a CBOR array of EventRecords
        fs::write(self.dir.join("log.cbor"), crate::cbor::to_vec(&self.records)?)?;

        // `self` is consumed here, so move (don't clone) the owned fields. The
        // borrowing reads (`records`, `record_hashes`) are evaluated first.
        let manifest = Manifest {
            format_version: FORMAT_VERSION.to_string(),
            profile: self.profile,
            event_count: self.records.len() as u64,
            head_seq: self.seq.saturating_sub(1),
            head_hash: self.prev_hash,
            merkle_root: merkle_root(&self.record_hashes),
            created_hlc: self.created_hlc,
            run_id: self.run_id,
            determinism: self.determinism,
        };
        let manifest_bytes = manifest.to_cbor()?;
        fs::write(self.dir.join("manifest.cbor"), &manifest_bytes)?;

        let attestation = Attestation::sign(manifest_bytes, kp);
        fs::write(self.dir.join("attestation.cbor"), attestation.to_cbor()?)?;

        Ok(manifest)
    }
}
