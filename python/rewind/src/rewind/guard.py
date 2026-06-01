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
    planned: set[str] = field(default_factory=set)

    def is_clean(self) -> bool:
        return not self.uncovered

    def render(self) -> str:
        lines = ["rewind capture-coverage:"]
        for s in sorted(self.covered):
            lines.append(f"  [✓] {s}")
        for s in sorted(self.planned):
            lines.append(f"  [~] {s}  (PLANNED — not yet shimmed; replay may diverge)")
        for s in sorted(self.uncovered):
            lines.append(f"  [✗] {s}  (UNCOVERED — replay may diverge)")
        return "\n".join(lines)


# Sources we ACTUALLY intercept today.
_COVERED: set[str] = {
    "http.httpx",       # transport chokepoint (capture.py) — the only live shim in v0
}

# Sources we intend to shim but have NOT wired yet (`# TODO(phase-1)`). These are
# reported as PLANNED, never as covered — claiming coverage we don't deliver would
# let a run silently diverge on replay (e.g. an agent calling uuid4()/time.time()).
_PLANNED: set[str] = {
    "time.time",
    "random.Random",
    "uuid.uuid4",
}


class Guard:
    def __init__(self, strict: bool = True) -> None:
        self.strict = strict
        self.report = CoverageReport()

    def assert_covered(self, source: str) -> None:
        """Call at each interception point. A covered source is recorded and served.
        A PLANNED source (known but not yet shimmed) and any unknown source cannot be
        faithfully captured/replayed -> fail loud (strict) or record for the report."""
        if source in _COVERED:
            self.report.covered.add(source)
            return
        if source in _PLANNED:
            self.report.planned.add(source)
            if self.strict:
                raise UncoveredNondeterminismError(
                    f"nondeterminism source '{source}' is PLANNED but not yet shimmed (phase-1); "
                    f"replay cannot be faithful. Disable strict, add a shim, or mark the boundary OpaqueTool."
                )
            return
        self.report.uncovered.add(source)
        if self.strict:
            raise UncoveredNondeterminismError(
                f"nondeterminism source '{source}' is not covered; "
                f"replay cannot be faithful. Add a shim or mark the boundary OpaqueTool."
            )
