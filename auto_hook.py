#!/usr/bin/env python3
"""Prompt hook for Claude Code and Gemini CLI; emits only short JSON context."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", choices=("claude", "gemini"))
    parser.add_argument("--mode", choices=("explicit", "smart"), default="explicit")
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict) or not isinstance(event.get("prompt"), str):
            print("{}")
            return 0
        from auto_skill_router import forget_route, route_once
        route = route_once(event["prompt"], mode=args.mode, agent=args.agent,
                           session_id=event.get("session_id"))
        if not route.get("triggered") or not route.get("query"):
            print("{}")
            return 0
        from capability_finder import search
        try:
            matches = search(route["query"], kind="skill", limit=3, agent=args.agent)
        except Exception:
            forget_route(event["prompt"], mode=args.mode, agent=args.agent,
                         session_id=event.get("session_id"))
            raise
        if not matches:
            print("{}")
            return 0
        lines = [f"{item['id']} {item['name']}" for item in matches[:3]]
        context = (
            "find-best-skills matched this task. Cached local skill search for "
            f"{route['query']!r}: " + "; ".join(lines) +
            ". Choose at most one primary and one complementary result. "
            "Call local-capability-finder.get_capability for exact IDs, read only "
            "the selected SKILL.md files, then complete the user's task. "
            "Do not repeat this search or activate skills automatically."
        )
        output = {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit" if args.agent == "claude" else "BeforeAgent",
            "additionalContext": context,
        }}
        print(json.dumps(output, ensure_ascii=True, separators=(",", ":")))
    except Exception:
        # Hooks must never stop the agent from processing the user's prompt.
        print("{}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
