#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from .replay_estimator import read_jsonl, read_jsonl_with_info
except ImportError:
    from replay_estimator import read_jsonl, read_jsonl_with_info


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = REPO_ROOT / "tools" / "replay_estimator.py"
BASELINE_SCRIPT = REPO_ROOT / "tools" / "_baseline_all.py"
VALID_SENSOR_ROW = {
    "recordType": "sensor",
    "timestampMs": 1000,
    "measurementActive": True,
    "accX": 0.0,
    "accY": 0.0,
    "accZ": 9.80665,
    "gyroX": 0.0,
    "gyroY": 0.0,
    "gyroZ": 0.0,
}


def encoded_row(row: object) -> bytes:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ReplayJsonlTruncatedTailTests(unittest.TestCase):
    def run_cli(self, path: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REPLAY_SCRIPT),
                str(path),
                "--no-strict-start",
                *extra_args,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_strict_cli_rejects_unterminated_malformed_tail(self) -> None:
        malformed_tail = b'{"timestampMs":1001'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "strict.jsonl"
            path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n" + malformed_tail)

            result = self.run_cli(path)

        self.assertEqual(result.returncode, 2)
        self.assertIn(":2: invalid JSONL:", result.stderr)
        self.assertNotIn('"inputIntegrity"', result.stdout)

    def test_allow_flag_ignores_only_tail_and_marks_summary_incomplete(self) -> None:
        malformed_tail = b'{"timestampMs":1001'
        original_bytes = encoded_row(VALID_SENSOR_ROW) + b"\n" + malformed_tail
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "allowed.jsonl"
            path.write_bytes(original_bytes)

            result = self.run_cli(path, "--allow-truncated-tail")
            self.assertEqual(path.read_bytes(), original_bytes)

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        integrity = summary["inputIntegrity"]
        self.assertEqual(integrity["status"], "incomplete_truncated_tail_ignored")
        self.assertFalse(integrity["complete"])
        self.assertTrue(integrity["allowTruncatedTail"])
        self.assertTrue(integrity["truncatedTailIgnored"])
        self.assertEqual(integrity["ignoredTail"]["lineNumber"], 2)
        self.assertEqual(integrity["ignoredTail"]["byteCount"], len(malformed_tail))
        self.assertIn("input JSONL is incomplete", result.stderr)

    def test_allow_flag_still_rejects_malformed_middle_row(self) -> None:
        malformed_callback = b'{"recordType":"sensor_callback","timestampMs":1001'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "middle.jsonl"
            path.write_bytes(
                encoded_row(VALID_SENSOR_ROW)
                + b"\n"
                + malformed_callback
                + b"\n"
                + encoded_row({"recordType": "event", "timestampMs": 1002})
            )

            with self.assertRaisesRegex(ValueError, r":2: invalid JSONL:"):
                read_jsonl(path, allow_truncated_tail=True)

    def test_allow_flag_still_rejects_terminated_malformed_last_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "terminated-tail.jsonl"
            path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n{\"timestampMs\":1001\n")

            with self.assertRaisesRegex(ValueError, r":2: invalid JSONL:"):
                read_jsonl(path, allow_truncated_tail=True)

    def test_allow_flag_does_not_hide_object_or_field_validation_errors(self) -> None:
        invalid_tails = (
            (encoded_row([1, 2, 3]), "each JSONL row must be an object"),
            (encoded_row({"recordType": "event"}), "timestampMs must be a finite integer"),
            (
                encoded_row({"recordType": "sensor_callback"}),
                "timestampMs must be a finite integer",
            ),
            (
                encoded_row({"timestampMs": 1001, "recordSeq": -1}),
                "recordSeq must be a non-negative integer",
            ),
        )
        for tail, expected_error in invalid_tails:
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "validation.jsonl"
                    path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n" + tail)

                    with self.assertRaisesRegex(ValueError, expected_error):
                        read_jsonl(path, allow_truncated_tail=True)

    def test_valid_unterminated_last_row_is_not_reported_as_incomplete(self) -> None:
        final_row = {"recordType": "event", "timestampMs": 1001}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "valid-no-newline.jsonl"
            path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n" + encoded_row(final_row))

            rows, read_info = read_jsonl_with_info(path, allow_truncated_tail=True)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1], final_row)
        self.assertTrue(read_info.complete)
        self.assertEqual(
            read_info.summary(allow_truncated_tail=True),
            {
                "status": "complete",
                "complete": True,
                "allowTruncatedTail": True,
                "truncatedTailIgnored": False,
            },
        )

    def test_batch_runner_never_reports_truncated_input_as_complete(self) -> None:
        malformed_tail = b'{"timestampMs":1001'
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "truncated.jsonl"
            path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n" + malformed_tail)

            result = subprocess.run(
                [
                    sys.executable,
                    str(BASELINE_SCRIPT),
                    "--dir",
                    temp_dir,
                    "--files",
                    path.name,
                    "--allow-truncated-tail",
                    "--no-strict-start",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("INCOMPLETE truncated.jsonl:", result.stdout)
        self.assertIn("input_complete=false", result.stdout)
        self.assertIn("incomplete=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
