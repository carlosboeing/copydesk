#!/usr/bin/env python3
"""Which files the gate judges, and which channel judges them.

Routing lives here rather than in the model's judgement. The gate resolves a
path to an action and a channel; a conditional needing judgement does not
survive a long session, so the design leaves none of that kind.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

ACTIONS = ("ignore", "warn", "block")
# Strictest first. Within one layer, a path on several lists takes the
# strictest reading. Across layers, the later layer wins outright.
STRICTNESS = {"ignore": 0, "block": 1, "warn": 2}
# Fixed order. The fallback needs no glob, so it claims last.
CLAIM_ORDER = ("commits", "reviews", "documents")

_CACHE: dict = {}


class PathRule(NamedTuple):
    """One pattern, tagged with the layer and the root it was written against.

    The root travels with the pattern because a project file's `scratch/**`
    means that project's directory, and a worktree has a different one.
    """
    layer: int
    action: str
    pattern: str
    root: str


def _to_regex(pattern: str) -> "re.Pattern[str]":
    cached = _CACHE.get(pattern)
    if cached is not None:
        return cached
    out = ["(?s:"]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 3] == "**/":
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern[index : index + 2] == "**":
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        out.append(re.escape(char))
        index += 1
    out.append(")\\Z")
    compiled = re.compile("".join(out))
    _CACHE[pattern] = compiled
    return compiled


def matches(pattern: str, path: str) -> bool:
    # `relative_to` owns normalisation. Stripping a leading slash here would
    # let a root-anchored pattern claim a file outside the root.
    normalised = path.replace("\\", "/")
    if pattern.endswith("/**"):
        # A bare directory pattern covers the directory itself and its contents.
        head = pattern[:-3]
        if normalised == head or normalised.startswith(head + "/"):
            return True
    return bool(_to_regex(pattern).match(normalised))


class Decision(NamedTuple):
    action: str
    channel: Optional[str]


def relative_to(path: str, root: str) -> str:
    """A path expressed the way patterns are written, or left absolute.

    A file outside the root cannot match a root-anchored pattern, and saying
    so by leaving it absolute is simpler than a special case at every call.
    """
    normalised = path.replace("\\", "/")
    base = root.replace("\\", "/").rstrip("/")
    if base and normalised.startswith(base + "/"):
        return normalised[len(base) + 1:]
    if normalised.startswith("./"):
        return normalised[2:]
    return normalised


def _action_for(path: str, rules) -> str:
    """Two rules, at two scopes, exactly as the design states them.

    Within one list, the last matching pattern decides and `!` re-includes.
    Within one layer, a path on several lists takes the strictest reading.
    Across layers, the last layer with any verdict wins.

    That is why the built-in `**/*.md` block does not beat a user file naming
    CHANGELOG.md as warn: strictness never crosses a layer boundary.
    """
    by_layer: dict = {}
    for rule in rules:
        target = relative_to(path, rule.root)
        pattern = rule.pattern
        negated = pattern.startswith("!")
        if negated:
            pattern = pattern[1:]
        if not matches(pattern, target):
            continue
        # Later entries in one list overwrite earlier ones, so a trailing
        # `!` re-inclusion removes the verdict its own list had set.
        by_layer.setdefault(rule.layer, {})[rule.action] = not negated

    verdict = "ignore"
    for index in sorted(by_layer):
        hits = [action for action, state in by_layer[index].items() if state]
        if hits:
            verdict = sorted(hits, key=lambda name: STRICTNESS[name])[0]
    return verdict


def _channel_for(path: str, channel_settings: dict) -> Optional[str]:
    for name in CLAIM_ORDER:
        settings = channel_settings.get(name) or {}
        if not settings.get("enabled", True):
            continue
        globs = settings.get("match") or []
        if globs:
            if any(matches(glob, path) for glob in globs):
                return name
        elif name == "documents":
            return name
    return None


DEFAULT_PATH_RULES = (PathRule(0, "block", "**/*.md", ""),)


def decide(path: Optional[str], resolved: dict) -> Decision:
    if not path or path == "<stdin>":
        return Decision("block", "documents")
    path_rules = resolved.get("pathRules")
    if path_rules is None:
        rules = DEFAULT_PATH_RULES
    else:
        rules = path_rules
    action = _action_for(path, rules)
    if action == "ignore":
        return Decision("ignore", None)
    root = rules[-1].root if rules else ""
    channel = _channel_for(relative_to(path, root), resolved.get("channels") or {})
    if channel is None:
        return Decision("ignore", None)
    return Decision(action, channel)
