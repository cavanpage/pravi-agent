"""End-to-end test execution + report parsing (ADR 0007)."""

from pravi.e2e.playwright_report import (
    E2EFailure,
    ParsedReport,
    parse_playwright_json,
)

__all__ = ["E2EFailure", "ParsedReport", "parse_playwright_json"]
