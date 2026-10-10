"""Reversible, opt-in pilot for archiving specialist agent skills.

The default command only writes a reviewable dry-run manifest. It never moves
or removes skills unless called with --apply. Restore is explicit via --restore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USER_ROOT = Path(os.environ.get("CAPFIND_HOME", str(Path.home())))


def _default_library() -> Path:
    override = os.environ.get("CAPFIND_LIBRARY")
    return Path(override).expanduser() if override else USER_ROOT / "ai-agent-library"


def _archive_base(manifest: dict | None = None) -> Path:
    """Shared vault location: the manifest's own root, else the configured one.

    Older manifests recorded a vault under the repo checkout; honoring the
    manifest keeps those restores working now that the default lives in the
    user library. CAPFIND_ARCHIVE overrides the default for new manifests.
    """
    if manifest:
        recorded = manifest.get("archive_root")
        if isinstance(recorded, str) and recorded:
            return Path(recorded)
    override = os.environ.get("CAPFIND_ARCHIVE")
    if override:
        return Path(override).expanduser()
    return _default_library() / "on-demand"


DATA = _archive_base()
MANIFEST = DATA / "pilot-manifest.json"
PILOT = ["cad-viewer", "bambu-labs", "cudaq-guide", "cuopt-install", "cupynumeric-install"]
STORE_ROOTS = {
    "agents": USER_ROOT / ".agents" / "skills",
    "codex": USER_ROOT / ".codex" / "skills",
    "claude": USER_ROOT / ".claude" / "skills",
    "gemini": USER_ROOT / ".gemini" / "skills",
    "antigravity": USER_ROOT / ".gemini" / "config" / "skills",
    "openclaw": USER_ROOT / ".openclaw" / "skills",
    "openclaw-autoclaw": USER_ROOT / ".openclaw-autoclaw" / "skills",
}
ALL_AGENT_ROOTS = {
    "agents": USER_ROOT / ".agents" / "skills",
    "gemini": USER_ROOT / ".gemini" / "skills",
    "antigravity": USER_ROOT / ".gemini" / "config" / "skills",
    "claude": USER_ROOT / ".claude" / "skills",
    "codex": USER_ROOT / ".codex" / "skills",
    "commandcode": USER_ROOT / ".commandcode" / "skills",
    "opencode": USER_ROOT / ".config" / "opencode" / "skills",
    "cursor": USER_ROOT / ".cursor" / "skills",
    "pi_agent": USER_ROOT / ".pi" / "agent" / "skills",
    "pi": USER_ROOT / ".pi" / "skills",
    "omp_agent": USER_ROOT / ".omp" / "agent" / "skills",
    "omp_managed": USER_ROOT / ".omp" / "managed-skills",
    "cline": USER_ROOT / ".cline" / "skills",
    "roo": USER_ROOT / ".roo" / "skills",
    "openclaw": USER_ROOT / ".openclaw" / "skills",
    "openclaw-autoclaw": USER_ROOT / ".openclaw-autoclaw" / "skills",
    "hermes": USER_ROOT / ".hermes" / "skills",
    "kilocode": USER_ROOT / ".kilocode" / "skills",
    "trae": USER_ROOT / ".trae" / "skills",
    "continue": USER_ROOT / ".continue" / "skills",
    "aider_desk": USER_ROOT / ".aider-desk" / "skills",
}


def inside(path: Path, root: Path) -> bool:
    try:
        candidate = normalized_path(path.resolve(strict=False))
        boundary = normalized_path(root.resolve(strict=False))
        return os.path.commonpath((candidate, boundary)) == boundary
    except (ValueError, OSError, RuntimeError):
        return False


def normalized_path(path: Path) -> str:
    value = os.path.normcase(os.path.abspath(str(path)))
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return value.rstrip("\\/")


def same_path(left: Path, right: Path) -> bool:
    return normalized_path(left.resolve(strict=False)) == normalized_path(right.resolve(strict=False))


def is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def link_target(path: Path) -> str | None:
    if not is_link(path):
        return None
    return os.readlink(path)


def junction(path: Path) -> bool:
    probe = getattr(path, "is_junction", None)
    if probe:
        return bool(probe())
    try:
        return getattr(path.lstat(), "st_reparse_tag", 0) == 0xA0000003
    except OSError:
        return False


def link_kind(path: Path) -> str:
    if junction(path):
        return "junction"
    if path.is_symlink():
        return "directory_symlink" if path.is_dir() else "file_symlink"
    return "directory"


def safe_archive_destination(path: Path, base: Path | None = None) -> bool:
    """Reject symlink/reparse-point parents and any destination outside base."""
    base = base or DATA
    if not inside(path, base) or not inside(base, USER_ROOT) or is_link(base):
        return False
    current = path
    while current != base and inside(current, base):
        if current.exists() or current.is_symlink():
            if is_link(current):
                return False
        current = current.parent
    return current == base


def remove_directory_link(path: Path, kind: str) -> None:
    # Junctions need directory removal; every symlink flavor unlinks on all
    # platforms (os.rmdir on a POSIX directory symlink raises ENOTDIR).
    if kind == "junction":
        os.rmdir(path)
    else:
        path.unlink()


def recreate_link(path: Path, raw_target: str, kind: str) -> None:
    if kind == "junction":
        # Keep paths in stdin as data; cmd.exe /c mklink would parse metacharacters
        # in a recorded target as commands during restore.
        script = (
            "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false); "
            "$link = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
            "New-Item -ItemType Junction -Path $link.path -Target $link.target "
            "-ErrorAction Stop | Out-Null"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=json.dumps({"path": str(path), "target": normalized_path(Path(raw_target))}),
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if result.returncode:
            raise OSError(result.stderr.strip() or result.stdout.strip() or f"junction creation failed for {path}")
    elif kind == "directory_symlink":
        os.symlink(raw_target, path, target_is_directory=True)
    elif kind == "file_symlink":
        os.symlink(raw_target, path, target_is_directory=False)
    else:
        raise RuntimeError(f"unsupported recorded link kind: {kind}")


def tree_hash(path: Path) -> str:
    """Hash names, file contents and link targets without following nested links."""
    h = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix().casefold()):
        rel = item.relative_to(path).as_posix()
        h.update(rel.encode("utf-8", "surrogatepass") + b"\0")
        if is_link(item):
            h.update(b"L\0" + os.readlink(item).encode("utf-8", "surrogatepass"))
        elif item.is_file():
            h.update(b"F\0")
            with item.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        else:
            h.update(b"D\0")
    return h.hexdigest()


def load_candidates(library: Path | None = None) -> dict[str, dict[str, str]]:
    """Map skill name -> {store: skill_dir} from the generated skill catalog."""
    catalog = Path(library).expanduser() if library else _default_library()
    catalog = catalog / "skills_index.json"
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"skill catalog unreadable: {catalog}; run: library_catalog.py") from exc
    rows = data.get("skills", []) if isinstance(data, dict) else []
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("name") or not row.get("path"):
            continue
        stores = [str(store) for store in row.get("roots", []) if isinstance(store, str)]
        if not stores and row.get("source"):
            stores = [str(row["source"])]
        for store in stores:
            index.setdefault(str(row["name"]), {}).setdefault(store, str(row["path"]))
    return index


def build_manifest(names: list[str] | None = None, library: Path | None = None) -> dict:
    if not safe_archive_destination(DATA):
        raise RuntimeError(f"archive destination escapes user root: {DATA}")
    catalog_path = (Path(library).expanduser() if library else _default_library()) / "skills_index.json"
    candidates = load_candidates(library)
    selected = names or PILOT
    if not selected or len(set(selected)) != len(selected):
        raise RuntimeError("select at least one unique skill name")
    entries = []
    for name in selected:
        catalogued = candidates.get(name, {})
        unknown = [s for s in catalogued if s != "library" and s not in ALL_AGENT_ROOTS]
        if unknown:
            raise RuntimeError(f"skill {name} lists stores this archiver cannot manage: {sorted(unknown)}")
        stores = [s for s in catalogued if s == "library" or s in ALL_AGENT_ROOTS]
        # Capture every existing name entry in a managed root as well.
        stores.extend(s for s, root in ALL_AGENT_ROOTS.items() if (root / name).exists() or is_link(root / name))
        if "library" in catalogued:
            canonical = _default_library() / "skills" / name
            if (canonical / "SKILL.md").is_file() or canonical.is_symlink():
                stores.append("library")
        stores = list(dict.fromkeys(stores))
        if not stores:
            raise RuntimeError(f"skill absent from catalog and managed roots: {name}")
        paths = []
        for store in stores:
            root = ALL_AGENT_ROOTS.get(store) or STORE_ROOTS.get(store)
            if store == "library":
                root = _default_library() / "skills"
            if root is None:
                raise RuntimeError(f"unrecognized store {store!r} for {name}")
            candidate = root / name
            if not candidate.exists():
                raise RuntimeError(f"missing skill path: {candidate}")
            resolved = candidate.resolve(strict=True)
            if not inside(candidate, USER_ROOT) or not inside(resolved, USER_ROOT):
                raise RuntimeError(f"skill path or resolved target escapes user root: {candidate} -> {resolved}")
            if not inside(candidate.parent, root) or candidate.parent.resolve(strict=True) != root.resolve(strict=True):
                raise RuntimeError(f"skill path escapes store root: {candidate}")
            target = link_target(candidate)
            paths.append({
                "store": store,
                "store_root": str(root),
                "original_path": str(candidate),
                "resolved_path": str(resolved),
                "kind": "junction_or_symlink" if target else "directory",
                "link_kind": link_kind(candidate),
                "link_target": target,
                "resolved_link_target": str(resolved) if target else None,
                "sha256_tree": tree_hash(resolved),
            })
        # Each distinct real folder is archived once; aliases are recorded for exact restoration.
        unique = {p["resolved_path"].casefold() for p in paths}
        if len(unique) != 1:
            raise RuntimeError(f"different store variants for {name}; pilot expects identical source, got {len(unique)}")
        source = Path(paths[0]["resolved_path"])
        archive_path = DATA / "skills" / MANIFEST.stem / name
        if not inside(source, USER_ROOT) or not safe_archive_destination(archive_path):
            raise RuntimeError(f"source or archive path outside user root for {name}")
        entries.append({
            "name": name,
            "archive_batch": MANIFEST.stem,
            "estimated_list_tokens_across_stores": 0,
            "source_sha256": tree_hash(source),
            "archive_path": str(archive_path),
            "store_paths": paths,
        })
    return {
        "format": 1,
        "status": "dry_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_root": str(USER_ROOT),
        "archive_root": str(DATA),
        "catalog": str(catalog_path),
        "candidate_count": len(candidates),
        "selected_names": selected,
        "manifest_path": str(MANIFEST),
        "store_roots_source": "ALL_AGENT_ROOTS in skill_archive.py",
        "restore_procedure": [
            f"Run: python {ROOT / 'skill_archive.py'} --restore --manifest {MANIFEST.name}",
            "Restore moves each archived canonical folder to its recorded resolved_path.",
            "Restore recreates recorded junctions/symlinks after validating every target and parent path.",
            "Source and archive tree hashes must match the manifest before any change; conflicts stop the operation.",
            "Successful archive and restore automatically refresh library_catalog.py; if refresh fails, rerun it with --quiet.",
        ],
        "entries": entries,
    }


def write_manifest(manifest: dict) -> None:
    if not inside(MANIFEST, DATA) or not safe_archive_destination(MANIFEST):
        raise RuntimeError(f"manifest destination is unsafe: {MANIFEST}")
    DATA.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_suffix(MANIFEST.suffix + ".tmp")
    temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, MANIFEST)


def refresh_catalog() -> bool:
    """Refresh indexed capabilities after a completed move; never alter manifest status."""
    catalog = ROOT / "library_catalog.py"
    try:
        result = subprocess.run(
            [sys.executable, str(catalog), "--quiet"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        print(f"warning: catalog refresh could not start: {exc}", file=sys.stderr)
        return False
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        print(f"warning: catalog refresh failed (exit {result.returncode}): {detail}", file=sys.stderr)
        return False
    return True


def apply_archive(manifest: dict) -> None:
    """Move canonical trees and remove only recorded reparse-point aliases."""
    if manifest.get("status") != "dry_run":
        raise RuntimeError("apply requires a fresh dry-run manifest")
    if not safe_archive_destination(DATA):
        raise RuntimeError(f"archive root is unsafe: {DATA}")
    for entry in manifest["entries"]:
        src, dst = Path(entry["store_paths"][0]["resolved_path"]), Path(entry["archive_path"])
        if not src.is_dir() or dst.exists() or not safe_archive_destination(dst) or tree_hash(src) != entry["source_sha256"]:
            raise RuntimeError(f"source changed or archive already exists; stopped before changes: {src}")
    # Revalidate all paths before first filesystem mutation.
    for entry in manifest["entries"]:
        for p in entry["store_paths"]:
            original, resolved = Path(p["original_path"]), Path(p["resolved_path"])
            if not inside(original, USER_ROOT) or not inside(resolved, USER_ROOT):
                raise RuntimeError(f"unsafe restore path: {original}")
            canonical = Path(entry["store_paths"][0]["resolved_path"])
            if original != canonical and (not is_link(original) or original.resolve(strict=True) != canonical):
                raise RuntimeError(f"expected junction alias changed; stopped before changes: {original}")
            if original != canonical and p["link_kind"] not in ("junction", "directory_symlink"):
                raise RuntimeError(f"unsupported directory alias kind: {original}")
    manifest["status"] = "archiving"
    for entry in manifest["entries"]:
        entry.setdefault("archive_state", "pending")
    write_manifest(manifest)
    try:
        for entry in manifest["entries"]:
            if entry["archive_state"] == "archived":
                continue
            src = Path(entry["store_paths"][0]["resolved_path"])
            dst = Path(entry["archive_path"])
            entry["archive_state"] = "moving"
            write_manifest(manifest)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            entry["archive_state"] = "moved"
            write_manifest(manifest)
            for p in entry["store_paths"]:
                alias = Path(p["original_path"])
                if alias == src:
                    continue
                remove_directory_link(alias, p["link_kind"])
                write_manifest(manifest)
            entry["archive_state"] = "archived"
            write_manifest(manifest)
        manifest["status"] = "archived"
        write_manifest(manifest)
        refresh_catalog()
    except Exception as exc:
        manifest["status"] = "partial_archive"
        manifest["last_error"] = str(exc)
        write_manifest(manifest)
        raise


def restore_archive(manifest: dict) -> None:
    if manifest.get("status") not in ("archived", "partial_archive", "restoring", "partial_restore"):
        raise RuntimeError("manifest is not in archived state")
    base = _archive_base(manifest)
    # Preflight every entry before changing anything.
    for entry in manifest["entries"]:
        if entry.get("archive_state") in (None, "pending"):
            continue
        archived = Path(entry["archive_path"])
        source = Path(entry["store_paths"][0]["resolved_path"])
        canonical_root = Path(entry["store_paths"][0]["store_root"])
        if (not inside(source, USER_ROOT) or not inside(canonical_root, USER_ROOT)
                or source.parent.resolve(strict=False) != canonical_root.resolve(strict=False)
                or source.name != entry["name"]
                or archived != (base / "skills" / entry["archive_batch"] / entry["name"]
                                if entry.get("archive_batch") else base / "skills" / entry["name"])):
            raise RuntimeError(f"manifest contains an unsafe source or archive path for {entry.get('name')}")
        if archived.exists():
            if not inside(archived, USER_ROOT) or not safe_archive_destination(archived, base) or not archived.is_dir() or tree_hash(archived) != entry["source_sha256"]:
                raise RuntimeError(f"archive missing, unsafe, or modified: {archived}")
        elif not source.is_dir() or tree_hash(source) != entry["source_sha256"]:
            raise RuntimeError(f"neither intact archive nor source is available: {archived}")
        for p in entry["store_paths"]:
            original = Path(p["original_path"])
            parent = original.parent
            expected_root = Path(p["store_root"])
            if not inside(parent, USER_ROOT) or not inside(expected_root, USER_ROOT) or parent.resolve(strict=True) != expected_root.resolve(strict=True):
                raise RuntimeError(f"restore parent is unsafe: {parent}")
            if original == source:
                if original.exists() and archived.exists():
                    raise RuntimeError(f"both source and archive exist: {original}")
            elif original.exists() or is_link(original):
                if not is_link(original) or link_target(original) != p["link_target"]:
                    raise RuntimeError(f"restore destination is occupied: {original}")
            if p["link_kind"] in ("junction", "directory_symlink"):
                target = Path(p["link_target"])
                if not inside(target, USER_ROOT) or not same_path(target, source):
                    raise RuntimeError(f"recorded link target is unsafe or mismatched: {target}")
    manifest["status"] = "restoring"
    write_manifest(manifest)
    try:
        for entry in manifest["entries"]:
            if entry.get("archive_state") in (None, "pending", "restored"):
                continue
            archived = Path(entry["archive_path"])
            canonical = Path(entry["store_paths"][0]["resolved_path"])
            if archived.exists():
                canonical.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(archived), str(canonical))
            for p in entry["store_paths"]:
                alias = Path(p["original_path"])
                if alias == canonical or alias.exists() or is_link(alias):
                    continue
                alias.parent.mkdir(parents=True, exist_ok=True)
                recreate_link(alias, p["link_target"], p["link_kind"])
            entry["archive_state"] = "restored"
            write_manifest(manifest)
        manifest["status"] = "restored"
        write_manifest(manifest)
        refresh_catalog()
    except Exception as exc:
        manifest["status"] = "partial_restore"
        manifest["last_error"] = str(exc)
        write_manifest(manifest)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="perform the archive operation (explicit opt-in)")
    mode.add_argument("--restore", action="store_true", help="restore using the saved manifest")
    parser.add_argument("--names", help="comma-separated catalog skill names for this batch")
    parser.add_argument("--manifest", help="manifest filename under the archive vault")
    parser.add_argument("--library", help="skill library root (default: ~/ai-agent-library or CAPFIND_LIBRARY)")
    args = parser.parse_args()
    try:
        global MANIFEST
        if args.manifest:
            manifest_arg = Path(args.manifest)
            MANIFEST = manifest_arg if manifest_arg.is_absolute() else DATA / manifest_arg
            if not inside(MANIFEST, DATA) or MANIFEST.suffix.lower() != ".json":
                raise RuntimeError("--manifest must be a .json path inside on-demand")
        names = [part.strip() for part in args.names.split(",") if part.strip()] if args.names else None
        if args.restore:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            restore_archive(manifest)
            print(f"restored {len(manifest['entries'])} skills from {MANIFEST}")
        else:
            if MANIFEST.exists():
                prior = json.loads(MANIFEST.read_text(encoding="utf-8"))
                if prior.get("status") not in ("dry_run", "restored"):
                    raise RuntimeError(f"existing {prior.get('status')} manifest must be restored or resolved first: {MANIFEST}")
                if args.apply and names is None and prior.get("status") == "dry_run":
                    names = prior.get("selected_names")
            names = names or PILOT
            if len(set(names)) != len(names):
                raise RuntimeError("--names contains duplicate skill names")
            manifest = build_manifest(names, args.library)
            write_manifest(manifest)
            print(f"dry run: {len(manifest['entries'])} skills; no files moved; manifest: {MANIFEST}")
            if args.apply:
                apply_archive(manifest)
                print("archive complete")
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"skill archive: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
