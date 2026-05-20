"""Canonical operator-facing block payload for PCO gate failures."""

from __future__ import annotations

from .gate_state import GateRecord


_ALLOWED_REASONS = {
    "missing_report",
    "schema_failed",
    "envelope_drift",
    "duplicate_completed",
    "terminal_packet_missing",
    "terminal_packet_ordering",
    "validator_unavailable",
}


def render_block(reason: str, record: GateRecord) -> str:
    safe_reason = reason if reason in _ALLOWED_REASONS else "validator_unavailable"
    return f"""⚠️ Completion-report runtime hook blocked this response.

Reason: {safe_reason}

Open gate:
  envelope_ref:       {record.envelope_ref or "unknown"}
  envelope_sha256:    {record.envelope_sha256 or "unknown"}
  controller_id:      {record.controller_id or "unknown-controller"}
  lane_id:            {record.lane_id or "single"}
  gate_opened_at:     {record.ratified_at or "unknown"}

Required to release:
  1. Author a completion-report YAML sidecar conforming to
     schemas/completion-report.schema.yaml.
  2. Place it at one of:
       .hermes/research/<run-archive>/completion-report-<ts>.yaml
       .hermes/completion-reports/<lane_id>/<ts>.yaml
  3. Render a Markdown body adjacent (same basename, .md) with the
     three canonical headers, in order:
       Summary
       Recommended immediate next step
       Exact next Source prompt pointer+SHA256
  4. Re-emit the terminal answer with those three headers literally
     present in canonical order in the response text.

Substrate contract:
  docs/operations/COMPLETION_REPORT_PROTOCOL.md
  schemas/completion-report.schema.yaml
  validators/creator_engine_validator/checks/completion_report_schema.py
  validators/creator_engine_validator/checks/completion_report_required_for_envelope.py
  validators/creator_engine_validator/checks/completion_report_terminal_sections.py

If you believe this gate should NOT be report-required (Class G or H
per protocol §d.2), surface that to Source rather than removing the
ratification record.

(pco-completion-gate v1; rationale code: {safe_reason})"""
