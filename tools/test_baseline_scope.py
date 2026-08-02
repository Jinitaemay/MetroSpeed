import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_SCRIPT = REPO_ROOT / "tools" / "_baseline_all.py"


class BaselineScopeTests(unittest.TestCase):
    def run_baseline(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("METROSPEED_DATA_DIR", None)
        return subprocess.run(
            [sys.executable, str(BASELINE_SCRIPT), *arguments],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_requires_explicit_data_directory(self) -> None:
        result = self.run_baseline()

        self.assertEqual(result.returncode, 2)
        self.assertIn("请用 --dir <目录>", result.stderr)

    def test_rejects_missing_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            with self.subTest("missing"):
                result = self.run_baseline("--dir", str(root / "missing"))
                self.assertEqual(result.returncode, 2)
                self.assertIn("数据目录不存在", result.stderr)

            with self.subTest("empty"):
                result = self.run_baseline(f"--dir={root}")
                self.assertEqual(result.returncode, 2)
                self.assertIn("数据目录顶层没有可回放的 JSONL", result.stderr)

    def test_discovers_only_top_level_non_replay_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "current.jsonl").write_text("{\n", encoding="utf-8")
            (root / "ignored_replay_result.jsonl").write_text("{\n", encoding="utf-8")
            history_dir = root / "50Hz"
            history_dir.mkdir()
            (history_dir / "historical.jsonl").write_text("{\n", encoding="utf-8")

            result = self.run_baseline("--dir", str(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("current.jsonl", result.stdout)
        self.assertNotIn("ignored_replay_result.jsonl", result.stdout)
        self.assertNotIn("historical.jsonl", result.stdout)

    def test_files_is_explicit_subset_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "selected.jsonl").write_text("{\n", encoding="utf-8")
            (root / "other.jsonl").write_text("{\n", encoding="utf-8")

            result = self.run_baseline(
                "--dir",
                str(root),
                "--files=selected.jsonl",
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("不代表完整主回归集", result.stderr)
        self.assertIn("selected.jsonl", result.stdout)
        self.assertNotIn("other.jsonl", result.stdout)

    def test_rejects_empty_files_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_baseline("--dir", temp_dir, "--files", "")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--files 不能为空", result.stderr)


if __name__ == "__main__":
    unittest.main()
