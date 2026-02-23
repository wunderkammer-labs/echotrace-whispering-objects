"""Security-oriented tests for CSV event logging."""

from __future__ import annotations

import csv
from pathlib import Path

from hub.event_logging import CsvEventLogger


def _read_latest_row(log_dir: Path) -> dict[str, str]:
    files = sorted(log_dir.glob("*_events.csv"))
    assert files, "Expected a CSV log file to be created."
    with files[-1].open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "Expected at least one logged row."
    return rows[-1]


def test_csv_logger_neutralizes_formula_cells(tmp_path: Path) -> None:
    """Cells beginning with spreadsheet formula prefixes should be escaped."""
    logger = CsvEventLogger(tmp_path)
    logger.record_event("=event", "@node", "+SUM(A1:A2)")
    logger.close()

    row = _read_latest_row(tmp_path)
    assert row["event"].startswith("'=")
    assert row["node_id"].startswith("'@")
    assert row["detail"].startswith("'+")


def test_csv_logger_limits_detail_length(tmp_path: Path) -> None:
    """Untrusted detail payloads should be capped to a bounded size."""
    logger = CsvEventLogger(tmp_path)
    logger.record_event("heartbeat_received", "object1", "x" * 5000)
    logger.close()

    row = _read_latest_row(tmp_path)
    assert len(row["detail"]) == 2048
