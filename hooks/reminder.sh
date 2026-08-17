#!/usr/bin/env bash
# Plain English per-turn chat reminder for Claude Code UserPromptSubmit hook.
# Hand-written précis (47 words) reinforcing style against long-session decay.

cat << 'EOF'
Answer in one bold line first. Numbered sections split by `---`; in chat, no `##` headers — they render as plain bold. Sentences under 25 words, each self-contained. Gloss every link, SHA and line number inline. End with a numbered decisions list; nothing actionable lives only in the body.
EOF
