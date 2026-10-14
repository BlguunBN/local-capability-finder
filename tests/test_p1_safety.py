"""P1: activation transactions, archive safety, and doctor diagnostics.

All fixtures live in temp dirs. The real library is never touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capability_finder as finder
import skill_archive


def make_skill(folder: Path, name: str, description: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )
    (folder / "notes.txt").write_text("payload", encoding="utf-8")


def make_catalog(home: Path, library: Path, skills: dict[str, str]) -> None:
    rows = []
    for name, description in skills.items():
        folder = home / ".agents" / "skills" / name
        make_skill(folder, name, description)
        rows.append(
            {
                "name": name,
                "description": description,
                "path": str(folder),
                "skill_md": str(folder / "SKILL.md"),
                "source": "agents",
                "canonical": True,
                "roots": ["agents"],
            }
        )
    library.mkdir(parents=True, exist_ok=True)
    (library / "skills_index.json").write_text(
        json.dumps({"skills": rows}), encoding="utf-8"
    )
    (library / "mcp_servers.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")
    finder.refresh_index(home=home, library=library)


class ActivationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        make_catalog(self.home, self.library, {"alpha-skill": "Alpha testing skill"})
        self.cap_id = finder.search(
            "alpha testing", kind="skill", home=self.home, library=self.library
        )[0]["id"]

    def target(self):
        return self.home / ".codex" / "skills" / "alpha-skill"

    def test_preview_dry_run_changes_nothing(self):
        plan = finder.preview_activation(
            self.cap_id, "codex", home=self.home, library=self.library
        )
        self.assertEqual("ready", plan["status"])
        self.assertFalse(os.path.lexists(plan["target"]))
        self.assertFalse((self.library / "_activations").exists())

    def test_existing_unmanaged_target_is_not_adopted_without_manifest(self):
        target = self.target()
        target.mkdir(parents=True)
        source = self.home / ".agents" / "skills" / "alpha-skill"
        manifest = self.library / "_activations" / "codex__alpha-skill.json"
        finder._adopt_activation_manifest(self.library, manifest, {
            "id": self.cap_id, "agent": "codex", "name": "alpha-skill",
            "source": str(source), "target": str(target), "method": "link",
        })
        self.assertFalse(manifest.exists())
        self.assertFalse((self.library / "activations-lock.json").exists())

    def test_activate_writes_manifest_and_deactivate_removes_only_link(self):
        result = finder.activate(
            self.cap_id, "codex", home=self.home, library=self.library
        )
        self.assertEqual("activated", result["status"])
        manifest = self.library / "_activations" / "codex__alpha-skill.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("applied", data["status"])
        self.assertTrue((self.target() / "SKILL.md").is_file())
        source = self.home / ".agents" / "skills" / "alpha-skill"
        self.assertTrue((source / "SKILL.md").is_file())
        out = finder.deactivate_skill(
            self.cap_id, "codex", home=self.home, library=self.library
        )
        self.assertEqual("deactivated", out["status"])
        self.assertFalse(os.path.lexists(self.target()))
        self.assertFalse(manifest.exists())
        self.assertTrue((source / "SKILL.md").is_file())
        self.assertEqual([], finder.doctor(home=self.home, library=self.library))

    @unittest.skipIf(os.name == "nt", "POSIX directory symlink removal semantics")
    def test_directory_symlink_deactivation_unlinks_link_not_target_directory(self):
        source = self.home / "source"
        source.mkdir()
        target = self.home / "link"
        target.symlink_to(source, target_is_directory=True)
        finder._remove_managed_link(target, "directory_symlink")
        self.assertFalse(os.path.lexists(target))
        self.assertTrue(source.is_dir())

    def test_failed_activate_leaves_recovery_manifest(self):
        with (
            mock.patch.object(
                finder.os, "symlink", side_effect=PermissionError("denied")
            ),
            mock.patch("subprocess.run") as run,
        ):
            run.return_value = mock.Mock(returncode=1, stderr="nope")
            with self.assertRaises((PermissionError, OSError)):
                finder.activate(
                    self.cap_id, "codex", home=self.home, library=self.library
                )
        manifest = self.library / "_activations" / "codex__alpha-skill.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual("failed", data["status"])
        self.assertFalse(os.path.lexists(self.target()))
        codes = {f["code"] for f in finder.doctor(home=self.home, library=self.library)}
        self.assertIn("INTERRUPTED_ACTIVATION", codes)

    def test_deactivate_recovers_dangling_link_from_failed_manifest(self):
        src = self.home / ".agents" / "skills" / "alpha-skill"
        target = self.target()
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, target, target_is_directory=True)
        (src / "SKILL.md").unlink()  # dangle the link
        manifest = self.library / "_activations"
        manifest.mkdir(parents=True, exist_ok=True)
        (manifest / "codex__alpha-skill.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "status": "failed",
                    "error": "boom",
                    "rolled_back": False,
                    "id": self.cap_id,
                    "agent": "codex",
                    "name": "alpha-skill",
                    "source": str(src),
                    "target": str(target),
                }
            ),
            encoding="utf-8",
        )
        out = finder.deactivate_skill(
            self.cap_id, "codex", home=self.home, library=self.library
        )
        self.assertEqual("deactivated", out["status"])
        self.assertFalse(os.path.lexists(target))

    def test_deactivate_refuses_unmanaged_directory(self):
        target = self.target()
        target.mkdir(parents=True, exist_ok=True)
        (target / "precious.txt").write_text("user data", encoding="utf-8")
        manifest = self.library / "_activations"
        manifest.mkdir(parents=True, exist_ok=True)
        (manifest / "codex__alpha-skill.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "status": "applied",
                    "stage": "verified",
                    "id": self.cap_id,
                    "agent": "codex",
                    "name": "alpha-skill",
                    "source": str(self.home / "elsewhere"),
                    "target": str(target),
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(FileExistsError):
            finder.deactivate_skill(
                self.cap_id, "codex", home=self.home, library=self.library
            )
        self.assertEqual("user data", (target / "precious.txt").read_text())


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.library = self.base / "lib"
        make_catalog(
            self.home, self.library, {"alpha-skill": "Alpha", "beta-skill": "Beta"}
        )
        self.vault = self.library / "on-demand"
        self._patch = [
            mock.patch.object(skill_archive, "DATA", self.vault),
            mock.patch.object(
                skill_archive, "MANIFEST", self.vault / "test-manifest.json"
            ),
            mock.patch.object(skill_archive, "USER_ROOT", self.base),
            mock.patch.object(skill_archive, "refresh_catalog", return_value=True),
            mock.patch.object(
                skill_archive,
                "ALL_AGENT_ROOTS",
                {"agents": self.home / ".agents" / "skills"},
            ),
        ]
        for entered in self._patch:
            entered.start()
        self.addCleanup(lambda: [p.stop() for p in self._patch])
        self.hashes = {
            name: skill_archive.tree_hash(self.home / ".agents" / "skills" / name)
            for name in ("alpha-skill", "beta-skill")
        }

    def names(self):
        return ["alpha-skill", "beta-skill"]

    def test_archive_restore_is_byte_identical(self):
        manifest = skill_archive.build_manifest(self.names(), self.library)
        skill_archive.write_manifest(manifest)
        self.assertEqual("dry_run", manifest["status"])
        # Dry run moved nothing.
        self.assertTrue((self.home / ".agents" / "skills" / "alpha-skill").is_dir())
        skill_archive.apply_archive(manifest)
        self.assertEqual("archived", manifest["status"])
        for name in self.names():
            archived = self.vault / "skills" / "test-manifest" / name
            self.assertTrue((archived / "SKILL.md").is_file())
            self.assertEqual(self.hashes[name], skill_archive.tree_hash(archived))
            self.assertFalse((self.home / ".agents" / "skills" / name).exists())
        skill_archive.restore_archive(manifest)
        self.assertEqual("restored", manifest["status"])
        for name in self.names():
            live = self.home / ".agents" / "skills" / name
            self.assertEqual("payload", (live / "notes.txt").read_text())
            self.assertEqual(self.hashes[name], skill_archive.tree_hash(live))

    def test_mid_apply_failure_records_partial_state_and_recovers(self):
        manifest = skill_archive.build_manifest(self.names(), self.library)
        real_move = skill_archive.shutil.move

        def fail_on_beta(src, dst, *args, **kwargs):
            if "beta-skill" in str(src):
                raise RuntimeError("injected disk failure")
            return real_move(src, dst, *args, **kwargs)

        with mock.patch.object(skill_archive.shutil, "move", side_effect=fail_on_beta):
            with self.assertRaises(RuntimeError):
                skill_archive.apply_archive(manifest)
        self.assertEqual("partial_archive", manifest["status"])
        states = {e["name"]: e["archive_state"] for e in manifest["entries"]}
        self.assertEqual("archived", states["alpha-skill"])
        self.assertNotEqual("archived", states["beta-skill"])
        codes = {f["code"] for f in finder.doctor(home=self.home, library=self.library)}
        self.assertIn("INTERRUPTED_ARCHIVE", codes)
        # Recovery from the manifest restores the moved skill; nothing is lost.
        skill_archive.restore_archive(manifest)
        for name in self.names():
            live = self.home / ".agents" / "skills" / name
            self.assertTrue((live / "SKILL.md").is_file())
            self.assertEqual(self.hashes[name], skill_archive.tree_hash(live))

    def test_restore_refuses_when_source_reoccupied(self):
        manifest = skill_archive.build_manifest(["alpha-skill"], self.library)
        skill_archive.write_manifest(manifest)
        skill_archive.apply_archive(manifest)
        live = self.home / ".agents" / "skills" / "alpha-skill"
        live.mkdir(parents=True)
        (live / "SKILL.md").write_text("different content", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            skill_archive.restore_archive(manifest)
        archived = self.vault / "skills" / "test-manifest" / "alpha-skill"
        self.assertEqual(self.hashes["alpha-skill"], skill_archive.tree_hash(archived))

    def test_directory_symlink_removal_unlinks_not_target(self):
        source = Path(self.temp.name) / "link-source"
        source.mkdir()
        link = Path(self.temp.name) / "link-alias"
        try:
            link.symlink_to(source, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation needs privilege on this host")
        skill_archive.remove_directory_link(link, "directory_symlink")
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(source.is_dir())

    def test_unknown_skill_is_rejected(self):
        with self.assertRaises(RuntimeError):
            skill_archive.build_manifest(["no-such-skill"], self.library)

    def test_custom_library_root_is_used_for_library_store(self):
        custom = self.base / "custom-lib"
        skill = custom / "skills" / "custom-skill"
        make_skill(skill, "custom-skill", "Custom")
        custom.mkdir(parents=True, exist_ok=True)
        (custom / "skills_index.json").write_text(json.dumps({"skills": [{
            "name": "custom-skill", "path": str(skill), "roots": ["library"],
        }]}), encoding="utf-8")
        with (mock.patch.object(skill_archive, "_default_library", return_value=self.base / "other-lib"),
              mock.patch.object(skill_archive, "USER_ROOT", self.base)):
            manifest = skill_archive.build_manifest(["custom-skill"], custom)
        self.assertEqual(str(custom / "skills"), manifest["entries"][0]["store_paths"][0]["store_root"])

    def test_catalog_store_aliases_resolve_to_archiver_roots(self):
        roots = {
            "pi_agent": self.home / ".pi" / "agent" / "skills",
            "pi": self.home / ".pi" / "skills",
            "omp_agent": self.home / ".omp" / "agent" / "skills",
            "omp_managed": self.home / ".omp" / "managed-skills",
            "aider_desk": self.home / ".aider-desk" / "skills",
        }
        with (mock.patch.object(skill_archive, "ALL_AGENT_ROOTS", roots),
              mock.patch.object(skill_archive, "USER_ROOT", self.base)):
            for name, store, resolved_store in (
                ("pi-current", "pi", "pi_agent"),
                ("pi-old", "pi-legacy", "pi"),
                ("omp-current", "omp", "omp_agent"),
                ("omp-old", "omp-managed", "omp_managed"),
                ("aider", "aider-desk", "aider_desk"),
            ):
                root = roots[resolved_store]
                folder = root / name
                make_skill(folder, name, "Alias")
                (self.library / "skills_index.json").write_text(json.dumps({"skills": [{
                    "name": name, "path": str(folder), "roots": [store],
                }]}), encoding="utf-8")
                manifest = skill_archive.build_manifest([name], self.library)
                self.assertEqual(resolved_store, manifest["entries"][0]["store_paths"][0]["store"])
                import shutil
                shutil.rmtree(folder)

    def test_legacy_vault_restore_persists_to_legacy_manifest(self):
        manifest = skill_archive.build_manifest(["alpha-skill"], self.library)
        entry = manifest["entries"][0]
        legacy_repo = self.base / "repo"
        legacy = legacy_repo / "on-demand"
        legacy_manifest = legacy / "pilot-manifest.json"
        entry["archive_path"] = str(legacy / "skills" / entry["archive_batch"] / entry["name"])
        manifest["archive_root"] = str(legacy)
        manifest["manifest_path"] = str(legacy_manifest)
        archived = Path(entry["archive_path"])
        archived.parent.mkdir(parents=True)
        import shutil
        shutil.move(entry["store_paths"][0]["resolved_path"], archived)
        entry["archive_state"] = "archived"
        manifest["status"] = "archived"
        legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
        legacy_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with (mock.patch.object(skill_archive, "MANIFEST", self.vault / "current.json"),
              mock.patch.object(skill_archive, "ROOT", legacy_repo),
              mock.patch.object(skill_archive, "USER_ROOT", self.home)):
            skill_archive.restore_archive(manifest, manifest_path=legacy_manifest)
        saved = json.loads(legacy_manifest.read_text(encoding="utf-8"))
        self.assertEqual("restored", saved["status"])
        self.assertFalse((self.vault / "current.json").exists())

    def test_cli_accepts_explicit_checkout_legacy_manifest(self):
        legacy_repo = self.base / "repo"
        legacy = legacy_repo / "on-demand"
        manifest_path = legacy / "pilot-manifest.json"
        legacy.mkdir(parents=True)
        manifest_path.write_text(json.dumps({
            "archive_root": str(legacy), "status": "archived", "entries": [],
        }), encoding="utf-8")
        with (mock.patch.object(skill_archive, "ROOT", legacy_repo),
              mock.patch.object(skill_archive, "USER_ROOT", self.home),
              mock.patch.object(skill_archive, "DATA", self.vault),
              mock.patch.object(skill_archive, "MANIFEST", self.vault / "current.json"),
              mock.patch.object(skill_archive, "restore_archive") as restore,
              mock.patch.object(sys, "argv", ["skill_archive.py", "--restore", "--manifest", str(manifest_path)])):
            self.assertEqual(0, skill_archive.main())
        restore.assert_called_once()
        self.assertEqual(manifest_path, restore.call_args.kwargs["manifest_path"])



class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        make_catalog(self.home, self.library, {"alpha-skill": "Alpha"})

    def test_doctor_clean_on_healthy_index(self):
        self.assertEqual([], finder.doctor(home=self.home, library=self.library))

    def test_doctor_reports_non_object_trust_json_as_corrupt(self):
        (self.library / "trust-overrides.json").write_text("[]", encoding="utf-8")
        findings = finder.doctor(home=self.home, library=self.library)
        self.assertIn("CORRUPT_MANIFEST", {finding["code"] for finding in findings})
        metrics = finder.collect_metrics(home=self.home, library=self.library)
        self.assertGreater(metrics["doctor"]["findings"], 0)

    def test_doctor_flags_broken_variant(self):
        (self.home / ".agents" / "skills" / "alpha-skill" / "SKILL.md").unlink()
        codes = {f["code"] for f in finder.doctor(home=self.home, library=self.library)}
        self.assertIn("BROKEN_VARIANT", codes)

    def test_doctor_flags_renamed_root(self):
        moved = Path(self.temp.name) / "moved-lib"
        self.library.rename(moved)
        findings = finder.doctor(home=self.home, library=self.library)
        self.assertTrue(findings)
        self.assertIn(
            findings[0]["code"], ("INDEX_MISSING", "INDEX_STALE_OR_UNREADABLE")
        )

    def test_shared_env_config_is_honored(self):
        custom = Path(self.temp.name) / "custom-lib"
        with mock.patch.dict(
            os.environ,
            {"CAPFIND_LIBRARY": str(custom), "CAPFIND_ARCHIVE": str(custom / "vault")},
        ):
            _, library = finder._paths(self.home, None)
            self.assertEqual(custom.resolve(), library)
            self.assertEqual(
                (custom / "vault").resolve(), skill_archive._archive_base().resolve()
            )

    def test_cli_doctor_and_dry_run(self):
        env = dict(
            os.environ, CAPFIND_HOME=str(self.home), CAPFIND_LIBRARY=str(self.library)
        )
        repo = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, str(repo / "capability_finder.py"), "doctor", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], json.loads(proc.stdout))
        cap_id = finder.search(
            "alpha", kind="skill", home=self.home, library=self.library
        )[0]["id"]
        proc = subprocess.run(
            [
                sys.executable,
                str(repo / "capability_finder.py"),
                "activate",
                cap_id,
                "--agent",
                "codex",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertEqual("ready", plan["status"])
        self.assertFalse((self.home / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
