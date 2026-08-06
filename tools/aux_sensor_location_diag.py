#!/usr/bin/env python3
"""Diagnose actual callback cadence in MetroSpeed schema-15/16/17 research JSONL.

The requested interval is only a subscription request.  This tool recomputes
intervals from the recorded sensor timestamps and callback timestamps instead
of treating ``requestedIntervalNs`` or the recorder's derived interval fields
as the actual rate.  Schema 17 device-health records are also checked and
summarized separately from the high-rate sensor stream.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import shutil
import sys
import tempfile
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

SUPPORTED_SCHEMA_VERSIONS = {15, 16, 17}
SCHEMA16_PLUS_SENSOR_TYPES = {
    "accelerometer",
    "gyroscope",
    "rotation_vector",
    "magnetic_field",
    "gyroscope_uncalibrated",
    "magnetic_field_uncalibrated",
}
DEVICE_HEALTH_REASONS = {"start", "periodic", "stop"}
THERMAL_LEVEL_NAMES = {
    0: "cool",
    1: "normal",
    2: "warm",
    3: "hot",
    4: "overheated",
    5: "warning",
    6: "emergency",
    7: "escape",
}


@dataclass(frozen=True, slots=True)
class Gap:
    interval_ms: float
    line_no: int
    record_seq: int | None
    estimated_missing: int


@dataclass(slots=True)
class SeriesStats:
    point_count: int
    interval_count: int
    effective_hz: float | None
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    reverse_count: int
    duplicate_count: int
    large_gaps: list[Gap] = field(default_factory=list)
    large_gap_count: int = 0
    estimated_missing: int = 0


@dataclass(slots=True)
class SeriesAccumulator:
    """Keep exact interval quantiles in a compact double array, not per-row objects."""

    point_count: int = 0
    first_ms: float | None = None
    last_ms: float | None = None
    previous_ms: float | None = None
    max_ms: float | None = None
    reverse_count: int = 0
    duplicate_count: int = 0
    positive_intervals: array = field(default_factory=lambda: array("d"))

    def add(self, value_ms: float) -> None:
        if self.first_ms is None:
            self.first_ms = value_ms
        if self.previous_ms is not None:
            interval_ms = value_ms - self.previous_ms
            if interval_ms > 0:
                self.positive_intervals.append(interval_ms)
                if self.max_ms is None or interval_ms > self.max_ms:
                    self.max_ms = interval_ms
            elif interval_ms < 0:
                self.reverse_count += 1
            else:
                self.duplicate_count += 1
        self.previous_ms = value_ms
        self.last_ms = value_ms
        self.point_count += 1

    def summarize(self) -> SeriesStats:
        effective_hz: float | None = None
        if (
            self.point_count > 1
            and self.first_ms is not None
            and self.last_ms is not None
            and self.last_ms > self.first_ms
        ):
            effective_hz = (self.point_count - 1) * 1000.0 / (
                self.last_ms - self.first_ms
            )
        return SeriesStats(
            point_count=self.point_count,
            interval_count=len(self.positive_intervals),
            effective_hz=effective_hz,
            p50_ms=percentile(self.positive_intervals, 0.50),
            p95_ms=percentile(self.positive_intervals, 0.95),
            max_ms=self.max_ms,
            reverse_count=self.reverse_count,
            duplicate_count=self.duplicate_count,
            large_gaps=[],
        )

    def release_intervals(self) -> None:
        self.positive_intervals = array("d")


@dataclass(slots=True)
class SequenceStats:
    first: int | None = None
    last: int | None = None
    missing: int = 0
    reverse: int = 0
    duplicate: int = 0

    def add(self, record_seq: int) -> None:
        if self.first is None:
            self.first = record_seq
        if self.last is not None:
            if record_seq > self.last:
                self.missing += max(0, record_seq - self.last - 1)
            elif record_seq < self.last:
                self.reverse += 1
            else:
                self.duplicate += 1
        self.last = record_seq


@dataclass(frozen=True, slots=True)
class SensorCallback:
    sensor_type: str
    callback_ms: float
    sensor_ms: float
    requested_ns: float


@dataclass(slots=True)
class GapScanner:
    p50_ms: float | None
    gap_factor: float
    keep_count: int
    previous_ms: float | None = None
    large_gap_count: int = 0
    estimated_missing: int = 0
    largest: list[tuple[float, int, Gap]] = field(default_factory=list)

    def add(self, value_ms: float, line_no: int, record_seq: int | None) -> None:
        if self.previous_ms is None:
            self.previous_ms = value_ms
            return
        interval_ms = value_ms - self.previous_ms
        self.previous_ms = value_ms
        if (
            self.p50_ms is None
            or self.p50_ms <= 0
            or interval_ms <= self.p50_ms * self.gap_factor
        ):
            return
        estimated_missing = max(0, round(interval_ms / self.p50_ms) - 1)
        gap = Gap(interval_ms, line_no, record_seq, estimated_missing)
        self.large_gap_count += 1
        self.estimated_missing += estimated_missing
        if self.keep_count <= 0:
            return
        entry = (interval_ms, -line_no, gap)
        if len(self.largest) < self.keep_count:
            heapq.heappush(self.largest, entry)
        elif entry[:2] > self.largest[0][:2]:
            heapq.heapreplace(self.largest, entry)

    def apply(self, stats: SeriesStats) -> None:
        stats.large_gap_count = self.large_gap_count
        stats.estimated_missing = self.estimated_missing
        stats.large_gaps = sorted(
            (entry[2] for entry in self.largest),
            key=lambda gap: (-gap.interval_ms, gap.line_no),
        )


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def optional_int(value: Any) -> int | None:
    number = finite_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def select_kth(values: array, target: int) -> float:
    """Select one exact order statistic in-place without expanding doubles to objects."""
    left = 0
    right = len(values) - 1
    while left < right:
        pivot = values[(left + right) // 2]
        lower = left
        upper = right
        while lower <= upper:
            while values[lower] < pivot:
                lower += 1
            while values[upper] > pivot:
                upper -= 1
            if lower <= upper:
                values[lower], values[upper] = values[upper], values[lower]
                lower += 1
                upper -= 1
        if target <= upper:
            right = upper
        elif target >= lower:
            left = lower
        else:
            return float(values[target])
    return float(values[left])


def percentile(values: array, quantile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    lower_value = select_kth(values, lower)
    if lower == upper:
        return lower_value
    upper_value = select_kth(values, upper)
    fraction = position - lower
    return lower_value * (1.0 - fraction) + upper_value * fraction


def format_number(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def mode_requested_interval_ms(counts: Counter[float]) -> tuple[float | None, int]:
    if not counts:
        return None, 0
    value_ms, count = counts.most_common(1)[0]
    return value_ms, count


def parse_sensor_callback(row: dict[str, Any]) -> tuple[SensorCallback | None, list[str]]:
    errors: list[str] = []
    raw_sensor_type = row.get("sensorType")
    sensor_type = raw_sensor_type if isinstance(raw_sensor_type, str) else ""
    if not sensor_type.strip():
        errors.append("sensorType")

    callback_ms = finite_number(row.get("timestampMs"))
    if callback_ms is None or callback_ms < 0:
        errors.append("timestampMs")

    sensor_timestamp_ns = finite_number(row.get("sensorTimestamp"))
    if sensor_timestamp_ns is None or sensor_timestamp_ns < 0:
        errors.append("sensorTimestamp")

    requested_ns = finite_number(row.get("requestedIntervalNs"))
    if requested_ns is None or requested_ns <= 0:
        errors.append("requestedIntervalNs")

    if errors:
        return None, errors
    return (
        SensorCallback(
            sensor_type=sensor_type,
            callback_ms=callback_ms,
            sensor_ms=sensor_timestamp_ns / 1_000_000.0,
            requested_ns=requested_ns,
        ),
        [],
    )


def remember_line(lines: list[int], line_no: int, limit: int = 10) -> None:
    if len(lines) < limit:
        lines.append(line_no)


def copy_source_to_snapshot(source: str, snapshot: BinaryIO) -> None:
    if source == "-":
        stdin_buffer = getattr(sys.stdin, "buffer", None)
        if stdin_buffer is not None:
            shutil.copyfileobj(stdin_buffer, snapshot, length=1024 * 1024)
        else:
            while chunk := sys.stdin.read(1024 * 1024):
                snapshot.write(chunk.encode("utf-8", errors="surrogatepass"))
    else:
        with Path(source).open("rb") as stream:
            shutil.copyfileobj(stream, snapshot, length=1024 * 1024)
    snapshot.seek(0)


def parse_json_object(raw_line: bytes) -> dict[str, Any] | None:
    try:
        row = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return row if isinstance(row, dict) else None


def print_series(label: str, stats: SeriesStats, gap_factor: float, show_gaps: int) -> None:
    print(
        f"  {label}: effective={format_number(stats.effective_hz)} Hz, "
        f"P50={format_number(stats.p50_ms)} ms, "
        f"P95={format_number(stats.p95_ms)} ms, "
        f"max={format_number(stats.max_ms)} ms"
    )
    print(
        f"    intervals={stats.interval_count}, reverse={stats.reverse_count}, "
        f"duplicate={stats.duplicate_count}, "
        f"gaps(>{gap_factor:g}xP50)={stats.large_gap_count}, "
        f"estimated_missing~={stats.estimated_missing}"
    )
    for gap in stats.large_gaps[:show_gaps]:
        seq_text = "n/a" if gap.record_seq is None else str(gap.record_seq)
        print(
            f"      gap={gap.interval_ms:.2f} ms at line={gap.line_no}, "
            f"recordSeq={seq_text}, estimated_missing~={gap.estimated_missing}"
        )


def _diagnose_snapshot(
    snapshot: BinaryIO, source: str, gap_factor: float, show_gaps: int
) -> bool:
    sensor_accumulators: dict[str, dict[str, SeriesAccumulator]] = defaultdict(
        lambda: {"sensor": SeriesAccumulator(), "callback": SeriesAccumulator()}
    )
    requested_modes: dict[str, Counter[float]] = defaultdict(Counter)
    system_accumulators: dict[str, SeriesAccumulator] = defaultdict(SeriesAccumulator)
    schema_versions: Counter[int] = Counter()
    session_sequences: dict[str, SequenceStats] = defaultdict(SequenceStats)
    invalid_json_lines: list[int] = []
    invalid_json_count = 0
    invalid_record_seq_lines: list[int] = []
    invalid_record_seq_count = 0
    invalid_metadata_lines: list[int] = []
    invalid_metadata_count = 0
    invalid_metadata_fields: Counter[str] = Counter()
    metadata_reference: dict[str, int | str] = {}
    invalid_sensor_callback_lines: list[int] = []
    invalid_sensor_callback_count = 0
    invalid_sensor_callback_fields: Counter[str] = Counter()
    valid_rows = 0
    sensor_callback_rows = 0
    valid_sensor_callback_rows = 0
    observed_sensor_types: set[str] = set()
    device_health_rows = 0
    device_health_accumulator = SeriesAccumulator()
    device_health_reasons: Counter[str] = Counter()
    device_health_temperature_count = 0
    device_health_temperature_min: float | None = None
    device_health_temperature_max: float | None = None
    device_health_thermal_levels: Counter[int] = Counter()
    unavailable_battery_temperature_rows = 0
    unavailable_thermal_level_rows = 0
    invalid_device_health_lines: list[int] = []
    invalid_device_health_count = 0
    first_record: dict[str, Any] | None = None
    last_record: dict[str, Any] | None = None
    sequence_integrity_ok = True

    snapshot.seek(0)
    for line_no, raw_line in enumerate(snapshot, start=1):
        if not raw_line.strip():
            continue
        row = parse_json_object(raw_line)
        if row is None:
            invalid_json_count += 1
            remember_line(invalid_json_lines, line_no)
            continue

        valid_rows += 1
        if first_record is None:
            first_record = row
        last_record = row
        schema_version = optional_int(row.get("recordSchemaVersion"))
        if schema_version is not None:
            schema_versions[schema_version] += 1

        metadata_errors: list[str] = []
        app_version_code = optional_int(row.get("appVersionCode"))
        if app_version_code is None or app_version_code < 1:
            metadata_errors.append("appVersionCode")
        elif "appVersionCode" not in metadata_reference:
            metadata_reference["appVersionCode"] = app_version_code
        elif app_version_code != metadata_reference["appVersionCode"]:
            metadata_errors.append("appVersionCode_mismatch")

        algorithm_version = row.get("algorithmVersion")
        if not isinstance(algorithm_version, str) or not algorithm_version.strip():
            metadata_errors.append("algorithmVersion")
        elif "algorithmVersion" not in metadata_reference:
            metadata_reference["algorithmVersion"] = algorithm_version
        elif algorithm_version != metadata_reference["algorithmVersion"]:
            metadata_errors.append("algorithmVersion_mismatch")

        session_id = row.get("sessionId")
        if not isinstance(session_id, str) or not session_id.strip():
            metadata_errors.append("sessionId")
        elif "sessionId" not in metadata_reference:
            metadata_reference["sessionId"] = session_id
        elif session_id != metadata_reference["sessionId"]:
            metadata_errors.append("sessionId_mismatch")

        if metadata_errors:
            invalid_metadata_count += 1
            invalid_metadata_fields.update(metadata_errors)
            remember_line(invalid_metadata_lines, line_no)

        record_seq = optional_int(row.get("recordSeq"))
        if record_seq is not None:
            sequence_session = (
                session_id
                if isinstance(session_id, str) and session_id.strip()
                else "<invalid>"
            )
            session_sequences[sequence_session].add(record_seq)
        else:
            invalid_record_seq_count += 1
            remember_line(invalid_record_seq_lines, line_no)

        record_type = row.get("recordType")
        callback_ms = finite_number(row.get("timestampMs"))
        if record_type == "sensor_callback":
            sensor_callback_rows += 1
            raw_sensor_type = row.get("sensorType")
            observed_sensor_types.add(
                raw_sensor_type
                if isinstance(raw_sensor_type, str) and raw_sensor_type.strip()
                else "<missing>"
            )
            callback, callback_errors = parse_sensor_callback(row)
            if callback is None:
                invalid_sensor_callback_count += 1
                invalid_sensor_callback_fields.update(callback_errors)
                remember_line(invalid_sensor_callback_lines, line_no)
                continue
            valid_sensor_callback_rows += 1
            sensor_accumulators[callback.sensor_type]["callback"].add(
                callback.callback_ms
            )
            sensor_accumulators[callback.sensor_type]["sensor"].add(
                callback.sensor_ms
            )
            requested_modes[callback.sensor_type][
                round(callback.requested_ns / 1_000_000.0, 6)
            ] += 1
        elif record_type == "device_health":
            device_health_rows += 1
            row_valid = True
            if callback_ms is not None:
                device_health_accumulator.add(callback_ms)
            else:
                row_valid = False

            reason = row.get("deviceHealthReason")
            if isinstance(reason, str) and reason in DEVICE_HEALTH_REASONS:
                device_health_reasons[reason] += 1
            else:
                row_valid = False

            raw_temperature = row.get("batteryTemperatureC")
            temperature = finite_number(raw_temperature)
            if raw_temperature is None:
                unavailable_battery_temperature_rows += 1
            elif temperature is None:
                row_valid = False
            else:
                device_health_temperature_count += 1
                if (
                    device_health_temperature_min is None
                    or temperature < device_health_temperature_min
                ):
                    device_health_temperature_min = temperature
                if (
                    device_health_temperature_max is None
                    or temperature > device_health_temperature_max
                ):
                    device_health_temperature_max = temperature

            raw_thermal_level = row.get("thermalLevel")
            thermal_level = optional_int(raw_thermal_level)
            if raw_thermal_level is None:
                unavailable_thermal_level_rows += 1
            elif thermal_level not in THERMAL_LEVEL_NAMES:
                row_valid = False
            else:
                device_health_thermal_levels[thermal_level] += 1

            if not row_valid:
                invalid_device_health_count += 1
                remember_line(invalid_device_health_lines, line_no)
        elif record_type in {"location", "satellite"} and callback_ms is not None:
            system_accumulators[str(record_type)].add(callback_ms)

    sensor_stats = {
        sensor_type: {
            clock: accumulator.summarize()
            for clock, accumulator in clocks.items()
        }
        for sensor_type, clocks in sensor_accumulators.items()
    }
    system_stats = {
        record_type: accumulator.summarize()
        for record_type, accumulator in system_accumulators.items()
    }
    device_health_stats = device_health_accumulator.summarize()

    sensor_gap_scanners = {
        sensor_type: {
            clock: GapScanner(stats.p50_ms, gap_factor, show_gaps)
            for clock, stats in clocks.items()
        }
        for sensor_type, clocks in sensor_stats.items()
    }
    system_gap_scanners = {
        record_type: GapScanner(stats.p50_ms, gap_factor, show_gaps)
        for record_type, stats in system_stats.items()
    }
    device_health_gap_scanner = GapScanner(
        device_health_stats.p50_ms, gap_factor, show_gaps
    )

    for clocks in sensor_accumulators.values():
        for accumulator in clocks.values():
            accumulator.release_intervals()
    for accumulator in system_accumulators.values():
        accumulator.release_intervals()
    device_health_accumulator.release_intervals()

    snapshot.seek(0)
    for line_no, raw_line in enumerate(snapshot, start=1):
        if not raw_line.strip():
            continue
        row = parse_json_object(raw_line)
        if row is None:
            continue
        record_seq = optional_int(row.get("recordSeq"))
        record_type = row.get("recordType")
        callback_ms = finite_number(row.get("timestampMs"))
        if record_type == "sensor_callback":
            callback, _ = parse_sensor_callback(row)
            if callback is None:
                continue
            scanners = sensor_gap_scanners.get(callback.sensor_type)
            if scanners is None:
                continue
            scanners["callback"].add(callback.callback_ms, line_no, record_seq)
            scanners["sensor"].add(callback.sensor_ms, line_no, record_seq)
        elif record_type == "device_health" and callback_ms is not None:
            device_health_gap_scanner.add(callback_ms, line_no, record_seq)
        elif record_type in system_gap_scanners and callback_ms is not None:
            system_gap_scanners[str(record_type)].add(
                callback_ms, line_no, record_seq
            )

    for sensor_type, clocks in sensor_stats.items():
        for clock, stats in clocks.items():
            sensor_gap_scanners[sensor_type][clock].apply(stats)
    for record_type, stats in system_stats.items():
        system_gap_scanners[record_type].apply(stats)
    device_health_gap_scanner.apply(device_health_stats)

    print(f"\n=== {source} ===")
    print(
        f"rows={valid_rows}, sensor_callback={sensor_callback_rows}, "
        f"invalid_json={invalid_json_count}, "
        f"schemas={dict(sorted(schema_versions.items()))}"
    )
    if invalid_sensor_callback_count:
        print(
            f"valid_sensor_callback={valid_sensor_callback_rows}, "
            f"invalid_sensor_callback={invalid_sensor_callback_count}"
        )
    uniform_schema = len(schema_versions) == 1
    schema_version = next(iter(schema_versions), None) if uniform_schema else None
    schema_ok = (
        valid_rows > 0
        and uniform_schema
        and schema_version in SUPPORTED_SCHEMA_VERSIONS
        and schema_versions.get(schema_version, 0) == valid_rows
    )
    print(
        f"supported_uniform_schema={'yes' if schema_ok else 'no'} "
        f"(supported={sorted(SUPPORTED_SCHEMA_VERSIONS)})"
    )
    if invalid_json_count:
        shown = ", ".join(str(line) for line in invalid_json_lines[:10])
        suffix = " ..." if invalid_json_count > len(invalid_json_lines) else ""
        print(f"invalid JSON lines: {shown}{suffix}")
    if invalid_record_seq_count:
        shown = ", ".join(str(line) for line in invalid_record_seq_lines[:10])
        suffix = " ..." if invalid_record_seq_count > len(invalid_record_seq_lines) else ""
        print(f"missing/invalid recordSeq lines: {shown}{suffix}")
        sequence_integrity_ok = False
    if invalid_metadata_count:
        shown = ", ".join(str(line) for line in invalid_metadata_lines)
        suffix = " ..." if invalid_metadata_count > len(invalid_metadata_lines) else ""
        print(f"invalid metadata lines: {shown}{suffix}")
        print(f"invalid metadata fields: {dict(sorted(invalid_metadata_fields.items()))}")
    if invalid_sensor_callback_count:
        shown = ", ".join(str(line) for line in invalid_sensor_callback_lines)
        suffix = (
            " ..."
            if invalid_sensor_callback_count > len(invalid_sensor_callback_lines)
            else ""
        )
        print(f"invalid sensor_callback lines: {shown}{suffix}")
        print(
            "invalid sensor_callback fields: "
            f"{dict(sorted(invalid_sensor_callback_fields.items()))}"
        )

    if len(session_sequences) != 1:
        print(f"session_count={len(session_sequences)} (expected 1)")
        sequence_integrity_ok = False

    for session_id, sequence in session_sequences.items():
        print(
            f"recordSeq[{session_id}]: first={sequence.first}, last={sequence.last}, "
            f"missing={sequence.missing}, reverse={sequence.reverse}, "
            f"duplicate={sequence.duplicate}"
        )
        if (
            sequence.first != 1
            or sequence.missing > 0
            or sequence.reverse > 0
            or sequence.duplicate > 0
        ):
            sequence_integrity_ok = False

    start_ok = (
        first_record is not None
        and first_record.get("recordType") == "lifecycle"
        and first_record.get("event") == "start_record"
    )
    print(f"initial_start_record={'yes' if start_ok else 'no'}")

    terminal_ok = False
    if last_record is not None:
        terminal_ok = (
            last_record.get("recordType") == "lifecycle"
            and last_record.get("event") == "stop_record"
        )
        print(f"terminal_stop_record={'yes' if terminal_ok else 'no'}")

    sensor_set_ok = True
    if schema_version is not None and schema_version >= 16:
        unexpected_sensor_types = sorted(
            observed_sensor_types - SCHEMA16_PLUS_SENSOR_TYPES
        )
        sensor_set_ok = not unexpected_sensor_types
        print(
            "schema16_plus_sensor_set="
            f"{'yes' if sensor_set_ok else 'no'} "
            f"(unexpected={unexpected_sensor_types})"
        )

    if not sensor_stats:
        print("No valid schema-15/16/17 sensor_callback records found.")
    else:
        print("\nSensor callback cadence:")
        for sensor_type in sorted(sensor_stats):
            clocks = sensor_stats[sensor_type]
            request_ms, request_count = mode_requested_interval_ms(
                requested_modes[sensor_type]
            )
            request_hz = None if request_ms is None or request_ms <= 0 else 1000.0 / request_ms
            print(
                f"\n{sensor_type}: records={clocks['callback'].point_count}, "
                f"requested_mode={format_number(request_ms)} ms/"
                f"{format_number(request_hz)} Hz ({request_count} rows)"
            )
            if clocks["sensor"].point_count:
                print_series(
                    "sensor timestamp",
                    clocks["sensor"],
                    gap_factor,
                    show_gaps,
                )
            else:
                print("  sensor timestamp: unavailable")
            print_series(
                "callback clock",
                clocks["callback"],
                gap_factor,
                show_gaps,
            )

    if system_stats:
        print("\nSystem callback cadence:")
        for record_type in sorted(system_stats):
            print(f"\n{record_type}: records={system_stats[record_type].point_count}")
            print_series(
                "callback clock",
                system_stats[record_type],
                gap_factor,
                show_gaps,
            )

    device_health_ok = True
    if schema_version == 17:
        device_health_ok = (
            device_health_rows >= 2
            and device_health_reasons["start"] == 1
            and device_health_reasons["stop"] == 1
            and invalid_device_health_count == 0
        )
        print("\nDevice health:")
        print(
            f"  records={device_health_rows}, reasons={dict(device_health_reasons)}, "
            f"valid={'yes' if device_health_ok else 'no'}"
        )
        if device_health_stats.point_count:
            print_series(
                "sampling clock",
                device_health_stats,
                gap_factor,
                show_gaps,
            )
        if device_health_temperature_count:
            print(
                "  batteryTemperatureC: "
                f"min={device_health_temperature_min:.1f}, "
                f"max={device_health_temperature_max:.1f}, "
                f"unavailable={unavailable_battery_temperature_rows}"
            )
        else:
            print(
                "  batteryTemperatureC: unavailable "
                f"({unavailable_battery_temperature_rows} rows)"
            )
        if device_health_thermal_levels:
            level_summary = {
                f"{level}:{THERMAL_LEVEL_NAMES[level]}": count
                for level, count in sorted(device_health_thermal_levels.items())
            }
            print(
                f"  thermalLevel: {level_summary}, "
                f"unavailable={unavailable_thermal_level_rows}"
            )
        else:
            print(
                f"  thermalLevel: unavailable ({unavailable_thermal_level_rows} rows)"
            )
        if invalid_device_health_count:
            shown = ", ".join(str(line) for line in invalid_device_health_lines[:10])
            suffix = (
                " ..."
                if invalid_device_health_count > len(invalid_device_health_lines)
                else ""
            )
            print(f"  invalid device_health lines: {shown}{suffix}")

    return (
        valid_rows > 0
        and valid_sensor_callback_rows > 0
        and invalid_sensor_callback_count == 0
        and invalid_json_count == 0
        and invalid_metadata_count == 0
        and schema_ok
        and sensor_set_ok
        and device_health_ok
        and sequence_integrity_ok
        and start_ok
        and terminal_ok
    )


def diagnose(source: str, gap_factor: float, show_gaps: int) -> bool:
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        copy_source_to_snapshot(source, snapshot)
        return _diagnose_snapshot(snapshot, source, gap_factor, show_gaps)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute actual sensor/callback rates from MetroSpeed schema-15/16/17 JSONL. "
            "Use '-' to read JSONL from stdin."
        )
    )
    parser.add_argument("jsonl", nargs="+", help="JSONL path(s), or '-' for stdin")
    parser.add_argument(
        "--gap-factor",
        type=float,
        default=2.5,
        help="mark a positive interval as a gap when it exceeds this multiple of P50 (default: 2.5)",
    )
    parser.add_argument(
        "--show-gaps",
        type=int,
        default=3,
        help="show this many largest gaps per clock and sensor (default: 3)",
    )
    args = parser.parse_args()

    if not math.isfinite(args.gap_factor) or args.gap_factor <= 1.0:
        parser.error("--gap-factor must be finite and greater than 1")
    if args.show_gaps < 0:
        parser.error("--show-gaps must be non-negative")
    if args.jsonl.count("-") > 1 or ("-" in args.jsonl and len(args.jsonl) > 1):
        parser.error("stdin '-' can only be used as the sole input")

    all_clean = True
    for source in args.jsonl:
        try:
            all_clean = diagnose(source, args.gap_factor, args.show_gaps) and all_clean
        except OSError as error:
            print(f"{source}: {error}", file=sys.stderr)
            all_clean = False
    return 0 if all_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
