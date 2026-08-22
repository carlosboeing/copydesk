#!/usr/bin/env python3
"""The guided setup wizard, flags, proof run, and uninstaller for CopyDesk."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence, TextIO

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "lib"))

import adapters
import apply
import config as config_mod
import guidance
import hook
import instructions
import jsonc
import linter
import prompt
import styles


class Preset(NamedTuple):
    label: str
    consequence: str
    style: str
    verbosity: str
    guidance: tuple[str, ...]


_CUSTOMIZE = Preset("Customize…", "pick each setting yourself.", "", "", ())

PRESETS: dict[str, list[Preset]] = {
    "chat": [
        Preset("Short and direct", "answer first, one reason, stop.",
               "plain", "low", ("recommendations", "direction", "progress")),
        Preset("More explanatory", "defines terms, adds context.",
               "general", "medium", ("recommendations", "direction", "progress")),
        Preset("Thorough", "full reasoning, every step shown.",
               "plain", "high", ("recommendations", "direction", "progress")),
        _CUSTOMIZE,
    ],
    "documents": [
        Preset("Clear and complete", "plain wording, full detail.",
               "plain", "high", ("recommendations",)),
        Preset("For any reader", "every term explained.",
               "general", "high", ("recommendations",)),
        Preset("Formal", "flowing prose, for publication.",
               "editorial", "high", ("recommendations",)),
        _CUSTOMIZE,
    ],
    "commits": [
        Preset("Subject only", "one line saying what changed.", "engineer", "low", ()),
        Preset("Subject and body", "that line, plus why.", "engineer", "medium", ()),
        _CUSTOMIZE,
    ],
    "reviews": [
        Preset("Direct and specific", "names the file and line.", "plain", "medium", ("pushback",)),
        Preset("Thorough", "adds the counter-case.", "plain", "high", ("pushback", "alternatives")),
        _CUSTOMIZE,
    ],
}

CHANNEL_PRESELECTED = {"chat": True, "documents": True, "commits": True, "reviews": False}

EXAMPLES: dict[tuple[str, str, str], str] = {
    ("chat", "plain", "low"): "In .env - set the port, then restart the server.",
    ("chat", "general", "medium"): (
        "The port is set in .env, a file of settings the app reads at startup. "
        "Change the port there, then restart the server so it reads the new value."
    ),
    ("chat", "plain", "high"): (
        "In .env - set the port, then restart. The server reads .env once at "
        "startup, so a running process keeps the old value. Nothing else "
        "references the port."
    ),
    ("documents", "plain", "high"): (
        "## Run it locally\n\nInstall with `npm install`, then start with "
        "`npm start`. The app opens at `http://localhost:3000`."
    ),
    ("documents", "general", "high"): (
        "## Run it locally\n\nRun `npm install`, which downloads the code the "
        "app depends on. Then run `npm start`. Open `http://localhost:3000` in "
        "a browser to see it."
    ),
    ("documents", "editorial", "high"): (
        "## Run it locally\n\nInstalling takes one command and starting takes "
        "another. Run `npm install`, then `npm start`, and the app is waiting "
        "at `http://localhost:3000`."
    ),
    ("commits", "engineer", "low"): "Expire reset tokens after first use.",
    ("commits", "engineer", "medium"): (
        "Expire reset tokens after first use.\n\nA used token stayed valid for "
        "an hour, so a leaked reset link worked twice."
    ),
    ("reviews", "plain", "medium"): "auth.ts:42 - the reset token never expires. Add an expiry check.",
    ("reviews", "plain", "high"): (
        "auth.ts:42 - the reset token never expires. Add an expiry check.\n"
        "A short expiry is the usual fix. Single-use marking also works, and "
        "costs a write on every redemption."
    ),
}

COPY = {
    "intro": (
        "CopyDesk sets up writing rules for your AI coding tools.\n\n"
        "It configures plain style instructions and installs gate hooks."
    ),
    "tools": "Which AI tools should CopyDesk configure?",
    # Git is asked separately. It is not an AI tool, and it is the only
    # question whose answer touches the current directory rather than the
    # home directory, which is worth saying rather than implying.
    "git": "Check your commit messages in this repository too?",
    "git_yes": "Install a commit-msg hook here",
    "git_yes_because": "git rejects a message that breaks a rule, so you fix it and retry.",
    "git_no": "Leave this repository alone",
    "git_no_because": "nothing is written outside your home directory, and nothing here changes.",
    "where": "Where should CopyDesk apply your writing rules?",
    "review": "These files will change:",
    "confirm": "Apply these changes?",
    "progress": "Configuring tools and writing files...",
    "outro_success": (
        "Done. The gate is live - it just blocked a sample file to prove it.\n"
        "Undo anytime with: copydesk uninstall"
    ),
    "outro_cancelled": "Cancelled. Nothing was written.",
    "outro_hook_next": "Other repositories: run `copydesk hook add` inside each one.",
    "outro_no_tools": (
        "No supported tools were found on this machine.\n"
        "Install one first, then run copydesk setup again."
    ),
    "rerun": "CopyDesk is already installed. Choose an action:",
    "non_tty": "Run setup in an interactive terminal, or pass --defaults.",
}


def _missing(name: str) -> str:
    """Why a tool cannot be picked. Git has no executable to look for."""
    return "not a git repository" if name == "git" else "not found on this machine"


def tool_line(name: str, available: bool) -> str:
    adapter = adapters.REGISTRY[name]
    if available:
        return f"{adapter.label} - {adapter.installs}"
    return f"{adapter.label} - {_missing(name)}"


_SAMPLE = "Great question - let me walk you through this robust and comprehensive change.\n"

PROOF_SESSION_ID = "copydesk-setup-proof"


def prove(home: Path) -> tuple[bool, str]:
    """Lint a known-bad sample through the installed gate.

    A scan returning nothing proves nothing until a known-present pattern
    returns a hit through the same command form. Install gets the same rule.
    """
    hook = home / ".claude" / "hooks" / "copydesk" / "gate.sh"
    if not hook.is_file():
        return False, "the gate hook is not installed"

    # The proof reuses one session id, so its retry state outlives any single
    # setup run. Deleting it first makes every proof a first attempt: the
    # gate's identical-content escape valve never fires on a healthy install,
    # and entries from earlier homes cannot pile up behind the session. The
    # path comes from the linter so both sides resolve one location.
    state_path = linter._state_path(linter._state_directory(), PROOF_SESSION_ID)
    undeleted = ""
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        # Setup never crashes on a state directory it cannot write, so the
        # swallow stays. Absence is the healthy case; a permission error or
        # a directory at the path leaves retry state behind that decides
        # what this proof runs against, so the survivor joins the result.
        undeleted = f"; previous proof state at {state_path} could not be deleted ({error})"

    payload = json.dumps({
        "session_id": PROOF_SESSION_ID,
        "tool_name": "Write",
        "tool_input": {"file_path": str(home / "copydesk-sample.md"), "content": _SAMPLE},
    })
    env = dict(os.environ)
    py_dir = str(Path(sys.executable).parent)
    needed = [py_dir, "/bin", "/usr/bin", "/usr/local/bin"]
    current_paths = env.get("PATH", "").split(os.pathsep)
    for p in needed:
        if p not in current_paths:
            current_paths.insert(0, p)
    env["PATH"] = os.pathsep.join(current_paths)
    bash_path = shutil.which("bash", path=env["PATH"]) or "/bin/bash"

    result = subprocess.run(
        [bash_path, str(hook)], input=payload, capture_output=True, text=True, env=env,
    )
    blocked = result.returncode == 2
    reason = (result.stderr or result.stdout).strip().splitlines()
    text = reason[0] if reason else "no finding reported"
    return blocked, text + undeleted


# The marker and the git directory answers live in hook.py, which owns hook
# management across repositories. These aliases keep the names existing
# callers and tests import from here.
HOOK_MARKER = hook.HOOK_MARKER

hooks_directory = hook.hooks_directory


class HookResult(NamedTuple):
    installed: bool
    message: str


class HookPlan(NamedTuple):
    write: Optional[apply.Write]  # None when there is nothing to write
    message: str
    ok: bool = True               # False means setup must stop before writing


def _foreign_hook_message(target: Path) -> str:
    return (
        f"skipped  {target} already exists\n"
        f"         Run `copydesk hook add` in this repository to chain CopyDesk into it."
    )


def plan_commit_hook(hooks_dir: Path) -> HookPlan:
    """Decide the commit-msg write before any of the plan is applied.

    Deciding needs to read the existing hook, and a hook someone else wrote is
    left alone. Doing that here means the write itself joins the setup plan,
    so a hook that cannot be written rolls back every earlier write with it.
    Installing it after the plan applied made a failure there leave the home
    directory changed and setup still reporting success.
    """
    target = hooks_dir / "commit-msg"
    source = BUNDLE_ROOT / "git-hooks" / "commit-msg"
    try:
        hook_src = source.read_text(encoding="utf-8")
    except OSError as error:
        return HookPlan(None, f"cannot read {source}: {error}", ok=False)
    if not target.is_file():
        return HookPlan(apply.Write(target, hook_src), f"installed {target}")
    try:
        existing = target.read_text(encoding="utf-8")
    except OSError as error:
        return HookPlan(None, f"cannot read {target}: {error}", ok=False)
    if HOOK_MARKER in existing:
        return HookPlan(apply.Write(target, hook_src), f"updated {target}")
    return HookPlan(None, _foreign_hook_message(target))


def install_commit_hook(cwd: Path) -> HookResult:
    """Install the hook on its own. `run_setup` goes through the plan instead,
    so that a failure there rolls back with every other write."""
    hooks_dir = hooks_directory(cwd)
    if hooks_dir is None:
        return HookResult(False, f"{cwd} is not a git repository")
    planned = plan_commit_hook(hooks_dir)
    if planned.write is None:
        return HookResult(False, planned.message)
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        planned.write.path.write_text(planned.write.content, encoding="utf-8")
        os.chmod(planned.write.path, 0o755)
    except OSError as error:
        return HookResult(False, f"cannot write {planned.write.path}: {error}")
    return HookResult(True, planned.message)



def _format_config(channels_config: dict, selected_agents: list[str]) -> str:
    """Format the generated user config with schema line and comments."""
    channels_dict = {}
    for name in ("chat", "documents", "commits", "reviews"):
        if name in channels_config:
            channels_dict[name] = channels_config[name]

    doc = {
        "$schema": instructions.SCHEMA_URL,
        "version": 1,
        "channels": channels_dict,
        "agents": selected_agents,
    }
    raw = json.dumps(doc, indent=2)
    lines = raw.splitlines()
    out = []
    for line in lines:
        if line.strip().startswith('"channels":'):
            out.append('  // Channels configure how each kind of writing reads.')
        elif line.strip().startswith('"agents":'):
            out.append('  // Agents configure which tools CopyDesk sets up.')
        out.append(line)
    return "\n".join(out) + "\n"


def _default_channels() -> dict:
    """The settings `--defaults` picks: each channel's first preset, or off."""
    settings: dict = {}
    for name, preselected in CHANNEL_PRESELECTED.items():
        if not preselected:
            settings[name] = {"enabled": False}
            continue
        default_preset = PRESETS[name][0]
        entry: dict = {
            "enabled": True,
            "style": default_preset.style,
            "verbosity": default_preset.verbosity,
        }
        if default_preset.guidance:
            entry["guidance"] = {g: True for g in default_preset.guidance}
        settings[name] = entry
    return settings


def _resolved_from(config_body: str) -> dict:
    """Resolve exactly the config this run is about to write.

    Reading it back through `config.resolve` is what makes an installed
    output style match what doctor re-renders. A hand-built dict skips the
    layer merge, and guidance merges key by key there, so the two would
    differ by a key nobody chose and every install would read as drift.

    The arguments match `linter.user_layer()` deliberately: that is the
    function doctor compares against.
    """
    with tempfile.TemporaryDirectory() as directory:
        staged = Path(directory) / "config.json"
        staged.write_text(config_body, encoding="utf-8")
        return _resolve_user_config(staged)


def _resolve_user_config(path: Path) -> dict:
    """One config file, resolved the way `linter.user_layer()` resolves it."""
    return config_mod.resolve(
        BUNDLE_ROOT / "rules", None,
        user_path=path, project_path=None, local_path=None, channel="chat",
    )


def _read_config(path: Path) -> Optional[dict]:
    """The user's own config, and which of three cases this is.

    `{}` means there is no file, so there is nothing to preserve. A dict means
    a file that read, whatever keys it holds. `None` means a file is there and
    could not be read, which is the case setup must refuse rather than write
    over: its contents were never understood, so no plan can preserve them.
    """
    if not path.is_file():
        return {}
    try:
        document = json.loads(jsonc.strip_comments(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _read_settings(path: Path) -> tuple[Optional[dict], str]:
    """A harness settings file, and why it could not be read.

    `({}, "")` is no file, so there is nothing to preserve. `(document, "")` is
    a file that parsed. `(None, reason)` is a file that exists and did not,
    which setup refuses rather than writes over: its keys were never
    understood, so a plan built from an empty document drops every one of them.
    """
    if not path.is_file():
        return {}, ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, error.strerror or str(error)
    try:
        document = json.loads(jsonc.strip_comments(text))
    except ValueError as error:
        # json.JSONDecodeError and jsonc.UnterminatedComment are both
        # ValueError, and both name the position the user is looking at.
        return None, str(error)
    if not isinstance(document, dict):
        return None, "the top level is not an object"
    return document, ""


def _build_plan(
    home: Path,
    config_path: Path,
    config_body: str,
    selected_tools: list[str],
    resolved_config: dict,
    settings_doc: dict,
    write_config: bool = True,
) -> apply.Plan:
    # Repair rebuilds what CopyDesk generates. The config is the user's own
    # writing, so repairing leaves it alone rather than restoring defaults
    # over it.
    writes = [apply.Write(config_path, config_body)] if write_config else []

    if "claude-code" in selected_tools:
        hooks_dir = home / ".claude" / "hooks" / "copydesk"
        gate_src = BUNDLE_ROOT / "hooks" / "gate.sh"
        reminder_src = BUNDLE_ROOT / "hooks" / "reminder.sh"
        writes.append(apply.Write(hooks_dir / "gate.sh", gate_src.read_text(encoding="utf-8")))
        writes.append(apply.Write(hooks_dir / "reminder.sh", reminder_src.read_text(encoding="utf-8")))

        lib_dir = BUNDLE_ROOT / "lib"
        if lib_dir.is_dir():
            for py_file in lib_dir.glob("*.py"):
                writes.append(apply.Write(hooks_dir / py_file.name, py_file.read_text(encoding="utf-8")))

        rules_dir = BUNDLE_ROOT / "rules"
        if rules_dir.is_dir():
            for json_file in rules_dir.glob("*.json"):
                writes.append(apply.Write(hooks_dir / "rules" / json_file.name, json_file.read_text(encoding="utf-8")))

        # Rendered from the settings this run is about to write, never copied
        # from the repository. A copy carries the default style whatever the
        # user picked, and doctor re-renders from their config, so a fresh
        # install would read as drift the moment it finished.
        out_styles_dir = home / ".claude" / "output-styles"
        for level in instructions.VERBOSITY_LEVELS:
            writes.append(apply.Write(
                out_styles_dir / f"copydesk-{level}.md",
                instructions.render_output_style(resolved_config, level, writer=instructions.SETUP_WRITER),
            ))

        # The document is read once, by the caller, so that a settings file
        # that will not parse stops setup before the first write rather than
        # being silently replaced here by an empty one.
        settings_path = home / ".claude" / "settings.json"
        hooks = settings_doc.setdefault("hooks", {})
        pre_tool = hooks.setdefault("PreToolUse", [])
        pre_tool = [
            e for e in pre_tool
            if not any("copydesk" in str(h.get("command", "")) for h in e.get("hooks", []))
        ]
        pre_tool.append({
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": str(hooks_dir / "gate.sh")}],
        })
        hooks["PreToolUse"] = pre_tool

        prompt_submit = hooks.setdefault("UserPromptSubmit", [])
        prompt_submit = [
            e for e in prompt_submit
            if not any("copydesk" in str(h.get("command", "")) for h in e.get("hooks", []))
        ]
        prompt_submit.append({
            "matcher": ".*",
            "hooks": [{"type": "command", "command": str(hooks_dir / "reminder.sh")}],
        })
        hooks["UserPromptSubmit"] = prompt_submit

        writes.append(apply.Write(settings_path, json.dumps(settings_doc, indent=2) + "\n"))

    # One instruction file per harness. Which channels a file carries is
    # decided per real file, after symlinks resolve: where two harnesses
    # share one file through a symlink, one write lands and carries what
    # both read, chat included. A file only Claude Code reads leaves chat
    # out here rather than delivering it twice — the output style has it.
    chat_by_real: dict[Path, bool] = {}
    for tool in selected_tools:
        if tool == "git":
            continue
        adapter = adapters.REGISTRY.get(tool)
        if not adapter or not adapter.instruction_file:
            continue
        target_file = home / adapter.home.replace("~/", "") / adapter.instruction_file
        real = target_file.resolve()
        chat_by_real[real] = chat_by_real.get(real, False) or tool != "claude-code"

    for real, wants_chat in chat_by_real.items():
        block = instructions.render_agents_block(resolved_config, include_chat=wants_chat)
        if not block:
            continue
        orig = ""
        if real.is_file():
            orig = real.read_text(encoding="utf-8")
        writes.append(apply.Write(real, apply.splice_marked_block(orig, block)))

    return apply.Plan(writes=writes)


def run_setup(argv: list[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(prog="copydesk setup", description="Set up CopyDesk.")
    parser.add_argument("--defaults", action="store_true", help="accept default options without prompting")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="print planned changes and exit")
    parser.add_argument("--repair", action="store_true", help="repair existing installation")
    args = parser.parse_args(argv)

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout

    copydesk_home = Path(os.environ.get("COPYDESK_HOME", Path.home())).expanduser().resolve()
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", copydesk_home / ".config")).expanduser().resolve()
    config_file = xdg_config / "copydesk" / "config.json"

    # Detect available tools. Git is not a harness and has no executable to
    # find, so its test is whether this directory is a repository to install
    # a commit-msg hook into. It is also not a harness for the question below:
    # a machine with a repository and no assistant still has nothing to set up.
    repository = Path.cwd()
    repository_hooks = hooks_directory(repository)
    in_repository = repository_hooks is not None
    harness_tools = [
        name for name in adapters.REGISTRY
        if name != "git" and adapters.detect(name, copydesk_home)
    ]
    if not harness_tools:
        out_stream.write(COPY["outro_no_tools"] + "\n")
        out_stream.flush()
        return 0
    available_tools = [
        name for name in adapters.REGISTRY
        if name in harness_tools or (name == "git" and in_repository)
    ]

    interactive = prompt.is_interactive(in_stream) and not args.defaults

    if config_file.is_file() and not args.repair and not args.defaults:
        fork_options = [
            prompt.Option("Change settings", "update your writing rules"),
            prompt.Option("Repair the install", "rewrite hooks and styles"),
            prompt.Option("Start over", "reset settings to defaults"),
        ]
        try:
            choice = prompt.select(COPY["rerun"], fork_options, 0, stdin=in_stream, stdout=out_stream)
            if choice == 1:
                args.repair = True
        except prompt.Cancelled:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0

    channels_settings: dict = {}
    selected_tools = available_tools

    existing_config = _read_config(config_file) if args.repair else {}
    if existing_config is None:
        out_stream.write(
            f"error  {config_file} exists and cannot be read. "
            "Fix it or move it aside, then run setup again.\n"
        )
        return 1
    # Any config that reads is the user's own writing, whatever keys it holds.
    # Testing for `channels` alone discarded one carrying only rules, paths,
    # gate or telemetry settings. Repairing with no config at all has nothing
    # to preserve, so it falls through to the defaults below and writes one.
    repairing_settings = bool(existing_config)

    if repairing_settings:
        channels_settings = existing_config.get("channels") or {}
        recorded = existing_config.get("agents")
        if isinstance(recorded, list):
            kept = [name for name in recorded if name in adapters.REGISTRY]
            if kept:
                selected_tools = kept
    elif not args.defaults and not args.repair and interactive:
        out_stream.write(COPY["intro"] + "\n\n")
        out_stream.flush()

        # Every question below is a step. `prompt.ask_in_order` runs a list of
        # them and sends Escape back to the one before, and a step that is
        # itself a list nests: Escape at its first question re-raises, which
        # lands the user on the question that opened the group. Escape at the
        # very first question has nothing behind it, so it cancels.
        #
        # Each step reads its own last answer for its default, so going back
        # and forward again shows what was chosen rather than resetting.
        answers: dict = {}

        # Git is asked on its own. It is not an AI tool, and its hook goes
        # into this repository rather than the home directory.
        harness_names = [name for name in adapters.REGISTRY if name != "git"]

        def ask_tools() -> None:
            options = [
                prompt.Option(
                    adapters.REGISTRY[name].label,
                    adapters.REGISTRY[name].installs if name in available_tools else _missing(name),
                    name in available_tools,
                )
                for name in harness_names
            ]
            default = answers.get(
                "tool_indices",
                [i for i, name in enumerate(harness_names) if name in available_tools],
            )
            chosen = prompt.multiselect(
                COPY["tools"], options, default, stdin=in_stream, stdout=out_stream
            )
            answers["tool_indices"] = chosen
            answers["tools"] = [harness_names[i] for i in chosen]

        def ask_git() -> None:
            if not in_repository:
                answers["git"] = False
                return
            options = [
                prompt.Option(COPY["git_yes"], COPY["git_yes_because"], True),
                prompt.Option(COPY["git_no"], COPY["git_no_because"], True),
            ]
            chosen = prompt.select(
                COPY["git"], options, answers.get("git_index", 0), stdin=in_stream, stdout=out_stream
            )
            answers["git_index"] = chosen
            answers["git"] = chosen == 0

        channel_names = ["chat", "documents", "commits", "reviews"]
        channel_labels = {
            "chat": ("Chat replies", "conversation with coding assistants"),
            "documents": ("Documents", "markdown files and documentation"),
            "commits": ("Commit messages", "git commit subjects and bodies"),
            "reviews": ("Pull request reviews", "review comments and summaries"),
        }

        def ask_channels() -> None:
            options = [
                prompt.Option(channel_labels[ch][0], channel_labels[ch][1], True)
                for ch in channel_names
            ]
            default = answers.get(
                "channel_indices",
                [i for i, ch in enumerate(channel_names) if CHANNEL_PRESELECTED[ch]],
            )
            chosen = prompt.multiselect(
                COPY["where"], options, default, stdin=in_stream, stdout=out_stream
            )
            answers["channel_indices"] = chosen
            answers["channels"] = [channel_names[i] for i in chosen]

        def channel_step(ch: str) -> Callable[[], None]:
            """One channel's questions, as a step in the list above.

            The preset question and the Customize sub-questions are their own
            group, so Escape inside Customize returns to the preset question
            rather than to the channel before this one.
            """
            presets = PRESETS[ch]

            def ask_preset() -> None:
                options = [prompt.Option(pre.label, pre.consequence, True) for pre in presets]
                chosen = prompt.select(
                    f"{channel_labels[ch][0]} - how should they read?",
                    options,
                    answers.get(f"{ch}.preset", 0),
                    stdin=in_stream,
                    stdout=out_stream,
                )
                answers[f"{ch}.preset"] = chosen

            def ask_style() -> None:
                options = [
                    prompt.Option(name, styles.DESCRIPTIONS.get(name, ""))
                    for name in styles.STYLE_NAMES
                ]
                answers[f"{ch}.style"] = prompt.select(
                    f"Which style? (channels.{ch}.style)",
                    options,
                    answers.get(f"{ch}.style", 0),
                    stdin=in_stream,
                    stdout=out_stream,
                )

            def ask_verbosity() -> None:
                options = [prompt.Option(v, "") for v in instructions.VERBOSITY_LEVELS]
                answers[f"{ch}.verbosity"] = prompt.select(
                    f"How much detail? (channels.{ch}.verbosity)",
                    options,
                    answers.get(f"{ch}.verbosity", 0),
                    stdin=in_stream,
                    stdout=out_stream,
                )

            def ask_guidance() -> None:
                ids = list(guidance.IDS)
                options = [prompt.Option(gid, guidance.SNIPPETS.get(gid, "")) for gid in ids]
                answers[f"{ch}.guidance"] = prompt.multiselect(
                    f"Guidance deliverables (channels.{ch}.guidance)",
                    options,
                    answers.get(f"{ch}.guidance", []),
                    stdin=in_stream,
                    stdout=out_stream,
                )

            def ask_customize() -> None:
                if presets[answers[f"{ch}.preset"]].label != "Customize\u2026":
                    return
                prompt.ask_in_order([ask_style, ask_verbosity, ask_guidance])

            def run() -> None:
                if ch not in answers["channels"]:
                    channels_settings[ch] = {"enabled": False}
                    return
                prompt.ask_in_order([ask_preset, ask_customize])
                chosen_preset = presets[answers[f"{ch}.preset"]]
                if chosen_preset.label == "Customize\u2026":
                    ids = list(guidance.IDS)
                    channels_settings[ch] = {
                        "enabled": True,
                        "style": styles.STYLE_NAMES[answers[f"{ch}.style"]],
                        "verbosity": instructions.VERBOSITY_LEVELS[answers[f"{ch}.verbosity"]],
                        "guidance": {ids[i]: True for i in answers[f"{ch}.guidance"]},
                    }
                    return
                # `enabled` is written out rather than implied. Reviews ship
                # off, so a channel the user ticked would come back off when
                # the config is read against the defaults.
                ch_dict: dict = {
                    "enabled": True,
                    "style": chosen_preset.style,
                    "verbosity": chosen_preset.verbosity,
                }
                if chosen_preset.guidance:
                    ch_dict["guidance"] = {g: True for g in chosen_preset.guidance}
                channels_settings[ch] = ch_dict

            return run

        def ask_every_channel() -> None:
            prompt.ask_in_order([channel_step(ch) for ch in channel_names])

        try:
            prompt.ask_in_order([ask_tools, ask_git, ask_channels, ask_every_channel])
        except prompt.Cancelled:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0

        selected_tools = list(answers["tools"])
        if answers.get("git"):
            selected_tools.append("git")
    else:
        channels_settings = _default_channels()

    config_body = _format_config(channels_settings, selected_tools)

    try:
        # A repair renders from the config on disk, never from a body rebuilt
        # out of `channels_settings`. Rebuilding drops every key the wizard
        # does not write — rules, paths, gate, telemetry — so the installed
        # styles would differ from what doctor resolves out of the same file.
        resolved_config = (
            _resolve_user_config(config_file) if repairing_settings
            else _resolved_from(config_body)
        )
    except config_mod.ConfigError as error:
        out_stream.write(f"error  {error}\n")
        return 1

    # A settings.json that will not parse is refused rather than written over.
    # Treating it as absent replaced the file with CopyDesk's hooks and nothing
    # else, dropping every key it held, and a successful apply keeps no copy of
    # what was there. Only the harness that owns the file is affected, so
    # setting up Codex alone is not blocked by a broken Claude settings file.
    settings_doc: dict = {}
    if "claude-code" in selected_tools:
        settings_path = copydesk_home / ".claude" / "settings.json"
        read_settings, reason = _read_settings(settings_path)
        if read_settings is None:
            out_stream.write(
                f"error  {settings_path} exists and cannot be read ({reason}). "
                "Fix it or move it aside, then run setup again.\n"
            )
            return 1
        settings_doc = read_settings

    plan = _build_plan(
        copydesk_home, config_file, config_body, selected_tools, resolved_config,
        settings_doc, write_config=not repairing_settings,
    )

    # The commit-msg hook lives in the repository rather than under the home
    # directory, but it joins the same plan: setup is all-or-nothing, so a
    # hook that cannot be written must roll back every write before it.
    hook_plan = None
    if repository_hooks is not None and "git" in selected_tools:
        hook_plan = plan_commit_hook(repository_hooks)
        if not hook_plan.ok:
            out_stream.write(f"error  {hook_plan.message}\n")
            return 1
        if hook_plan.write is not None:
            plan = apply.Plan(writes=[*plan.writes, hook_plan.write])

    # Review panel
    out_stream.write("Configured tools:\n")
    for tool in selected_tools:
        adapter = adapters.REGISTRY[tool]
        out_stream.write(f"  {adapter.label}\n")
    out_stream.write("\n")
    out_stream.write(COPY["review"] + "\n")
    for write in plan.writes:
        out_stream.write(f"  {write.path}\n")
    if hook_plan is not None and hook_plan.write is None:
        # A hook someone else wrote is named here and left alone, so the
        # commits gate never silently replaces a repository's own check.
        out_stream.write(hook_plan.message + "\n")
    out_stream.flush()

    if args.dry_run:
        return 0

    if not args.yes:
        if not interactive:
            out_stream.write(COPY["non_tty"] + "\n")
            return 1
        try:
            confirmed = prompt.confirm(COPY["confirm"], default=True, stdin=in_stream, stdout=out_stream)
        except prompt.Cancelled:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0
        if not confirmed:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0

    res = apply.execute(plan)
    if not res.ok:
        out_stream.write(f"error  {res.message}\n")
        return 1

    if hook_plan is not None and hook_plan.write is not None:
        # git ignores a hook it cannot execute, so a failure here leaves the
        # commits gate silently off. It is reported rather than assumed.
        try:
            os.chmod(hook_plan.write.path, 0o755)
        except OSError as error:
            out_stream.write(f"error  cannot make {hook_plan.write.path} executable: {error}\n")
            return 1
        out_stream.write(hook_plan.message + "\n")
        # Setup wrote every hook currently on disk, and the registry starts
        # empty on upgrade. Recording here is what lets `copydesk hook list`
        # name this repository without `hook add` ever running.
        hook.record_repository(repository, "installed")
        out_stream.write(COPY["outro_hook_next"] + "\n")
    elif hook_plan is not None:
        # A hook someone else wrote stayed in place. Recording the skip makes
        # it visible to `copydesk hook list` rather than silently absent.
        hook.record_repository(repository, "skipped")

    if "claude-code" in selected_tools:
        gate_path = copydesk_home / ".claude" / "hooks" / "copydesk" / "gate.sh"
        reminder_path = copydesk_home / ".claude" / "hooks" / "copydesk" / "reminder.sh"
        if gate_path.is_file():
            os.chmod(gate_path, 0o755)
        if reminder_path.is_file():
            os.chmod(reminder_path, 0o755)

        blocked, reason = prove(copydesk_home)
        if blocked:
            out_stream.write(COPY["outro_success"] + "\n")
        else:
            out_stream.write(f"Setup complete, but proof run failed: {reason}. Run copydesk doctor for details.\n")
    else:
        out_stream.write(COPY["outro_success"] + "\n")

    out_stream.flush()
    return 0


def run_uninstall(argv: list[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    parser = argparse.ArgumentParser(prog="copydesk uninstall", description="Uninstall CopyDesk.")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="print planned removals and exit")
    parser.add_argument("--purge", action="store_true", help="also remove user configuration")
    args = parser.parse_args(argv)

    in_stream = stdin or sys.stdin
    out_stream = stdout or sys.stdout

    copydesk_home = Path(os.environ.get("COPYDESK_HOME", Path.home())).expanduser().resolve()
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", copydesk_home / ".config")).expanduser().resolve()
    config_file = xdg_config / "copydesk" / "config.json"

    targets: list[apply.Target] = []

    # 1. Instruction files with marked blocks
    for name, adapter in adapters.REGISTRY.items():
        if name == "git" or not adapter.instruction_file:
            continue
        inst_file = copydesk_home / adapter.home.replace("~/", "") / adapter.instruction_file
        if inst_file.is_file():
            try:
                text = inst_file.read_text(encoding="utf-8")
                if apply.MARKER_START in text:
                    targets.append(apply.Target(real=inst_file, kind="marked-block"))
            except OSError:
                pass

    # 2. Hook directory
    hooks_dir = copydesk_home / ".claude" / "hooks" / "copydesk"
    if hooks_dir.exists():
        targets.append(apply.Target(real=hooks_dir, kind="created"))

    # 3. Output styles
    out_styles_dir = copydesk_home / ".claude" / "output-styles"
    for level in ("low", "medium", "high"):
        st = out_styles_dir / f"copydesk-{level}.md"
        if st.is_file():
            targets.append(apply.Target(real=st, kind="created"))

    # 4. Settings.json hooks
    settings_path = copydesk_home / ".claude" / "settings.json"
    if settings_path.is_file():
        targets.append(apply.Target(real=settings_path, kind="hook-keys"))

    # 5. This repository's commit-msg hook, when CopyDesk is what wrote it.
    # The marker is the test, so a hook someone else wrote survives. A foreign
    # script carrying CopyDesk's appended block is stripped, never deleted.
    strip_hook: Optional[Path] = None
    repository_hooks = hooks_directory(Path.cwd())
    commit_hook = repository_hooks / "commit-msg" if repository_hooks else None
    if commit_hook is not None and commit_hook.is_file():
        try:
            hook_content = commit_hook.read_text(encoding="utf-8")
            if hook.BLOCK_START in hook_content:
                strip_hook = commit_hook
                commit_hook = None
            elif HOOK_MARKER in hook_content:
                targets.append(apply.Target(real=commit_hook, kind="created"))
            else:
                commit_hook = None
        except OSError:
            commit_hook = None
    else:
        commit_hook = None

    # 6. Purge user config
    if args.purge and config_file.is_file():
        targets.append(apply.Target(real=config_file, kind="created"))

    out_stream.write("These files will be modified or removed:\n")
    for t in targets:
        out_stream.write(f"  {t.real}\n")
    if strip_hook is not None:
        out_stream.write(f"  {strip_hook} (CopyDesk's block stripped, the script stays)\n")
    others_preview = hook.other_entries(Path.cwd())
    if others_preview:
        out_stream.write(
            f"\n{len(others_preview)} other "
            f"{'repository holds' if len(others_preview) == 1 else 'repositories hold'} a CopyDesk hook:\n"
        )
        for entry in others_preview:
            out_stream.write(f"  {Path(entry['hooks_dir']) / entry['hook']}\n")
    out_stream.flush()

    if args.dry_run:
        return 0

    if not args.yes:
        if not prompt.is_interactive(in_stream):
            out_stream.write("Pass --yes to uninstall in a non-interactive shell.\n")
            return 1
        try:
            confirmed = prompt.confirm("Proceed with uninstall?", default=True, stdin=in_stream, stdout=out_stream)
        except prompt.Cancelled:
            out_stream.write("Uninstall cancelled.\n")
            return 0
        if not confirmed:
            out_stream.write("Uninstall cancelled.\n")
            return 0

    # Every harness home the prune must stop below, plus the config root.
    # A directory CopyDesk created and emptied goes; ~/.claude never does.
    homes = [
        copydesk_home / adapter.home.replace("~/", "")
        for adapter in adapters.REGISTRY.values()
        if adapter.home != "."
    ]
    homes.append(config_file.parent.parent)
    if repository_hooks is not None:
        # git owns its hooks directory. Removing the last hook in it must not
        # take the directory with it.
        homes.append(repository_hooks)
    res = apply.remove_owned(targets, homes=homes)
    if not res.ok:
        out_stream.write(f"error  {res.message}\n")
        return 1

    # A start marker whose region does not match leaves the script alone, the
    # rule `copydesk hook remove` follows. Uninstall must not report success
    # over it: the lines are still in a hook someone else wrote.
    block_stripped = True
    if strip_hook is not None:
        try:
            block_stripped = hook.strip_file(strip_hook)
        except OSError as error:
            out_stream.write(f"error  cannot strip {strip_hook}: {error}\n")
            return 1
        if not block_stripped:
            out_stream.write(
                f"left  {strip_hook} (the marker is present but the block could not be located)\n"
            )

    # Other recorded repositories. Verifying after the removal prunes this
    # repository's own entry, because its hook just left the disk. The
    # question defaults to yes: the closing line has always promised the
    # whole installation can be reversed, and leaving hooks behind is the
    # behaviour being fixed. --yes takes them all without asking.
    others = hook.other_entries(Path.cwd())
    if others:
        take_others = args.yes
        if not take_others:
            try:
                take_others = prompt.confirm(
                    f"Remove the CopyDesk hook from "
                    f"{'this other recorded repository' if len(others) == 1 else f'these {len(others)} other recorded repositories'}?",
                    default=True, stdin=in_stream, stdout=out_stream,
                )
            except prompt.Cancelled:
                take_others = False
        if take_others:
            hook.remove_entries(others, yes=args.yes, stdin=in_stream, stdout=out_stream)
            others = hook.other_entries(Path.cwd())
        if others:
            out_stream.write(
                f"\n{len(others)} "
                f"{'repository still holds' if len(others) == 1 else 'repositories still hold'} a CopyDesk hook:\n"
            )
            for entry in others:
                # The path alone is enough to remove the hook by hand, which
                # matters once no CopyDesk command is left to do it.
                out_stream.write(f"  {Path(entry['hooks_dir']) / entry['hook']}\n")
            out_stream.write("Remove them with `copydesk hook remove --all`, or delete the files above.\n")

    if not block_stripped:
        out_stream.write(
            f"Everything else is gone, but CopyDesk's block is still in {strip_hook}.\n"
            f"Delete the marked lines by hand.\n"
        )
        out_stream.flush()
        return 1

    out_stream.write("Uninstall complete.\n")
    out_stream.flush()
    return 0


def run(argv: list[str], stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> int:
    if argv and argv[0] == "uninstall":
        return run_uninstall(argv[1:], stdin=stdin, stdout=stdout)
    if argv and argv[0] in ("setup", "init"):
        return run_setup(argv[1:], stdin=stdin, stdout=stdout)
    return run_setup(argv, stdin=stdin, stdout=stdout)
