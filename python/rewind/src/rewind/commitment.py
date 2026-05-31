"""Forensic commitment + auditable redaction.

The signed artifact commits to the PRE-redaction raw bytes (technical plan §3.5):
we hash+sign the raw bytes, then redact in the background. Low-entropy redacted
fields use a keyed HMAC (BLAKE3 keyed mode), NOT a plain salted hash, to defeat
dictionary/preimage attacks during selective disclosure.

v0 ships a regex redactor; the optional `pii` extra swaps in Presidio.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import blake3

# Conservative v0 patterns. Presidio (pii extra) replaces these in production.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "bearer": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    "api_key": re.compile(r"(?i)(sk-|key-)[A-Za-z0-9]{16,}"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}


@dataclass
class Commitment:
    """Redacted bytes + the auditable transform that produced them. The raw bytes
    themselves are content-addressed and chained by rewind-core (the signed
    commitment target), so we don't recompute CIDs here."""

    redacted: bytes
    transform_desc: bytes
    # field name -> keyed HMAC of the original value (for selective disclosure)
    field_hmacs: dict[str, str]


def _hmac(value: bytes, disclosure_key: bytes) -> str:
    return blake3.blake3(value, key=disclosure_key).hexdigest()


def commit(raw: bytes, disclosure_key: bytes) -> Commitment:
    """Produce redacted bytes + an auditable transform descriptor.

    `disclosure_key` is a 32-byte per-run key held outside the artifact; revealing
    it lets an auditor verify a disclosed field without it being brute-forceable.
    """
    text = raw.decode("utf-8", errors="replace")

    transform: list[dict[str, object]] = []
    field_hmacs: dict[str, str] = {}

    def _sub(kind: str, m: re.Match[str]) -> str:
        original = m.group(0)
        field_hmacs[f"{kind}@{m.start()}"] = _hmac(original.encode(), disclosure_key)
        transform.append({"kind": kind, "start": m.start(), "len": len(original)})
        return f"⟦{kind}⟧"

    for kind, pat in _PATTERNS.items():
        text = pat.sub(lambda m, k=kind: _sub(k, m), text)

    redacted = text.encode("utf-8")
    transform_desc = json.dumps(
        {"redactor": "regex-v0", "spans": transform}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    return Commitment(redacted=redacted, transform_desc=transform_desc, field_hmacs=field_hmacs)
