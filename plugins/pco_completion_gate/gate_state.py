"""Session-scoped state for the PCO completion-report gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import threading
from typing import Dict, Optional

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - defensive import for isolated tests
    get_hermes_home = None  # type: ignore[assignment]


@dataclass
class GateRecord:
    session_id: str
    controller_id: str | None
    lane_id: str | None
    envelope_ref: str | None
    envelope_sha256: str | None
    ratified_at: str
    source: str
    required: bool = True
    cleared_by_report_path: str | None = None
    cleared_at: str | None = None


class GateRegistry:
    """RLock-guarded in-process registry keyed by session id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, GateRecord] = {}

    def set(self, record: GateRecord) -> None:
        with self._lock:
            self._records[record.session_id] = record

    def get(self, session_id: str) -> Optional[GateRecord]:
        with self._lock:
            return self._records.get(session_id)

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def all(self) -> list[GateRecord]:
        with self._lock:
            return list(self._records.values())


registry = GateRegistry()
_installed_at_override: str | None = None
_install_lock = threading.RLock()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value or value.startswith(("commit:", "source-controlled:")):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _state_path() -> Path:
    if get_hermes_home is not None:
        home = Path(get_hermes_home())
    else:
        home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return home / "pco-completion-gate" / "installed_at"


def installed_at() -> str:
    """Return the install timestamp, creating it on first plugin load."""
    global _installed_at_override
    if _installed_at_override is not None:
        return _installed_at_override
    path = _state_path()
    with _install_lock:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
            path.parent.mkdir(parents=True, exist_ok=True)
            value = utc_now_iso()
            path.write_text(value + "\n", encoding="utf-8")
            return value
        except OSError:
            return utc_now_iso()


def set_installed_at_for_tests(value: str | None) -> None:
    global _installed_at_override
    _installed_at_override = value


def is_historical(record: GateRecord) -> bool:
    opened = _parse_iso(record.ratified_at)
    installed = _parse_iso(installed_at())
    if opened is None or installed is None:
        return False
    return opened < installed
