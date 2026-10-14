#!/usr/bin/env python3
"""Regenerate the shared AI agent library catalog.

Scans every agent skill store on this machine, deduplicates by real path so
Windows junctions are not double-counted, and writes three artifacts:

  skills_index.json  machine-readable catalog with real absolute paths
  skills_index.md    human-readable catalog grouped by category; do not load into prompts
  skills_index.tsv   one tab-separated line per skill, for grep

Run from anywhere:

    python library_catalog.py

The catalog is a cache, not the source of truth. The SKILL.md files are.
Re-run whenever skills are installed, moved, or removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone

HOME = os.environ.get("CAPFIND_HOME", os.path.expanduser("~"))
LIBRARY = os.environ.get("CAPFIND_LIBRARY", os.path.join(HOME, "ai-agent-library"))

# Root name -> absolute path. Priority order decides which copy wins when the
# same skill is reachable from several stores: the first root that contains a
# skill name claims it, and every later root only contributes an alias.
ROOTS: list[tuple[str, str]] = [
    ("library", os.path.join(LIBRARY, "skills")),
    ("agents", os.path.join(HOME, ".agents", "skills")),
    ("codex", os.path.join(HOME, ".codex", "skills")),
    ("claude", os.path.join(HOME, ".claude", "skills")),
    ("gemini", os.path.join(HOME, ".gemini", "skills")),
    ("antigravity", os.path.join(HOME, ".gemini", "config", "skills")),
    ("openclaw", os.path.join(HOME, ".openclaw", "skills")),
    ("openclaw-autoclaw", os.path.join(HOME, ".openclaw-autoclaw", "skills")),
    ("commandcode", os.path.join(HOME, ".commandcode", "skills")),
    ("opencode", os.path.join(HOME, ".config", "opencode", "skills")),
    ("cursor", os.path.join(HOME, ".cursor", "skills")),
    ("pi", os.path.join(HOME, ".pi", "agent", "skills")),
    ("pi-legacy", os.path.join(HOME, ".pi", "skills")),
    ("omp", os.path.join(HOME, ".omp", "agent", "skills")),
    ("omp-managed", os.path.join(HOME, ".omp", "managed-skills")),
    ("cline", os.path.join(HOME, ".cline", "skills")),
    ("roo", os.path.join(HOME, ".roo", "skills")),
    ("hermes", os.path.join(HOME, ".hermes", "skills")),
    ("kilocode", os.path.join(HOME, ".kilocode", "skills")),
    ("trae", os.path.join(HOME, ".trae", "skills")),
    ("continue", os.path.join(HOME, ".continue", "skills")),
    ("aider-desk", os.path.join(HOME, ".aider-desk", "skills")),
    ("plugin-cache", os.path.join(HOME, ".codex", "plugins", "cache")),
    ("archive", os.path.join(LIBRARY, "on-demand", "skills")),
]

# Directory names that never contain a first-party skill worth cataloging.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".curator_backups", ".archive"}

FM_BLOCK = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)
FM_NAME = re.compile(r"^name:[ \t]*(.+?)[ \t]*$", re.M)
FM_VERSION = re.compile(r"^version:[ \t]*(.+?)[ \t]*$", re.M)
FM_LIST_FIELDS = ("tags", "compatible_agents", "agents", "requirements", "requires")
FM_LIST = re.compile(r"^(tags|compatible_agents|agents|requirements|requires):[ \t]*(.+?)[ \t]*$", re.M)
FM_DESC_INLINE = re.compile(r"^description:[ \t]*(.+?)[ \t]*$", re.M)
FM_DESC_BLOCK = re.compile(r"^description:\s*[|>]-?\s*\n((?:[ \t]+.*\n?)+)", re.M)


def parse_frontmatter(text: str) -> tuple[str | None, str | None]:
    """Return (name, description) from a SKILL.md. Either may be None."""
    block = FM_BLOCK.match(text)
    if not block:
        return None, None
    fm = block.group(1)

    name = None
    m = FM_NAME.search(fm)
    if m:
        name = m.group(1).strip().strip('"').strip("'")

    description = None
    m = FM_DESC_INLINE.search(fm)
    if m and m.group(1).strip() not in {"", "|", ">", "|-", ">-"}:
        description = m.group(1).strip().strip('"').strip("'")
    else:
        m = FM_DESC_BLOCK.search(fm)
        if m:
            description = " ".join(
                line.strip() for line in m.group(1).splitlines() if line.strip()
            )

    if description:
        # Collapse newlines/tabs so the TSV stays one line per skill.
        description = re.sub(r"\s+", " ", description).strip()
    return name, description


def parse_metadata(text: str) -> dict:
    """Return version/tags/agents/requirements declared in frontmatter."""
    meta: dict = {"version": None, "tags": [],
                  "compatible_agents": [], "requirements": []}
    block = FM_BLOCK.match(text)
    if not block:
        return meta
    fm = block.group(1)
    match = FM_VERSION.search(fm)
    if match:
        meta["version"] = match.group(1).strip().strip('"').strip("'") or None
    for field, raw in FM_LIST.findall(fm):
        value = raw.strip()
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        items = [part.strip().strip('"').strip("'") for part in re.split(r"[,;]", value)]
        if len(items) == 1 and "," not in raw:
            items = [part for part in value.split() if part]
        items = [item for item in items if item]
        if field == "tags":
            meta["tags"] = items
        elif field in ("compatible_agents", "agents"):
            meta["compatible_agents"] = items
        else:
            meta["requirements"] = items
    return meta


def _sources_path(library: str) -> str:
    return os.path.join(library, "sources.json")


def load_sources(library: str | None = None) -> list[dict]:
    """Registered extra skill sources (e.g. the companion Stack as `aes`)."""
    path = _sources_path(library or LIBRARY)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return [s for s in sources if isinstance(s, dict) and s.get("id") and s.get("path")]


def save_sources(library: str, sources: list[dict]) -> str:
    os.makedirs(os.path.dirname(_sources_path(library)), exist_ok=True)
    path = _sources_path(library)
    payload = {"schema_version": 1, "sources": sources}
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def register_source(library: str, source_id: str, source_path: str,
                    priority: int = 10, trust: str = "approved") -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source_id):
        raise ValueError(f"invalid source id: {source_id!r}")
    if trust not in ("approved", "unscanned", "needs_review", "blocked"):
        raise ValueError(f"invalid trust status: {trust!r}")
    resolved = os.path.realpath(os.path.expanduser(source_path))
    if not os.path.isdir(resolved):
        raise ValueError(f"source path is not a directory: {source_path}")
    builtins = {name for name, _ in ROOTS}
    if source_id in builtins:
        raise ValueError(f"source id collides with a built-in root: {source_id}")
    sources = [s for s in load_sources(library) if s.get("id") != source_id]
    record = {"id": source_id, "path": resolved, "priority": priority,
              "trust": trust, "kinds": ["skill"],
              "decided_by": "user-registration"}
    sources.append(record)
    sources.sort(key=lambda s: (s.get("priority", 10), s["id"]))
    save_sources(library, sources)
    return record


def unregister_source(library: str, source_id: str) -> None:
    sources = load_sources(library)
    remaining = [s for s in sources if s.get("id") != source_id]
    if len(remaining) == len(sources):
        raise ValueError(f"unknown source: {source_id}")
    save_sources(library, remaining)


def _ordered_roots(library: str | None = None) -> list[tuple[str, str]]:
    """Registered sources first (explicit origin wins), then built-in roots."""
    extra = [(s["id"], s["path"]) for s in load_sources(library)
             if isinstance(s.get("path"), str) and os.path.isdir(s["path"])]
    extra.sort(key=lambda item: next(
        (s.get("priority", 10) for s in load_sources(library) if s.get("id") == item[0]), 10))
    seen = {name for name, _ in extra}
    return extra + [(name, path) for name, path in ROOTS if name not in seen]


def category_of(root: str, skill_dir: str) -> str:
    rel = os.path.relpath(skill_dir, root)
    parts = rel.split(os.sep)
    if len(parts) > 1 and not parts[0].startswith("."):
        return parts[0]
    if parts[0].startswith("."):
        return parts[0]
    return "(top-level)"


def _previous_state(library: str | None = None) -> dict:
    """Parse results cached from the last catalog write, keyed by real path."""
    try:
        with open(os.path.join(library or LIBRARY, "skills_index.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    state = data.get("file_state", {}) if isinstance(data, dict) else {}
    return state if isinstance(state, dict) else {}


def collect(library: str | None = None) -> tuple[list[dict], dict[str, int]]:
    """Walk every root, dedupe by real path, return (skills, per-root counts)."""
    by_realpath: dict[str, dict] = {}
    name_owner: dict[str, str] = {}
    counts: dict[str, int] = {}
    previous = _previous_state(library)

    for root_name, root in _ordered_roots(library):
        found = 0
        if not os.path.isdir(root):
            counts[root_name] = 0
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if "SKILL.md" not in filenames:
                continue

            skill_md = os.path.join(dirpath, "SKILL.md")
            real = os.path.realpath(skill_md)
            found += 1
            try:
                stat = os.stat(skill_md)
            except OSError as exc:
                print(f"  ! unreadable: {skill_md} ({exc})", file=sys.stderr)
                continue
            stat_key = [stat.st_mtime_ns, stat.st_size]

            if real in by_realpath:
                # Same physical file already cataloged: record the alias only.
                entry = by_realpath[real]
                if root_name not in entry["roots"]:
                    entry["roots"].append(root_name)
                continue

            cached = previous.get(real)
            if isinstance(cached, dict) and cached.get("stat") == stat_key:
                fm_name, description = cached.get("name"), cached.get("description", "")
                meta = {"version": cached.get("version"),
                        "tags": cached.get("tags", []),
                        "compatible_agents": cached.get("compatible_agents", []),
                        "requirements": cached.get("requirements", []),
                        "content_hash": cached.get("content_hash")}
            else:
                try:
                    with open(skill_md, "rb") as handle:
                        raw = handle.read()
                except OSError as exc:
                    print(f"  ! unreadable: {skill_md} ({exc})", file=sys.stderr)
                    continue
                text = raw.decode("utf-8", errors="replace")
                fm_name, description = parse_frontmatter(text)
                meta = parse_metadata(text)
                meta["content_hash"] = "sha256:" + hashlib.sha256(raw).hexdigest()
            dir_name = os.path.basename(dirpath)
            name = fm_name or dir_name

            # A later root may hold a physically distinct skill that happens to
            # share a name. Keep it, but the first root's copy is canonical.
            canonical = name_owner.setdefault(name, root_name)

            by_realpath[real] = {
                "name": name,
                "category": category_of(root, dirpath),
                "path": dirpath,
                "skill_md": skill_md,
                "description": description or "",
                "source": root_name,
                "canonical": canonical == root_name,
                "roots": [root_name],
                "version": meta["version"],
                "tags": meta["tags"],
                "compatible_agents": meta["compatible_agents"],
                "requirements": meta["requirements"],
                "content_hash": meta["content_hash"],
                "realpath": real,
                "stat": stat_key,
            }

        counts[root_name] = found

    skills = sorted(
        by_realpath.values(),
        key=lambda s: (s["category"], s["name"].lower()),
    )
    return skills, counts


def _atomic_write(path: str, content: str) -> None:
    """Replace one generated catalog file only after its new content is complete."""
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=os.path.dirname(path),
            prefix=".catalog-", suffix=".tmp", delete=False,
        ) as fh:
            temp_path = fh.name
            fh.write(content)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def write_json(skills: list[dict], counts: dict[str, int]) -> str:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "library_root": LIBRARY,
        "counts_by_root": counts,
        "count": len(skills),
        "skills": skills,
        "file_state": {
            s["realpath"]: {"stat": s.get("stat"), "name": s.get("name"),
                            "description": s.get("description", ""),
                            "version": s.get("version"), "tags": s.get("tags", []),
                            "compatible_agents": s.get("compatible_agents", []),
                            "requirements": s.get("requirements", []),
                            "content_hash": s.get("content_hash")}
            for s in skills if isinstance(s, dict) and s.get("realpath")
        },
    }
    out = os.path.join(LIBRARY, "skills_index.json")
    _atomic_write(out, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return out


def write_markdown(skills: list[dict], counts: dict[str, int]) -> str:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for s in skills:
        by_cat[s["category"]].append(s)

    lines = [
        "# Skill Catalog",
        "",
        "> **Do not load this catalog into an agent prompt.** Use the capability finder to search and read one selected `SKILL.md`.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ",
        f"Skills: **{len(skills)}** across **{len(by_cat)}** categories  ",
        "Canonical store: `" + os.path.join(LIBRARY, "skills") + "`",
        "",
        "Every `SKILL.md` below is a real absolute path. Read the file to use the skill.",
        "This catalog is a cache — the `SKILL.md` files are the source of truth.",
        "",
        "| Root | SKILL.md found |",
        "| --- | --- |",
    ]
    for root_name, _ in ROOTS:
        lines.append(f"| `{root_name}` | {counts.get(root_name, 0)} |")

    for cat in sorted(by_cat):
        entries = by_cat[cat]
        lines += ["", f"## {cat} ({len(entries)})", ""]
        for s in entries:
            desc = s["description"] or "(no description)"
            alias = "" if s["canonical"] else " *(duplicate name)*"
            lines.append(f"- **{s['name']}**{alias} — {desc}")
            lines.append(f"  - `{s['skill_md']}`")

    out = os.path.join(LIBRARY, "skills_index.md")
    _atomic_write(out, "\n".join(lines) + "\n")
    return out


def write_tsv(skills: list[dict]) -> str:
    out = os.path.join(LIBRARY, "skills_index.tsv")
    lines = ["name\tcategory\tsource\tskill_md\tdescription"]
    for s in skills:
        lines.append("\t".join(
            [s["name"], s["category"], s["source"], s["skill_md"], s["description"]]
        ))
    _atomic_write(out, "\n".join(lines) + "\n")
    return out


def scan_secrets(quiet: bool = False) -> int:
    """Count obvious plaintext secrets in non-profile agent configs."""
    key_re = re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password"
        r"|personal[_-]?access[_-]?token|bot[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*(.+)"
    )
    targets = [
        os.path.join(HOME, ".hermes", "config.yaml"),
        os.path.join(HOME, ".openclaw", "openclaw.json.last-good"),
        os.path.join(HOME, ".omp", "agent", "mcp.json"),
        os.path.join(HOME, ".codex", "config.toml"),
    ]
    findings = []
    for path in targets:
        if not os.path.isfile(path):
            continue
        for lineno, line in enumerate(
            open(path, encoding="utf-8", errors="replace"), start=1
        ):
            m = key_re.search(line)
            if not m:
                continue
            value = m.group(2).strip().strip(",").strip('"').strip("'")
            if not value or value.startswith("$") or value.startswith("${"):
                continue
            if re.search(r"(?i)(replace|your[_-]|example|placeholder|changeme)", value):
                continue
            if len(value) < 12:
                continue
            findings.append((path, lineno, m.group(1)))
    if findings and not quiet:
        print("\nPlaintext secret candidates (review these):")
        for path, lineno, key in findings:
            print(f"  {path}:{lineno}  {key}")
    return len(findings)


def catalog_age_hours() -> float | None:
    """Age of skills_index.json in hours, or None when it does not exist.

    Deliberately O(1): a full walk to decide whether to walk costs about half a
    full regeneration, so gating on a walk would save little.
    """
    path = os.path.join(LIBRARY, "skills_index.json")
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("register-source", "unregister-source", "list-sources"):
        return _source_command(args)
    ap = argparse.ArgumentParser(description="Regenerate the shared skill catalog.")
    ap.add_argument(
        "--if-stale",
        type=float,
        default=None,
        metavar="HOURS",
        help="do nothing when the catalog is younger than HOURS",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress output (for hooks and scheduled tasks)",
    )
    args = ap.parse_args(argv)

    def say(message: str) -> None:
        if not args.quiet:
            print(message)

    if args.if_stale is not None:
        age = catalog_age_hours()
        if age is not None and age < args.if_stale:
            from capability_finder import _load
            try:
                _load(None, None)
            except (FileNotFoundError, ValueError):
                pass
            else:
                say(f"catalog is {age:.1f}h old (< {args.if_stale:g}h); skipping")
                return 0

    say(f"Scanning roots under {HOME} ...")
    skills, counts = collect()
    for root_name, _ in ROOTS:
        say(f"  {root_name:10} {counts.get(root_name, 0):5} SKILL.md")
    say(f"  {'cataloged':10} {len(skills):5} unique skills")

    for writer in (write_json, write_markdown, write_tsv):
        path = writer(skills, counts) if writer is not write_tsv else writer(skills)
        say(f"  wrote {path}")

    try:
        from capability_finder import refresh_index
        indexed = refresh_index()
        say(f"  {'capabilities':10} {len(indexed['capabilities']):5} indexed")
    except (OSError, ValueError) as exc:
        print(f"  ! capability index refresh failed: {exc}", file=sys.stderr)
        return 1

    scan_secrets(quiet=args.quiet)
    return 0


def _source_command(args: list[str]) -> int:
    """Register, remove, or list extra skill sources (e.g. the Stack as `aes`)."""
    command = args[0]
    rest = args[1:]
    if command == "list-sources":
        ap = argparse.ArgumentParser(description="List registered skill sources.")
        ap.add_argument("--library", default=LIBRARY)
        ap.add_argument("--json", action="store_true")
        opts = ap.parse_args(rest)
        sources = load_sources(opts.library)
        if opts.json:
            print(json.dumps(sources, indent=2))
        else:
            for source in sources:
                print(f"{source['id']}  {source['path']}  trust={source.get('trust')}")
            if not sources:
                print("no registered sources")
        return 0
    if command == "register-source":
        ap = argparse.ArgumentParser(description="Register an extra skill source.")
        ap.add_argument("--id", required=True)
        ap.add_argument("--path", required=True)
        ap.add_argument("--priority", type=int, default=10)
        ap.add_argument("--trust", default="approved",
                        choices=("approved", "unscanned", "needs_review", "blocked"))
        ap.add_argument("--library", default=LIBRARY)
        opts = ap.parse_args(rest)
        try:
            record = register_source(opts.library, opts.id, opts.path,
                                     opts.priority, opts.trust)
        except (OSError, ValueError) as exc:
            print(f"register-source: {exc}", file=sys.stderr)
            return 2
        print(f"registered {record['id']} -> {record['path']}; run library_catalog.py to index it")
        return 0
    ap = argparse.ArgumentParser(description="Remove a registered skill source.")
    ap.add_argument("--id", required=True)
    ap.add_argument("--library", default=LIBRARY)
    opts = ap.parse_args(rest)
    try:
        unregister_source(opts.library, opts.id)
    except (OSError, ValueError) as exc:
        print(f"unregister-source: {exc}", file=sys.stderr)
        return 2
    print(f"unregistered {opts.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
