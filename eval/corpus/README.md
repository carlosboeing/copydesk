# The fixed prompt corpus

Eight sequences of ten turns, used to measure whether a rule change alters how an agent writes, and whether that change survives a long session. The prompts were authored before any measurement ran against them.

**Do not change these files once a baseline has been measured against them.** Every before-and-after comparison is anchored to this exact text. Editing a turn discards the comparison rather than improving it. Every threshold in this corpus is free to tune; the prompts are not.

## Target

All eight sequences run against **CrossRev, pinned at `c72d978`**.

Deliberately not this repository. Running the corpus against CopyDesk itself would let the model read the rules, the output style and this very page, priming it to write plainly in every run and contaminating the thing being measured. CrossRev is real work, varied enough to carry all six categories, and contains no writing-style content.

Several turns write files, so the checkout is reset between runs. Run two must start from the same tree as run one.

## Coverage

| Sequence | Category |
|---|---|
| 01, 02 | Long implementation — the canonical decay case |
| 03, 04 | Debugging — iterative, and where dense output costs most |
| 05 | Brainstorm — open-ended, invites aphorism in place of information |
| 06 | Design review — critiquing prose, where register is contagious |
| 07 | Repo status — short factual answers, where padding shows |
| 08 | Code explanation — the "explain it plainly" complaint, directly |

Each sequence mixes turns that write Markdown with turns that only answer in chat. Those two streams are measured separately and never summed: chat is governed by the rules alone, while a file write also passes the gate when one is installed.

## The rule that shaped the writing

**No turn may refer to the model's previous answer.** "Fix the bug you found" breaks when there was no bug, and a corpus whose turn 7 depends on turn 6 measures the model's luck as much as its style. Every turn refers to the repository or the task instead, and reads naturally whatever came before it.

Turn 10 is a summary or compression request in most sequences. That is deliberate: it is where density is worst, and it lands on a checkpoint.

## Format

Plain text, not Markdown, so the prose gate never lints the corpus itself.

Lines beginning `#` before the first turn are comments. Each turn starts with a line matching `^### turn (\d+)$`; everything up to the next such line is the prompt, verbatim. Prompts may span several lines.

## Running

Checkpoints are turns 1, 5 and 10. The metric of interest is the drift from turn 1 to turn 10, not the absolute score at any one of them.

Claude Code runs 3 times per sequence. Codex and Kimi run once each, escalating to 3 for any sequence landing between a 5% and 15% rise. Report median and interquartile range per checkpoint, never a pooled mean.

Each sequence runs as one continuous session. A sequence split across sessions measures nothing, because drift within a session is the whole point.
