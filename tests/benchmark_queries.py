"""Run after refresh to check representative local intent searches."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capability_finder import search
import tiktoken


CASES = [
    ("view CAD file", "skill", "cad-viewer"),
    ("install cuopt", "skill", "cuopt-install"),
    ("CUDA quantum computing", "skill", "cudaq-guide"),
    ("make a PowerPoint", None, "powerpoint"),
    ("review code security", "skill", "review-security"),
    ("deploy Cloudflare Workers", None, "cloudflare"),
    ("browser automation", None, "browser"),
    ("analyze spreadsheet", None, "spreadsheets"),
    ("edit PDF", None, "pdf"),
    ("LinkedIn marketing", "skill", "linkedin-marketing"),
    ("Bambu Labs printer", "skill", "bambu-labs"),
    ("debug broken UI", "skill", "debug-broken-ui"),
    ("write PRD", "skill", "create-prd"),
    ("Remotion video", "skill", "remotion-video-production"),
    ("build MCP server", "skill", "build-mcp-server"),
    ("knowledge graph", "skill", "graphify"),
    ("design dashboard", "skill", "dashboard-design"),
    ("fix DICOM series", "skill", "dicom-series-preflight"),
    ("create Figma design", None, "figma-generate-design"),
    ("playwright browser", "tool", "playwright"),
    ("cloudflare", "plugin", "cloudflare"),
]


def main() -> int:
    failures = []
    max_bytes = 0
    max_tokens = 0
    encoder = tiktoken.get_encoding("cl100k_base")
    for query, kind, expected in CASES:
        results = search(query, kind=kind)
        response = json.dumps(results, ensure_ascii=False)
        max_bytes = max(max_bytes, len(response.encode("utf-8")))
        max_tokens = max(max_tokens, len(encoder.encode(response)))
        if expected.casefold() not in {row["name"].casefold() for row in results}:
            failures.append((query, expected, [row["name"] for row in results]))
    print(f"top-3: {len(CASES) - len(failures)}/{len(CASES)}; max JSON bytes: {max_bytes}; max cl100k tokens: {max_tokens}")
    for failure in failures:
        print("MISS", failure)
    return 1 if failures or max_tokens >= 600 else 0


if __name__ == "__main__":
    raise SystemExit(main())
