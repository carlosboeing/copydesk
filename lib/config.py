#!/usr/bin/env python3
"""Config discovery and the preset cascade for CopyDesk.

Two files are discovered and merged rather than chosen between: a user file
and the nearest project file walking up from the document being linted.

Resolution order is built-in preset, then each preset named by `extends` in
array order, then the user file, then the project file. Later entries win.
Word lists merge through `add` and `remove`, never replacement, because
replacement would make extending a preset require restating it.

The gate fails open on a config error. A hook that blocks on its own
misconfiguration is worse than one that lets the write through, so callers
catch ConfigError, report it, and carry on with the built-in preset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional, Union

import jsonc

import channels
import styles

SCHEMA_VERSION = 1

PROJECT_CONFIG_STEM = "copydesk.config"
LOCAL_CONFIG_NAME = "copydesk.local.json"
JSON_SUFFIX = ".json"
# Recognised but refused at v0. Naming them lets the message say why.
FOREIGN_SUFFIXES = (".yaml", ".yml", ".toml")

PATHS_DEFAULTS = {"ignore": [], "warn": [], "block": ["**/*.md"]}

# The repo owns what gets committed into it; the user owns what is said to
# them. A project file setting one of these is ignored and reported, never
# an error, because a project should not be able to break a contributor's
# install.
PROJECT_FORBIDDEN_PREFIXES = ("channels.chat", "agents", "telemetry")

# The config vocabulary is frozen at three values. The internal severity
# strings are deliberately free, so the mapping happens here and the engine
# keeps the strings it already uses.
SEVERITY_TO_INTERNAL = {"error": "error", "warn": "warning", "off": "off"}


class ConfigError(Exception):
    """A config problem the user must fix. Callers fail open and report it."""


def _read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"{path}: cannot be read ({error.strerror})") from error
    try:
        stripped = jsonc.strip_comments(text)
    except jsonc.UnterminatedComment as error:
        raise ConfigError(f"{path}: {error}") from error
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{path}: is not valid JSON (line {error.lineno}, column {error.colno})"
        ) from error
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path}: the top level must be an object")
    return loaded


def _check_version(path: Path, document: dict) -> None:
    if "version" not in document:
        raise ConfigError(f"{path}: no version field. Add \"version\": {SCHEMA_VERSION}.")
    version = document["version"]
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: version {version!r} is not supported. This build reads version {SCHEMA_VERSION}."
        )


def user_config_path() -> Optional[Path]:
    """`$XDG_CONFIG_HOME/copydesk/config.json`, falling back to `~/.config`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    candidate = base / "copydesk" / "config.json"
    return candidate if candidate.is_file() else None


def _configs_in(directory: Path) -> list[Path]:
    found = []
    for suffix in (JSON_SUFFIX,) + FOREIGN_SUFFIXES:
        candidate = directory / f"{PROJECT_CONFIG_STEM}{suffix}"
        if candidate.is_file():
            found.append(candidate)
    return found


def project_config_path(start: Union[str, Path]) -> Optional[Path]:
    """The nearest `copydesk.config.json`, walking up from the document.

    Two config files in one directory are an error rather than a merge or a
    first-wins choice. Format precedence was the bug, not the fix.
    """
    here = Path(start).resolve()
    directory = here if here.is_dir() else here.parent
    for candidate_dir in (directory, *directory.parents):
        found = _configs_in(candidate_dir)
        if not found:
            continue
        if len(found) > 1:
            names = ", ".join(sorted(p.name for p in found))
            raise ConfigError(
                f"{candidate_dir}: two config files in one directory ({names}). "
                "Keep one. There is no precedence between formats."
            )
        only = found[0]
        if only.suffix != JSON_SUFFIX:
            raise ConfigError(
                f"{only}: this build reads JSON only. Rename it to {PROJECT_CONFIG_STEM}{JSON_SUFFIX}."
            )
        return only
    return None


def local_config_path(start: Union[str, Path]) -> Optional[Path]:
    """`copydesk.local.json` in the directory that holds the project file."""
    here = Path(start).resolve()
    directory = here if here.is_dir() else here.parent
    for candidate_dir in (directory, *directory.parents):
        local = candidate_dir / LOCAL_CONFIG_NAME
        project = _configs_in(candidate_dir)
        if local.is_file():
            return local
        if project:
            return None
    return None


def _strip_forbidden(path: Path, document: dict) -> list[str]:
    """Remove personal keys from a project layer. Returns what was dropped."""
    warnings: list[str] = []
    for prefix in PROJECT_FORBIDDEN_PREFIXES:
        head, _, tail = prefix.partition(".")
        if not tail:
            if head in document:
                document.pop(head)
                warnings.append(f"{path}: {head} is a personal key. Ignored.")
            continue
        nested = document.get(head)
        if isinstance(nested, dict) and tail in nested:
            nested.pop(tail)
            warnings.append(f"{path}: {prefix} is a personal key. Ignored.")
    return warnings


def _merge_list(base: Iterable[str], layer: dict) -> list[str]:
    """Additive semantics. `add` appends unseen entries, `remove` deletes."""
    merged = list(base)
    for entry in layer.get("add", ()) or ():
        if entry not in merged:
            merged.append(entry)
    for entry in layer.get("remove", ()) or ():
        while entry in merged:
            merged.remove(entry)
    return merged


# Renames from 0.1.x. The old spelling keeps working; the new one is what the
# schema, the docs and the wizard write.
THRESHOLD_ALIASES = {
    "hard_max": "hardMax",
    "max_sentences": "maxSentences",
    "min_stdev": "minStdev",
    "max_rate": "maxRate",
    "exemption_ratio": "exemptionRatio",
}


def _normalise_rule_keys(layer: dict) -> dict:
    """One spelling reaches the engine. Both spellings reach this function."""
    normalised = dict(layer)
    for old, new in THRESHOLD_ALIASES.items():
        if old in normalised and new not in normalised:
            normalised[new] = normalised.pop(old)
    if "vocabulary" in normalised and isinstance(normalised["vocabulary"], dict):
        # unglossed-term.vocabulary.add is the 0.1.0 shape; add is the new one.
        nested = normalised.pop("vocabulary")
        merged = list(normalised.get("add", ()) or ())
        for entry in nested.get("add", ()) or ():
            if entry not in merged:
                merged.append(entry)
        if merged:
            normalised["add"] = merged
    return normalised


def _token_phrase(token: Union[str, dict]) -> str:
    return token if isinstance(token, str) else token["phrase"]


def _apply_rule_layer(preset: dict, rule_id: str, layer: dict) -> None:
    """Apply one config entry onto the effective preset, in place."""
    if not isinstance(layer, dict):
        raise ConfigError(f"rules.{rule_id}: must be an object")

    layer = _normalise_rule_keys(layer)

    if "severity" in layer:
        severity = layer["severity"]
        if severity not in SEVERITY_TO_INTERNAL:
            allowed = ", ".join(sorted(SEVERITY_TO_INTERNAL))
            raise ConfigError(f"rules.{rule_id}.severity: {severity!r} is not one of {allowed}")
        internal = SEVERITY_TO_INTERNAL[severity]
        for block in preset["patterns"]:
            if block["id"] == rule_id:
                block["severity"] = internal
        preset["rules"].setdefault(rule_id, {})["severity"] = severity

    if "add" in layer or "remove" in layer:
        blocks = [b for b in preset["patterns"] if b["id"] == rule_id]
        if not blocks and rule_id == "unglossed-term":
            target = preset["rules"].setdefault(rule_id, {})
            vocab = target.setdefault("vocabulary", {})
            current = list(vocab.get("add", []) or target.get("add", []))
            updated = _merge_list(current, layer)
            vocab["add"] = updated
            target["add"] = updated
        elif not blocks:
            raise ConfigError(
                f"rules.{rule_id}: add and remove apply to pattern rules only. "
                f"{rule_id} is a metric or structural rule."
            )
        else:
            # `add` goes to the first block for the rule; `remove` sweeps them all,
            # so removing a token does not depend on knowing which block holds it.
            removals = set(layer.get("remove", ()) or ())
            for block in blocks:
                block["tokens"] = [t for t in block["tokens"] if _token_phrase(t) not in removals]
            existing = {_token_phrase(t) for b in blocks for t in b["tokens"]}
            for entry in layer.get("add", ()) or ():
                if entry not in existing:
                    blocks[0]["tokens"].append(entry)
                    existing.add(entry)

    for key, value in layer.items():
        if key in ("severity", "add", "remove"):
            continue
        target = preset["rules"].setdefault(rule_id, {})
        if key == "vocabulary" and isinstance(value, dict):
            current = target.get("vocabulary", {}).get("add", [])
            target.setdefault("vocabulary", {})["add"] = _merge_list(current, value)
        else:
            target[key] = value


def load_preset_document(rules_dir: Path, preset_id: str) -> dict:
    path = rules_dir / f"{preset_id}{JSON_SUFFIX}"
    if not path.is_file():
        available = sorted(p.stem for p in rules_dir.glob(f"*{JSON_SUFFIX}")) if rules_dir.is_dir() else []
        listed = ", ".join(available) if available else "none"
        raise ConfigError(f"extends: no preset named {preset_id!r}. Available: {listed}.")
    document = _read_json(path)
    _check_version(path, document)
    return document


def _extends_list(document: dict, source: Path) -> list[str]:
    value = document.get("extends")
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"{source}: extends must be a string or an array of strings")


CHANNEL_NAMES = ("chat", "documents", "commits", "reviews")

# Every value here is a default the design's Part 2 example states.
CHANNEL_DEFAULTS = {
    "chat": {
        "enabled": True,
        "style": "plain",
        "verbosity": "low",
        "guidance": {
            "recommendations": True,
            "direction": True,
            "progress": True,
            "pushback": False,
            "alternatives": False,
        },
        "match": [],
    },
    "documents": {
        "enabled": True,
        "style": "plain",
        "verbosity": "high",
        "guidance": {"recommendations": True},
        "match": [],
    },
    "commits": {
        "enabled": True,
        "style": "engineer",
        "verbosity": "low",
        "guidance": {},
        "match": [],
    },
    "reviews": {
        "enabled": False,
        "style": "plain",
        "verbosity": "medium",
        "guidance": {"pushback": True},
        "match": [],
    },
}

DEFAULT_AGENTS = ["claude-code"]
GATE_DEFAULTS = {"retries": 3}
TELEMETRY_DEFAULTS = {"events": True, "saveText": False}


def _merge_channels(base: dict, layer: dict, source: Path, warnings: list, provenance: dict) -> dict:
    merged = {name: dict(body) for name, body in base.items()}
    for name, settings in (layer or {}).items():
        if name not in CHANNEL_NAMES:
            warnings.append(f"{source}: channels.{name} is not a known channel. Ignored.")
            continue
        if not isinstance(settings, dict):
            raise ConfigError(f"{source}: channels.{name} must be an object")
        target = merged[name]
        for key, value in settings.items():
            if key == "guidance" and isinstance(value, dict):
                # Booleans merge key by key, so turning one id on never
                # silently turns the channel's other ids off.
                target["guidance"] = {**target.get("guidance", {}), **value}
                for guidance_id in value:
                    provenance[f"channels.{name}.guidance.{guidance_id}"] = str(source)
            else:
                # Scalars, enums and match globs replace. A glob set edits
                # badly by patch, so it is replaced wholesale on purpose.
                target[key] = value
                provenance[f"channels.{name}.{key}"] = str(source)
    return merged


def resolve(
    rules_dir: Path,
    target: Optional[Union[str, Path]] = None,
    *,
    default_preset: str = "plain",
    user_path: Optional[Path] = None,
    project_path: Optional[Path] = None,
    local_path: Optional[Path] = None,
    channel: Optional[str] = None,
) -> dict:
    """Return the effective preset for one document.

    Raises ConfigError. Callers fail open and report rather than blocking.
    """
    layers: list[tuple[Path, dict]] = []

    if user_path is None:
        user_path = user_config_path()
    if project_path is None and target is not None:
        project_path = project_config_path(target)
    if local_path is None and target is not None:
        local_path = local_config_path(target)

    warnings: list[str] = []
    for path, kind in ((user_path, "user"), (project_path, "project"), (local_path, "local")):
        if path is None:
            continue
        document = _read_json(path)
        _check_version(path, document)
        if kind == "project":
            warnings.extend(_strip_forbidden(path, document))
        layers.append((path, document))

    channels_config = {name: dict(body) for name, body in CHANNEL_DEFAULTS.items()}
    agents = list(DEFAULT_AGENTS)
    gate = dict(GATE_DEFAULTS)
    telemetry = dict(TELEMETRY_DEFAULTS)
    # Stamp every leaf, not every branch: doctor attributes values, and
    # `channels.documents` is not a value anyone asks about.
    provenance = {}

    def _stamp(prefix: str, node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                _stamp(f"{prefix}.{key}" if prefix else key, value)
        else:
            provenance[prefix] = "built-in"

    _stamp("channels", channels_config)
    _stamp("gate", gate)
    _stamp("telemetry", telemetry)
    provenance["agents"] = "built-in"
    provenance["paths"] = "built-in"

    for path, document in layers:
        channels_config = _merge_channels(channels_config, document.get("channels"), path, warnings, provenance)
        if "agents" in document:
            agents = list(document["agents"])
            provenance["agents"] = str(path)
        for block, block_target in (("gate", gate), ("telemetry", telemetry)):
            for key, value in (document.get(block) or {}).items():
                block_target[key] = value
                provenance[f"{block}.{key}"] = str(path)

    # The base preset, then the channel style (if channel is set), then presets named by extends
    named: list[str] = [default_preset]
    if channel is not None:
        channel_settings = channels_config.get(channel) or {}
        style_preset = styles.preset_for(channel_settings.get("style", "plain"))
        if style_preset not in named:
            named.insert(1, style_preset)

    for path, document in layers:
        for preset_id in _extends_list(document, path):
            if preset_id not in named:
                named.append(preset_id)

    effective = load_preset_document(rules_dir, named[0])
    effective.setdefault("rules", {})
    for preset_id in named[1:]:
        extra = load_preset_document(rules_dir, preset_id)
        effective["patterns"].extend(extra.get("patterns", []))
        for rule_id, layer in (extra.get("rules") or {}).items():
            _apply_rule_layer(effective, rule_id, layer)

    for path, document in layers:
        for rule_id, layer in (document.get("rules") or {}).items():
            _apply_rule_layer(effective, rule_id, layer)

    base_root = None
    if project_path is not None:
        base_root = str(project_path.parent)
    elif target is not None:
        p = Path(target).resolve()
        base_root = str(p if p.is_dir() else p.parent)
    else:
        base_root = str(Path.cwd())

    paths_merged = {k: list(v) for k, v in PATHS_DEFAULTS.items()}
    path_rules: list[channels.PathRule] = []

    # Layer 0: built-in
    for action in ("ignore", "warn", "block"):
        for pat in PATHS_DEFAULTS.get(action, ()):
            path_rules.append(channels.PathRule(0, action, pat, base_root))

    layer_idx = 0
    for path, kind in ((user_path, "user"), (project_path, "project"), (local_path, "local")):
        layer_idx += 1
        if path is None:
            continue
        doc = next((d for p, d in layers if p == path), None)
        if doc is None:
            continue
        layer_root = str(path.parent) if kind in ("project", "local") else base_root
        layer_paths = doc.get("paths") or {}
        if isinstance(layer_paths, dict):
            for action in ("ignore", "warn", "block"):
                pats = layer_paths.get(action) or []
                if isinstance(pats, list):
                    for pat in pats:
                        if isinstance(pat, str):
                            paths_merged.setdefault(action, []).append(pat)
                            path_rules.append(channels.PathRule(layer_idx, action, pat, layer_root))
                            provenance[f"paths.{action}"] = str(path)

    effective["channels"] = channels_config
    effective["agents"] = agents
    effective["gate"] = gate
    effective["telemetry"] = telemetry
    effective["paths"] = paths_merged
    effective["pathRules"] = path_rules
    effective["provenance"] = provenance
    effective["sources"] = [str(p) for p, _ in layers]
    effective["warnings"] = warnings
    return effective


def describe_discovery(target: Optional[Union[str, Path]] = None) -> dict:
    """What `copydesk doctor` reports. Never mutates and never raises."""
    report: dict = {"user": None, "project": None, "errors": []}
    try:
        user = user_config_path()
        report["user"] = str(user) if user else None
    except ConfigError as error:
        report["errors"].append(str(error))
    if target is not None:
        try:
            project = project_config_path(target)
            report["project"] = str(project) if project else None
        except ConfigError as error:
            report["errors"].append(str(error))
    return report
