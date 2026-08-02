from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    from . import aux_sensor_location_diag
except ImportError:
    import aux_sensor_location_diag


SESSION_ID = "session-1000"


def base_row(schema: int, seq: int, timestamp_ms: int, record_type: str) -> dict[str, object]:
    return {
        "timestampMs": timestamp_ms,
        "sessionId": SESSION_ID,
        "recordSchemaVersion": schema,
        "appVersionCode": 1,
        "algorithmVersion": "test",
        "recordSeq": seq,
        "recordType": record_type,
        "measurementRunId": None,
        "measurementActive": False,
        "measurementStartMs": None,
        "measurementElapsedMs": None,
    }


def lifecycle_row(schema: int, seq: int, timestamp_ms: int, event: str) -> dict[str, object]:
    row = base_row(schema, seq, timestamp_ms, "lifecycle")
    row["event"] = event
    return row


def sensor_callback_row(schema: int, seq: int, timestamp_ms: int) -> dict[str, object]:
    row = base_row(schema, seq, timestamp_ms, "sensor_callback")
    row.update(
        {
            "sensorType": "accelerometer",
            "sensorTimestamp": timestamp_ms * 1_000_000,
            "requestedIntervalNs": 10_000_000,
        }
    )
    return row


def device_health_row(
    seq: int,
    timestamp_ms: int,
    reason: str,
    temperature_c: float | None = 32.5,
    thermal_level: int | None = 1,
) -> dict[str, object]:
    row = base_row(17, seq, timestamp_ms, "device_health")
    row.update(
        {
            "deviceHealthReason": reason,
            "batteryTemperatureC": temperature_c,
            "thermalLevel": thermal_level,
        }
    )
    return row


class AuxSensorLocationDiagTest(unittest.TestCase):
    def diagnose_rows(self, rows: list[dict[str, object]]) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.jsonl"
            path.write_text(
                "".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = aux_sensor_location_diag.diagnose(str(path), 2.5, 3)
            return result, output.getvalue()

    def test_schema16_remains_compatible_without_device_health(self) -> None:
        rows = [
            lifecycle_row(16, 1, 1000, "start_record"),
            sensor_callback_row(16, 2, 1010),
            lifecycle_row(16, 3, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertNotIn("Device health:", output)

    def test_schema17_accepts_start_periodic_and_stop_device_health(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start"),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 11000, "periodic", 33.2, 2),
            device_health_row(5, 11500, "stop", 33.3, 2),
            lifecycle_row(17, 6, 11500, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertIn("records=3", output)
        self.assertIn("valid=yes", output)
        self.assertIn("2:warm", output)

    def test_schema17_rejects_missing_device_health(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            sensor_callback_row(17, 2, 1010),
            lifecycle_row(17, 3, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertFalse(result)
        self.assertIn("records=0", output)
        self.assertIn("valid=no", output)

    def test_schema17_reports_unavailable_health_values_without_corrupting_log(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start", None, None),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 2000, "stop", None, None),
            lifecycle_row(17, 5, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertIn("batteryTemperatureC: unavailable", output)
        self.assertIn("thermalLevel: unavailable", output)

    def test_schema17_rejects_invalid_health_values(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start", 32.5, 99),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 2000, "stop", 32.6, 1),
            lifecycle_row(17, 5, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertFalse(result)
        self.assertIn("invalid device_health lines: 2", output)


if __name__ == "__main__":
    unittest.main()
