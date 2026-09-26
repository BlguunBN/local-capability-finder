"""Portable search-quality benchmark using a synthetic local catalog."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capability_finder import refresh_index, search


SKILLS = {
    "cad-viewer": "Inspect and review STEP CAD files and 3D models",
    "cudaq-guide": "Develop CUDA quantum computing programs",
    "cloudflare": "Deploy applications to Cloudflare Workers",
    "pdf-editor": "Edit PDF documents and pages",
    "dashboard-design": "Design analytics dashboards and charts",
    "dicom-series-preflight": "Validate DICOM medical imaging series",
}
CASES = [
    ("inspect a STEP CAD file", "skill", "cad-viewer"),
    ("CUDA quantum computing", "skill", "cudaq-guide"),
    ("deploy Cloudflare Workers", "skill", "cloudflare"),
    ("edit a PDF document", "skill", "pdf-editor"),
    ("design analytics dashboard", "skill", "dashboard-design"),
    ("check DICOM imaging series", "skill", "dicom-series-preflight"),
    ("automate browser with Playwright", "tool", "playwright"),
    ("manage Figma designs", "plugin", "figma"),
    ("qzxqzxunmatched", None, None),
]


def create_fixture(home: Path) -> Path:
    """Create a small complete catalog, with all paths inside the caller's home."""
    library = home / "ai-agent-library"
    library.mkdir(parents=True)
    rows = []
    for name, description in SKILLS.items():
        folder = home / ".agents" / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8"
        )
        rows.append({"name": name, "description": description, "path": str(folder),
                     "skill_md": str(folder / "SKILL.md"), "source": "agents",
                     "canonical": True, "roots": ["agents"]})
    (library / "skills_index.json").write_text(json.dumps({"skills": rows}), encoding="utf-8")
    (library / "mcp_servers.yaml").write_text(
        "mcp_servers:\n  playwright:\n    description: Automate browser with Playwright\n    command: npx\n    args: ['@playwright/mcp']\n",
        encoding="utf-8",
    )
    manifest = home / ".codex" / "plugins" / "cache" / "sample" / "figma" / "1.0" / ".codex-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": "figma", "version": "1.0", "description": "Manage Figma designs"}),
        encoding="utf-8",
    )
    return library


def main() -> int:
    misses = []
    max_bytes = 0
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        library = create_fixture(home)
        refresh_index(home=home, library=library)
        for query, kind, expected in CASES:
            results = search(query, kind=kind, home=home, library=library)
            names = [row["name"] for row in results]
            max_bytes = max(max_bytes, len(json.dumps(results, ensure_ascii=False).encode("utf-8")))
            if (expected is None and results) or (expected is not None and expected not in names):
                misses.append((query, expected, names))
    # A conservative byte ceiling keeps default result payloads compact without
    # depending on a particular model's tokeniser or a native Python package.
    print(f"top-3: {len(CASES) - len(misses)}/{len(CASES)}; max JSON bytes: {max_bytes}")
    for miss in misses:
        print("MISS", miss)
    return 1 if misses or max_bytes >= 2400 else 0


if __name__ == "__main__":
    raise SystemExit(main())
