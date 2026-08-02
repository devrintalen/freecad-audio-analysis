"""Preflight check framework tests.

The framework is exercised with stub objects so it stays testable without FreeCAD; the
checks themselves are run against real documents in test_freecad_integration.py.
"""

from __future__ import annotations

import pytest

from freecad.audio_analysis.checks import (
    CheckReport,
    Diagnostic,
    Severity,
    registered_checks,
    report_lumped_validity,
    run_checks,
)


def diagnostic(severity: Severity, code: str = "x") -> Diagnostic:
    return Diagnostic(severity=severity, code=code, message="m", why="w", remedy="r")


class TestDiagnostic:
    def test_format_includes_all_guidance(self):
        text = Diagnostic(
            severity=Severity.WARNING,
            code="c",
            message="Something is off.",
            why="Because physics.",
            remedy="Do this instead.",
            reference="STRUCTURE.md §2.4",
            subject="Environment",
        ).format()
        assert "[warning] Environment: Something is off." in text
        assert "Because physics." in text
        assert "Do this instead." in text
        assert "STRUCTURE.md §2.4" in text

    def test_format_omits_empty_fields(self):
        text = Diagnostic(severity=Severity.INFO, code="c", message="Just so you know.").format()
        assert text == "[info] Just so you know."

    def test_severity_orders_worst_first(self):
        assert Severity.ERROR > Severity.WARNING > Severity.INFO


class TestCheckReport:
    def test_empty_report_can_solve(self):
        assert CheckReport().can_solve
        assert CheckReport().format() == "No issues found."

    def test_warnings_do_not_block_solving(self):
        report = CheckReport([diagnostic(Severity.WARNING)])
        assert report.can_solve
        assert len(report.warnings) == 1

    def test_errors_block_solving(self):
        report = CheckReport([diagnostic(Severity.ERROR)])
        assert not report.can_solve

    def test_sorted_puts_errors_first(self):
        report = CheckReport(
            [diagnostic(Severity.INFO, "a"), diagnostic(Severity.ERROR, "b"),
             diagnostic(Severity.WARNING, "c")]
        )
        assert [d.severity for d in report.sorted()] == [
            Severity.ERROR, Severity.WARNING, Severity.INFO
        ]

    def test_summary_counts_each_severity(self):
        report = CheckReport(
            [diagnostic(Severity.ERROR, "a"), diagnostic(Severity.WARNING, "b"),
             diagnostic(Severity.WARNING, "c"), diagnostic(Severity.INFO, "d")]
        )
        assert report.summary() == "1 error(s), 2 warning(s), 1 note(s)"


class TestRunChecks:
    def test_checks_are_registered(self):
        assert len(registered_checks()) >= 3

    def test_a_broken_check_is_reported_not_raised(self, monkeypatch):
        """One faulty check must not hide the findings of the others."""
        import freecad.audio_analysis.checks as checks_module

        def exploding(analysis):
            raise RuntimeError("boom")

        monkeypatch.setattr(checks_module, "_REGISTRY", [exploding])
        report = run_checks(object())
        assert len(report.diagnostics) == 1
        assert report.diagnostics[0].code == "check-failed"
        assert "boom" in report.diagnostics[0].message
        # It must be framed as a workbench defect, not the user's fault.
        assert "not a problem with your model" in report.diagnostics[0].why


class TestLumpedValidityReporting:
    def test_within_range_is_informational(self):
        # A 10 mm cavity is good to ~4.3 kHz.
        d = report_lumped_validity(0.010, 2000.0)
        assert d.severity is Severity.INFO
        assert d.code == "lumped-validity-ok"

    def test_beyond_range_warns_with_the_number(self):
        # The over-ear cup case: valid to ~400 Hz, swept to 20 kHz.
        d = report_lumped_validity(0.1056, 20000.0)
        assert d.severity is Severity.WARNING
        assert d.code == "beyond-lumped-validity"
        assert "406 Hz" in d.message or "407 Hz" in d.message
        assert "20000 Hz" in d.message

    def test_warning_explains_why_and_what_to_do(self):
        d = report_lumped_validity(0.1056, 20000.0)
        assert "standing waves" in d.why
        assert "3D solve" in d.remedy

    def test_boundary_is_not_a_warning(self):
        from freecad.audio_analysis.physics import air

        props = air.AirProperties.at()
        limit = props.lumped_validity_limit(0.05)
        assert report_lumped_validity(0.05, limit, props).severity is Severity.INFO
        assert report_lumped_validity(0.05, limit * 1.01, props).severity is Severity.WARNING

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_rejects_nonphysical_dimension(self, bad):
        with pytest.raises(ValueError):
            report_lumped_validity(bad, 1000.0)
