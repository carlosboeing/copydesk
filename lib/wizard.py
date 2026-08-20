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
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, TextIO

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE_ROOT / "lib"))

import adapters
import apply
import config as config_mod
import guidance
import instructions
import jsonc
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
    "where": "Where should CopyDesk apply your writing rules?",
    "review": "These files will change:",
    "confirm": "Apply these changes?",
    "progress": "Configuring tools and writing files...",
    "outro_success": (
        "Done. The gate is live - it just blocked a sample file to prove it.\n"
        "Undo anytime with: copydesk uninstall"
    ),
    "outro_cancelled": "Cancelled. Nothing was written.",
    "outro_no_tools": (
        "No supported tools were found on this machine.\n"
        "Install one first, then run copydesk setup again."
    ),
    "rerun": "CopyDesk is already installed. Choose an action:",
    "non_tty": "Run setup in an interactive terminal, or pass --defaults.",
}


def tool_line(name: str, available: bool) -> str:
    adapter = adapters.REGISTRY[name]
    if available:
        return f"{adapter.label} - {adapter.installs}"
    return f"{adapter.label} - not found on this machine"


_SAMPLE = "Great question - let me walk you through this robust and comprehensive change.\n"


def prove(home: Path) -> tuple[bool, str]:
    """Lint a known-bad sample through the installed gate.

    A scan returning nothing proves nothing until a known-present pattern
    returns a hit through the same command form. Install gets the same rule.
    """
    hook = home / ".claude" / "hooks" / "copydesk" / "gate.sh"
    if not hook.is_file():
        return False, "the gate hook is not installed"

    payload = json.dumps({
        "session_id": "copydesk-setup-proof",
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
    return blocked, reason[0] if reason else "no finding reported"


class HookResult(NamedTuple):
    installed: bool
    message: str


def _clean_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def hooks_directory(cwd: Path) -> Optional[Path]:
    """Git's own answer, which honours worktrees and core.hooksPath."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
            cwd=str(cwd), capture_output=True, text=True, env=_clean_git_env(),
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if not out:
            return None
        p = Path(out)
        return p.resolve() if p.is_absolute() else (cwd / p).resolve()
    except OSError:
        return None


def install_commit_hook(cwd: Path) -> HookResult:
    hooks_dir = hooks_directory(cwd)
    if hooks_dir is None:
        return HookResult(False, f"{cwd} is not a git repository")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    target = hooks_dir / "commit-msg"
    hook_src = (BUNDLE_ROOT / "git-hooks" / "commit-msg").read_text(encoding="utf-8")
    if target.is_file():
        try:
            existing = target.read_text(encoding="utf-8")
            if "# CopyDesk commits gate" in existing:
                target.write_text(hook_src, encoding="utf-8")
                os.chmod(target, 0o755)
                return HookResult(True, f"updated {target}")
            else:
                return HookResult(
                    False,
                    f"skipped  {target} already exists\n"
                    f"         To chain CopyDesk into it, add these lines at the end:\n"
                    f'             copydesk check --commit-msg "$1"; status=$?\n'
                    f'             [ "$status" -eq 1 ] && exit 1\n'
                    f'             [ "$status" -gt 1 ] && echo "copydesk: exit $status; commit allowed" >&2',
                )
        except OSError as error:
            return HookResult(False, f"error reading {target}: {error}")
    target.write_text(hook_src, encoding="utf-8")
    os.chmod(target, 0o755)
    return HookResult(True, f"installed {target}")



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


def _build_plan(
    home: Path,
    config_path: Path,
    config_body: str,
    selected_tools: list[str],
    resolved_config: dict,
) -> apply.Plan:
    writes = [apply.Write(config_path, config_body)]

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

        out_styles_dir = home / ".claude" / "output-styles"
        src_styles_dir = BUNDLE_ROOT / "output-styles"
        for level in ("low", "medium", "high"):
            style_file = src_styles_dir / f"copydesk-{level}.md"
            if style_file.is_file():
                writes.append(apply.Write(out_styles_dir / f"copydesk-{level}.md", style_file.read_text(encoding="utf-8")))

        settings_path = home / ".claude" / "settings.json"
        settings_doc = {}
        if settings_path.is_file():
            try:
                settings_doc = json.loads(jsonc.strip_comments(settings_path.read_text(encoding="utf-8")))
            except Exception:
                settings_doc = {}
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

    # Instruction files for other tools
    parts = [
        instructions.render_documents(resolved_config),
        instructions.render_commits(resolved_config),
        instructions.render_reviews(resolved_config),
    ]
    non_empty = [part for part in parts if part]
    if non_empty:
        agents_body = "\n\n".join(non_empty)
        target_paths = []
        for tool in selected_tools:
            if tool == "claude-code" or tool == "git":
                continue
            adapter = adapters.REGISTRY.get(tool)
            if not adapter:
                continue
            target_file = home / adapter.home.replace("~/", "") / "AGENTS.md"
            target_paths.append(target_file)

        for target in apply.plan_targets(target_paths, agents_body):
            orig = ""
            if target.real.is_file():
                orig = target.real.read_text(encoding="utf-8")
            region = f"{apply.MARKER_START}\n{agents_body}\n{apply.MARKER_END}\n"
            if apply._REGION.search(orig):
                new_content = apply._REGION.sub(region, orig, count=1)
            else:
                sep = "" if not orig or orig.endswith("\n\n") else "\n"
                new_content = orig + sep + region
            writes.append(apply.Write(target.real, new_content))

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

    # Detect available tools
    available_tools = [name for name in adapters.REGISTRY if name != "git" and adapters.detect(name, copydesk_home)]
    if not available_tools:
        out_stream.write(COPY["outro_no_tools"] + "\n")
        out_stream.flush()
        return 0

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

    if not args.defaults and not args.repair and interactive:
        out_stream.write(COPY["intro"] + "\n\n")
        out_stream.flush()

        # Tools multiselect
        tool_options = [
            prompt.Option(
                adapters.REGISTRY[name].label,
                adapters.REGISTRY[name].installs if name in available_tools else "not found on this machine",
                name in available_tools,
            )
            for name in adapters.REGISTRY if name != "git"
        ]
        tool_names = [name for name in adapters.REGISTRY if name != "git"]
        preselected_tools = [i for i, name in enumerate(tool_names) if name in available_tools]
        try:
            chosen_tool_indices = prompt.multiselect(
                COPY["tools"], tool_options, preselected_tools, stdin=in_stream, stdout=out_stream
            )
            selected_tools = [tool_names[i] for i in chosen_tool_indices]
        except prompt.Cancelled:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0

        # Channels selection
        channel_names = ["chat", "documents", "commits", "reviews"]
        channel_labels = {
            "chat": ("Chat replies", "conversation with coding assistants"),
            "documents": ("Documents", "markdown files and documentation"),
            "commits": ("Commit messages", "git commit subjects and bodies"),
            "reviews": ("Pull request reviews", "review comments and summaries"),
        }
        channel_options = [
            prompt.Option(channel_labels[ch][0], channel_labels[ch][1], True)
            for ch in channel_names
        ]
        preselected_channels = [i for i, ch in enumerate(channel_names) if CHANNEL_PRESELECTED[ch]]
        try:
            chosen_channel_indices = prompt.multiselect(
                COPY["where"], channel_options, preselected_channels, stdin=in_stream, stdout=out_stream
            )
        except prompt.Cancelled:
            out_stream.write(COPY["outro_cancelled"] + "\n")
            return 0

        selected_channels = [channel_names[i] for i in chosen_channel_indices]

        # Per channel configuration
        for ch in channel_names:
            enabled = ch in selected_channels
            if not enabled:
                channels_settings[ch] = {"enabled": False}
                continue

            presets = PRESETS[ch]
            preset_options = [prompt.Option(p.label, p.consequence, True) for p in presets]
            try:
                chosen_preset_idx = prompt.select(
                    f"{channel_labels[ch][0]} - how should they read?",
                    preset_options,
                    0,
                    stdin=in_stream,
                    stdout=out_stream,
                )
            except prompt.Cancelled:
                out_stream.write(COPY["outro_cancelled"] + "\n")
                return 0

            chosen_preset = presets[chosen_preset_idx]
            if chosen_preset.label == "Customize…":
                style_opts = [prompt.Option(s, styles.DESCRIPTIONS.get(s, "")) for s in styles.STYLE_NAMES]
                style_idx = prompt.select(
                    f"Which style? (channels.{ch}.style)", style_opts, 0, stdin=in_stream, stdout=out_stream
                )
                sel_style = styles.STYLE_NAMES[style_idx]

                verb_opts = [prompt.Option(v, "") for v in instructions.VERBOSITY_LEVELS]
                verb_idx = prompt.select(
                    f"How much detail? (channels.{ch}.verbosity)", verb_opts, 0, stdin=in_stream, stdout=out_stream
                )
                sel_verb = instructions.VERBOSITY_LEVELS[verb_idx]

                guidance_ids = list(guidance.IDS)
                guid_opts = [prompt.Option(gid, guidance.SNIPPETS.get(gid, "")) for gid in guidance_ids]
                guid_indices = prompt.multiselect(
                    f"Guidance deliverables (channels.{ch}.guidance)",
                    guid_opts,
                    [],
                    stdin=in_stream,
                    stdout=out_stream,
                )
                guid_dict = {guidance_ids[i]: True for i in guid_indices}
                channels_settings[ch] = {
                    "enabled": True,
                    "style": sel_style,
                    "verbosity": sel_verb,
                    "guidance": guid_dict,
                }
            else:
                ch_dict: dict = {
                    "style": chosen_preset.style,
                    "verbosity": chosen_preset.verbosity,
                }
                if chosen_preset.guidance:
                    ch_dict["guidance"] = {g: True for g in chosen_preset.guidance}
                channels_settings[ch] = ch_dict
    else:
        for ch, pre in CHANNEL_PRESELECTED.items():
            if pre:
                default_p = PRESETS[ch][0]
                ch_dict = {
                    "style": default_p.style,
                    "verbosity": default_p.verbosity,
                }
                if default_p.guidance:
                    ch_dict["guidance"] = {g: True for g in default_p.guidance}
                channels_settings[ch] = ch_dict
            else:
                channels_settings[ch] = {"enabled": False}

    config_body = _format_config(channels_settings, selected_tools)

    resolved_config = {
        "channels": channels_settings,
        "agents": selected_tools,
    }
    plan = _build_plan(copydesk_home, config_file, config_body, selected_tools, resolved_config)

    # Review panel
    out_stream.write("Configured tools:\n")
    for tool in selected_tools:
        adapter = adapters.REGISTRY[tool]
        out_stream.write(f"  {adapter.label}\n")
    out_stream.write("\n")
    out_stream.write(COPY["review"] + "\n")
    for write in plan.writes:
        out_stream.write(f"  {write.path}\n")
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
        if name == "git":
            continue
        inst_file = copydesk_home / adapter.home.replace("~/", "") / "AGENTS.md"
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

    # 5. Purge user config
    if args.purge and config_file.is_file():
        targets.append(apply.Target(real=config_file, kind="created"))

    out_stream.write("These files will be modified or removed:\n")
    for t in targets:
        out_stream.write(f"  {t.real}\n")
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

    res = apply.remove_owned(targets)
    if not res.ok:
        out_stream.write(f"error  {res.message}\n")
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
