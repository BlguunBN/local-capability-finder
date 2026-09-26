#!/usr/bin/env python3
"""Search the shared AI agent library catalog.

Usage:
    python find_skills.py <query> [<query> ...] [--limit N] [--category C]
                          [--json] [--list-categories] [--show PATH]

Examples:
    python find_skills.py linkedin post
    python find_skills.py --category software-development --limit 40
    python find_skills.py "security audit"
    python find_skills.py --list-categories
    python find_skills.py --show "C:/.../systematic-debugging/SKILL.md"

Exit codes:
    0  at least one match
    1  no matches
    2  catalog missing (run library_catalog.py first)

Output is one line per match by default:
    <score>  <category>/<name>  <skill_md>  <description>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HOME = os.path.expanduser("~")
CATALOG = os.path.join(HOME, "ai-agent-library", "skills_index.json")

# Words that carry no routing signal; ignored unless they are the only tokens.
STOPWORDS = {
    # articles, prepositions, pronouns, auxiliaries
    "a", "an", "and", "the", "for", "with", "to", "of", "in", "on", "my",
    "me", "i", "is", "it", "this", "that", "please", "help", "want", "need",
    "can", "you", "how", "do", "does", "use", "using", "some", "from", "into",
    "out", "up", "down", "over", "when", "where", "which", "there", "here",
    "then", "than", "like", "just", "also", "very", "more", "most", "all",
    "any", "each", "every", "no", "not", "only", "own", "same", "so", "too",
    "now", "will", "would", "should", "could", "may", "might", "must", "shall",
    # action verbs: they describe what the agent does, not what the skill is
    "write", "add", "create", "build", "make", "fix", "improve", "update",
    "change", "get", "set", "run", "check", "find", "give", "show", "tell",
    "look", "new", "working", "work",
}

# Length of the prefix kept when comparing words, so that query and catalog
# vocabulary converge despite inflection ("migrate" ~ "migration").
STEM_MIN = 4


def load(path: str) -> list[dict]:
    if not os.path.isfile(path):
        print(
            f"catalog not found: {path}\n"
            f"regenerate it with: python {os.path.join(HOME, 'ai-agent-library', 'library_catalog.py')}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["skills"]


def _split_words(text: str) -> list[str]:
    return [
        w
        for w in "".join(c if c.isalnum() or c in "-_" else " " for c in text.lower()).split()
        if w
    ]


def tokens(query: str) -> list[str]:
    """Strip noise words, but never return an empty query."""
    raw = _split_words(query)
    meaningful = [t for t in raw if t not in STOPWORDS and len(t) > 1]
    return meaningful or raw


def stem(word: str) -> str:
    """Crude suffix fold so 'migrate'/'migration' and 'test'/'tests' align."""
    if len(word) > STEM_MIN and word.endswith("e"):
        return word[:-1]
    return word


def word_hits(term: str, field: str) -> bool:
    """True when `term` matches `field`, tolerating inflection."""
    if not field:
        return False
    if term in field:
        return True
    s = stem(term)
    for token in _split_words(field):
        if token == s or token.startswith(s):
            return True
        if len(token) >= STEM_MIN and s.startswith(token):
            return True
    return False


# Path fragments marking a vendored/embedded copy rather than a primary store.
VENDOR_MARKERS = (
    ".codex-marketplace",
    ".codex-plugin",
    ".claude-plugin",
    "openai-curated",
    "openai-bundled",
    "openai-primary-runtime",
    os.sep + ".hub" + os.sep,
    "node_modules",
)


def is_vendored(skill: dict) -> bool:
    path = skill["skill_md"]
    return any(marker in path for marker in VENDOR_MARKERS)


def score(skill: dict, terms: list[str], require_all: bool = True) -> int:
    """Higher is better. Name hits dominate, then category, then description.

    Store quality dominates topical fit, so a canonical copy always outranks a
    vendored duplicate even when the duplicate sits in a better-named folder.

    With require_all, every term must match somewhere (AND). Otherwise partial
    matches score proportionally, which the caller uses as a fallback pass.
    """
    name = skill["name"].lower()
    category = skill["category"].lower()
    desc = (skill["description"] or "").lower()
    total = 0
    matched = 0
    for term in terms:
        hit = False
        if term == name:
            total += 100
            hit = True
        elif word_hits(term, name):
            total += 60 if name.startswith(stem(term)) else 45
            hit = True
        if word_hits(term, category):
            total += 25
            hit = True
        if word_hits(term, desc):
            total += 12
            hit = True
        if hit:
            matched += 1
        elif require_all:
            return 0
    if require_all and matched != len(terms):
        return 0
    if matched == 0:
        return 0
    total += 60 if skill.get("canonical", True) else -60
    if is_vendored(skill):
        total -= 40
    return total


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--category", default=None)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--list-categories", action="store_true")
    ap.add_argument("--show", default=None, help="print a SKILL.md by path or name")
    ap.add_argument("--catalog", default=CATALOG)
    args = ap.parse_args()

    if args.show:
        target = args.show
        if not os.path.isfile(target):
            for s in load(args.catalog):
                if s["name"] == target:
                    target = s["skill_md"]
                    break
        if not os.path.isfile(target):
            print(f"not found: {args.show}", file=sys.stderr)
            return 1
        with open(target, encoding="utf-8", errors="replace") as fh:
            print(fh.read())
        return 0

    skills = load(args.catalog)

    if args.list_categories:
        counts: dict[str, int] = {}
        for s in skills:
            counts[s["category"]] = counts.get(s["category"], 0) + 1
        for cat, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{n:5}  {cat}")
        return 0

    if args.category:
        skills = [s for s in skills if s["category"].lower() == args.category.lower()]

    if not args.query:
        for s in skills[: args.limit]:
            print(f"    0  {s['category']}/{s['name']}  {s['skill_md']}")
        return 0 if skills else 1

    terms = tokens(" ".join(args.query))

    def rank(require_all: bool) -> list[tuple[int, dict]]:
        """Collapse duplicates so each skill yields one actionable path."""
        best: dict[str, tuple[int, dict]] = {}
        for s in skills:
            sc = score(s, terms, require_all=require_all)
            if sc <= 0:
                continue
            prev = best.get(s["name"])
            if prev is None or (sc, not is_vendored(s)) > (prev[0], not is_vendored(prev[1])):
                best[s["name"]] = (sc, s)
        return sorted(best.values(), key=lambda x: (-x[0], x[1]["name"].lower()))

    # Strict AND first; if that is empty, fall back to partial matches so a
    # multi-word request never dead-ends on one unknown keyword.
    ranked = rank(True)
    relaxed = False
    if not ranked and len(terms) > 1:
        ranked = rank(False)
        relaxed = True

    if args.as_json:
        print(
            json.dumps(
                [{"score": sc, **s} for sc, s in ranked[: args.limit]],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if ranked else 1

    if not ranked:
        print(f"no match for: {' '.join(terms)}", file=sys.stderr)
        print(
            "try: --list-categories, a single broader term, or grep " +
            os.path.join(HOME, "ai-agent-library", "skills_index.tsv"),
            file=sys.stderr,
        )
        return 1

    if relaxed:
        print("(relaxed: no skill matched every term)", file=sys.stderr)
    for sc, s in ranked[: args.limit]:
        desc = (s["description"] or "(no description)")[:110]
        dup = "" if s.get("canonical", True) else " [dup]"
        print(f"{sc:5}  {s['category']}/{s['name']}{dup}  {s['skill_md']}")
        print(f"       {desc}")
    print(f"\n{len(ranked)} match(es); showing {min(len(ranked), args.limit)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
