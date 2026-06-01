//! DSSE-style Ed25519 attestation over the manifest bytes.
//!
//! v0 uses a single Ed25519 signature. The roadmap (technical plan, deferred)
//! adds Sigstore/Rekor transparency for keyless signing.

use crate::error::{Error, Result};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand_core::OsRng;
use serde::{Deserialize, Serialize};

pub const PAYLOAD_TYPE: &str = "application/vnd.rewind.manifest+cbor";

/// An Ed25519 signing keypair wrapper.
pub struct Keypair {
    signing: SigningKey,
}

impl Keypair {
    pub fn generate() -> Self {
        Keypair {
            signing: SigningKey::generate(&mut OsRng),
        }
    }

    pub fn from_secret_bytes(bytes: &[u8; 32]) -> Self {
        Keypair {
            signing: SigningKey::from_bytes(bytes),
        }
    }

    pub fn secret_bytes(&self) -> [u8; 32] {
        self.signing.to_bytes()
    }

    pub fn verifying_key(&self) -> VerifyingKey {
        self.signing.verifying_key()
    }

    /// `ed25519:<hex pubkey>` — stable key identifier.
    pub fn keyid(&self) -> String {
        format!("ed25519:{}", hex::encode(self.verifying_key().to_bytes()))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SignatureEntry {
    pub keyid: String,
    pub sig: Vec<u8>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Attestation {
    pub payload_type: String,
    /// The exact manifest CBOR bytes that were signed.
    pub payload: Vec<u8>,
    pub signatures: Vec<SignatureEntry>,
}

impl Attestation {
    pub fn sign(payload: Vec<u8>, kp: &Keypair) -> Self {
        let sig: Signature = kp.signing.sign(&payload);
        Attestation {
            payload_type: PAYLOAD_TYPE.to_string(),
            payload,
            signatures: vec![SignatureEntry {
                keyid: kp.keyid(),
                sig: sig.to_bytes().to_vec(),
            }],
        }
    }

    /// Returns true if any signature verifies against `vk`.
    pub fn verify(&self, vk: &VerifyingKey) -> Result<bool> {
        for entry in &self.signatures {
            if entry.sig.len() != 64 {
                continue;
            }
            let mut arr = [0u8; 64];
            arr.copy_from_slice(&entry.sig);
            let sig = Signature::from_bytes(&arr);
            if vk.verify(&self.payload, &sig).is_ok() {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub fn to_cbor(&self) -> Result<Vec<u8>> {
        crate::cbor::to_vec(self)
    }

    pub fn from_cbor(bytes: &[u8]) -> Result<Self> {
        crate::cbor::from_slice(bytes)
    }
}

/// Parse a hex-encoded 32-byte Ed25519 public key.
pub fn verifying_key_from_hex(s: &str) -> Result<VerifyingKey> {
    let raw = hex::decode(s.trim()).map_err(|e| Error::Signature(format!("bad hex: {e}")))?;
    let arr: [u8; 32] = raw
        .try_into()
        .map_err(|_| Error::Signature("public key must be 32 bytes".into()))?;
    VerifyingKey::from_bytes(&arr).map_err(|e| Error::Signature(e.to_string()))
}
