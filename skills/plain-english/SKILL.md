---
name: plain-english
description: Use when a Markdown file was written through Bash, needs an on-demand prose check, or passed through the gate after three failed attempts.
---

# Plain English check

Use this skill to check Markdown that the Write and Edit gate cannot see. Bash writes through `cat`, `tee`, `sed`, or a heredoc bypass that gate.

The `plain-english` CLI must already be on `PATH`. A checkout owner can expose it once with `tools/plain-english/install.sh`. Do not run that installer as part of a check.

Run the CLI against the named file:

```bash
plain-english path/to/document.md
```

If the command is unavailable, say that the CLI needs installing and stop. Report the command output with its line numbers. If an AI-tell error appears in a human-facing document, suggest `/humanizer`.

Do not edit the file. Do not install this skill or enable the hook as part of a check.
