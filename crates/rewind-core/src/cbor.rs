//! Centralized CBOR (de)serialization.
//!
//! Every artifact byte that gets hashed or signed flows through here, so the
//! planned move to deterministic dCBOR canonicalization (root Cargo.toml TODO) is
//! a one-place change instead of touching every struct.

use crate::error::{Error, Result};
use serde::de::DeserializeOwned;
use serde::Serialize;

pub fn to_vec<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    ciborium::into_writer(value, &mut buf).map_err(|e| Error::Cbor(e.to_string()))?;
    Ok(buf)
}

pub fn from_slice<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    ciborium::from_reader(bytes).map_err(|e| Error::Cbor(e.to_string()))
}
