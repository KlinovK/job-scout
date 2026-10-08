import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")

import main


class CollectorStatePersistenceTests(unittest.TestCase):
    def assert_private_file(self, path: Path) -> None:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_first_creation_is_private_with_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "collector_state.json"
            previous_umask = os.umask(0o022)
            try:
                main.persist_state(state_path, {"123": 456})
            finally:
                os.umask(previous_umask)

            self.assert_private_file(state_path)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"123": 456},
            )

    def test_replacement_corrects_existing_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "collector_state.json"
            state_path.write_text('{"123": 1}', encoding="utf-8")
            state_path.chmod(0o644)

            main.persist_state(state_path, {"123": 2})

            self.assert_private_file(state_path)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"123": 2},
            )

    def test_repeated_persistence_remains_private_and_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "collector_state.json"

            for message_id in range(1, 4):
                main.persist_state(state_path, {"123": message_id})
                self.assert_private_file(state_path)

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"123": 3},
            )

    def test_atomic_replace_uses_private_complete_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "collector_state.json"
            state_path.write_text('{"123": 1}', encoding="utf-8")
            real_replace = os.replace

            def inspect_then_replace(source: Path, destination: Path) -> None:
                source_path = Path(source)
                self.assertEqual(
                    json.loads(state_path.read_text(encoding="utf-8")),
                    {"123": 1},
                )
                self.assert_private_file(source_path)
                self.assertEqual(
                    json.loads(source_path.read_text(encoding="utf-8")),
                    {"123": 2},
                )
                real_replace(source, destination)

            with patch("main.os.replace", side_effect=inspect_then_replace) as replace:
                main.persist_state(state_path, {"123": 2})

            replace.assert_called_once()
            self.assert_private_file(state_path)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"123": 2},
            )
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_replace_failure_preserves_previous_state_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "collector_state.json"
            state_path.write_text('{"123": 1}', encoding="utf-8")

            with (
                patch("main.os.replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                main.persist_state(state_path, {"123": 2})

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {"123": 1},
            )
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


class SmartClassifierTests(unittest.TestCase):
    def assert_category(self, text: str, category: str) -> None:
        result = main.classify_message(text)
        self.assertTrue(result.matched, result)
        self.assertIn(category, result.categories)
        self.assertGreaterEqual(result.score, main.MATCH_THRESHOLD)
        self.assertTrue(result.reasons)

    def assert_rejected(self, text: str) -> None:
        result = main.classify_message(text)
        self.assertFalse(result.matched, result)

    def test_ios_roles_and_vacancies(self) -> None:
        self.assert_category(
            "Senior iOS Engineer — remote, full-time",
            "iOS/Mobile",
        )
        self.assert_category(
            "Вакансия: iOS разработчик. Требования: SwiftUI",
            "iOS/Mobile",
        )

    def test_ios_news_is_rejected(self) -> None:
        self.assert_rejected("Apple iOS release notes and news")

    def test_entry_level_python_backend(self) -> None:
        self.assert_category(
            "Hiring Junior Python Backend Engineer, FastAPI, remote",
            "Python Backend",
        )
        self.assert_category(
            "Вакансия: стажёр Django разработчик, можно без опыта",
            "Python Backend",
        )
        self.assert_category(
            "Entry-level Python Developer vacancy",
            "Python Backend",
        )

    def test_python_requires_entry_level(self) -> None:
        self.assert_rejected(
            "Hiring Python Backend Engineer, FastAPI, remote"
        )
        self.assert_rejected("Вакансия Django разработчика")
        self.assert_rejected("Middle Python Developer vacancy")
        self.assert_rejected("Senior Python Engineer, remote")
        self.assert_rejected("Vacancy: Junior Python QA automation")

    def test_python_learning_content_is_rejected(self) -> None:
        self.assert_rejected("Python course and developer roadmap")
        self.assert_rejected("FastAPI tutorial")

    def test_fullstack(self) -> None:
        self.assert_category(
            "Junior Full-stack Developer",
            "Fullstack",
        )
        self.assert_category(
            "Вакансия: frontend and backend engineer",
            "Fullstack",
        )

    def test_project_management(self) -> None:
        self.assert_category(
            "Ищем Technical Project Manager, remote",
            "Project Management",
        )
        self.assert_category(
            "Вакансия: руководитель проекта",
            "Project Management",
        )

    def test_project_management_course_is_rejected(self) -> None:
        self.assert_rejected("Project Management course and roadmap")

    def test_resume_and_candidate_posts_are_rejected(self) -> None:
        self.assert_rejected("#резюме Senior iOS Engineer")
        self.assert_rejected("#resume Python Backend Developer")
        self.assert_rejected("Open to work as a Project Manager")
        self.assert_rejected("Ищу работу Fullstack Developer")

    def test_generic_junior_role_is_rejected(self) -> None:
        self.assert_rejected("Вакансия: Junior Graphic Designer")

    def test_vibe_coding_search_is_preserved(self) -> None:
        self.assert_category(
            "Ищем вайбкодера для быстрого прототипирования",
            "Vibe Coding",
        )

    def test_multiple_categories(self) -> None:
        result = main.classify_message(
            "Vacancy: Technical Project Manager for an iOS team"
        )
        self.assertTrue(result.matched)
        self.assertIn("iOS/Mobile", result.categories)
        self.assertIn("Project Management", result.categories)

    def test_boolean_compatibility_wrapper(self) -> None:
        self.assertTrue(main.contains_keyword("Junior Python Developer"))
        self.assertFalse(main.contains_keyword("Python Developer"))
        self.assertFalse(main.contains_keyword("Python tutorial"))

    def test_match_audit_log(self) -> None:
        result = main.classify_message("Senior iOS Engineer")
        message = SimpleNamespace(
            chat_id=-100123,
            id=42,
            date=None,
        )
        chat = SimpleNamespace(
            title="Test Jobs",
            username="test_jobs",
        )
        original_path = main.MATCH_LOG_FILE

        try:
            with tempfile.TemporaryDirectory() as directory:
                main.MATCH_LOG_FILE = Path(directory) / "matches.jsonl"
                main.record_match(message, chat, result)
                payload = json.loads(
                    main.MATCH_LOG_FILE.read_text(encoding="utf-8")
                )

            self.assertEqual(payload["message_id"], 42)
            self.assertEqual(payload["categories"], ["iOS/Mobile"])
            self.assertGreaterEqual(payload["score"], 6)
            self.assertTrue(payload["reasons"])
        finally:
            main.MATCH_LOG_FILE = original_path


if __name__ == "__main__":
    unittest.main()
