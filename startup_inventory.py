"""Snapshot active skill roots before and after an on-demand archive batch.

This measures a consistent skill-list proxy. Agent-specific prompt usage requires
telemetry from each agent and is not inferred from this output.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from capability_finder import AGENT_DIRS


def snapshot(home: Path) -> dict:
    try:
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        encoder = None

    stores = {}
    for agent, relative in AGENT_DIRS.items():
        root = home / relative
        if not root.is_dir():
            continue
        lines = []
        inaccessible = []
        for folder in root.iterdir():
            try:
                if not folder.is_dir():
                    continue
                skill_md = folder / "SKILL.md"
                if not skill_md.is_file():
                    continue
                header = skill_md.read_text(encoding="utf-8", errors="replace")[:8192]
            except OSError as exc:
                inaccessible.append({"path": str(folder), "error": str(exc)})
                continue
            match = re.search(r"^description:\s*(.+)$", header, re.MULTILINE)
            description = match.group(1).strip().strip('"\'') if match else ""
            lines.append(f"{folder.name}: {description}")
        text = "\n".join(sorted(lines))
        stores[agent] = {
            "root": str(root),
            "active_skills": len(lines),
            "normalized_list_characters": len(text),
            "normalized_list_tokens_cl100k": len(encoder.encode(text)) if encoder else None,
            "inaccessible": inaccessible,
        }
    return {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "method": "top-level SKILL.md folders; normalized 'name: description' lines",
        "actual_agent_prompt_tokens": None,
        "stores": stores,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    content = json.dumps(snapshot(args.home), indent=2) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
