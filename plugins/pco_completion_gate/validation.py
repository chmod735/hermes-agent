"""Validation helpers for completion-report runtime enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import importlib
import json
import re
import shutil
import subprocess
import sys

import yaml

from . import discovery

CANONICAL_HEADERS: tuple[str, ...] = (
    "Summary",
    "Recommended immediate next step",
    "Exact next Source prompt pointer+SHA256",
)
_REQUIRED_REPORT_FIELDS = {
    "kind",
    "schema_version",
    "gate_class",
    "envelope_ref",
    "envelope_sha256",
    "controller_id",
    "lane_id",
    "gate_opened_at",
    "gate_closed_at",
    "outcome",
    "summary",
    "recommended_immediate_next_step",
    "exact_next_source_prompt",
    "terminal_packet_sections_present",
}


def _header_positions(text: str) -> list[tuple[str, int]]:
    positions: list[tuple[str, int]] = []
    pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    for line_index, line in enumerate(text.splitlines()):
        match = pattern.match(line)
        if match is None:
            continue
        visible = match.group(2).strip().rstrip("#").strip()
        if visible in CANONICAL_HEADERS:
            positions.append((visible, line_index))
    return positions


def terminal_section_violation(text: str) -> str | None:
    positions = _header_positions(text)
    if len(positions) < 3:
        return "terminal_packet_missing"
    first_index: dict[str, int] = {}
    for header, line_idx in positions:
        first_index.setdefault(header, line_idx)
    if any(header not in first_index for header in CANONICAL_HEADERS):
        return "terminal_packet_missing"
    actual_order = tuple(name for name, _ in sorted(first_index.items(), key=lambda kv: kv[1]))
    if actual_order != CANONICAL_HEADERS:
        return "terminal_packet_ordering"
    return None


def _direct_validator_errors(record: dict[str, Any], path: Path, repo_root: Path) -> list[str] | None:
    inserted = False
    validators = repo_root / "validators"
    if validators.is_dir() and str(validators) not in sys.path:
        sys.path.insert(0, str(validators))
        inserted = True
    try:
        mod = importlib.import_module(
            "creator_engine_validator.checks.completion_report_schema"
        )
        errors = mod.validate_completion_report_record(record, path)
        return [str(err) for err in errors]
    except Exception:
        return None
    finally:
        if inserted:
            try:
                sys.path.remove(str(validators))
            except ValueError:
                pass


def _subprocess_validator_errors(path: Path, repo_root: Path) -> list[str] | None:
    commands: list[list[str]] = []
    exe = shutil.which("creator-engine-validator")
    if exe:
        commands.append([exe, "--check", "completion_report_schema", str(path)])
    commands.append([sys.executable, "-m", "creator_engine_validator.cli", "--check", "completion_report_schema", str(path)])
    env = None
    validators = repo_root / "validators"
    if validators.is_dir():
        env = dict(**__import__("os").environ)
        env["PYTHONPATH"] = str(validators) + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, cwd=str(repo_root), env=env, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        combined = (proc.stdout + proc.stderr).strip()
        if proc.returncode == 0:
            return []
        if "No module named 'creator_engine_validator'" in combined or "No module named creator_engine_validator" in combined:
            continue
        if "No module named" in combined and "creator_engine_validator" in combined:
            continue
        if combined:
            return [combined]
    return None


def _local_schema_errors(record: dict[str, Any], repo_root: Path) -> list[str] | None:
    schema_path = repo_root / "schemas" / "completion-report.schema.yaml"
    if not schema_path.is_file():
        return None
    try:
        import jsonschema

        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
        jsonschema.Draft202012Validator(schema).validate(record)
        return []
    except Exception as exc:
        return [str(exc)]


def validate_report(record: dict[str, Any], path: Path, repo_root: Path) -> str | None:
    if not isinstance(record, dict):
        return "schema_failed"
    if not _REQUIRED_REPORT_FIELDS.issubset(record):
        return "schema_failed"
    if record.get("kind") != "completion-report":
        return "schema_failed"

    errors = _direct_validator_errors(record, path, repo_root)
    if errors is not None:
        return "schema_failed" if errors else None
    errors = _subprocess_validator_errors(path, repo_root)
    if errors is not None:
        return "schema_failed" if errors else None

    errors = _local_schema_errors(record, repo_root)
    if errors is None:
        return "validator_unavailable"
    return "schema_failed" if errors else None


def envelope_drift(record: dict[str, Any], repo_root: Path) -> bool:
    ref = record.get("envelope_ref")
    sha = record.get("envelope_sha256")
    if not isinstance(ref, str) or not isinstance(sha, str):
        return True
    path = repo_root / ref
    actual = discovery.sha256_file(path)
    return actual != sha
