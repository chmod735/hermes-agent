"""Filesystem discovery helpers for completion-report enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import hashlib

import yaml

from .gate_state import GateRecord

_RELEASED = {"released", "lapsed", "completed", "aborted", "handed_off"}


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* until a repository marker is found.

    Discovery never scans above the returned root; callers use the result as
    the hard boundary for `.hermes/` artifact searches.
    """
    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists() or (candidate / "validators").exists():
            return candidate
    return None


def repo_relative(path: Path, repo_root: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _yaml_files(path: Path) -> Iterable[Path]:
    if not path.is_dir():
        return []
    return sorted(path.rglob("*.yml")) + sorted(path.rglob("*.yaml"))


def iter_ledger_records(repo_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    ledger_dir = repo_root / ".hermes" / "active-work-ledger"
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in _yaml_files(ledger_dir):
        data = load_yaml(path)
        if isinstance(data, dict) and data.get("kind") == "active-work-ledger-record":
            records.append((path, data))
    return records


def open_claims(repo_root: Path, session_id: str = "") -> list[GateRecord]:
    claims: list[GateRecord] = []
    for _path, data in iter_ledger_records(repo_root):
        if data.get("record_type") != "claim":
            continue
        if data.get("released_at") or data.get("release_reason") in _RELEASED:
            continue
        if data.get("status") in _RELEASED:
            continue
        ref = data.get("envelope_ref") or data.get("recommended_prompt_ref")
        sha = data.get("envelope_sha256")
        if not isinstance(ref, str) or ref == "none" or not isinstance(sha, str) or len(sha) != 64:
            continue
        claims.append(
            GateRecord(
                session_id=session_id or "default",
                controller_id=data.get("controller_id"),
                lane_id=data.get("lane_id") or "single",
                envelope_ref=ref,
                envelope_sha256=sha,
                ratified_at=data.get("record_timestamp") or data.get("claimed_at") or "",
                source="active_work_ledger_open_claim",
                required=True,
            )
        )
    return claims


def discover_completion_reports(repo_root: Path, lane_id: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    research = repo_root / ".hermes" / "research"
    if research.is_dir():
        candidates.extend(sorted(research.rglob("completion-report-*.yml")))
        candidates.extend(sorted(research.rglob("completion-report-*.yaml")))
    reports_dir = repo_root / ".hermes" / "completion-reports" / (lane_id or "single")
    if reports_dir.is_dir():
        candidates.extend(_yaml_files(reports_dir))
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def matching_reports(repo_root: Path, record: GateRecord) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in discover_completion_reports(repo_root, record.lane_id):
        data = load_yaml(path)
        if not isinstance(data, dict) or data.get("kind") != "completion-report":
            continue
        if data.get("envelope_ref") == record.envelope_ref and data.get("envelope_sha256") == record.envelope_sha256:
            matches.append((path, data))
    return matches


def select_report(matches: list[tuple[Path, dict[str, Any]]]) -> tuple[str | None, Path | None, dict[str, Any] | None]:
    completed = [(p, r) for p, r in matches if r.get("outcome") == "completed"]
    if len(completed) > 1:
        return "duplicate_completed", None, None
    if not matches:
        return "missing_report", None, None
    if len(completed) == 1:
        return None, completed[0][0], completed[0][1]
    chosen = sorted(matches, key=lambda item: item[0].name)[-1]
    return None, chosen[0], chosen[1]
