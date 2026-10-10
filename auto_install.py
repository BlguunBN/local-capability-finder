#!/usr/bin/env python3
"""Install or remove concise, reversible skill-finder instructions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from install_agents import AGENTS

START = "<!-- capfind:find-best-skills:start -->"
END = "<!-- capfind:find-best-skills:end -->"
ROOT = Path(__file__).resolve().parent


def instruction(mode: str) -> str:
    trigger = (
        'When a user asks to use the best skills for a task, or says "use skills", '
        '"find skills", or equivalent,'
        if mode == "explicit" else
        "When a task is specialized or the user asks for the best skills,"
    )
    return (
        f"{START}\n"
        "## find-best-skills\n"
        f"{trigger} call local-capability-finder.search_capabilities with the task, "
        "kind=skill, limit=3, and your lowercase agent name when supported. "
        "Select at most one primary and one complementary result "
        "that actually fit. Call get_capability for exact IDs, then read only their "
        "SKILL.md files and carry out the user's task. Retry search once with tighter "
        "terms if needed. Do not search for trivial tasks or when the user opts out. "
        "Do not activate or install a skill unless the user asks. "
        "Avoid repeating a search for the same task in one session.\n"
        f"{END}\n"
    )


def target(agent: str, home: Path) -> Path:
    paths = {
        "codex": home / ".codex" / "AGENTS.md",
        "claude": home / ".claude" / "CLAUDE.md",
        "gemini": home / ".gemini" / "GEMINI.md",
        "antigravity": home / ".gemini" / "GEMINI.md",
        "hermes": home / "AGENTS.md",
        "cursor": home / "AGENTS.md",
        "opencode": home / ".config" / "opencode" / "AGENTS.md",
        "omp": home / ".omp" / "agent" / "AGENTS.md",
    }
    return paths[agent]


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.bak-capfind-{stamp}")
    suffix = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-capfind-{stamp}-{suffix}")
        suffix += 1
    shutil.copy2(path, backup)
    return backup


def _write(path: Path, value: str) -> Path | None:
    if path.is_symlink():
        raise ValueError(f"refusing symlinked instruction file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(path)
    bom = path.exists() and path.read_bytes().startswith(b"\xef\xbb\xbf")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as out:
        out.write((b"\xef\xbb\xbf" if bom else b"") + value.encode("utf-8"))
        temp = Path(out.name)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return backup


def _block(text: str) -> tuple[int, int] | None:
    starts, ends = text.count(START), text.count(END)
    if starts != ends or starts > 1:
        raise ValueError("malformed or duplicate capfind instruction markers")
    if not starts:
        return None
    start = text.index(START)
    end = text.index(END, start) + len(END)
    if text.startswith("\r\n", end):
        end += 2
    elif end < len(text) and text[end] == "\n":
        end += 1
    return start, end


def _updated(text: str, mode: str) -> str:
    span = _block(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    block = instruction(mode).replace("\n", newline)
    if span:
        return text[:span[0]] + block + text[span[1]:]
    return text + ("" if not text or text.endswith("\n") else newline) + (newline if text else "") + block


def _removed(text: str) -> str:
    span = _block(text)
    if not span:
        return text
    before, after = text[:span[0]], text[span[1]:]
    if before.endswith("\r\n\r\n"):
        before = before[:-2]
    elif before.endswith("\n\n"):
        before = before[:-1]
    return before + after


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return source.read()


def _hook_path(agent: str, home: Path) -> Path:
    return home / (".claude" if agent == "claude" else ".gemini") / "settings.json"


def _hook_status(agent: str, home: Path) -> str:
    path = _hook_path(agent, home)
    data = json.loads(_read(path) or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"expected object in {path}")
    event = "UserPromptSubmit" if agent == "claude" else "BeforeAgent"
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"expected hooks object in {path}")
    groups = hooks.get(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"expected {event} array in {path}")
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks", [])
        if isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("name") == "capfind-find-best-skills"
            for entry in entries
        ):
            return "hook enabled"
    return "hook disabled"


def _hook_entry(agent: str, mode: str) -> dict:
    command = f'"{sys.executable}" "{ROOT / "auto_hook.py"}" {agent} --mode {mode}'
    timeout = 10 if agent == "claude" else 10000  # Claude seconds; Gemini milliseconds.
    return {"hooks": [{"type": "command", "command": command, "timeout": timeout,
                       "name": "capfind-find-best-skills"}]}


def _edit_hook(agent: str, home: Path, mode: str | None, dry_run: bool) -> str:
    path = _hook_path(agent, home)
    if path.is_symlink():
        raise ValueError(f"refusing symlinked hook config: {path}")
    data = json.loads(_read(path) or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"expected object in {path}")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"expected hooks object in {path}")
    event = "UserPromptSubmit" if agent == "claude" else "BeforeAgent"
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"expected {event} array in {path}")
    old = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks", [])
        if not isinstance(entries, list):
            raise ValueError(f"expected hook entries array in {path}")
        if any(isinstance(item, dict) and item.get("name") == "capfind-find-best-skills"
               for item in entries):
            old.append(group)
    if len(old) > 1:
        raise ValueError("duplicate capfind hooks")
    retained = []
    for group in groups:
        if group not in old:
            retained.append(group)
            continue
        other_entries = [item for item in group["hooks"]
                         if not (isinstance(item, dict) and
                                 item.get("name") == "capfind-find-best-skills")]
        if other_entries:
            retained.append({**group, "hooks": other_entries})
    groups[:] = retained
    if mode:
        groups.append(_hook_entry(agent, mode))
    if not groups:
        hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    if not old and mode is None:
        return "hook absent"
    if old and mode and old[0] == _hook_entry(agent, mode):
        return "hook unchanged"
    if dry_run:
        return "would update hook"
    backup = _write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return f"hook updated (backup: {backup or 'new file'})"


def action(agent: str, home: Path, command: str, mode: str, hooks: bool,
           dry_run: bool) -> str:
    path = target(agent, home)
    current = _read(path)
    span = _block(current)
    if command == "status":
        state = "enabled" if span else "disabled"
        if span:
            state += f" ({'smart' if 'When a task is specialized' in current[span[0]:span[1]] else 'explicit'})"
        if agent in {"claude", "gemini"}:
            state += f"; {_hook_status(agent, home)}"
        return f"{state}: {path}"
    if command == "enable":
        wanted = _updated(current, mode)
    else:
        wanted = _removed(current)
    if wanted == current:
        result = f"unchanged: {path}"
    elif dry_run:
        result = f"would {'enable' if command == 'enable' else 'disable'}: {path}"
    else:
        backup = _write(path, wanted)
        result = f"{'enabled' if command == 'enable' else 'disabled'}: {path} (backup: {backup or 'new file'})"
    if agent in {"claude", "gemini"} and (
        hooks or command == "disable" or _hook_status(agent, home) == "hook enabled"
    ):
        next_mode = mode if command == "enable" and (hooks or _hook_status(agent, home) == "hook enabled") else None
        result += f"; {_edit_hook(agent, home, next_mode, dry_run)}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("enable", "status", "disable", "test"))
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--agents", nargs="+", choices=AGENTS, default=AGENTS)
    parser.add_argument("--mode", choices=("explicit", "smart"), default="explicit")
    parser.add_argument("--hooks", action="store_true", help="add Claude/Gemini prompt hooks")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "test":
        if not args.prompt:
            parser.error("test requires a quoted prompt")
        from auto_skill_router import route_prompt
        result = route_prompt(args.prompt, mode=args.mode)
        print(json.dumps(result, ensure_ascii=False) if args.json else result)
        return 0
    if args.prompt:
        parser.error("prompt is only valid for test")
    failed = False
    home = Path.home()
    gemini_settings = _hook_path("gemini", home)
    before_gemini = gemini_settings.stat().st_mtime_ns if gemini_settings.exists() else None
    seen: set[Path] = set()
    for agent in dict.fromkeys(args.agents):
        try:
            path = target(agent, home)
            # Gemini CLI and Antigravity share one global context file.
            if path in seen and args.command != "status":
                print(f"{agent}: shared instruction already handled: {path}")
                continue
            seen.add(path)
            print(f"{agent}: {action(agent, home, args.command, args.mode, args.hooks, args.dry_run)}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            failed = True
            print(f"{agent}: {error}", file=sys.stderr)
    after_gemini = gemini_settings.stat().st_mtime_ns if gemini_settings.exists() else None
    if not args.dry_run and before_gemini != after_gemini:
        try:
            from capability_finder import refresh_index
            refresh_index()
            print("Refreshed capability index after Gemini hook configuration changed")
        except (OSError, ValueError) as error:
            failed = True
            print(f"capability index refresh failed: {error}", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
