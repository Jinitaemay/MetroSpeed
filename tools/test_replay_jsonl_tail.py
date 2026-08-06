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


def protocol_row(
    record_seq: int,
    record_type: str,
    event: str | None = None,
    *,
    schema: int = 17,
    app_version_code: int = 1785776456,
    session_id: str = "session-test",
    algorithm_version: str = "inertial-speed-v4-calibrated-r4",
) -> dict[str, object]:
    row: dict[str, object] = {
        "timestampMs": 1000 + record_seq,
        "sessionId": session_id,
        "recordSchemaVersion": schema,
        "appVersionCode": app_version_code,
        "algorithmVersion": algorithm_version,
        "recordSeq": record_seq,
        "recordType": record_type,
    }
    if event is not None:
        row["event"] = event
    return row


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
            (
                encoded_row({
                    "recordType": "location",
                    "timestampMs": 1001,
                    "locationTimeMs": {"invalid": True},
                }),
                "locationTimeMs must be a finite integer",
            ),
            (
                encoded_row({"recordType": "event", "timestampMs": 10**400}),
                "timestampMs must be a finite integer",
            ),
            (
                encoded_row({
                    "recordType": "event",
                    "timestampMs": 1001,
                    "recordSeq": 10**400,
                }),
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

    def test_invalid_numeric_fields_fail_cli_without_traceback(self) -> None:
        invalid_rows = (
            (
                {
                    "recordType": "location",
                    "timestampMs": 1001,
                    "locationTimeMs": {"invalid": True},
                },
                "locationTimeMs must be a finite integer",
            ),
            (
                {"recordType": "event", "timestampMs": 10**400},
                "timestampMs must be a finite integer",
            ),
            (
                {
                    "recordType": "event",
                    "timestampMs": 1001,
                    "recordSeq": 10**400,
                },
                "recordSeq must be a non-negative integer",
            ),
        )
        for invalid_row, expected_error in invalid_rows:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "invalid-number.jsonl"
                path.write_bytes(
                    encoded_row(VALID_SENSOR_ROW) + b"\n" + encoded_row(invalid_row)
                )

                result = self.run_cli(path)

                self.assertEqual(result.returncode, 2)
                self.assertIn(expected_error, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_schema_less_valid_json_has_unknown_structural_completeness(self) -> None:
        final_row = {"recordType": "event", "timestampMs": 1001}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "valid-no-newline.jsonl"
            path.write_bytes(encoded_row(VALID_SENSOR_ROW) + b"\n" + encoded_row(final_row))

            rows, read_info = read_jsonl_with_info(path, allow_truncated_tail=True)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1], final_row)
        self.assertIsNone(read_info.complete)
        summary = read_info.summary(allow_truncated_tail=True)
        self.assertEqual(summary["status"], "unknown_structure")
        self.assertIsNone(summary["complete"])
        self.assertFalse(summary["truncatedTailIgnored"])
        self.assertEqual(
            summary["structure"]["compatibility"],
            "unknown_missing_schema",
        )

    def test_supported_session_requires_terminal_stop_record(self) -> None:
        rows = [
            protocol_row(1, "lifecycle", "start_record"),
            {
                **protocol_row(2, "sensor"),
                "accX": 0.0,
                "accY": 0.0,
                "accZ": 9.80665,
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing-stop.jsonl"
            path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

            _, read_info = read_jsonl_with_info(path)

        self.assertFalse(read_info.complete)
        self.assertEqual(read_info.status, "incomplete_structure")
        issues = read_info.structure["issues"]
        self.assertIn("last row is not stop_record", issues)
        self.assertIn("stop_record count is 0, expected 1", issues)

    def test_supported_session_is_complete_without_final_newline(self) -> None:
        rows = [
            protocol_row(1, "lifecycle", "start_record"),
            protocol_row(2, "sensor_callback"),
            protocol_row(3, "lifecycle", "stop_record"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "complete.jsonl"
            path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

            replay_rows, read_info = read_jsonl_with_info(path)

        self.assertEqual(len(replay_rows), 2)
        self.assertTrue(read_info.complete)
        self.assertEqual(read_info.status, "complete")
        self.assertEqual(read_info.structure["recordSeq"]["last"], 3)

    def test_legacy_schemas_allow_sensor_rows_without_repeated_metadata(self) -> None:
        for schema in (11, 13):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as temp_dir:
                sensor = {
                    "timestampMs": 1002,
                    "recordSeq": 2,
                    "recordType": "sensor",
                    "accX": 0.0,
                    "accY": 0.0,
                    "accZ": 9.80665,
                }
                rows = [
                    protocol_row(1, "lifecycle", "start_record", schema=schema),
                    sensor,
                    protocol_row(3, "lifecycle", "stop_record", schema=schema),
                ]
                path = Path(temp_dir) / f"schema-{schema}.jsonl"
                path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

                _, read_info = read_jsonl_with_info(path)

                self.assertTrue(read_info.complete)
                self.assertEqual(read_info.structure["recordSchemaVersion"], schema)

    def test_full_metadata_schemas_14_through_17_are_explicitly_supported(self) -> None:
        for schema in (14, 15, 16, 17):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as temp_dir:
                middle_type = "sensor" if schema == 14 else "sensor_callback"
                rows = [
                    protocol_row(1, "lifecycle", "start_record", schema=schema),
                    protocol_row(2, middle_type, schema=schema),
                    protocol_row(3, "lifecycle", "stop_record", schema=schema),
                ]
                path = Path(temp_dir) / f"schema-{schema}.jsonl"
                path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

                _, read_info = read_jsonl_with_info(path)

                self.assertTrue(read_info.complete)
                self.assertEqual(read_info.structure["recordSchemaVersion"], schema)

    def test_modern_lifecycle_events_require_lifecycle_record_type(self) -> None:
        for schema in (14, 15, 16, 17):
            for invalid_event in ("start_record", "stop_record"):
                with (
                    self.subTest(schema=schema, invalid_event=invalid_event),
                    tempfile.TemporaryDirectory() as temp_dir,
                ):
                    start = protocol_row(1, "lifecycle", "start_record", schema=schema)
                    stop = protocol_row(2, "lifecycle", "stop_record", schema=schema)
                    (start if invalid_event == "start_record" else stop)["recordType"] = "event"
                    path = Path(temp_dir) / f"schema-{schema}-{invalid_event}.jsonl"
                    path.write_bytes(encoded_row(start) + b"\n" + encoded_row(stop))

                    _, read_info = read_jsonl_with_info(path)

                    self.assertFalse(read_info.complete)
                    self.assertIn(
                        "start_record/stop_record rows with non-lifecycle recordType: 1",
                        read_info.structure["issues"],
                    )

    def test_supported_session_detects_metadata_and_sequence_changes(self) -> None:
        mutations = {
            "recordSeq": {"recordSeq": 4},
            "appVersionCode": {"appVersionCode": 1785776457},
            "sessionId": {"sessionId": "other-session"},
            "recordSchemaVersion": {"recordSchemaVersion": 14},
            "algorithmVersion": {"algorithmVersion": "different-algorithm"},
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                middle = {**protocol_row(2, "location"), **mutation}
                stop_seq = 5 if name == "recordSeq" else 3
                rows = [
                    protocol_row(1, "lifecycle", "start_record"),
                    middle,
                    protocol_row(stop_seq, "lifecycle", "stop_record"),
                ]
                path = Path(temp_dir) / f"changed-{name}.jsonl"
                path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

                _, read_info = read_jsonl_with_info(path)

                self.assertFalse(read_info.complete)
                self.assertEqual(read_info.status, "incomplete_structure")
                if name == "appVersionCode":
                    self.assertIn(
                        "appVersionCode changes within the file",
                        read_info.structure["issues"],
                    )
                    self.assertEqual(
                        read_info.structure["appVersionCodeExamples"],
                        [1785776456, 1785776457],
                    )

    def test_supported_session_detects_missing_app_version_code(self) -> None:
        middle = protocol_row(2, "location")
        del middle["appVersionCode"]
        rows = [
            protocol_row(1, "lifecycle", "start_record"),
            middle,
            protocol_row(3, "lifecycle", "stop_record"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing-app-version.jsonl"
            path.write_bytes(b"\n".join(encoded_row(row) for row in rows))

            _, read_info = read_jsonl_with_info(path)

        self.assertFalse(read_info.complete)
        self.assertEqual(read_info.status, "incomplete_structure")
        self.assertIn("missing appVersionCode rows: 1", read_info.structure["issues"])

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
