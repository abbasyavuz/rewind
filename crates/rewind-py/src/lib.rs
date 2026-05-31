//! PyO3 bindings exposing the `.rewind` artifact engine to the Python capture SDK.
//!
//! Python feeds boundaries; Rust owns CID/HLC/causal-id/chain/Merkle/signing —
//! one source of truth, no re-implementation drift. Bytes cross the boundary as
//! `bytes` in, hex `str` out (avoids PyO3's Vec<u8>->list-of-ints surprise).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rewind_core::attest::verifying_key_from_hex;
use rewind_core::{
    causal_boundary_id, load_log, verify_artifact, ArtifactWriter, BoundaryKind, CaptureSurface,
    Cid, Keypair, Profile,
};
use std::collections::BTreeMap;
use std::path::PathBuf;

fn err<E: std::fmt::Display>(e: E) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn parse_kind(s: &str) -> PyResult<BoundaryKind> {
    Ok(match s {
        "ModelCall" => BoundaryKind::ModelCall,
        "ToolCall" => BoundaryKind::ToolCall,
        "Retrieval" => BoundaryKind::Retrieval,
        "Clock" => BoundaryKind::Clock,
        "Rng" => BoundaryKind::Rng,
        "Http" => BoundaryKind::Http,
        "OpaqueTool" => BoundaryKind::OpaqueTool,
        other => return Err(err(format!("unknown BoundaryKind: {other}"))),
    })
}

fn parse_surface(s: &str) -> PyResult<CaptureSurface> {
    Ok(match s {
        "SdkHttpx" => CaptureSurface::SdkHttpx,
        "Gateway" => CaptureSurface::Gateway,
        "Opaque" => CaptureSurface::Opaque,
        other => return Err(err(format!("unknown CaptureSurface: {other}"))),
    })
}

fn cid_from_bytes(b: &[u8]) -> PyResult<Cid> {
    let arr: [u8; 32] = b
        .try_into()
        .map_err(|_| err("CID must be exactly 32 bytes"))?;
    Ok(Cid(arr))
}

/// Incremental builder for one `.rewind` artifact, backed by rewind-core.
#[pyclass]
struct Writer {
    inner: Option<ArtifactWriter>,
}

#[pymethods]
impl Writer {
    #[new]
    fn new(dir: String, run_id: String, profile: String, node: u64) -> PyResult<Self> {
        let p = match profile.as_str() {
            "replayable" => Profile::Replayable,
            "record-only" => Profile::RecordOnly,
            other => return Err(err(format!("unknown profile: {other}"))),
        };
        let w = ArtifactWriter::create(&dir, &run_id, p, node).map_err(err)?;
        Ok(Writer { inner: Some(w) })
    }

    /// Append one boundary. `parent` is the 32-byte causal-parent id (Cid::ZERO at
    /// the root). Returns `(record_hash_hex, causal_boundary_id_hex)`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (kind, surface, parent, semantic_request, raw, redacted=None, transform=None, meta=BTreeMap::new(), physical_ms=0))]
    fn append(
        &mut self,
        kind: String,
        surface: String,
        parent: Vec<u8>,
        semantic_request: Vec<u8>,
        raw: Vec<u8>,
        redacted: Option<Vec<u8>>,
        transform: Option<Vec<u8>>,
        meta: BTreeMap<String, String>,
        physical_ms: u64,
    ) -> PyResult<(String, String)> {
        let w = self
            .inner
            .as_mut()
            .ok_or_else(|| err("writer already finalized"))?;
        let (h, cbid) = w
            .append_boundary(
                parse_kind(&kind)?,
                parse_surface(&surface)?,
                cid_from_bytes(&parent)?,
                &semantic_request,
                &raw,
                redacted.as_deref(),
                transform.as_deref(),
                meta,
                physical_ms,
            )
            .map_err(err)?;
        Ok((h.to_hex(), cbid.to_hex()))
    }

    /// Stamp manifest determinism metadata (e.g. fork_of/fork_seq). Call before finalize.
    fn set_determinism(&mut self, determinism: BTreeMap<String, String>) -> PyResult<()> {
        self.inner
            .as_mut()
            .ok_or_else(|| err("writer already finalized"))?
            .set_determinism(determinism);
        Ok(())
    }

    /// Sign and write manifest.cbor + attestation.cbor + log.cbor. Consumes the
    /// writer. `secret_key` is 32 bytes. Returns the public key (hex).
    fn finalize(&mut self, secret_key: Vec<u8>) -> PyResult<String> {
        let w = self
            .inner
            .take()
            .ok_or_else(|| err("writer already finalized"))?;
        let sk: [u8; 32] = secret_key
            .try_into()
            .map_err(|_| err("secret key must be 32 bytes"))?;
        let kp = Keypair::from_secret_bytes(&sk);
        w.finalize(&kp).map_err(err)?;
        Ok(hex::encode(kp.verifying_key().to_bytes()))
    }
}

/// Generate a fresh Ed25519 secret key (hex, 32 bytes).
#[pyfunction]
fn generate_secret_key() -> String {
    hex::encode(Keypair::generate().secret_bytes())
}

/// BLAKE3-256 of `data` as hex — must match Python's `blake3` and rewind-core.
#[pyfunction]
fn cid_hex(data: Vec<u8>) -> String {
    Cid::of(&data).to_hex()
}

/// The anti-swap causal boundary id (and replay match key), as hex. Exposed so
/// the replay engine matches live calls byte-identically to the recording, and so
/// Python can parity-check its reference derivation against rewind-core.
#[pyfunction]
fn causal_id_hex(parent: Vec<u8>, semantic_hash: Vec<u8>) -> PyResult<String> {
    let parent = cid_from_bytes(&parent)?;
    let semantic = cid_from_bytes(&semantic_hash)?;
    Ok(causal_boundary_id(parent, semantic).to_hex())
}

/// Load the event log for replay: a list of dicts with `seq`, `causal_boundary_id`
/// (hex), `raw_cid` (hex), `kind`, `surface`. Integrity is NOT checked here.
#[pyfunction]
fn load_events(py: Python<'_>, dir: String) -> PyResult<Py<PyList>> {
    let recs = load_log(&PathBuf::from(dir)).map_err(err)?;
    let list = PyList::empty(py);
    for r in recs {
        let d = PyDict::new(py);
        d.set_item("seq", r.seq)?;
        d.set_item("causal_boundary_id", r.causal_boundary_id.to_hex())?;
        d.set_item("raw_cid", r.raw_cid.to_hex())?;
        d.set_item("kind", format!("{:?}", r.kind))?;
        d.set_item("surface", format!("{:?}", r.capture_surface))?;
        list.append(d)?;
    }
    Ok(list.into())
}

/// Verify an artifact directory; returns a dict of the checks.
#[pyfunction]
#[pyo3(signature = (dir, pubkey_hex=None))]
fn verify(py: Python<'_>, dir: String, pubkey_hex: Option<String>) -> PyResult<Py<PyDict>> {
    let vk = match pubkey_hex {
        Some(h) => Some(verifying_key_from_hex(&h).map_err(err)?),
        None => None,
    };
    let r = verify_artifact(&PathBuf::from(dir), vk.as_ref()).map_err(err)?;
    let overall = r.ok();
    let d = PyDict::new(py);
    d.set_item("run_id", r.run_id)?;
    d.set_item("event_count", r.event_count)?;
    d.set_item("chain_ok", r.chain_ok)?;
    d.set_item("merkle_ok", r.merkle_ok)?;
    d.set_item("raw_objects_ok", r.raw_objects_ok)?;
    d.set_item("redaction_auditable", r.redaction_auditable)?;
    d.set_item("signature_ok", r.signature_ok)?; // Option<bool> -> None | bool
    d.set_item("ok", overall)?;
    Ok(d.into())
}

#[pymodule]
fn rewind_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Writer>()?;
    m.add_function(wrap_pyfunction!(generate_secret_key, m)?)?;
    m.add_function(wrap_pyfunction!(cid_hex, m)?)?;
    m.add_function(wrap_pyfunction!(causal_id_hex, m)?)?;
    m.add_function(wrap_pyfunction!(load_events, m)?)?;
    m.add_function(wrap_pyfunction!(verify, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
