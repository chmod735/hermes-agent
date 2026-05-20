"""Unit tests for the opt-in PCO completion-report runtime gate."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from plugins.pco_completion_gate import gate_state
from plugins.pco_completion_gate import discovery
from plugins.pco_completion_gate import validation
from plugins.pco_completion_gate import remediation
from plugins.pco_completion_gate import (
    _on_pre_llm_call,
    _on_session_end,
    _on_session_start,
    _on_transform_llm_output,
)


SUMMARY_PACKET = """# Summary
Done.

# Recommended immediate next step
None.

# Exact next Source prompt pointer+SHA256
None.
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_schema(repo: Path) -> None:
    schema_dir = repo / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "completion-report.schema.yaml").write_text(
        """
type: object
required:
  - kind
  - schema_version
  - gate_class
  - envelope_ref
  - envelope_sha256
  - controller_id
  - lane_id
  - gate_opened_at
  - gate_closed_at
  - outcome
  - summary
  - recommended_immediate_next_step
  - exact_next_source_prompt
  - terminal_packet_sections_present
properties:
  kind:
    const: completion-report
  schema_version:
    const: '1'
  gate_class:
    enum: [A, C-merge, C-pr-only, D, E, F]
  envelope_sha256:
    pattern: '^[0-9a-f]{64}$'
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _make_repo(tmp_path: Path, *, schema: bool = True) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    env = repo / ".hermes" / "research" / "run" / "source-ratify-test.md"
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("ratified test prompt\n", encoding="utf-8")
    if schema:
        _write_schema(repo)
    return repo, ".hermes/research/run/source-ratify-test.md", _sha(env)


def _record(envelope_ref: str, envelope_sha: str, **overrides):
    record = {
        "kind": "completion-report",
        "schema_version": "1",
        "gate_class": "A",
        "envelope_ref": envelope_ref,
        "envelope_sha256": envelope_sha,
        "controller_id": "hermes-primary",
        "lane_id": "single",
        "gate_opened_at": "2026-05-20T08:00:00Z",
        "gate_closed_at": "2026-05-20T08:01:00Z",
        "outcome": "completed",
        "summary": "Test completion report.",
        "recommended_immediate_next_step": {
            "description": "No next gate.",
            "rationale": "Test finished.",
            "next_action_kind": "no_next_gate",
        },
        "exact_next_source_prompt": {
            "kind": "none",
            "none_rationale": "source_paused_program",
        },
        "terminal_packet_sections_present": {
            "summary": True,
            "recommended_immediate_next_step": True,
            "exact_next_source_prompt_pointer_sha256": True,
        },
    }
    record.update(overrides)
    return record


def _write_report(repo: Path, record: dict, name: str = "completion-report-20260520T080100Z.yaml") -> Path:
    import yaml

    report = repo / ".hermes" / "research" / "run" / name
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    report.with_suffix(".md").write_text(SUMMARY_PACKET, encoding="utf-8")
    return report


def _claim(envelope_ref: str, envelope_sha: str, **overrides) -> gate_state.GateRecord:
    data = {
        "session_id": "s1",
        "controller_id": "hermes-primary",
        "lane_id": "single",
        "envelope_ref": envelope_ref,
        "envelope_sha256": envelope_sha,
        "ratified_at": "2026-05-20T08:00:00Z",
        "source": "active_work_ledger_open_claim",
        "required": True,
    }
    data.update(overrides)
    return gate_state.GateRecord(**data)


@pytest.fixture(autouse=True)
def clear_registry(monkeypatch, tmp_path):
    gate_state.registry.clear()
    gate_state.set_installed_at_for_tests("2026-05-20T00:00:00Z")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    yield
    gate_state.registry.clear()
    gate_state.set_installed_at_for_tests(None)


def test_no_open_gate_returns_none(tmp_path, monkeypatch):
    repo, _, _ = _make_repo(tmp_path)
    monkeypatch.chdir(repo)

    assert _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1") is None


def test_class_g_chat_pass_through(tmp_path, monkeypatch):
    repo, _, _ = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    _on_pre_llm_call(user_message="How does the validator find the worktree root?", session_id="s1")

    assert _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1") is None


def test_open_claim_without_report_blocks_missing_report(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Completion-report runtime hook blocked" in out
    assert "Reason: missing_report" in out


def test_open_claim_with_valid_report_passes(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha))

    assert _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1") is None


def test_open_claim_with_schema_invalid_report_blocks(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    bad = _record(ref, sha)
    bad.pop("recommended_immediate_next_step")
    _write_report(repo, bad)

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Reason: schema_failed" in out


def test_envelope_sha_drift_blocks(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha))
    (repo / ref).write_text("drifted prompt\n", encoding="utf-8")

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Reason: envelope_drift" in out


def test_terminal_packet_missing_blocks(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha))

    out = _on_transform_llm_output(response_text="# Summary\nOnly one section.\n", session_id="s1")

    assert "Reason: terminal_packet_missing" in out


def test_terminal_packet_out_of_order_blocks(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha))
    wrong = """# Recommended immediate next step
Next.

# Summary
Done.

# Exact next Source prompt pointer+SHA256
None.
"""

    out = _on_transform_llm_output(response_text=wrong, session_id="s1")

    assert "Reason: terminal_packet_ordering" in out


def test_duplicate_completed_blocks(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha), "completion-report-20260520T080100Z.yaml")
    _write_report(repo, _record(ref, sha), "completion-report-20260520T080200Z.yaml")

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Reason: duplicate_completed" in out


def test_validator_unavailable_blocks_report_required_gate(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path, schema=False)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))
    _write_report(repo, _record(ref, sha))
    monkeypatch.setattr(validation, "_direct_validator_errors", lambda *_args, **_kw: None)
    monkeypatch.setattr(validation, "_subprocess_validator_errors", lambda *_args, **_kw: None)

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Reason: validator_unavailable" in out


def test_historical_gate_advisory_only(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    old = _claim(ref, sha, ratified_at="2026-05-19T00:00:00Z")
    gate_state.registry.set(old)
    gate_state.set_installed_at_for_tests("2026-05-20T00:00:00Z")

    assert _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1") is None


def test_ratification_line_pattern_recognised_in_user_message(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    msg = f"Source ratifies prompt:{ref} with SHA:{sha}"

    _on_pre_llm_call(user_message=msg, session_id="s1")

    record = gate_state.registry.get("s1")
    assert record is not None
    assert record.envelope_ref == ref
    assert record.envelope_sha256 == sha
    assert record.source == "ratification_line_in_user_message"


def test_unknown_controller_logs_but_does_not_block(tmp_path, monkeypatch, caplog):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha, controller_id=None))

    out = _on_transform_llm_output(response_text=SUMMARY_PACKET, session_id="s1")

    assert "Reason: missing_report" in out
    assert "unknown controller" in caplog.text.lower()


def test_session_lifecycle_clears_state(tmp_path, monkeypatch):
    repo, ref, sha = _make_repo(tmp_path)
    monkeypatch.chdir(repo)
    gate_state.registry.set(_claim(ref, sha))

    _on_session_start(session_id="s1")
    assert gate_state.registry.get("s1") is None
    gate_state.registry.set(_claim(ref, sha))
    _on_session_end(session_id="s1")
    assert gate_state.registry.get("s1") is None
