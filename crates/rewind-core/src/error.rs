//! Error types for the `.rewind` artifact engine.

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("io error: {0}")]
    Io(String),

    #[error("cbor (de)serialization error: {0}")]
    Cbor(String),

    #[error("hash chain broken at seq {seq}: prev_hash does not match preceding record")]
    ChainBroken { seq: u64 },

    #[error("missing content-addressed object: {0}")]
    MissingObject(String),

    #[error("cid mismatch: object {0} content does not hash to its claimed CID")]
    CidMismatch(String),

    #[error("signature error: {0}")]
    Signature(String),

    #[error("duplicate causal boundary id at seq {seq}: two boundaries share a parent and request (concurrent siblings) — the run is not deterministically replayable")]
    AmbiguousBoundary { seq: u64 },

    #[error("malformed artifact: {0}")]
    Malformed(String),
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;
