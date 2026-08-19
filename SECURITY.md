# Security

## Reporting

Report a vulnerability through GitHub's private advisory form at
https://github.com/carlosboeing/copydesk/security/advisories/new, or by
opening an issue if the problem carries no exploitation risk.

Expect an acknowledgement within a week. CopyDesk is maintained by one person,
so please allow time before disclosing publicly.

## What CopyDesk touches

Worth knowing when judging severity.

- **It reads the text an agent is about to write**, as a `PreToolUse` hook. Document content passes through `lib/linter.py` and is not sent anywhere.
- **It writes a telemetry event log** under `$XDG_STATE_HOME/copydesk/`, falling back to `~/.local/state/copydesk/`. Events record rule names, line numbers and counts. Set `COPYDESK_LOG_FLAGGED_TEXT=0` to omit flagged text snippets, or `COPYDESK_LOG=0` to disable event writing entirely.
- **Nothing leaves the machine.** There is no network call anywhere in the tool.
- **The gate fails open.** A malformed payload, an unreadable config or an internal error lets the write through rather than blocking it. Treating CopyDesk as a security control would be a mistake: it is a writing-quality gate, and its failure mode is deliberately permissive.

## Supported versions

Pre-1.0. Only the latest release gets fixes.
