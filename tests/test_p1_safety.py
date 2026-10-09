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

    def test_unknown_skill_is_rejected(self):
        with self.assertRaises(RuntimeError):
            skill_archive.build_manifest(["no-such-skill"], self.library)



class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.library = Path(self.temp.name) / "lib"
        make_catalog(self.home, self.library, {"alpha-skill": "Alpha"})

    def test_doctor_clean_on_healthy_index(self):
        self.assertEqual([], finder.doctor(home=self.home, library=self.library))

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
