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
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TextIO

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


@dataclass(frozen=True)
class Point:
    value_ms: float
    line_no: int
    record_seq: int | None


@dataclass(frozen=True)
class Gap:
    interval_ms: float
    line_no: int
    record_seq: int | None
    estimated_missing: int


@dataclass
class SeriesStats:
    point_count: int
    interval_count: int
    effective_hz: float | None
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    reverse_count: int
    duplicate_count: int
    large_gaps: list[Gap]


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def optional_int(value: Any) -> int | None:
    number = finite_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def percentile(sorted_values: list[float], quantile: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def analyse_series(points: list[Point], gap_factor: float) -> SeriesStats:
    positive_intervals: list[float] = []
    interval_endpoints: list[tuple[float, Point]] = []
    reverse_count = 0
    duplicate_count = 0

    for previous, current in zip(points, points[1:]):
        interval_ms = current.value_ms - previous.value_ms
        if interval_ms > 0:
            positive_intervals.append(interval_ms)
            interval_endpoints.append((interval_ms, current))
        elif interval_ms < 0:
            reverse_count += 1
        else:
            duplicate_count += 1

    sorted_intervals = sorted(positive_intervals)
    p50_ms = percentile(sorted_intervals, 0.50)
    p95_ms = percentile(sorted_intervals, 0.95)
    max_ms = sorted_intervals[-1] if sorted_intervals else None

    effective_hz: float | None = None
    if len(points) > 1:
        span_ms = points[-1].value_ms - points[0].value_ms
        if span_ms > 0:
            effective_hz = (len(points) - 1) * 1000.0 / span_ms

    large_gaps: list[Gap] = []
    if p50_ms is not None and p50_ms > 0:
        threshold_ms = p50_ms * gap_factor
        for interval_ms, endpoint in interval_endpoints:
            if interval_ms > threshold_ms:
                estimated_missing = max(0, round(interval_ms / p50_ms) - 1)
                large_gaps.append(
                    Gap(
                        interval_ms=interval_ms,
                        line_no=endpoint.line_no,
                        record_seq=endpoint.record_seq,
                        estimated_missing=estimated_missing,
                    )
                )
        large_gaps.sort(key=lambda gap: gap.interval_ms, reverse=True)

    return SeriesStats(
        point_count=len(points),
        interval_count=len(positive_intervals),
        effective_hz=effective_hz,
        p50_ms=p50_ms,
        p95_ms=p95_ms,
        max_ms=max_ms,
        reverse_count=reverse_count,
        duplicate_count=duplicate_count,
        large_gaps=large_gaps,
    )


def format_number(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def mode_requested_interval_ms(values_ns: Iterable[float]) -> tuple[float | None, int]:
    rounded_ms = [round(value / 1_000_000.0, 6) for value in values_ns if value > 0]
    if not rounded_ms:
        return None, 0
    counts = Counter(rounded_ms)
    value_ms, count = counts.most_common(1)[0]
    return value_ms, count


def open_source(source: str) -> tuple[TextIO, bool]:
    if source == "-":
        return sys.stdin, False
    return Path(source).open("r", encoding="utf-8"), True


def print_series(label: str, stats: SeriesStats, gap_factor: float, show_gaps: int) -> None:
    estimated_missing = sum(gap.estimated_missing for gap in stats.large_gaps)
    print(
        f"  {label}: effective={format_number(stats.effective_hz)} Hz, "
        f"P50={format_number(stats.p50_ms)} ms, "
        f"P95={format_number(stats.p95_ms)} ms, "
        f"max={format_number(stats.max_ms)} ms"
    )
    print(
        f"    intervals={stats.interval_count}, reverse={stats.reverse_count}, "
        f"duplicate={stats.duplicate_count}, "
        f"gaps(>{gap_factor:g}xP50)={len(stats.large_gaps)}, "
        f"estimated_missing~={estimated_missing}"
    )
    for gap in stats.large_gaps[:show_gaps]:
        seq_text = "n/a" if gap.record_seq is None else str(gap.record_seq)
        print(
            f"      gap={gap.interval_ms:.2f} ms at line={gap.line_no}, "
            f"recordSeq={seq_text}, estimated_missing~={gap.estimated_missing}"
        )


def diagnose(source: str, gap_factor: float, show_gaps: int) -> bool:
    sensor_points: dict[str, dict[str, list[Point]]] = defaultdict(
        lambda: {"sensor": [], "callback": []}
    )
    requested_ns: dict[str, list[float]] = defaultdict(list)
    system_points: dict[str, list[Point]] = defaultdict(list)
    schema_versions: Counter[int] = Counter()
    session_sequences: dict[str, list[tuple[int, int]]] = defaultdict(list)
    invalid_json_lines: list[int] = []
    invalid_record_seq_lines: list[int] = []
    valid_rows = 0
    sensor_callback_rows = 0
    device_health_rows = 0
    device_health_points: list[Point] = []
    device_health_reasons: Counter[str] = Counter()
    device_health_temperatures: list[float] = []
    device_health_thermal_levels: Counter[int] = Counter()
    unavailable_battery_temperature_rows = 0
    unavailable_thermal_level_rows = 0
    invalid_device_health_lines: list[int] = []
    first_record: dict[str, Any] | None = None
    last_record: dict[str, Any] | None = None
    sequence_integrity_ok = True

    stream, should_close = open_source(source)
    try:
        for line_no, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                invalid_json_lines.append(line_no)
                continue
            if not isinstance(row, dict):
                invalid_json_lines.append(line_no)
                continue

            valid_rows += 1
            if first_record is None:
                first_record = row
            last_record = row
            schema_version = optional_int(row.get("recordSchemaVersion"))
            if schema_version is not None:
                schema_versions[schema_version] += 1

            record_seq = optional_int(row.get("recordSeq"))
            session_id = row.get("sessionId")
            if record_seq is not None:
                session_sequences[str(session_id or "<missing>")].append((record_seq, line_no))
            else:
                invalid_record_seq_lines.append(line_no)

            record_type = row.get("recordType")
            callback_ms = finite_number(row.get("timestampMs"))
            if record_type == "sensor_callback":
                sensor_type = row.get("sensorType")
                if not isinstance(sensor_type, str) or not sensor_type:
                    sensor_type = "<missing>"
                sensor_callback_rows += 1

                if callback_ms is not None:
                    sensor_points[sensor_type]["callback"].append(
                        Point(callback_ms, line_no, record_seq)
                    )
                sensor_timestamp_ns = finite_number(row.get("sensorTimestamp"))
                if sensor_timestamp_ns is not None:
                    sensor_points[sensor_type]["sensor"].append(
                        Point(sensor_timestamp_ns / 1_000_000.0, line_no, record_seq)
                    )
                requested = finite_number(row.get("requestedIntervalNs"))
                if requested is not None and requested > 0:
                    requested_ns[sensor_type].append(requested)
            elif record_type == "device_health":
                device_health_rows += 1
                row_valid = True
                if callback_ms is not None:
                    device_health_points.append(Point(callback_ms, line_no, record_seq))
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
                    device_health_temperatures.append(temperature)

                raw_thermal_level = row.get("thermalLevel")
                thermal_level = optional_int(raw_thermal_level)
                if raw_thermal_level is None:
                    unavailable_thermal_level_rows += 1
                elif thermal_level not in THERMAL_LEVEL_NAMES:
                    row_valid = False
                else:
                    device_health_thermal_levels[thermal_level] += 1

                if not row_valid:
                    invalid_device_health_lines.append(line_no)
            elif record_type in {"location", "satellite"} and callback_ms is not None:
                system_points[str(record_type)].append(Point(callback_ms, line_no, record_seq))
    finally:
        if should_close:
            stream.close()

    print(f"\n=== {source} ===")
    print(
        f"rows={valid_rows}, sensor_callback={sensor_callback_rows}, "
        f"invalid_json={len(invalid_json_lines)}, "
        f"schemas={dict(sorted(schema_versions.items()))}"
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
    if invalid_json_lines:
        shown = ", ".join(str(line) for line in invalid_json_lines[:10])
        suffix = " ..." if len(invalid_json_lines) > 10 else ""
        print(f"invalid JSON lines: {shown}{suffix}")
    if invalid_record_seq_lines:
        shown = ", ".join(str(line) for line in invalid_record_seq_lines[:10])
        suffix = " ..." if len(invalid_record_seq_lines) > 10 else ""
        print(f"missing/invalid recordSeq lines: {shown}{suffix}")
        sequence_integrity_ok = False

    if len(session_sequences) != 1:
        print(f"session_count={len(session_sequences)} (expected 1)")
        sequence_integrity_ok = False

    for session_id, entries in session_sequences.items():
        gaps = 0
        reverse = 0
        duplicate = 0
        for (previous, _), (current, _) in zip(entries, entries[1:]):
            if current > previous:
                gaps += max(0, current - previous - 1)
            elif current < previous:
                reverse += 1
            else:
                duplicate += 1
        print(
            f"recordSeq[{session_id}]: first={entries[0][0]}, last={entries[-1][0]}, "
            f"missing={gaps}, reverse={reverse}, duplicate={duplicate}"
        )
        if entries[0][0] != 1 or gaps > 0 or reverse > 0 or duplicate > 0:
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
        unexpected_sensor_types = sorted(set(sensor_points) - SCHEMA16_PLUS_SENSOR_TYPES)
        sensor_set_ok = not unexpected_sensor_types
        print(
            "schema16_plus_sensor_set="
            f"{'yes' if sensor_set_ok else 'no'} "
            f"(unexpected={unexpected_sensor_types})"
        )

    if not sensor_points:
        print("No schema-15/16/17 sensor_callback records found.")
    else:
        print("\nSensor callback cadence:")
        for sensor_type in sorted(sensor_points):
            sensor_series = sensor_points[sensor_type]
            request_ms, request_count = mode_requested_interval_ms(requested_ns[sensor_type])
            request_hz = None if request_ms is None or request_ms <= 0 else 1000.0 / request_ms
            print(
                f"\n{sensor_type}: records={len(sensor_series['callback'])}, "
                f"requested_mode={format_number(request_ms)} ms/"
                f"{format_number(request_hz)} Hz ({request_count} rows)"
            )
            if sensor_series["sensor"]:
                print_series(
                    "sensor timestamp",
                    analyse_series(sensor_series["sensor"], gap_factor),
                    gap_factor,
                    show_gaps,
                )
            else:
                print("  sensor timestamp: unavailable")
            print_series(
                "callback clock",
                analyse_series(sensor_series["callback"], gap_factor),
                gap_factor,
                show_gaps,
            )

    if system_points:
        print("\nSystem callback cadence:")
        for record_type in sorted(system_points):
            print(f"\n{record_type}: records={len(system_points[record_type])}")
            print_series(
                "callback clock",
                analyse_series(system_points[record_type], gap_factor),
                gap_factor,
                show_gaps,
            )

    device_health_ok = True
    if schema_version == 17:
        device_health_ok = (
            device_health_rows >= 2
            and device_health_reasons["start"] == 1
            and device_health_reasons["stop"] == 1
            and not invalid_device_health_lines
        )
        print("\nDevice health:")
        print(
            f"  records={device_health_rows}, reasons={dict(device_health_reasons)}, "
            f"valid={'yes' if device_health_ok else 'no'}"
        )
        if device_health_points:
            print_series(
                "sampling clock",
                analyse_series(device_health_points, gap_factor),
                gap_factor,
                show_gaps,
            )
        if device_health_temperatures:
            print(
                "  batteryTemperatureC: "
                f"min={min(device_health_temperatures):.1f}, "
                f"max={max(device_health_temperatures):.1f}, "
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
        if invalid_device_health_lines:
            shown = ", ".join(str(line) for line in invalid_device_health_lines[:10])
            suffix = " ..." if len(invalid_device_health_lines) > 10 else ""
            print(f"  invalid device_health lines: {shown}{suffix}")

    return (
        valid_rows > 0
        and sensor_callback_rows > 0
        and not invalid_json_lines
        and schema_ok
        and sensor_set_ok
        and device_health_ok
        and sequence_integrity_ok
        and start_ok
        and terminal_ok
    )


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
