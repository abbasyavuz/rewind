# Security Policy

Rewind produces **tamper-evident, signed, offline-verifiable** `.rewind` artifacts. Anything that
lets a tampered artifact pass `rewind verify`, that breaks the causal-id / replay determinism in a
silent (not FAIL-LOUD) way, or that leaks captured secrets, is a security issue we take seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security reports. Instead, use GitHub's private
[Security Advisories](https://docs.github.com/en/code-security/security-advisories) ("Report a
vulnerability" on the repository's **Security** tab). If you can't, contact the maintainers privately
(add a security contact email here before publishing).

We aim to acknowledge within a few days. Please include a reproduction and the affected commit.

## In scope

- Verifier bypass: a modified artifact that still verifies (chain / Merkle / Ed25519 / cbid-uniqueness).
- Silent replay/fork divergence (a wrong cassette served instead of FAIL LOUD).
- Redaction / selective-disclosure weaknesses that leak committed secrets (note: v0 Merkle inclusion
  proofs provide *integrity*, not confidentiality).

## Handling secrets

Captured `.rewind` artifacts contain raw request/response bytes — **treat them as sensitive**. The
per-run redaction (`commitment.py`) is best-effort regex (v0); do not assume it removes every secret.
Never commit a `.rewind` (they are gitignored) and never commit `.env`.
