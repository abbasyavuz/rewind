"""The v0 capture chokepoint: the httpx transport.

We intercept at the httpx transport layer (technical plan §1-A): the single
source-of-truth for raw request/response bytes for the OpenAI and Anthropic SDKs.
Each boundary is fed to `rewind_native` (PyO3 -> rewind-core), which owns
CID/HLC/causal-id/chain/Merkle/signing — so the artifact this produces is a real,
signed, offline-verifiable `.rewind`.

HONEST SCOPE (technical plan §1-A, §3.7): Bedrock (boto3/urllib3), Vertex/Gemini
gRPC, and gateways (LiteLLM/Portkey) do NOT go through httpx — explicit fast-follow;
gateway runs get `CaptureSurface.GATEWAY` (auto-INDETERMINATE for the envelope).
Streaming/SSE incremental tee, and moving redaction off this synchronous path, are
`# TODO(phase-1)`.
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


class Recorder:
    """Feeds boundaries into a signed `.rewind` artifact via rewind-core."""

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
        ctx = context.current()
        if ctx is None:
            return
        self.guard.assert_covered("http.httpx")

        canon = _semantic_request_canon(request, req_body)
        # The framed boundary blob = the raw pre-redaction bytes rewind-core commits to.
        blob = json.dumps(
            {
                "request": {
                    "method": request.method,
                    "url": str(request.url),
                    "body": req_body.decode("utf-8", "replace"),
                },
                "response": {"status": resp_status, "body": resp_body.decode("utf-8", "replace")},
            },
            separators=(",", ":"),
        ).encode("utf-8")

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
    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=body,
        request=request,
        extensions=response.extensions,
    )


def install(strict: bool = True) -> None:
    """Patch the httpx transports to tee raw bytes into the active Recorder.

    Idempotent. Non-streaming only in v0 (SSE tee is TODO(phase-1)). When no run is
    active, calls pass through untouched.
    """
    global _PATCHED, _ORIG_SYNC, _ORIG_ASYNC
    if _PATCHED:
        return
    _ORIG_SYNC = httpx.HTTPTransport.handle_request
    _ORIG_ASYNC = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        ctx = context.current()
        req_body = request.read() if ctx is not None else b""
        response = _ORIG_SYNC(self, request)  # type: ignore[misc]
        if ctx is None:
            return response
        return _tee(ctx.recorder, request, req_body, response, response.read())

    async def handle_async_request(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        ctx = context.current()
        req_body = await request.aread() if ctx is not None else b""
        response = await _ORIG_ASYNC(self, request)  # type: ignore[misc]
        if ctx is None:
            return response
        return _tee(ctx.recorder, request, req_body, response, await response.aread())

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
