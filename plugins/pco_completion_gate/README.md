# pco-completion-gate

Opt-in runtime enforcement for Creator Engine completion-report gates.

The plugin registers these Hermes hooks:

- `on_session_start`
- `pre_llm_call`
- `transform_llm_output`
- `on_session_end`

It is intentionally staged as a standalone plugin. It is inert unless a live
Hermes profile explicitly enables `pco-completion-gate` in `plugins.enabled`.
This implementation does not edit core agent, CLI, gateway, transport, or
profile configuration files.

## Enforcement seam

The plugin uses `transform_llm_output` and returns a replacement string only
when all of these are true:

1. a report-required gate is open for the current session;
2. no valid completion-report sidecar matching the gate is present, or the
   matching sidecar fails validation, or the outgoing terminal packet lacks
   the canonical section headers in order.

For normal Q&A, non-ratified chat, and sessions with no open ledger claim, the
callback returns `None` and the response passes through unchanged.

## Block behavior

The plugin returns the canonical remediation payload rather than raising an
exception. This is safer with the current Hermes hook manager because callback
exceptions are logged and swallowed by design, while `transform_llm_output`
uses first non-empty string semantics for intentional replacements.

The payload does not echo the original response. It names only the reason code,
repo-relative envelope reference, envelope SHA, controller/lane fields, and the
repo-relative remediation contract pointers.

## Ordering

`plugin.yaml` records `priority: -1000` as the intended ordering contract for
future plugin-manager support. Current Hermes loads enabled standalone plugins
in manifest discovery order and no bundled sibling registers
`transform_llm_output`; with this plugin enabled as the sole completion gate it
is therefore first among active sibling LLM-output transformers. Core plugin
manager files are deliberately not edited in this slice.

## Historical gates

On first load, the plugin creates a profile-scoped installed-at marker under
Hermes home. Ledger claims older than that timestamp are advisory only and do
not block. This preserves in-flight sessions when Source later authorizes live
profile adoption.

## Validation strategy

For an open gate, validation tries these paths in order:

1. direct import of the Creator Engine completion-report validator;
2. subprocess execution of the validator entry point or Python module;
3. local schema validation from the discovered repo root.

If none is available for a report-required gate, the plugin blocks with
`validator_unavailable`. It never uses network access and never scans above the
discovered repo root.
