"""v0 capture + end-to-end signing/verify tests. Run: pytest (from python/rewind)."""

from __future__ import annotations

import httpx
import rewind_native

import rewind
from rewind.events import ZERO_CID, cid, derive_causal_boundary_id


def test_blake3_parity_python_vs_rust() -> None:
    """The cross-language invariant: Python's blake3 CID must equal rewind-core's.
    If this ever drifts, causal_boundary_ids won't match across record/replay."""
    for sample in (b"", b"abc", b"the quick brown fox", bytes(range(256))):
        assert rewind_native.cid_hex(sample) == cid(sample).hex()


def test_causal_boundary_id_is_deterministic_and_matches_layout() -> None:
    parent = ZERO_CID
    semantic = cid(b"some-request")
    a = derive_causal_boundary_id(parent, semantic)
    b = derive_causal_boundary_id(parent, semantic)
    assert a == b and len(a) == 32
    assert a != derive_causal_boundary_id(cid(b"other"), semantic)


def test_causal_id_parity_python_vs_rust() -> None:
    """The Python reference derivation must stay byte-identical to rewind-core's —
    otherwise causal boundary ids won't match across record/replay."""
    parent = ZERO_CID
    semantic = cid(b"a-request")
    py = derive_causal_boundary_id(parent, semantic).hex()
    native = rewind_native.causal_id_hex(parent, semantic)
    assert py == native


def test_streaming_tee_preserves_chunks_and_records_full_body() -> None:
    """SSE fix: each chunk reaches the consumer as it arrives (TTFT preserved), and
    the full body is committed exactly once when the stream ends — not buffered first."""
    from rewind.capture import _TeeSyncStream

    chunks = [b"data: a\n\n", b"data: b\n\n", b"data: [DONE]\n\n"]

    class _Inner(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks

        def close(self) -> None:
            pass

    captured: dict[str, object] = {}

    def _done(b: bytes, truncated: bool) -> None:
        captured["body"] = b
        captured["truncated"] = truncated

    tee = _TeeSyncStream(_Inner(), _done)
    out = list(tee)  # the consumer sees every chunk, in order
    assert out == chunks
    assert captured["body"] == b"".join(chunks)  # full body committed once, at the end
    assert captured["truncated"] is False  # clean stream -> not truncated


def test_record_then_sign_then_verify_end_to_end(tmp_path) -> None:
    out = str(tmp_path / "run.rewind")

    with rewind.record("test-run", out_dir=out) as rec:
        for i in range(3):
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages", json={"i": i})
            rec.record_boundary(
                kind=rewind.BoundaryKind.MODEL_CALL,
                surface=rewind.CaptureSurface.SDK_HTTPX,
                request=req,
                req_body=req.content,
                resp_status=200,
                resp_body=b'{"content": "ok", "email": "a@b.com"}',  # exercises redaction
                meta={"provider": "anthropic"},
            )

    assert rec.pubkey_hex is not None
    assert len(rec.events) == 3

    # Verify the signed artifact through rewind-core (same engine the CLI uses).
    report = rewind_native.verify(out, rec.pubkey_hex)
    assert report["chain_ok"] is True
    assert report["merkle_ok"] is True
    assert report["raw_objects_ok"] is True
    assert report["signature_ok"] is True
    assert report["ok"] is True
    assert report["event_count"] == 3

    # A DIFFERENT but valid signing key must fail the signature (integrity still holds).
    out2 = str(tmp_path / "run2.rewind")
    with rewind.record("other-run", out_dir=out2) as rec2:
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages", json={"x": 1})
        rec2.record_boundary(
            kind=rewind.BoundaryKind.MODEL_CALL,
            surface=rewind.CaptureSurface.SDK_HTTPX,
            request=req,
            req_body=req.content,
            resp_status=200,
            resp_body=b"{}",
            meta={},
        )
    assert rec2.pubkey_hex != rec.pubkey_hex
    bad = rewind_native.verify(out, rec2.pubkey_hex)
    assert bad["signature_ok"] is False
    assert bad["chain_ok"] is True  # integrity is independent of the signer
