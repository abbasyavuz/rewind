"""The v0 capture chokepoint: the httpx transport.

We intercept at the httpx transport layer (technical plan §1-A): the single
source-of-truth for raw request/response bytes for the OpenAI and Anthropic SDKs.
Each boundary is fed to `rewind_native` (PyO3 -> rewind-core), which owns
CID/HLC/causal-id/chain/Merkle/signing — so the artifact this produces is a real,
signed, offline-verifiable `.rewind`.

Streaming (SSE) responses are teed incrementally (TTFT preserved); non-streaming
responses are buffered + decoded.

HONEST SCOPE (technical plan §1-A, §3.7): Bedrock (boto3/urllib3), Vertex/Gemini
gRPC, and gateways (LiteLLM/Portkey) do NOT go through httpx — explicit fast-follow;
gateway runs would get `CaptureSurface.GATEWAY` (auto-INDETERMINATE for the envelope).
Moving redaction off the synchronous path is `# TODO(phase-1)`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import rewind_native

from . import context
from .commitment import commit
from .events import BoundaryKind, CaptureSurface

_PATCHED = False
_ORIG_SYNC = None
_ORIG_ASYNC = None


def _semantic_request_canon(request: httpx.Request, body: bytes) -> bytes:
    """v0 canonicalization stub. The real per-provider, versioned canonicalizer
    (separating causally-meaningful fields from benign SDK noise) is
    `# TODO(phase-1)` — see technical plan §2.1 Request Canonicalizer."""
    return b"\n".join([request.method.encode(), str(request.url).encode(), body])


def frame_blob(request: httpx.Request, req_body: bytes, status: int, body: bytes | str) -> bytes:
    """The framed boundary blob = the raw pre-redaction bytes rewind-core commits to.
    Shared by record (capture) and fork (replay tee) so both produce the SAME shape."""
    body_s = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    return json.dumps(
        {
            "request": {
                "method": request.method,
                "url": str(request.url),
                "body": req_body.decode("utf-8", "replace"),
            },
            "response": {"status": status, "body": body_s},
        },
        separators=(",", ":"),
    ).encode("utf-8")


class Recorder:
    """Feeds boundaries into a signed `.rewind` artifact via rewind-core."""

    mode = "record"

    def __init__(self, run_id: str, out_dir: str, strict: bool = True) -> None:
        from .guard import Guard

        self.run_id = run_id
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.guard = Guard(strict=strict)
        # Authoritative chain lives in rewind-core; we keep only the record-hash
        # of each boundary (hex) for lightweight introspection.
        self.events: list[str] = []
        self._writer = rewind_native.Writer(str(self.dir), run_id, "record-only", 1)
        self._secret = bytes.fromhex(rewind_native.generate_secret_key())
        # Held OUTSIDE the artifact; needed to verify a disclosed field.
        self.disclosure_key = os.urandom(32)
        self.pubkey_hex: str | None = None

    def record_boundary(
        self,
        *,
        kind: BoundaryKind,
        surface: CaptureSurface,
        request: httpx.Request,
        req_body: bytes,
        resp_status: int,
        resp_body: bytes,
        meta: dict[str, str],
    ) -> None:
        self.guard.assert_covered("http.httpx")

        canon = _semantic_request_canon(request, req_body)
        blob = frame_blob(request, req_body, resp_status, resp_body)

        c = commit(blob, self.disclosure_key)
        record_hash_hex, cbid_hex = self._writer.append(
            kind.value,
            surface.value,
            context.get_parent_boundary(),
            canon,
            blob,
            c.redacted,
            c.transform_desc,
            meta,
            time.time_ns() // 1_000_000,
        )
        context.set_parent_boundary(bytes.fromhex(cbid_hex))
        self.events.append(record_hash_hex)

    def finalize(self) -> None:
        """Sign and write the manifest; persist the public key next to the artifact."""
        self.pubkey_hex = self._writer.finalize(self._secret)
        (self.dir / "run-key.pub").write_text(self.pubkey_hex)


def _tee(
    recorder: Recorder,
    request: httpx.Request,
    req_body: bytes,
    response: httpx.Response,
    body: bytes,
) -> httpx.Response:
    """Record the boundary and hand back a buffered response. Shared tail of the
    sync and async transport hooks (they differ only in their (a)read await points)."""
    recorder.record_boundary(
        kind=BoundaryKind.MODEL_CALL,
        surface=CaptureSurface.SDK_HTTPX,
        request=request,
        req_body=req_body,
        resp_status=response.status_code,
        resp_body=body,
        meta={"host": request.url.host},
    )
    # `body` is already decoded (response.read() decompressed it). Drop the
    # content-encoding/length headers so the consumer doesn't try to decompress
    # decompressed bytes (otherwise: httpx DecodingError / zlib "incorrect header").
    drop = {"content-encoding", "content-length", "transfer-encoding"}
    headers = [(k, v) for k, v in response.headers.items() if k.lower() not in drop]
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=body,
        request=request,
        extensions=response.extensions,
    )


def _stream_headers(response: httpx.Response) -> list[tuple[str, str]]:
    """Headers for a teed STREAM. Unlike `_tee`, we hand the consumer the RAW stream
    (its own decoder still runs), so `content-encoding` is KEPT. But `content-length`
    is unreliable once we tee, and `transfer-encoding` is hop-by-hop — drop both so the
    consumer doesn't mis-frame the body. Previously the stream path passed headers
    through untouched, an inconsistency with `_tee`."""
    drop = {"content-length", "transfer-encoding"}
    return [(k, v) for k, v in response.headers.items() if k.lower() not in drop]


def _is_streaming(response: httpx.Response) -> bool:
    return "text/event-stream" in response.headers.get("content-type", "").lower()


def _record_streamed(
    recorder: Recorder,
    request: httpx.Request,
    req_body: bytes,
    response: httpx.Response,
    body: bytes,
    truncated: bool = False,
) -> None:
    meta = {"host": request.url.host, "stream": "sse"}
    if truncated:
        # The consumer cancelled or the inner stream raised mid-flight: the buffer is
        # a partial body. Mark it so replay doesn't serve it as a complete response. (CB-4)
        meta["truncated"] = "true"
    # We teed the RAW stream (so the consumer's content-decoder still works). If a
    # gateway/CDN gzipped the SSE, decode our buffer so the recording is replayable.
    if "gzip" in response.headers.get("content-encoding", "").lower():
        import gzip

        try:
            body = gzip.decompress(body)
        except Exception:
            # A partial/corrupt gzip buffer cannot be decoded. Keeping the raw
            # compressed bytes would have them silently mangled by utf-8 'replace' in
            # frame_blob and served as garbage on replay. Mark it loudly instead. (LB-7)
            meta["sse_decompression_failed"] = "true"
    recorder.record_boundary(
        kind=BoundaryKind.MODEL_CALL,
        surface=CaptureSurface.SDK_HTTPX,
        request=request,
        req_body=req_body,
        resp_status=response.status_code,
        resp_body=body,
        meta=meta,
    )


class _TeeSyncStream(httpx.SyncByteStream):
    """Yields each chunk to the caller (token-by-token streaming preserved) while
    teeing a copy into a buffer; commits the boundary only when the stream ends/closes.
    This keeps TTFT intact for streaming agents instead of buffering the whole body."""

    def __init__(self, inner: httpx.SyncByteStream, on_done) -> None:
        self._inner = inner
        self._on_done = on_done
        self._buf = bytearray()
        self._fired = False
        self._complete = False

    def __iter__(self):
        try:
            for chunk in self._inner:
                self._buf.extend(chunk)
                yield chunk
            self._complete = True
        finally:
            # Fire even if the inner stream raises mid-iteration — otherwise the
            # partial body is silently dropped and no boundary is recorded. (CB-4)
            self._fire()

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._fire()  # commit even on early cancel (partial body)

    def _fire(self) -> None:
        if not self._fired:
            self._fired = True
            self._on_done(bytes(self._buf), not self._complete)


class _TeeAsyncStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, on_done) -> None:
        self._inner = inner
        self._on_done = on_done
        self._buf = bytearray()
        self._fired = False
        self._complete = False

    async def __aiter__(self):
        try:
            async for chunk in self._inner:
                self._buf.extend(chunk)
                yield chunk
            self._complete = True
        finally:
            self._fire()  # fire on mid-stream raise too (CB-4)

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._fire()

    def _fire(self) -> None:
        if not self._fired:
            self._fired = True
            self._on_done(bytes(self._buf), not self._complete)


def _tee_stream_sync(recorder, request, req_body, response):
    tee = _TeeSyncStream(
        response.stream,
        lambda body, truncated: _record_streamed(recorder, request, req_body, response, body, truncated),
    )
    return httpx.Response(
        status_code=response.status_code, headers=_stream_headers(response),
        stream=tee, request=request, extensions=response.extensions,
    )


def _tee_stream_async(recorder, request, req_body, response):
    tee = _TeeAsyncStream(
        response.stream,
        lambda body, truncated: _record_streamed(recorder, request, req_body, response, body, truncated),
    )
    return httpx.Response(
        status_code=response.status_code, headers=_stream_headers(response),
        stream=tee, request=request, extensions=response.extensions,
    )


def install(strict: bool = True) -> None:
    """Patch the httpx transports to tee bytes into the active Recorder.

    Streaming (SSE) responses are teed incrementally (TTFT preserved); non-streaming
    responses are buffered+decoded. When no run is active, calls pass through untouched.
    Idempotent.
    """
    global _PATCHED, _ORIG_SYNC, _ORIG_ASYNC
    if _PATCHED:
        return
    _ORIG_SYNC = httpx.HTTPTransport.handle_request
    _ORIG_ASYNC = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        session = context.current()
        if session is None:
            return _ORIG_SYNC(self, request)  # type: ignore[misc]
        if session.mode == "replay":
            return session.serve(request, request.read())  # no network in replay
        req_body = request.read()
        response = _ORIG_SYNC(self, request)  # type: ignore[misc]
        if _is_streaming(response):
            return _tee_stream_sync(session, request, req_body, response)
        return _tee(session, request, req_body, response, response.read())

    async def handle_async_request(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        session = context.current()
        if session is None:
            return await _ORIG_ASYNC(self, request)  # type: ignore[misc]
        if session.mode == "replay":
            return session.serve(request, await request.aread())  # no network in replay
        req_body = await request.aread()
        response = await _ORIG_ASYNC(self, request)  # type: ignore[misc]
        if _is_streaming(response):
            return _tee_stream_async(session, request, req_body, response)
        return _tee(session, request, req_body, response, await response.aread())

    httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign]
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign]
    _PATCHED = True


def uninstall() -> None:
    global _PATCHED
    if not _PATCHED:
        return
    httpx.HTTPTransport.handle_request = _ORIG_SYNC  # type: ignore[method-assign,assignment]
    httpx.AsyncHTTPTransport.handle_async_request = _ORIG_ASYNC  # type: ignore[method-assign,assignment]
    _PATCHED = False
