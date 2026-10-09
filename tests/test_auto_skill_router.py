from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import auto_skill_router


class AutoSkillRouterTests(unittest.TestCase):
    EXPLICIT_POSITIVE = (
        "Build a React dashboard with the best skills",
        "Use skills to deploy a Cloudflare Worker",
        "Find the right skill for inspecting a CAD file",
        "Please choose relevant skills for debugging a Python package",
        "Design a landing page using skills",
        "Review this API for security with the best skill",
        "Help me to build a website withh the best skills",
        "Do this website with the skils best for this job",
    )
    SMART_POSITIVE = (
        "Build a React dashboard",
        "Deploy Cloudflare Workers with durable objects",
        "Debug a Python package import error",
        "Review a REST API for security vulnerabilities",
        "Migrate this database to PostgreSQL",
        "Automate invoice extraction from PDFs",
    )
    NEGATIVE = (
        "What is Python?",
        "Can you help me?",
        "Build it",
        "Use the best skills for this task",
        'Write a doc explaining "build a dashboard with the best skills"',
        "Explain `use skills for deployment`",
        "Build a dashboard without skills",
        "How are you today?",
        "List three colors",
        "Thanks for the help",
        "Please summarize this paragraph",
        "Don't use skills to deploy the app",
    )

    def test_labeled_corpus_precision_and_recall(self) -> None:
        for mode, positive in (("explicit", self.EXPLICIT_POSITIVE), ("smart", self.EXPLICIT_POSITIVE + self.SMART_POSITIVE)):
            true_positive = sum(bool(auto_skill_router.route_prompt(p, mode)["triggered"]) for p in positive)
            false_positive = sum(bool(auto_skill_router.route_prompt(p, mode)["triggered"]) for p in self.NEGATIVE)
            recall = true_positive / len(positive)
            precision = true_positive / (true_positive + false_positive)
            self.assertGreaterEqual(recall, 0.95, f"{mode} recall={recall:.2%}")
            self.assertGreaterEqual(precision, 0.95, f"{mode} precision={precision:.2%}")

    def test_explicit_request_extracts_task(self) -> None:
        result = auto_skill_router.route_prompt(
            "Can you help me build a React dashboard with the best skills?", agent="codex"
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["query"], "build a React dashboard")
        self.assertEqual(result["agent"], "codex")
        typo = auto_skill_router.route_prompt("Help me to build a website withh the best skills")
        self.assertEqual(typo["query"], "build a website")

    def test_explicit_mode_ignores_regular_work(self) -> None:
        result = auto_skill_router.route_prompt("Build a React dashboard")
        self.assertFalse(result["triggered"])

    def test_smart_mode_uses_specialist_work(self) -> None:
        result = auto_skill_router.route_prompt("Deploy Cloudflare Workers with durable objects", mode="smart")
        self.assertTrue(result["triggered"])
        self.assertEqual(result["query"], "Deploy Cloudflare Workers with durable objects")

    def test_smart_mode_ignores_small_talk_and_vague_work(self) -> None:
        for prompt in ("What is Python?", "Can you help me?", "Build it", "Help me do this"):
            with self.subTest(prompt=prompt):
                self.assertFalse(auto_skill_router.route_prompt(prompt, mode="smart")["triggered"])

    def test_opt_out_wins_over_trigger(self) -> None:
        result = auto_skill_router.route_prompt("Build a dashboard with the best skills, but don't use skills")
        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "skills explicitly declined")

    def test_quoted_examples_do_not_trigger(self) -> None:
        prompts = (
            'Write a doc explaining "build a dashboard with the best skills"',
            "Explain `use skills for deployment`",
            "```\nfind the best skills for React\n```\nSummarize this snippet",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertFalse(auto_skill_router.route_prompt(prompt)["triggered"])

    def test_vague_explicit_request_does_not_search(self) -> None:
        self.assertFalse(auto_skill_router.route_prompt("Use the best skills for this task")["triggered"])

    def test_duplicate_suppression_stores_hash_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            prompt = "Build a React dashboard with the best skills"
            first = auto_skill_router.route_once(prompt, session_id="secret-session", state_dir=state_dir)
            second = auto_skill_router.route_once(prompt, session_id="secret-session", state_dir=state_dir)
            third = auto_skill_router.route_once(prompt, session_id="other-session", state_dir=state_dir)
            self.assertTrue(first["triggered"])
            self.assertFalse(second["triggered"])
            self.assertEqual(second["reason"], "task already searched")
            self.assertTrue(third["triggered"])
            stored_paths = " ".join(str(p) for p in state_dir.rglob("*"))
            self.assertNotIn("secret-session", stored_paths)
            self.assertNotIn("React", stored_paths)

    def test_failed_search_can_release_session_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            prompt = "Build a React dashboard with the best skills"
            self.assertTrue(auto_skill_router.route_once(prompt, session_id="session", state_dir=state_dir)["triggered"])
            self.assertFalse(auto_skill_router.route_once(prompt, session_id="session", state_dir=state_dir)["triggered"])
            self.assertTrue(auto_skill_router.forget_route(prompt, session_id="session", state_dir=state_dir))
            self.assertFalse(auto_skill_router.forget_route(prompt, session_id="session", state_dir=state_dir))
            self.assertTrue(auto_skill_router.route_once(prompt, session_id="session", state_dir=state_dir)["triggered"])

    def test_cli_json_test_is_dry_run(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = auto_skill_router.main([
                "test", "Build a React dashboard with the best skills", "--mode", "explicit", "--agent", "codex", "--json"
            ])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["query"], "Build a React dashboard")
        self.assertTrue(result["triggered"])

    def test_search_helper_limits_results_and_retries_once(self) -> None:
        calls = []

        def empty_search(query: str, **kwargs: object) -> list[dict]:
            calls.append((query, kwargs))
            return []

        result = auto_skill_router.search_for_prompt("Build a React dashboard with the best skills", empty_search)
        self.assertEqual(result["searches"], 2)
        self.assertEqual([q for q, _ in calls], ["Build a React dashboard", "React dashboard"])
        self.assertTrue(all(options == {"kind": "skill", "limit": 3} for _, options in calls))

        def many_results(query: str, **kwargs: object) -> list[dict]:
            calls.append((query, kwargs))
            return [{"id": str(i)} for i in range(10)]

        calls.clear()
        result = auto_skill_router.search_for_prompt("Build a React dashboard with the best skills", many_results)
        self.assertEqual(result["searches"], 1)
        self.assertEqual(len(result["matches"]), 3)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
