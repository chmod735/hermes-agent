"""Integration tests for pco-completion-gate plugin registration."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

import hermes_cli.plugins as plugins_mod
from hermes_cli.plugins import PluginManager


SUMMARY_PACKET = """# Summary
Done.

# Recommended immediate next step
None.

# Exact next Source prompt pointer+SHA256
None.
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_min_schema(repo: Path) -> None:
    schema = repo / "schemas" / "completion-report.schema.yaml"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(
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
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _report(ref: str, sha: str) -> dict:
    return {
        "kind": "completion-report",
        "schema_version": "1",
        "gate_class": "A",
        "envelope_ref": ref,
        "envelope_sha256": sha,
        "controller_id": "hermes-primary",
        "lane_id": "single",
        "gate_opened_at": "2026-05-20T08:00:00Z",
        "gate_closed_at": "2026-05-20T08:01:00Z",
        "outcome": "completed",
        "summary": "Integration report.",
        "recommended_immediate_next_step": {
            "description": "No next gate.",
            "rationale": "Integration test finished.",
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


def _make_repo(tmp_path: Path, *, with_report: bool) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    _write_min_schema(repo)
    envelope = repo / ".hermes" / "research" / "run" / "source-ratify-test.md"
    envelope.parent.mkdir(parents=True, exist_ok=True)
    envelope.write_text("integration prompt\n", encoding="utf-8")
    ref = ".hermes/research/run/source-ratify-test.md"
    sha = _sha(envelope)
    ledger_dir = repo / ".hermes" / "active-work-ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "claim.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "active-work-ledger-record",
                "record_type": "claim",
                "schema_version": "2",
                "controller_id": "hermes-primary",
                "lane_id": "single",
                "record_timestamp": "2026-05-20T08:00:00Z",
                "claimed_at": "2026-05-20T08:00:00Z",
                "last_heartbeat_at": "2026-05-20T08:00:00Z",
                "worktree_path": ".",
                "envelope_ref": ref,
                "envelope_sha256": sha,
                "lease_seconds": 3600,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    if with_report:
        report = envelope.parent / "completion-report-20260520T080100Z.yaml"
        report.write_text(yaml.safe_dump(_report(ref, sha), sort_keys=False), encoding="utf-8")
        report.with_suffix(".md").write_text(SUMMARY_PACKET, encoding="utf-8")
    return repo, ref, sha


def _manager_with_plugin(tmp_path: Path, monkeypatch) -> PluginManager:
    hermes_home = tmp_path / "home"
    hermes_home.mkdir()
    marker = hermes_home / "pco-completion-gate" / "installed_at"
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-05-20T00:00:00Z\n", encoding="utf-8")
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["pco-completion-gate"]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    plugins_mod._plugin_manager = PluginManager()
    mgr = plugins_mod._plugin_manager
    mgr.discover_and_load(force=True)
    return mgr


def test_register_cycle_passes_valid_completion_report(tmp_path, monkeypatch):
    repo, _, _ = _make_repo(tmp_path, with_report=True)
    monkeypatch.chdir(repo)
    mgr = _manager_with_plugin(tmp_path, monkeypatch)

    mgr.invoke_hook("pre_llm_call", user_message="continue", session_id="s1")
    results = mgr.invoke_hook("transform_llm_output", response_text=SUMMARY_PACKET, session_id="s1")

    assert results == []


def test_register_cycle_blocks_invalid_completion_report_state(tmp_path, monkeypatch):
    repo, _, _ = _make_repo(tmp_path, with_report=False)
    monkeypatch.chdir(repo)
    mgr = _manager_with_plugin(tmp_path, monkeypatch)

    mgr.invoke_hook("pre_llm_call", user_message="continue", session_id="s1")
    results = mgr.invoke_hook("transform_llm_output", response_text=SUMMARY_PACKET, session_id="s1")

    assert len(results) == 1
    assert "Reason: missing_report" in results[0]
