"""Logging utilities for EchoTrace hub services."""

from __future__ import annotations

import csv
import datetime as dt

import fcntl
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO


LOGGER = logging.getLogger(__name__)

CSV_COLUMNS = ["timestamp", "event", "node_id", "detail"]
MAX_DETAIL_LENGTH = 2048
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class CsvEventLogger:
    """Append EchoTrace events to a daily CSV file with automatic rotation."""

    def __init__(self, logs_dir: Path) -> None:
        self._logs_dir = logs_dir
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: Optional[dt.date] = None
        self._file_path: Optional[Path] = None
        self._file_obj: Optional[TextIO] = None
        self._writer: Optional[csv.DictWriter] = None

    def record_event(self, event: str, node_id: Optional[str], detail: str) -> None:
        """Record a single event entry to the current CSV file."""
        timestamp = dt.datetime.now(tz=dt.timezone.utc)
        self._ensure_writer(timestamp.date())

        if self._writer is None:
            raise RuntimeError("CSV writer not initialised.")  # pragma: no cover

        row = {
            "timestamp": timestamp.isoformat(),
            "event": _sanitize_csv_cell(event),
            "node_id": _sanitize_csv_cell(node_id or ""),
            "detail": _sanitize_csv_cell(detail, max_length=MAX_DETAIL_LENGTH),
        }

        try:
            self._writer.writerow(row)
            if self._file_obj:
                self._file_obj.flush()
        except OSError as exc:
            raise RuntimeError(f"Failed to write event log: {exc}") from exc

    def close(self) -> None:
        """Close the current file handle, if any."""
        if self._file_obj:
            try:
                fcntl.flock(self._file_obj, fcntl.LOCK_UN)
                self._file_obj.close()
            except OSError:
                LOGGER.debug("Failed to close event log file cleanly.", exc_info=True)
        self._file_obj = None
        self._writer = None
        self._current_date = None

    def latest_csv(self) -> Optional[Path]:
        """Return the most recent CSV file in the logs directory."""
        candidates = sorted(self._logs_dir.glob("*_events.csv"))
        if not candidates:
            return None
        return candidates[-1]

    def _ensure_writer(self, current_date: dt.date) -> None:
        if self._current_date == current_date and self._writer is not None:
            return
        self.close()

        filename = f"{current_date.isoformat()}_events.csv"
        file_path = self._logs_dir / filename

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_exists = file_path.exists()
            self._file_obj = file_path.open("a", encoding="utf-8", newline="")
            try:
                fcntl.flock(self._file_obj, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                LOGGER.warning("Could not acquire lock on log file %s", file_path)
            self._writer = csv.DictWriter(self._file_obj, fieldnames=CSV_COLUMNS)
            if not file_exists:
                self._writer.writeheader()
        except OSError as exc:
            raise RuntimeError(f"Unable to open log file {file_path}: {exc}") from exc

        self._current_date = current_date
        self._file_path = file_path

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            LOGGER.debug(
                "Error while closing CsvEventLogger during garbage collection.",
                exc_info=True,
            )


@dataclass
class AnalyticsSummary:
    """Aggregate interpretation-ready analytics metrics."""

    by_node: dict[str, int]
    heartbeat_by_node: dict[str, int]
    narrative_unlocks: int
    total_triggers: int
    completion_rate: float
    mean_trigger_interval_seconds: float
    recent_events: list[dict[str, str]]


def summarize_events(logs_dir: Path) -> Optional[AnalyticsSummary]:
    """Parse the latest CSV log and return derived metrics."""
    logger = CsvEventLogger(logs_dir)
    latest = logger.latest_csv()
    logger.close()
    if latest is None or not latest.exists():
        return None

    by_node: dict[str, int] = {}
    heartbeat_by_node: dict[str, int] = {}
    narrative_unlocks = 0
    trigger_timestamps: list[dt.datetime] = []
    recent_events: list[dict[str, str]] = []

    try:
        with latest.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
    except OSError as exc:
        LOGGER.warning("Unable to read analytics log %s: %s", latest, exc)
        return None

    for row in rows:
        event = row.get("event", "")
        node_id = row.get("node_id", "") or ""
        detail = row.get("detail", "")
        timestamp_raw = row.get("timestamp", "")

        recent_events.append(
            {"timestamp": timestamp_raw, "event": event, "node_id": node_id, "detail": detail}
        )

        if event == "fragment_triggered":
            by_node[node_id] = by_node.get(node_id, 0) + 1
            if timestamp_raw:
                try:
                    trigger_timestamps.append(dt.datetime.fromisoformat(timestamp_raw))
                except ValueError:
                    pass
        elif event == "heartbeat_received":
            heartbeat_by_node[node_id] = heartbeat_by_node.get(node_id, 0) + 1
        elif event == "narrative_unlocked":
            narrative_unlocks += 1

    total_triggers = sum(by_node.values())
    completion_rate = 0.0
    if total_triggers > 0:
        completion_rate = min(1.0, narrative_unlocks / total_triggers)

    mean_interval = 0.0
    if len(trigger_timestamps) >= 2:
        trigger_timestamps.sort()
        deltas = [
            (trigger_timestamps[i] - trigger_timestamps[i - 1]).total_seconds()
            for i in range(1, len(trigger_timestamps))
        ]
        if deltas:
            mean_interval = sum(deltas) / len(deltas)

    recent_events = recent_events[-10:]

    return AnalyticsSummary(
        by_node=by_node,
        heartbeat_by_node=heartbeat_by_node,
        narrative_unlocks=narrative_unlocks,
        total_triggers=total_triggers,
        completion_rate=completion_rate,
        mean_trigger_interval_seconds=mean_interval,
        recent_events=recent_events,
    )


def _sanitize_csv_cell(value: str, *, max_length: int | None = None) -> str:
    """Prevent CSV formula execution and bound untrusted detail size."""
    text = value.replace("\x00", "").replace("\n", "\\n")
    if max_length is not None and len(text) > max_length:
        text = text[:max_length]
    if text.startswith(_CSV_FORMULA_PREFIXES):
        text = f"'{text}"
    return text


__all__ = ["AnalyticsSummary", "CSV_COLUMNS", "CsvEventLogger", "summarize_events"]
