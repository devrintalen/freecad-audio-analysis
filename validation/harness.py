"""The benchmark harness.

A validation case is not a unit test. A unit test asks whether the code does what the
author meant; a benchmark asks whether what the author meant is *true*, by comparing
against an answer obtained some other way — a closed-form solution, a published
alignment table, or a different solver entirely.

So every :class:`Comparison` names its reference explicitly and carries an explicit
tolerance. "Close enough" is not a judgement the code gets to make silently: if a result
sits inside 0.5% of theory, that number appears in the report, and if the tolerance ever
has to be widened, the widening shows up in a diff.

Cases that need a binary this machine does not have report ``skipped`` rather than
failing, in keeping with the graceful-degradation rule in CLAUDE.md. A skipped case is
visible in the report and never silently counted as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

#: Registered cases, in declaration order.
_REGISTRY: list["Case"] = []


class Skip(Exception):
    """Raised by a case that cannot run here — a missing binary, usually."""


@dataclass(frozen=True)
class Comparison:
    """One number checked against an independently known answer."""

    quantity: str
    computed: float
    expected: float
    #: Allowed deviation. Relative unless :attr:`absolute` is set.
    tolerance: float
    unit: str = ""
    absolute: bool = False
    note: str = ""

    @property
    def deviation(self) -> float:
        if self.absolute:
            return abs(self.computed - self.expected)
        if self.expected == 0.0:
            return abs(self.computed)
        return abs(self.computed - self.expected) / abs(self.expected)

    @property
    def passed(self) -> bool:
        return self.deviation <= self.tolerance

    def format(self) -> str:
        mark = "ok  " if self.passed else "FAIL"
        if self.absolute:
            margin = f"{self.deviation:.4g} of {self.tolerance:.4g} {self.unit}".strip()
        else:
            margin = f"{self.deviation * 100:.3g}% of {self.tolerance * 100:.3g}%"
        line = (
            f"  [{mark}] {self.quantity}: {self.computed:.6g} vs {self.expected:.6g} "
            f"{self.unit}".rstrip()
            + f"  ({margin})"
        )
        return line + (f"\n         {self.note}" if self.note else "")


@dataclass
class CaseResult:
    """What running one case produced."""

    case: "Case"
    comparisons: list[Comparison] = field(default_factory=list)
    skipped: str = ""

    @property
    def passed(self) -> bool:
        return not self.skipped and all(c.passed for c in self.comparisons)

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"

    def format(self) -> str:
        lines = [f"[{self.status}] {self.case.name}", f"       reference: {self.case.reference}"]
        if self.skipped:
            lines.append(f"       skipped: {self.skipped}")
        lines += [c.format() for c in self.comparisons]
        return "\n".join(lines)


@dataclass(frozen=True)
class Case:
    """One benchmark with a known-independent answer."""

    key: str
    name: str
    #: Where the expected answer comes from. Never "the previous run of this code".
    reference: str
    tier: int
    check: Callable[[], Iterable[Comparison]]

    def run(self) -> CaseResult:
        try:
            return CaseResult(self, list(self.check()))
        except Skip as exc:
            return CaseResult(self, [], skipped=str(exc))


def case(key: str, name: str, reference: str, tier: int) -> Callable:
    """Register a benchmark. Used as a decorator on the check function."""

    def decorate(func: Callable[[], Iterable[Comparison]]) -> Callable:
        _REGISTRY.append(Case(key, name, reference, tier, func))
        return func

    return decorate


def registered(tier: int | None = None) -> list[Case]:
    _load_cases()
    return [c for c in _REGISTRY if tier is None or c.tier == tier]


def _load_cases() -> None:
    """Import the case modules, which register themselves on import."""
    if _REGISTRY:
        return
    from validation import tier1  # noqa: F401  -- import for side effects


def run_all(tier: int | None = None) -> list[CaseResult]:
    return [c.run() for c in registered(tier)]


def report(results: list[CaseResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    skipped = sum(1 for r in results if r.skipped)
    failed = len(results) - passed - skipped
    body = "\n\n".join(r.format() for r in results)
    return (
        f"{body}\n\n"
        f"{passed} passed, {failed} failed, {skipped} skipped "
        f"out of {len(results)} benchmark(s)."
    )
