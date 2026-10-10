"""Decide when a user prompt merits an on-demand skill search.

This module never reads skill files, searches a catalog, or activates a skill.
Hosts use its small decision object to call search_capabilities themselves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

_EXPLICIT = re.compile(
    r"\b(?:best|right|appropriate|relevant|suitable|useful)\s+skil{1,2}s?\b"
    r"|\bskil{1,2}s?\s+(?:best|right|appropriate|relevant|suitable|useful)\s+for\b"
    r"|\b(?:find|choose|pick|select|use|apply|with|using)\s+(?:the\s+|some\s+|my\s+)?skil{1,2}s?\b"
    r"|\bskil{1,2}s?\s+for\s+(?:this|the|my)\s+(?:task|job|project|work)\b",
    re.IGNORECASE,
)
_OPT_OUT = re.compile(
    r"\b(?:do\s+not|don't|dont|without|avoid|skip|no|never)\s+(?:use\s+|using\s+|any\s+|the\s+|my\s+)?skil{1,2}s?\b"
    r"|\bskil{1,2}s?\s+(?:off|disabled|not\s+needed)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(?:build|create|implement|develop|deploy|debug|fix|refactor|design|"
    r"analy[sz]e|test|review|automate|migrate|integrate|optimi[sz]e|"
    r"configure|set\s+up|write|audit|convert|train)\b",
    re.IGNORECASE,
)
_SKILL_PHRASE = re.compile(
    r"\b(?:withh|with|using|use|apply|find|choose|pick|select)?\s*"
    r"(?:the\s+|some\s+|my\s+)?(?:best|right|appropriate|relevant|suitable|useful)?\s*"
    r"skil{1,2}s?\s*(?:best|right|appropriate|relevant|suitable|useful)?"
    r"(?:\s+for\s+(?:this|the|my)\s+(?:task|job|project|work))?\b",
    re.IGNORECASE,
)
_PREFIX = re.compile(
    r"^(?:(?:please|can\s+you|could\s+you|would\s+you|i\s+want\s+you\s+to|"
    r"i\s+need\s+you\s+to|help\s+me\s+to|help\s+me|do\s+this)\b[\s,:-]*)+",
    re.IGNORECASE,
)
_STOP = {"a", "an", "the", "to", "for", "me", "my", "this", "that", "it", "with", "of", "and", "in", "on", "do", "help", "job", "task", "project", "x"}


def _unquoted(prompt: str) -> str:
    """Ignore examples in code fences, inline code, and balanced quotations."""
    prompt = re.sub(r"```[\s\S]*?```", " ", prompt)
    prompt = re.sub(r"`[^`]*`", " ", prompt)
    prompt = re.sub(r'"[^"\n]*"', " ", prompt)
    prompt = re.sub(r"(?<!\w)'[^'\n]*'(?!\w)", " ", prompt)
    return prompt


def _query(text: str) -> str:
    text = _SKILL_PHRASE.sub(" ", text)
    text = _PREFIX.sub("", text.strip())
    text = re.sub(r"\b(?:for\s+this\s+job|for\s+this\s+task|for\s+the\s+job)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .?!,:;-\n\t")
    return text[:240]


def route_prompt(prompt: str, mode: str = "explicit", agent: str | None = None) -> dict[str, object]:
    """Return a deterministic, read-only routing decision for one prompt."""
    if mode not in {"explicit", "smart"}:
        raise ValueError("mode must be explicit or smart")
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    text = _unquoted(prompt).strip()
    result: dict[str, object] = {"triggered": False, "reason": "no trigger", "query": None, "mode": mode, "agent": agent}
    if not text:
        result["reason"] = "empty or quoted example"
        return result
    if _OPT_OUT.search(text):
        result["reason"] = "skills explicitly declined"
        return result
    explicit = bool(_EXPLICIT.search(text))
    if not explicit and mode == "explicit":
        return result
    if not explicit and text != prompt.strip() and re.search(r"\b(?:explain|explaining|quote|example|summari[sz]e)\b", text, re.IGNORECASE):
        result["reason"] = "quoted example"
        return result
    if not explicit and not _ACTION.search(text):
        return result
    query = _query(text)
    words = [w.casefold() for w in re.findall(r"[\w-]+", query, re.UNICODE)]
    content = [w for w in words if w not in _STOP and len(w) > 1]
    if not content or (not explicit and len(content) < 2):
        result["reason"] = "task too vague"
        return result
    result.update(triggered=True, reason="explicit skill request" if explicit else "specialist task", query=query)
    return result


def route_once(
    prompt: str,
    mode: str = "explicit",
    agent: str | None = None,
    session_id: str | None = None,
    state_dir: Path | None = None,
) -> dict[str, object]:
    """Suppress repeated searches for identical tasks in a host session.

    Only hashes are stored. Atomic marker creation avoids duplicate decisions
    when two hooks process the same prompt concurrently.
    """
    result = route_prompt(prompt, mode, agent)
    if not result["triggered"] or not session_id:
        return result
    marker = _marker_path(session_id, str(result["query"]), state_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        result.update(triggered=False, reason="task already searched", query=None)
    else:
        os.close(fd)
    return result


def _marker_path(session_id: str, query: str, state_dir: Path | None) -> Path:
    root = state_dir or Path(tempfile.gettempdir()) / "local-capability-finder" / "auto-sessions"
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    task_hash = hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()[:24]
    return root / session_hash / task_hash


def forget_route(
    prompt: str,
    mode: str = "explicit",
    agent: str | None = None,
    session_id: str | None = None,
    state_dir: Path | None = None,
) -> bool:
    """Release a route marker when its downstream search failed."""
    if not session_id:
        return False
    decision = route_prompt(prompt, mode, agent)
    if not decision["triggered"]:
        return False
    try:
        _marker_path(session_id, str(decision["query"]), state_dir).unlink()
    except FileNotFoundError:
        return False
    return True


def search_for_prompt(
    prompt: str,
    search: Callable[..., list[dict]],
    mode: str = "explicit",
    agent: str | None = None,
) -> dict[str, object]:
    """Run at most two read-only skill searches, with at most three matches each.

    Hosts may use this helper with capability_finder.search. It never calls
    show, activate, or any installer. The retry uses subject words only.
    """
    decision = route_prompt(prompt, mode, agent)
    decision["matches"] = []
    decision["searches"] = 0
    if not decision["triggered"]:
        return decision
    query = str(decision["query"])
    options = {"kind": "skill", "limit": 3}
    if agent:
        options["agent"] = agent
    matches = search(query, **options)
    decision["searches"] = 1
    if not matches:
        subject = re.sub(r"^\b(?:build|create|implement|develop|deploy|debug|fix|refactor|design|analy[sz]e|test|review|automate|migrate|integrate|optimi[sz]e|configure|set\s+up|write|audit|convert|train)\b\s*", "", query, flags=re.IGNORECASE)
        subject = re.sub(r"^(?:a|an|the)\s+", "", subject, flags=re.IGNORECASE).strip()
        if subject and subject.casefold() != query.casefold():
            matches = search(subject, **options)
            decision["searches"] = 2
    decision["matches"] = matches[:3]
    return decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide whether a prompt should trigger local skill discovery")
    parser.add_argument("command", choices=["test", "route"])
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--stdin", action="store_true", help="Read prompt from standard input")
    parser.add_argument("--mode", choices=["explicit", "smart"], default="explicit")
    parser.add_argument("--agent", help="Agent name included in output")
    parser.add_argument("--session", help="Session ID for duplicate suppression with route")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.stdin == (args.prompt is not None):
        parser.error("provide one prompt argument or --stdin")
    prompt = sys.stdin.read(65536) if args.stdin else args.prompt
    if args.command == "route":
        result = route_once(prompt, args.mode, args.agent, args.session)
    else:
        result = route_prompt(prompt, args.mode, args.agent)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    else:
        print(f"{'SEARCH' if result['triggered'] else 'SKIP'}: {result['reason']}")
        if result["query"]:
            print(f"Query: {result['query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
