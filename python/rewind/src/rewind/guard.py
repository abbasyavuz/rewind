"""Deny-by-default nondeterminism guard.

The set of nondeterminism sources is NOT a finite enumeration (technical plan §3.2):
C-extension RNG, `secrets`, native-lib clocks, local file/DB reads, local vector
stores (FAISS/Chroma/Lance — a whole RAG class that never touches httpx), mid-run
env mutation. Rather than list them, we deny by default: any *un-covered* source
reached during record OR replay must FAIL LOUD, and every run emits a
capture-coverage report so the user sees exactly what was/ wasn't captured.

v0 implements the registry + coverage report + an explicit raise. Wiring the actual
interception hooks for each source is Phase-1 work (`# TODO(phase-1)`).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class UncoveredNondeterminismError(RuntimeError):
    """Raised when a nondeterminism source is reached that we cannot faithfully
    capture/replay. Failing loud beats a silent, confidently-wrong reconstruction."""


@dataclass
class CoverageReport:
    covered: set[str] = field(default_factory=set)
    uncovered: set[str] = field(default_factory=set)

    def is_clean(self) -> bool:
        return not self.uncovered

    def render(self) -> str:
        lines = ["rewind capture-coverage:"]
        for s in sorted(self.covered):
            lines.append(f"  [✓] {s}")
        for s in sorted(self.uncovered):
            lines.append(f"  [✗] {s}  (UNCOVERED — replay may diverge)")
        return "\n".join(lines)


# Sources we know how to shim (extended in Phase 1).
_COVERED: set[str] = {
    "http.httpx",       # transport chokepoint (capture.py)
    "time.time",        # TODO(phase-1): wire actual shim
    "random.Random",    # TODO(phase-1)
    "uuid.uuid4",       # TODO(phase-1)
}


class Guard:
    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self.report = CoverageReport()

    def assert_covered(self, source: str) -> None:
        """Call at each interception point. Unknown source -> fail loud (strict) or record as uncovered."""
        if source in _COVERED:
            self.report.covered.add(source)
            return
        self.report.uncovered.add(source)
        if self.strict:
            raise UncoveredNondeterminismError(
                f"nondeterminism source '{source}' is not covered; "
                f"replay cannot be faithful. Add a shim or mark the boundary OpaqueTool."
            )
