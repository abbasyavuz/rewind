"""Nondeterminism-guard honesty tests. Run: pytest (from python/rewind).

Regression cover for LB-2: the guard must NOT claim to cover sources it doesn't
actually shim. Only `http.httpx` is live in v0; time/RNG/uuid are PLANNED and must be
reported (and fail loud in strict mode), never silently counted as covered.
"""

from __future__ import annotations

import pytest

from rewind.guard import Guard, UncoveredNondeterminismError


def test_only_httpx_is_actually_covered() -> None:
    g = Guard(strict=False)
    g.assert_covered("http.httpx")
    assert g.report.covered == {"http.httpx"}
    assert g.report.planned == set()
    assert g.report.uncovered == set()
    assert g.report.is_clean()  # a covered-only run has nothing uncovered


def test_planned_source_is_reported_not_covered() -> None:
    g = Guard(strict=False)
    for src in ("time.time", "random.Random", "uuid.uuid4"):
        g.assert_covered(src)
    assert g.report.covered == set()
    assert g.report.planned == {"time.time", "random.Random", "uuid.uuid4"}
    # Planned sources are not "uncovered", but the run is also not clean enough to
    # trust a faithful replay — they must be visible to the user.
    assert "PLANNED" in g.report.render()


def test_unknown_source_is_uncovered() -> None:
    g = Guard(strict=False)
    g.assert_covered("faiss.read")
    assert g.report.uncovered == {"faiss.read"}
    assert not g.report.is_clean()


def test_strict_planned_fails_loud() -> None:
    g = Guard(strict=True)
    with pytest.raises(UncoveredNondeterminismError, match="PLANNED"):
        g.assert_covered("uuid.uuid4")


def test_strict_unknown_fails_loud() -> None:
    g = Guard(strict=True)
    with pytest.raises(UncoveredNondeterminismError):
        g.assert_covered("secrets.token_hex")


def test_render_marks_each_class_distinctly() -> None:
    g = Guard(strict=False)
    g.assert_covered("http.httpx")
    g.assert_covered("uuid.uuid4")
    g.assert_covered("faiss.read")
    out = g.report.render()
    assert "[✓] http.httpx" in out
    assert "[~] uuid.uuid4" in out
    assert "[✗] faiss.read" in out
