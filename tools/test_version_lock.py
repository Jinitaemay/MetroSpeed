import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_VERSION_PATH = REPO_ROOT / "tools" / "sync_version.py"
HVIGOR_SOURCE = (REPO_ROOT / "hvigorfile.ts").read_text(encoding="utf-8")


def load_sync_version():
    spec = importlib.util.spec_from_file_location("sync_version_under_test", SYNC_VERSION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sync_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_sync_version()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.lock_path = Path(self.temp_dir.name) / ".sync-version.lock"
        self.module.LOCK_FILE = self.lock_path

    def assert_lock_initialization_failure_is_cleaned(self, failing_name: str) -> None:
        original_open = os.open
        original_close = os.close
        opened_fds: list[int] = []
        closed_fds: list[int] = []

        def track_open(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened_fds.append(fd)
            return fd

        def track_close(fd: int) -> None:
            closed_fds.append(fd)
            original_close(fd)

        def fail_once(*args, **kwargs):
            raise OSError(f"injected {failing_name} failure")

        with mock.patch.object(self.module.os, "open", side_effect=track_open):
            with mock.patch.object(self.module.os, "close", side_effect=track_close):
                with mock.patch.object(self.module.os, failing_name, side_effect=fail_once):
                    with self.assertRaises(OSError):
                        self.module.acquire_version_lock()

        self.assertEqual(len(opened_fds), 1)
        self.assertEqual(closed_fds, opened_fds)
        with self.assertRaises(OSError):
            os.fstat(opened_fds[0])
        self.assertFalse(self.lock_path.exists())
        fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        self.lock_path.unlink()

    def test_write_failure_closes_and_removes_new_lock(self) -> None:
        self.assert_lock_initialization_failure_is_cleaned("write")

    def test_fsync_failure_closes_and_removes_new_lock(self) -> None:
        self.assert_lock_initialization_failure_is_cleaned("fsync")

    def test_short_writes_are_retried_until_the_token_is_complete(self) -> None:
        original_write = os.write
        write_calls = 0

        def write_one_byte(fd: int, data: bytes) -> int:
            nonlocal write_calls
            write_calls += 1
            return original_write(fd, data[:1])

        with mock.patch.object(self.module.os, "write", side_effect=write_one_byte):
            lock_fd, token = self.module.acquire_version_lock()
        try:
            self.assertGreater(write_calls, 1)
            self.assertEqual(self.lock_path.read_text(encoding="ascii"), token)
        finally:
            self.module.release_version_lock(lock_fd, token)
        self.assertFalse(self.lock_path.exists())

    def test_zero_length_write_closes_and_removes_new_lock(self) -> None:
        with mock.patch.object(self.module.os, "write", return_value=0):
            with self.assertRaisesRegex(OSError, "made no progress"):
                self.module.acquire_version_lock()
        self.assertFalse(self.lock_path.exists())

    def test_main_reports_oserror_without_traceback(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(self.module, "sync", side_effect=OSError("injected")):
            with mock.patch.object(sys, "argv", ["sync_version.py", "--check"]):
                with contextlib.redirect_stderr(stderr):
                    result = self.module.main()
        self.assertEqual(result, 1)
        self.assertIn("version sync failed: injected", stderr.getvalue())

    def test_algorithm_update_rewrites_current_readme_list_item(self) -> None:
        fixture_root = Path(self.temp_dir.name) / "version-fixture"
        fixture_root.mkdir()
        app_json = fixture_root / "app.json5"
        recorder = fixture_root / "ResearchRecorder.ets"
        replay = fixture_root / "replay_estimator.py"
        readme = fixture_root / "README.md"
        app_json.write_text(
            '{"app": {"versionCode": 100, "versionName": "1.2.0"}}\n',
            encoding="utf-8",
        )
        recorder.write_text(
            "const APP_VERSION_CODE = 100;\n"
            "const ALGORITHM_VERSION = 'old-r1';\n",
            encoding="utf-8",
        )
        replay.write_text('ALGORITHM_VERSION = "old-r1"\n', encoding="utf-8")
        readme.write_text(
            "# Fixture\n\n- **算法版本**：`old-r1`\n",
            encoding="utf-8",
        )

        with mock.patch.multiple(
            self.module,
            APP_JSON=app_json,
            RECORDER=recorder,
            REPLAY_ESTIMATOR=replay,
            README=readme,
        ), contextlib.redirect_stdout(io.StringIO()):
            result = self.module._sync_unlocked(101, "new-r2", check=False)

        self.assertEqual(result, 0)
        self.assertIn('"versionCode": 101', app_json.read_text(encoding="utf-8"))
        self.assertIn("const APP_VERSION_CODE = 101;", recorder.read_text(encoding="utf-8"))
        self.assertIn(
            "const ALGORITHM_VERSION = 'new-r2';",
            recorder.read_text(encoding="utf-8"),
        )
        self.assertIn(
            'ALGORITHM_VERSION = "new-r2"',
            replay.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "- **算法版本**：`new-r2`",
            readme.read_text(encoding="utf-8"),
        )


class HvigorVersionHookContractTests(unittest.TestCase):
    def test_version_update_is_gated_to_assemble_tasks(self) -> None:
        self.assertIn("function shouldUpdateBuildVersion", HVIGOR_SOURCE)
        self.assertIn("assembleHap|assembleApp", HVIGOR_SOURCE)
        guard_start = HVIGOR_SOURCE.index(
            "if (shouldUpdateBuildVersion(process.argv.slice(2))) {"
        )
        guard_end = HVIGOR_SOURCE.index("\n}", guard_start)
        calls = [
            match.start()
            for match in re.finditer(
                r"(?m)^\s*updateBuildVersion\(\);\s*$", HVIGOR_SOURCE
            )
        ]
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], guard_start)
        self.assertLess(calls[0], guard_end)

    def test_hvigor_lock_initialization_has_cleanup(self) -> None:
        acquire_start = HVIGOR_SOURCE.index("function acquireVersionLock")
        acquire_end = HVIGOR_SOURCE.index("\nfunction releaseVersionLock", acquire_start)
        acquire_body = HVIGOR_SOURCE[acquire_start:acquire_end]
        self.assertIn("fs.closeSync(fd)", acquire_body)
        self.assertIn("fs.unlinkSync(lockPath)", acquire_body)
        self.assertIn("cleanup incomplete", acquire_body)


if __name__ == "__main__":
    unittest.main()
