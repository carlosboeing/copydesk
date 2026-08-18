#!/usr/bin/env bash
# Run one fixed corpus condition without changing the measured harness settings.
set -euo pipefail

EVAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORPUS_DIR="$EVAL_DIR/corpus"
PINNED_CROSSREV_COMMIT="c72d978"
HARNESS=""
CONDITION=""
REPO=""
RESULTS_ROOT="$EVAL_DIR/results"
SETTINGS_ROOT=""
SEQUENCE=""
RUNS=""
PREFLIGHT=false
CONFIRMED=false

usage() {
    printf '%s\n' "Usage: run-corpus.sh --harness claude|codex|kimi --condition LABEL --repo CROSSREV [--confirmed] [--sequence N] [--runs N]"
    printf '%s\n' "       run-corpus.sh --preflight [--settings-root ROOT] [--results-root DIR]"
}

die() {
    printf '%s\n' "$*" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --harness) HARNESS="$2"; shift 2 ;;
        --condition) CONDITION="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --results-root) RESULTS_ROOT="$2"; shift 2 ;;
        --settings-root) SETTINGS_ROOT="$2"; shift 2 ;;
        --sequence) SEQUENCE="$2"; shift 2 ;;
        --runs) RUNS="$2"; shift 2 ;;
        --preflight) PREFLIGHT=true; shift ;;
        --confirmed) CONFIRMED=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

write_controls() {
    mkdir -p "$RESULTS_ROOT"
    python3 - "$SETTINGS_ROOT" "$RESULTS_ROOT/controls.json" <<'PY'
import json
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1]) if sys.argv[1] else Path.home()
test_layout = bool(sys.argv[1])
claude_path = root / ("claude/settings.json" if test_layout else ".claude/settings.json")
codex_path = root / ("codex/config.toml" if test_layout else ".codex/config.toml")
kimi_path = root / ("kimi/config.toml" if test_layout else ".kimi-code/config.toml")

claude = json.loads(claude_path.read_text(encoding="utf-8"))
codex = tomllib.loads(codex_path.read_text(encoding="utf-8"))
kimi = tomllib.loads(kimi_path.read_text(encoding="utf-8"))
controls = {
    "claude": {
        "model": claude.get("model"),
        "effort": claude.get("effortLevel"),
        "approval_mode": (claude.get("permissions") or {}).get("defaultMode"),
        "output_style": claude.get("outputStyle"),
        "source": str(claude_path),
    },
    "codex": {
        "model": codex.get("model"),
        "effort": codex.get("model_reasoning_effort"),
        "approval_mode": codex.get("approval_policy") or "CLI default; no user-config override",
        "source": str(codex_path),
    },
    "kimi": {
        "model": kimi.get("default_model"),
        "effort": (kimi.get("thinking") or {}).get("effort"),
        "approval_mode": "prompt mode auto-approves tool calls",
        "source": str(kimi_path),
    },
}
Path(sys.argv[2]).write_text(json.dumps(controls, indent=2) + "\n", encoding="utf-8")
for harness, control in controls.items():
    print(f"{harness}: model={control['model']}; effort={control['effort']}; approval={control['approval_mode']}")
PY
}

write_controls
if "$PREFLIGHT"; then
    printf '%s\n' "No corpus launched. Review eval/results/controls.json, then re-run with --confirmed."
    exit 0
fi

case "$HARNESS" in
    claude|codex|kimi) ;;
    *) die "--harness must be claude, codex, or kimi" ;;
esac
[[ -n "$CONDITION" ]] || die "--condition is required"
"$CONFIRMED" || die "refusing to launch a corpus condition without --confirmed"
[[ -n "$REPO" ]] || die "--repo must name the CrossRev checkout"
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "--repo is not a git checkout: $REPO"
git -C "$REPO" rev-parse --verify "$PINNED_CROSSREV_COMMIT^{commit}" >/dev/null

if [[ -z "$RUNS" ]]; then
    if [[ "$HARNESS" == "claude" ]]; then
        RUNS=3
    else
        RUNS=1
    fi
fi
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || die "--runs must be a positive integer"

reset_crossrev() {
    git -C "$REPO" reset --hard "$PINNED_CROSSREV_COMMIT" >/dev/null
    git -C "$REPO" clean -fd >/dev/null
    [[ "$(git -C "$REPO" rev-parse HEAD)" == "$PINNED_CROSSREV_COMMIT"* ]] || die "CrossRev did not reset to $PINNED_CROSSREV_COMMIT"
}

copy_claude_session() {
    local session_id="$1" destination="$2" source
    source="$(find "$(python3 -c 'from pathlib import Path; print(Path.home() / ".claude/projects")')" -type f -name "*$session_id.jsonl" -print -quit)"
    [[ -n "$source" ]] || die "could not locate Claude transcript for $session_id"
    cp "$source" "$destination"
}

copy_codex_session() {
    local session_id="$1" destination="$2"
    python3 - "$session_id" "$destination" <<'PY'
import json
import sys
from pathlib import Path

session_id, destination = sys.argv[1:]
root = Path.home() / ".codex/sessions"
for path in root.rglob("*.jsonl"):
    try:
        first = json.loads(path.read_text(encoding="utf-8", errors="ignore").splitlines()[0])
    except (IndexError, json.JSONDecodeError):
        continue
    payload = first.get("payload") or {}
    if payload.get("session_id") == session_id or payload.get("id") == session_id:
        Path(destination).write_text(path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        raise SystemExit(0)
raise SystemExit(f"could not locate Codex transcript for {session_id}")
PY
}

copy_kimi_session() {
    local source="$1" destination="$2"
    [[ -f "$source" ]] || die "could not locate Kimi transcript: $source"
    cp "$source" "$destination"
}

json_value() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

path, wanted = sys.argv[1:]
def find(value):
    if isinstance(value, dict):
        if isinstance(value.get(wanted), str):
            return value[wanted]
        for child in value.values():
            found = find(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find(child)
            if found:
                return found
    return None

for line in open(path, encoding="utf-8", errors="ignore"):
    try:
        result = find(json.loads(line))
    except json.JSONDecodeError:
        continue
    if result:
        print(result)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

kimi_wire_after() {
    local marker="$1"
    python3 - "$marker" <<'PY'
import sys
from pathlib import Path

marker = Path(sys.argv[1]).stat().st_mtime_ns
root = Path.home() / ".kimi-code/sessions"
candidates = [path for path in root.glob("**/agents/main/wire.jsonl") if path.stat().st_mtime_ns >= marker]
if not candidates:
    raise SystemExit(1)
print(max(candidates, key=lambda path: path.stat().st_mtime_ns))
PY
}

run_sequence() {
    local corpus="$1" run_number="$2"
    local name output_dir turn prompt session_id kimi_marker kimi_wire
    name="$(basename "$corpus" .txt)"
    output_dir="$RESULTS_ROOT/$CONDITION/$HARNESS/$name/run-$run_number"
    mkdir -p "$output_dir"
    reset_crossrev
    turn=""
    prompt=""
    session_id=""
    kimi_wire=""

    run_turn() {
        local raw="$output_dir/turn-$(printf '%02d' "$turn").jsonl"
        local stderr="$output_dir/turn-$(printf '%02d' "$turn").stderr"
        case "$HARNESS" in
            claude)
                if [[ -z "$session_id" ]]; then
                    session_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
                    (cd "$REPO" && claude --print --output-format stream-json --verbose --session-id "$session_id" "$prompt") >"$raw" 2>"$stderr"
                else
                    (cd "$REPO" && claude --print --output-format stream-json --verbose --resume "$session_id" "$prompt") >"$raw" 2>"$stderr"
                fi
                ;;
            codex)
                if [[ -z "$session_id" ]]; then
                    (cd "$REPO" && codex exec --json "$prompt") >"$raw" 2>"$stderr"
                    session_id="$(json_value "$raw" thread_id)"
                else
                    (cd "$REPO" && codex exec resume --json "$session_id" "$prompt") >"$raw" 2>"$stderr"
                fi
                ;;
            kimi)
                if [[ -z "$session_id" ]]; then
                    kimi_marker="$output_dir/kimi-start"
                    : >"$kimi_marker"
                    (cd "$REPO" && kimi --prompt "$prompt" --output-format stream-json) >"$raw" 2>"$stderr"
                    kimi_wire="$(kimi_wire_after "$kimi_marker")"
                    session_id="$(basename "$(dirname "$(dirname "$(dirname "$kimi_wire")")")")"
                    session_id="${session_id#session_}"
                else
                    (cd "$REPO" && kimi --session "$session_id" --prompt "$prompt" --output-format stream-json) >"$raw" 2>"$stderr"
                fi
                ;;
        esac
    }

    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^###\ turn\ ([0-9]+)$ ]]; then
            if [[ -n "$turn" ]]; then
                run_turn
            fi
            turn="${BASH_REMATCH[1]}"
            prompt=""
        elif [[ -n "$turn" ]]; then
            prompt+="$line"$'\n'
        fi
    done < "$corpus"
    [[ -n "$turn" ]] || die "no corpus turns found in $corpus"
    run_turn

    printf '%s\n' "$session_id" >"$output_dir/session-id.txt"
    case "$HARNESS" in
        claude) copy_claude_session "$session_id" "$output_dir/claude-session.jsonl" ;;
        codex) copy_codex_session "$session_id" "$output_dir/codex-session.jsonl" ;;
        kimi) copy_kimi_session "$kimi_wire" "$output_dir/kimi-session.jsonl" ;;
    esac
    python3 - "$output_dir/manifest.json" "$HARNESS" "$CONDITION" "$name" "$run_number" "$session_id" <<'PY'
import json
import sys
from pathlib import Path
path, harness, condition, sequence, run, session_id = sys.argv[1:]
Path(path).write_text(json.dumps({"harness": harness, "condition": condition, "sequence": sequence, "run": int(run), "session_id": session_id, "target_commit": "c72d978"}, indent=2) + "\n", encoding="utf-8")
PY
    reset_crossrev
}

corpus_files=()
while IFS= read -r corpus_file; do
    corpus_files+=("$corpus_file")
done < <(find "$CORPUS_DIR" -maxdepth 1 -type f -name '[0-9][0-9]-*.txt' -print | sort)
[[ ${#corpus_files[@]} -eq 8 ]] || die "expected eight corpus sequences, found ${#corpus_files[@]}"
for corpus in "${corpus_files[@]}"; do
    if [[ -n "$SEQUENCE" && "$(basename "$corpus")" != "$SEQUENCE"* ]]; then
        continue
    fi
    for ((run_number = 1; run_number <= RUNS; run_number++)); do
        run_sequence "$corpus" "$run_number"
    done
done

# Measure this run's blocking-violation rate and emit the summary JSON the
# telemetry dashboard reads. The rate is derived from the transcripts just
# captured, never carried over from an earlier run.
python3 - "$EVAL_DIR" "$RESULTS_ROOT" "$CONDITION" "$HARNESS" "$RUNS" <<'PY'
import datetime
import importlib.util
import json
import re
import statistics
import sys
from pathlib import Path

eval_dir = Path(sys.argv[1])
results_root = Path(sys.argv[2])
condition, harness, runs = sys.argv[3], sys.argv[4], int(sys.argv[5])

sys.path.insert(0, str(eval_dir.parent / "lib"))
import linter  # noqa: E402

# The extractor's filename contains a hyphen, so load it by path.
spec = importlib.util.spec_from_file_location("extract_transcripts", eval_dir / "extract-transcripts.py")
extract_transcripts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract_transcripts)

WORD = re.compile(r"[A-Za-z0-9'’-]+")


def final_turn_measurement(transcript):
    """Blocking findings per 1,000 qualifying words in the last turn's chat."""
    streams = extract_transcripts.extract_file(harness, transcript)
    chat = streams.get("chat") or {}
    if not chat:
        return None
    turn = max(chat)
    text = "\n\n".join(chat[turn])
    if not text.strip():
        return None
    words = len(WORD.findall(linter.exclude_markdown(text)))
    if not words:
        return None
    blocking = sum(1 for f in linter.lint(text) if f.severity == "error")
    return {"turn": turn, "words": words, "blocking": blocking, "rate": round(blocking / words * 1000, 2)}


transcript_name = {"claude": "claude-session.jsonl", "codex": "codex-session.jsonl", "kimi": "kimi-session.jsonl"}[harness]
measured = []
for transcript in sorted((results_root / condition / harness).glob(f"*/run-*/{transcript_name}")):
    try:
        entry = final_turn_measurement(transcript)
    except (OSError, ValueError, KeyError):
        entry = None
    if entry is not None:
        entry["sequence"] = transcript.parent.parent.name
        entry["run"] = transcript.parent.name
        measured.append(entry)

if not measured:
    print("no transcripts measured; skipping summary JSON", file=sys.stderr)
    raise SystemExit(0)

today = datetime.datetime.now().strftime("%Y-%m-%d")
data = {
    "rate": round(statistics.median([item["rate"] for item in measured]), 2),
    "date": today,
    "statistic": "median across sequence runs of blocking findings per 1,000 words at the final turn",
    "source": f"eval/results/{condition}-results.md",
    "condition": condition,
    "harness": harness,
    "runs_per_sequence": runs,
    "measured": measured,
}
# Overwrite rather than skip: a second condition on the same day must not leave
# the first run's rate in place under this date.
(results_root / f"{today}-summary.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"summary: rate={data['rate']} across {len(measured)} sequence runs ({condition}/{harness})")
PY

