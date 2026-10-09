"""Measure local discovery response text with and without the MCP adapter.

Run with: python tests/measure_discovery_tokens.py --library PATH
The script prints counts only; it never prints skill contents or local paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import tiktoken


CASES = (
    ("form validation zod", "zod", "zod-validation-expert"),
    ("inspect a CAD file", "cad", "cad"),
    ("deploy Cloudflare Workers", "cloudflare", "cloudflare"),
    ("review code for security vulnerabilities", "security", "security-reviewer"),
)


def measure(library: Path) -> dict:
    encode = tiktoken.get_encoding("cl100k_base").encode
    tokens = lambda value: len(encode(value))
    tsv = library / "skills_index.tsv"
    catalog = json.loads((library / "skills_index.json").read_text(encoding="utf-8"))
    descriptions = "".join(
        f"{item['name']}: {item.get('description', '')}\n"
        for item in catalog["skills"]
    )
    server = subprocess.Popen(
        [sys.executable, str(library / "capability_mcp.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )

    def call(request_id: int, method: str, params: dict) -> dict:
        assert server.stdin is not None and server.stdout is not None
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                       "method": method, "params": params}) + "\n")
        server.stdin.flush()
        reply = json.loads(server.stdout.readline())
        if "error" in reply or reply.get("id") != request_id:
            raise RuntimeError(f"MCP request failed: {method}")
        return reply["result"]

    try:
        call(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "token-benchmark", "version": "1"}})
        tools = call(2, "tools/list", {})["tools"]
        schema_tokens = tokens(json.dumps(tools, ensure_ascii=True, separators=(",", ":")))
        rows = []
        for index, (query, keyword, chosen_name) in enumerate(CASES):
            start = time.perf_counter()
            search_text = call(10 + index * 2, "tools/call", {
                "name": "search_capabilities", "arguments": {
                    "query": query, "kind": "skill", "limit": 3, "agent": "codex"
                },
            })["content"][0]["text"]
            search_ms = round((time.perf_counter() - start) * 1000, 1)
            matches = json.loads(search_text)
            chosen = next((item for item in matches if item["name"] == chosen_name), None)
            if chosen is None:
                raise RuntimeError(f"Expected {chosen_name} in MCP top three for {query}")
            detail_text = call(11 + index * 2, "tools/call", {
                "name": "get_capability", "arguments": {"capability_id": chosen["id"]}
            })["content"][0]["text"]
            detail = json.loads(detail_text)
            skill_text = Path(detail["source_path"]).read_text(encoding="utf-8", errors="replace")

            start = time.perf_counter()
            manual = subprocess.run(
                ["rg", "-i", "-F", keyword, str(tsv)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            rg_ms = round((time.perf_counter() - start) * 1000, 1)
            if manual.returncode not in (0, 1):
                raise RuntimeError(f"rg failed for {query}")
            candidate_lines = manual.stdout.splitlines()
            rank = next((number for number, line in enumerate(candidate_lines, 1)
                         if line.split("\t", 1)[0].casefold() == chosen_name.casefold()), None)
            if rank is None:
                raise RuntimeError(f"Manual search did not expose {chosen_name} for {query}")
            mcp_discovery = tokens(search_text) + tokens(detail_text)
            rg_discovery = tokens(manual.stdout)
            rg_top3 = tokens("\n".join(candidate_lines[:3]) + ("\n" if candidate_lines else ""))
            skill_tokens = tokens(skill_text)
            rows.append({
                "query": query, "chosen_skill": chosen_name, "mcp_rank": next(
                    number for number, item in enumerate(matches, 1) if item["id"] == chosen["id"]),
                "mcp_search_tokens": tokens(search_text), "mcp_detail_tokens": tokens(detail_text),
                "mcp_discovery_tokens": mcp_discovery, "manual_candidate_count": len(candidate_lines),
                "manual_target_rank": rank, "manual_rg_tokens": rg_discovery,
                "manual_top3_tokens": rg_top3,
                "manual_target_in_top3": rank <= 3,
                "skill_md_tokens": skill_tokens,
                "mcp_with_skill_tokens": mcp_discovery + skill_tokens,
                "manual_with_skill_tokens": rg_discovery + skill_tokens,
                "manual_top3_with_skill_tokens": rg_top3 + skill_tokens if rank <= 3 else None,
                "mcp_search_ms": search_ms, "manual_rg_ms": rg_ms,
            })
        return {"tokenizer": "cl100k_base", "catalog_skills": len(catalog["skills"]),
                "catalog_description_tokens": tokens(descriptions),
                "mcp_tool_schema_tokens_once": schema_tokens, "cases": rows}
    finally:
        if server.stdin:
            server.stdin.close()
        server.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.home() / "ai-agent-library")
    print(json.dumps(measure(parser.parse_args().library), indent=2))
