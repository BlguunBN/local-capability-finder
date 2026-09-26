"""Build a categorized junction view of the local skill catalog with Laya."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "skills_index.json"
OUTPUT = ROOT / "organized-skills"
CACHE = OUTPUT / "assignments.json"
MANIFEST = OUTPUT / "manifest.json"
OVERRIDES = OUTPUT / "overrides.json"

GROUPS = {
    "software-engineering": "Code, testing, architecture, and developer tools",
    "ai-agents-and-ml": "AI agents, model use, training, and evaluation",
    "design-and-ux": "Interfaces, accessibility, and visual design",
    "creative-and-media": "Images, video, audio, slides, and documents",
    "data-and-research": "Data analysis, science, and research",
    "cloud-and-security": "Cloud, deployment, security, and systems",
    "business-and-marketing": "Business, sales, marketing, finance, and support",
    "writing-and-productivity": "Writing, planning, notes, and collaboration",
    "specialized-tools-and-hardware": "CAD, manufacturing, robotics, games, and devices",
}

CATEGORY_GROUP = {
    "software-development": "software-engineering",
    "superpowers": "software-engineering",
    "chisle": "software-engineering",
    "evaluate-skill": "software-engineering",
    "frontend-developer": "software-engineering",
    "nextjs-best-practices": "software-engineering",
    "react-patterns": "software-engineering",
    "tanstack-query-expert": "software-engineering",
    "trpc-fullstack": "software-engineering",
    "zod-validation-expert": "software-engineering",
    "zustand-store-ts": "software-engineering",
    "web-performance-optimization": "software-engineering",
    "mcp": "ai-agents-and-ml",
    "autonomous-ai-agents": "ai-agents-and-ml",
    "mlops": "ai-agents-and-ml",
    "omh": "ai-agents-and-ml",
    "skill-finder": "ai-agents-and-ml",
    "design-it": "design-and-ux",
    "bento-ui": "design-and-ux",
    "dark-mode": "design-and-ux",
    "dashboard-design": "design-and-ux",
    "glassmorphism": "design-and-ux",
    "high-end-visual-design": "design-and-ux",
    "interactive-portfolio": "design-and-ux",
    "minimalism": "design-and-ux",
    "neo-brutalism": "design-and-ux",
    "shadcn": "design-and-ux",
    "swiss-design": "design-and-ux",
    "tailwind-patterns": "design-and-ux",
    "typography-first": "design-and-ux",
    "ui-a11y": "design-and-ux",
    "ui-review": "design-and-ux",
    "ui-visual-validator": "design-and-ux",
    "ux-audit": "design-and-ux",
    "ux-copy": "design-and-ux",
    "ux-feedback": "design-and-ux",
    "ux-flow": "design-and-ux",
    "uxui-principles": "design-and-ux",
    "creative": "creative-and-media",
    "video-skills": "creative-and-media",
    "media": "creative-and-media",
    "web-artifacts-builder": "creative-and-media",
    "data-science": "data-and-research",
    "research": "data-and-research",
    "devops": "cloud-and-security",
    "cloudflare": "cloud-and-security",
    "red-teaming": "cloud-and-security",
    "linkedin-marketing": "business-and-marketing",
    "linkedin-skills": "business-and-marketing",
    "social-media": "business-and-marketing",
    "email": "business-and-marketing",
    "productivity": "writing-and-productivity",
    "note-taking": "writing-and-productivity",
    "apple": "specialized-tools-and-hardware",
    "gaming": "specialized-tools-and-hardware",
    "smart-home": "specialized-tools-and-hardware",
    "threejs-skills": "specialized-tools-and-hardware",
    "3d-web-experience": "design-and-ux",
    "scroll-experience": "design-and-ux",
}

NAME_GROUP = {
    "3d-web-experience": "design-and-ux",
    "accelerated-computing-cudf": "data-and-research",
    "agents-sdk": "ai-agents-and-ml",
    "aiq-deploy": "ai-agents-and-ml",
    "aiq-research": "data-and-research",
    "agent-browser-verify": "software-engineering",
    "ask-matt": "ai-agents-and-ml",
    "autopilot": "software-engineering",
    "autoresearch": "data-and-research",
    "bambu-labs": "specialized-tools-and-hardware",
    "baseline-ui": "design-and-ux",
    "bento-ui": "design-and-ux",
    "brag": "creative-and-media",
    "brainstorm-ideas-new": "business-and-marketing",
    "business-model": "business-and-marketing",
    "canvas": "creative-and-media",
    "cad": "specialized-tools-and-hardware",
    "cad-viewer": "specialized-tools-and-hardware",
    "clarify": "writing-and-productivity",
    "conversion-ops": "business-and-marketing",
    "create-design-md": "design-and-ux",
    "create-prd": "business-and-marketing",
    "data-designer": "data-and-research",
    "deck-generator": "creative-and-media",
    "deslop": "design-and-ux",
    "drive-desktop-app": "writing-and-productivity",
    "dxf": "specialized-tools-and-hardware",
    "embedded-captions": "creative-and-media",
    "evaluate-environments": "ai-agents-and-ml",
    "everything-search": "writing-and-productivity",
    "podcast-pipeline": "creative-and-media",
    "pr": "software-engineering",
    "privacy-policy": "business-and-marketing",
    "resolving-merge-conflicts": "software-engineering",
    "sdk": "software-engineering",
    "sendcutsend": "specialized-tools-and-hardware",
    "shell": "software-engineering",
    "slideshow": "creative-and-media",
    "split-to-prs": "software-engineering",
    "statusline": "software-engineering",
    "gcode": "specialized-tools-and-hardware",
    "motion-graphics": "creative-and-media",
}

PLUGIN_GROUP = {
    "sales": "business-and-marketing",
    "legal": "business-and-marketing",
    "operations": "business-and-marketing",
    "finance": "business-and-marketing",
    "product-management": "business-and-marketing",
    "marketing": "business-and-marketing",
    "customer-support": "business-and-marketing",
    "small-business": "business-and-marketing",
    "data": "data-and-research",
    "data-analytics": "data-and-research",
    "windsor-ai": "data-and-research",
    "engineering": "software-engineering",
    "design": "design-and-ux",
    "figma": "design-and-ux",
    "productivity": "writing-and-productivity",
    "google-drive": "writing-and-productivity",
    "airtable": "writing-and-productivity",
    "pdf-viewer": "creative-and-media",
    "canva": "creative-and-media",
    "app-69312da8e4dc81919370cb86fd172b6c": "creative-and-media",
    "youtube-conversation": "creative-and-media",
    "hugging-face": "ai-agents-and-ml",
    "openai-developers": "ai-agents-and-ml",
    "plugin-management": "ai-agents-and-ml",
}

NAME_PREFIX_GROUP = {
    "caveman-": "software-engineering",
    "cloudflare-": "cloud-and-security",
    "cudaq-": "specialized-tools-and-hardware",
    "cuopt-": "specialized-tools-and-hardware",
    "cupynumeric-": "data-and-research",
    "deepstream-": "ai-agents-and-ml",
    "dicom-": "data-and-research",
    "digital-health-": "ai-agents-and-ml",
    "dynamo-": "ai-agents-and-ml",
    "earth2studio-": "data-and-research",
    "firecrawl-": "data-and-research",
    "hyperframes-": "creative-and-media",
    "linkedin-": "business-and-marketing",
    "nemo-": "ai-agents-and-ml",
    "nemoclaw-": "ai-agents-and-ml",
    "nemotron-": "ai-agents-and-ml",
    "nv-": "ai-agents-and-ml",
    "physical-ai-": "ai-agents-and-ml",
    "physicsnemo-": "ai-agents-and-ml",
    "pr-to-video": "creative-and-media",
    "product-launch-video": "creative-and-media",
    "remotion-": "creative-and-media",
    "seo-": "business-and-marketing",
    "tao-": "ai-agents-and-ml",
    "vss-": "ai-agents-and-ml",
    "cad-": "specialized-tools-and-hardware",
    "dxf-": "specialized-tools-and-hardware",
    "gcode-": "specialized-tools-and-hardware",
    "bambu-": "specialized-tools-and-hardware",
    "ui-": "design-and-ux",
    "ux-": "design-and-ux",
    "design-": "design-and-ux",
    "image-": "creative-and-media",
    "video-": "creative-and-media",
    "audio-": "creative-and-media",
    "pdf-": "creative-and-media",
    "pptx-": "creative-and-media",
    "docx-": "creative-and-media",
    "marketing-": "business-and-marketing",
    "sales-": "business-and-marketing",
    "customer-": "business-and-marketing",
    "legal-": "business-and-marketing",
    "finance-": "business-and-marketing",
    "business-": "business-and-marketing",
    "data-": "data-and-research",
    "research-": "data-and-research",
    "cloud-": "cloud-and-security",
    "security-": "cloud-and-security",
}

NAME_TOKEN_GROUPS = [
    ("specialized-tools-and-hardware", {"cad", "bambu", "gcode", "urdf", "srdf", "sdf", "robotics", "manufacturing"}),
    ("business-and-marketing", {"linkedin", "seo", "finance", "financial", "sales", "marketing", "market", "business", "customer", "revenue", "pricing", "legal", "contract", "investment", "investor", "payments", "stripe"}),
    ("ai-agents-and-ml", {"ai", "agent", "agents", "llm", "model", "models", "train", "training", "finetune", "inference", "prompt", "rag", "mcp", "nemotron"}),
    ("design-and-ux", {"ui", "ux", "design", "figma", "accessibility", "a11y", "layout", "typography", "prototype"}),
    ("creative-and-media", {"video", "audio", "music", "caption", "captions", "podcast", "image", "photo", "slide", "slides", "presentation", "document", "documents", "pdf", "pptx", "docx", "spreadsheet", "graphics", "media"}),
    ("data-and-research", {"data", "dataset", "analytics", "research", "jupyter", "statistics", "science", "benchmark"}),
    ("cloud-and-security", {"cloud", "deploy", "deployment", "security", "docker", "kubernetes", "sandbox", "infra", "infrastructure", "server", "hosting", "network", "cdn"}),
    ("software-engineering", {"code", "coding", "test", "testing", "git", "github", "npm", "react", "nextjs", "sdk", "bug", "bugs", "pr", "repo", "browser", "api"}),
    ("writing-and-productivity", {"writing", "notes", "note", "meeting", "schedule", "calendar", "planning", "knowledge", "spec", "template", "report"}),
]


def name_token_group(name: str) -> str | None:
    tokens = set(name.lower().replace("_", "-").split("-"))
    return next((group for group, words in NAME_TOKEN_GROUPS if tokens & words), None)

QUESTION = {
    "category": {
        "type": "choice",
        "instructions": "Choose the primary topic of this agent skill from its name and description.",
        "criteria": GROUPS,
    }
}


def save_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def junction(link: Path, target: Path) -> None:
    if link.exists():
        if link.is_junction() and os.path.samefile(link, target):
            return
        raise RuntimeError(f"Refusing to replace existing path: {link}")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"Could not link {link} to {target}: {result.stderr or result.stdout}")


def main() -> None:
    skills = json.loads(CATALOG.read_text(encoding="utf-8"))["skills"]
    skills = [s for s in skills if s.get("canonical") and Path(s["path"]).is_dir()]
    OUTPUT.mkdir(exist_ok=True)
    previous = json.loads(MANIFEST.read_text(encoding="utf-8"))["skills"] if MANIFEST.exists() else []
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    if set(overrides.values()) - set(GROUPS):
        raise ValueError("overrides.json contains an unknown category")
    assignments = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    peer_groups = {}
    for skill in skills:
        group = CATEGORY_GROUP.get(skill["category"])
        if group:
            peer_groups.setdefault(skill["name"], set()).add(group)
    pending = []
    for skill in skills:
        path = skill["path"]
        name, category = skill["name"], skill["category"]
        if name in overrides:
            group, method = overrides[name], "manual override"
        elif name in NAME_GROUP:
            group, method = NAME_GROUP[name], "name rule"
        elif category in CATEGORY_GROUP:
            group, method = CATEGORY_GROUP[category], "existing category"
        elif (prefix := next((p for p in NAME_PREFIX_GROUP if name.startswith(p)), None)):
            group, method = NAME_PREFIX_GROUP[prefix], "name rule"
        elif category in ("claude-cowork", "openai-curated-remote") and (
            plugin := Path(path).parts[Path(path).parts.index(category) + 1]
        ) in PLUGIN_GROUP:
            group, method = PLUGIN_GROUP[plugin], "plugin category"
        elif len(peer_groups.get(name, ())) == 1:
            group, method = next(iter(peer_groups[name])), "peer category"
        elif (group := name_token_group(name)):
            method = "name tokens"
        elif path in assignments:
            continue
        else:
            pending.append(skill)
            continue
        assignments[path] = {"group": group, "method": method}
    save_json(CACHE, assignments)

    if pending:
        from laya import Router

        router = Router()
        for offset in range(0, len(pending), 32):
            batch = pending[offset : offset + 32]
            requests = [
                {
                    "state": {"skill_name": s["name"], "description": (s.get("description") or "")[:500]},
                    "questions": QUESTION,
                    "model": "english",
                }
                for s in batch
            ]
            results = router.predict_batch(requests, batch_size=8)
            for skill, result in zip(batch, results, strict=True):
                group = result["answers"]["category"]["choice"]
                if group not in GROUPS:
                    raise RuntimeError(f"Unexpected category {group!r} for {skill['name']}")
                assignments[skill["path"]] = {"group": group, "method": "laya"}
            save_json(CACHE, assignments)
            print(f"Laya categorized {min(offset + 32, len(pending))}/{len(pending)}", flush=True)

    records = []
    used = set()
    for skill in sorted(skills, key=lambda s: (s["name"], s["source"], s["path"])):
        path = skill["path"]
        group = assignments[path]["group"]
        link_name = skill["name"]
        if (group, link_name.casefold()) in used:
            link_name = f"{link_name}--{skill['source']}"
        suffix = 2
        base = link_name
        while (group, link_name.casefold()) in used:
            link_name = f"{base}-{suffix}"
            suffix += 1
        used.add((group, link_name.casefold()))
        folder = OUTPUT / group
        folder.mkdir(exist_ok=True)
        link = folder / link_name
        records.append({
            "name": skill["name"], "group": group, "link": str(link),
            "source": path, "method": assignments[path]["method"],
            "description": skill.get("description") or "",
        })

    expected = {record["link"]: record["source"] for record in records}
    for record in previous:
        old_link = Path(record["link"])
        if expected.get(str(old_link)) != record["source"] and old_link.parent.parent == OUTPUT and old_link.is_junction():
            old_link.rmdir()

    for record in records:
        junction(Path(record["link"]), Path(record["source"]))

    save_json(MANIFEST, {"count": len(records), "groups": dict(Counter(r["group"] for r in records)), "skills": records})
    print(f"Organized {len(records)} skills in {OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
