#!/usr/bin/env python3
import argparse
import bisect
import json
import math
import statistics
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALGORITHM_VERSION = "adaptive-gravity-20260705-v1"
APP_PARITY_CONFIG = {
    "curve_positive_scale": 0.35,
    "curve_negative_scale": 0.35,
    "low_confidence_positive_scale": 0.55,
    "low_confidence_negative_scale": 0.55,
    "braking_negative_scale": 1.0,
    "use_gyro_gravity": False,
    "use_sys_gravity": False,
    "gyro_gravity_sign": -1.0,
    "use_vibration_guard": True,
    "vibration_threshold": 0.85,
    "vibration_scale": 0.18,
}


class MotionState(IntEnum):
    IDLE = 0
    STOPPED = 1
    CALIBRATING = 2
    STRAIGHT_ACCELERATION = 3
    CRUISE = 4
    BRAKING = 5
    CURVE = 6
    LOW_CONFIDENCE = 7
    STRONG_VIBRATION = 8
    CONDUCTION_VIBRATION = 9


Vector3 = Tuple[float, float, float]


def v_empty() -> Vector3:
    return (0.0, 0.0, 0.0)


def v_add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a: Vector3, factor: float) -> Vector3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def v_dot(a: Vector3, b: Vector3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Vector3, b: Vector3) -> Vector3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_mag(a: Vector3) -> float:
    return math.sqrt(v_dot(a, a))


def v_norm(a: Vector3) -> Vector3:
    value = v_mag(a)
    if value < 0.0001:
      return (1.0, 0.0, 0.0)
    return v_scale(a, 1.0 / value)


def v_with_magnitude(a: Vector3, magnitude: float) -> Vector3:
    value = v_mag(a)
    if value < 0.0001:
        return (0.0, 0.0, magnitude)
    return v_scale(a, magnitude / value)


def v_mix(a: Vector3, b: Vector3, alpha: float) -> Vector3:
    return (
        a[0] + alpha * (b[0] - a[0]),
        a[1] + alpha * (b[1] - a[1]),
        a[2] + alpha * (b[2] - a[2]),
    )


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def clip_magnitude(a: Vector3, max_magnitude: float) -> Vector3:
    value = v_mag(a)
    if value <= max_magnitude or value < 0.0001:
        return a
    return v_scale(a, max_magnitude / value)


@dataclass
class SensorFrame:
    timestamp_ms: int
    sensor_timestamp: Optional[float]
    acceleration: Vector3
    gyroscope: Optional[Vector3]
    gyroscope_timestamp: Optional[float]
    sys_gravity: Optional[Vector3] = None
    mag: Optional[Vector3] = None


@dataclass
class AccelWindowFrame:
    timestamp_ms: int
    forward: float
    lateral: float
    gyro_magnitude: float
    filtered: Vector3
    acc_step: float
    gravity_deviation: float


@dataclass
class EstimatorOutput:
    timestamp_ms: int
    sensor_timestamp: Optional[float]
    speed_kmh: float
    confidence: float
    motion_state: MotionState
    raw: Vector3
    filtered: Vector3
    gravity: Vector3
    main_axis: Vector3
    calibration_count: int
    calibration_rejected: bool


class SpeedEstimator:
    def __init__(
        self,
        # effective acceleration scales (7 CLI existing)
        curve_positive_scale: float = 0.35,
        curve_negative_scale: float = 0.35,
        low_confidence_positive_scale: float = 0.55,
        low_confidence_negative_scale: float = 0.55,
        braking_negative_scale: float = 1.0,
        use_vibration_guard: bool = True,
        vibration_threshold: float = 0.85,
        vibration_scale: float = 0.18,
        # signal preprocessing
        low_pass_alpha: float = 0.22,
        accel_clip_ceiling: float = 3.5,
        dt_fallback: float = 0.02,
        dt_clamp_lo: float = 0.005,
        dt_clamp_hi: float = 0.08,
        # calibration
        calibration_duration_ms: int = 1500,
        calibration_rms_threshold: float = 0.25,
        calibration_gravity_error: float = 0.25,
        calibration_motion_gyro_mean: float = 0.08,
        calibration_motion_gyro_max: float = 0.25,
        calibration_motion_acc_step: float = 0.65,
        calibration_reject_cooldown_ms: int = 10000,
        calibration_parking_success_ms: int = 5000,
        calibration_parking_reject_ms: int = 5000,
        # axis initialization + tracking
        axis_init_acc_threshold: float = 0.18,
        axis_init_gyro_threshold: float = 0.18,
        axis_locked_lateral: float = 0.08,
        axis_locked_gyro_instant: float = 0.10,
        axis_locked_gyro_mean: float = 0.08,
        axis_locked_acc: float = 0.25,
        axis_locked_speed: float = 5.0,
        axis_unlocked_lateral: float = 0.18,
        axis_unlocked_gyro_instant: float = 0.16,
        axis_unlocked_gyro_mean: float = 0.14,
        axis_stop_update_speed: float = 8.0,
        axis_speed_threshold: float = 3.0,
        axis_acc_high_speed: float = 0.16,
        axis_acc_low_speed: float = 0.10,
        axis_mix_locked: float = 0.003,
        axis_mix_unlocked: float = 0.025,
        axis_ortho_threshold: float = 0.15,
        axis_reset_alignment: float = 0.35,
        axis_reset_speed: float = 2.0,
        axis_reset_acc: float = 0.18,
        axis_reset_seed_ratio: float = 1.8,
        # axis lock trigger + stability window
        axis_lock_speed: float = 5.0,
        axis_lock_time_ms: int = 30000,
        axis_lock_update_count: int = 60,
        axis_window_min_frames: int = 20,
        axis_window_max_frames: int = 80,
        axis_window_max_ms: int = 1800,
        axis_stable_acc_step: float = 0.35,
        axis_stable_gravity_dev: float = 0.45,
        axis_stable_gyro: float = 0.12,
        axis_stable_forward_var: float = 0.08,
        axis_unstable_acc_step: float = 0.85,
        axis_unstable_gravity_dev: float = 1.2,
        # motion state detection
        longcal_timeout_ms: int = 360000,
        idle_forward: float = 0.035,
        idle_speed: float = 0.25,
        accel_forward: float = 0.055,
        brake_forward: float = -0.055,
        low_conf_forward_var: float = 0.55,
        low_conf_gyro: float = 1.8,
        vibration_conduction_gyro: float = 0.06,
        vibration_strong_gyro: float = 0.06,
        vibration_strong_gravity_dev: float = 1.2,
        curve_a_lateral: float = 0.18,
        curve_a_gyro: float = 0.045,
        curve_a_ratio: float = 1.15,
        curve_b_gyro: float = 0.09,
        curve_b_gyro_var: float = 0.0018,
        curve_b_lateral: float = 0.10,
        # effective acceleration misc
        dead_zone: float = 0.025,
        conduction_scale: float = 0.45,
        # confidence
        confidence_base: float = 1.0,
        confidence_decay_rate: float = (0.85 / 180000),
        confidence_gyro_divisor: float = 3.0,
        confidence_gyro_max: float = 0.2,
        confidence_clamp_lo: float = 0.05,
        confidence_clamp_hi: float = 0.95,
        # experimental
        use_gyro_gravity: bool = False,
        gyro_gravity_sign: float = -1.0,
        use_sys_gravity: bool = False,
    ) -> None:
        # effective acceleration scales
        self.curve_positive_scale = curve_positive_scale
        self.curve_negative_scale = curve_negative_scale
        self.low_confidence_positive_scale = low_confidence_positive_scale
        self.low_confidence_negative_scale = low_confidence_negative_scale
        self.braking_negative_scale = braking_negative_scale
        self.use_vibration_guard = use_vibration_guard
        self.vibration_threshold = vibration_threshold
        self.vibration_scale = vibration_scale
        # signal preprocessing
        self.low_pass_alpha = low_pass_alpha
        self.accel_clip_ceiling = accel_clip_ceiling
        self.dt_fallback = dt_fallback
        self.dt_clamp_lo = dt_clamp_lo
        self.dt_clamp_hi = dt_clamp_hi
        # calibration
        self.calibration_duration_ms = calibration_duration_ms
        self.calibration_rms_threshold = calibration_rms_threshold
        self.calibration_gravity_error = calibration_gravity_error
        self.calibration_motion_gyro_mean = calibration_motion_gyro_mean
        self.calibration_motion_gyro_max = calibration_motion_gyro_max
        self.calibration_motion_acc_step = calibration_motion_acc_step
        self.calibration_reject_cooldown_ms = calibration_reject_cooldown_ms
        self.calibration_parking_success_ms = calibration_parking_success_ms
        self.calibration_parking_reject_ms = calibration_parking_reject_ms
        # axis initialization + tracking
        self.axis_init_acc_threshold = axis_init_acc_threshold
        self.axis_init_gyro_threshold = axis_init_gyro_threshold
        self.axis_locked_lateral = axis_locked_lateral
        self.axis_locked_gyro_instant = axis_locked_gyro_instant
        self.axis_locked_gyro_mean = axis_locked_gyro_mean
        self.axis_locked_acc = axis_locked_acc
        self.axis_locked_speed = axis_locked_speed
        self.axis_unlocked_lateral = axis_unlocked_lateral
        self.axis_unlocked_gyro_instant = axis_unlocked_gyro_instant
        self.axis_unlocked_gyro_mean = axis_unlocked_gyro_mean
        self.axis_stop_update_speed = axis_stop_update_speed
        self.axis_speed_threshold = axis_speed_threshold
        self.axis_acc_high_speed = axis_acc_high_speed
        self.axis_acc_low_speed = axis_acc_low_speed
        self.axis_mix_locked = axis_mix_locked
        self.axis_mix_unlocked = axis_mix_unlocked
        self.axis_ortho_threshold = axis_ortho_threshold
        self.axis_reset_alignment = axis_reset_alignment
        self.axis_reset_speed = axis_reset_speed
        self.axis_reset_acc = axis_reset_acc
        self.axis_reset_seed_ratio = axis_reset_seed_ratio
        # axis lock trigger + stability window
        self.axis_lock_speed = axis_lock_speed
        self.axis_lock_time_ms = axis_lock_time_ms
        self.axis_lock_update_count = axis_lock_update_count
        self.axis_window_min_frames = axis_window_min_frames
        self.axis_window_max_frames = axis_window_max_frames
        self.axis_window_max_ms = axis_window_max_ms
        self.axis_stable_acc_step = axis_stable_acc_step
        self.axis_stable_gravity_dev = axis_stable_gravity_dev
        self.axis_stable_gyro = axis_stable_gyro
        self.axis_stable_forward_var = axis_stable_forward_var
        self.axis_unstable_acc_step = axis_unstable_acc_step
        self.axis_unstable_gravity_dev = axis_unstable_gravity_dev
        # motion state detection
        self.longcal_timeout_ms = longcal_timeout_ms
        self.idle_forward = idle_forward
        self.idle_speed = idle_speed
        self.accel_forward = accel_forward
        self.brake_forward = brake_forward
        self.low_conf_forward_var = low_conf_forward_var
        self.low_conf_gyro = low_conf_gyro
        self.vibration_conduction_gyro = vibration_conduction_gyro
        self.vibration_strong_gyro = vibration_strong_gyro
        self.vibration_strong_gravity_dev = vibration_strong_gravity_dev
        self.curve_a_lateral = curve_a_lateral
        self.curve_a_gyro = curve_a_gyro
        self.curve_a_ratio = curve_a_ratio
        self.curve_b_gyro = curve_b_gyro
        self.curve_b_gyro_var = curve_b_gyro_var
        self.curve_b_lateral = curve_b_lateral
        # effective acceleration misc
        self.dead_zone = dead_zone
        self.conduction_scale = conduction_scale
        # confidence
        self.confidence_base = confidence_base
        self.confidence_decay_rate = confidence_decay_rate
        self.confidence_gyro_divisor = confidence_gyro_divisor
        self.confidence_gyro_max = confidence_gyro_max
        self.confidence_clamp_lo = confidence_clamp_lo
        self.confidence_clamp_hi = confidence_clamp_hi
        # experimental
        self.use_gyro_gravity = use_gyro_gravity
        self.gyro_gravity_sign = gyro_gravity_sign
        self.use_sys_gravity = use_sys_gravity
        self.running = False
        self.start_ms = 0
        self.last_timestamp_ms = 0
        self.last_sensor_timestamp_ns: Optional[float] = None
        self.velocity_mps = 0.0
        self.distance_m = 0.0
        self.max_speed_kmh = 0.0
        self.filtered_acceleration = v_empty()
        self.gravity_estimate = (0.0, 0.0, 9.80665)
        self.main_axis = (1.0, 0.0, 0.0)
        self.main_axis_initialized = False
        self.main_axis_locked = False
        self.main_axis_seed_magnitude = 0.0
        self.main_axis_update_count = 0
        self.main_axis_last_update_ms = 0
        self.calibration_count = 0
        self.calibration_until_ms = 0
        self.calibration_sum = v_empty()
        self.calibration_square_sum = 0.0
        self.calibration_gyro_sum = 0.0
        self.calibration_gyro_max = 0.0
        self.calibration_max_step = 0.0
        self.calibration_last_acceleration: Optional[Vector3] = None
        self.calibration_samples = 0
        self.calibration_rejected_until_ms = 0
        self.last_calibration_ms = 0
        self.motion_state = MotionState.IDLE
        self.confidence = 0.0
        self.window_frames: List[AccelWindowFrame] = []
        self.pre_cal_buffer: List[Tuple[Vector3, float, float]] = []
        self.parking_calibration_pending = False
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.last_raw_acceleration: Optional[Vector3] = None

    def start(self, timestamp_ms: int) -> None:
        self.running = True
        self.start_ms = timestamp_ms
        self.last_timestamp_ms = timestamp_ms
        self.last_sensor_timestamp_ns = None
        self.velocity_mps = 0.0
        self.distance_m = 0.0
        self.max_speed_kmh = 0.0
        self.filtered_acceleration = v_empty()
        self.gravity_estimate = (0.0, 0.0, 9.80665)
        self.main_axis = (1.0, 0.0, 0.0)
        self.main_axis_initialized = False
        self.main_axis_locked = False
        self.main_axis_seed_magnitude = 0.0
        self.main_axis_update_count = 0
        self.main_axis_last_update_ms = 0
        self.calibration_count = 0
        self.confidence = 0.0
        self.window_frames = []
        self.pre_cal_buffer = []
        self.calibration_rejected_until_ms = 0
        self.parking_calibration_pending = False
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.last_raw_acceleration = None
        self.begin_calibration(timestamp_ms)

    def stop(self, timestamp_ms: int) -> None:
        self.running = False
        self.motion_state = MotionState.STOPPED
        self.velocity_mps = 0.0
        self.last_timestamp_ms = timestamp_ms
        self.last_sensor_timestamp_ns = None
        self.parking_calibration_pending = False
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.last_raw_acceleration = None
        self.pre_cal_buffer = []

    def reset(self, timestamp_ms: int) -> None:
        self.__init__()
        self.start_ms = timestamp_ms
        self.last_timestamp_ms = timestamp_ms

    def calibrate_at_stop(self, timestamp_ms: int) -> bool:
        self.last_timestamp_ms = timestamp_ms
        self.last_sensor_timestamp_ns = None
        self.parking_calibration_pending = True
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.begin_calibration(timestamp_ms)
        return True

    def refresh_gravity_at_entrance(self, timestamp_ms: int) -> bool:
        window_frames = 75
        if len(self.pre_cal_buffer) < window_frames:
            return False

        best_start = 0
        best_rms = float("inf")
        for i in range(len(self.pre_cal_buffer) - window_frames + 1):
            s = v_empty()
            sq = 0.0
            for j in range(i, i + window_frames):
                acc, _, _ = self.pre_cal_buffer[j]
                s = v_add(s, acc)
                sq += v_dot(acc, acc)
            mean = v_scale(s, 1.0 / window_frames)
            mean_sq = sq / window_frames
            mean_dot = v_dot(mean, mean)
            rms = math.sqrt(max(0.0, mean_sq - mean_dot))
            if rms < best_rms:
                best_rms = rms
                best_start = i

        cal_sum = v_empty()
        cal_sq_sum = 0.0
        cal_gyro_sum = 0.0
        cal_gyro_max = 0.0
        cal_max_step = 0.0
        last_acc: Optional[Vector3] = None
        for j in range(best_start, best_start + window_frames):
            acc, gyro, _ = self.pre_cal_buffer[j]
            cal_sum = v_add(cal_sum, acc)
            cal_sq_sum += v_dot(acc, acc)
            cal_gyro_sum += gyro
            cal_gyro_max = max(cal_gyro_max, gyro)
            if last_acc is not None:
                cal_max_step = max(cal_max_step, v_mag(v_sub(acc, last_acc)))
            last_acc = acc

        candidate = v_scale(cal_sum, 1.0 / window_frames)
        mean_square = cal_sq_sum / window_frames
        candidate_dot = v_dot(candidate, candidate)
        rms_deviation = math.sqrt(max(0.0, mean_square - candidate_dot))
        grav_mag = v_mag(candidate)
        raw_gravity_error = abs(grav_mag - 9.80665)
        gyro_average = cal_gyro_sum / window_frames
        motion = (
            gyro_average > self.calibration_motion_gyro_mean
            or cal_gyro_max > self.calibration_motion_gyro_max
            or cal_max_step > self.calibration_motion_acc_step
        )
        stable = (
            rms_deviation < self.calibration_rms_threshold
            and raw_gravity_error < self.calibration_gravity_error
            and not motion
        )

        if stable:
            if not self.use_sys_gravity:
                self.gravity_estimate = candidate
            return True
        return False

    def ingest(self, frame: SensorFrame) -> EstimatorOutput:
        if not self.running:
            return self.make_output(frame)

        dt = self.compute_delta_seconds(frame)
        raw_acceleration = frame.acceleration
        acc_step = self.compute_acc_step(raw_acceleration)

        pre_cal_gyro = v_mag(frame.gyroscope) if frame.gyroscope is not None else 0.0
        if len(self.pre_cal_buffer) >= 180:
            self.pre_cal_buffer.pop(0)
        self.pre_cal_buffer.append((raw_acceleration, pre_cal_gyro, self.velocity_mps))

        if frame.timestamp_ms <= self.calibration_until_ms:
            calibration_gyro_magnitude = v_mag(frame.gyroscope) if frame.gyroscope is not None else 0.0
            self.collect_calibration(
                raw_acceleration,
                calibration_gyro_magnitude,
            )
            if not self.parking_calibration_pending:
                self.motion_state = MotionState.CALIBRATING
                self.confidence = 0.35
                return self.make_output(frame, v_empty())

        self.finish_calibration_if_needed(frame.timestamp_ms)

        gyro_magnitude = v_mag(frame.gyroscope) if frame.gyroscope is not None else 0.0
        self.update_gravity_from_gyro(frame.gyroscope, dt, gyro_magnitude)
        if self.use_sys_gravity and frame.sys_gravity is not None:
            gravity_for_motion = frame.sys_gravity
        else:
            gravity_for_motion = self.gravity_estimate
        motion_acceleration = v_sub(raw_acceleration, gravity_for_motion)
        clipped = clip_magnitude(motion_acceleration, self.accel_clip_ceiling)
        filtered = self.low_pass(clipped, self.low_pass_alpha)
        self.filtered_acceleration = filtered

        self.update_main_axis_lock(frame.timestamp_ms)
        if self.should_update_main_axis(filtered, gyro_magnitude, frame.timestamp_ms):
            self.update_main_axis(filtered, frame.timestamp_ms)

        forward_acceleration = v_dot(filtered, self.main_axis)
        lateral_vector = v_sub(filtered, v_scale(self.main_axis, forward_acceleration))
        lateral_acceleration = v_mag(lateral_vector)
        self.push_window_frame(
            frame.timestamp_ms,
            forward_acceleration,
            lateral_acceleration,
            gyro_magnitude,
            filtered,
            acc_step,
            abs(v_mag(raw_acceleration) - 9.80665),
        )

        state = self.detect_motion_state(forward_acceleration, lateral_acceleration, gyro_magnitude, frame.timestamp_ms)
        effective_acceleration = self.effective_forward_acceleration(forward_acceleration, state)
        previous_velocity_mps = self.velocity_mps
        self.velocity_mps = max(0.0, self.velocity_mps + effective_acceleration * dt)
        self.distance_m += ((previous_velocity_mps + self.velocity_mps) / 2.0) * dt

        self.max_speed_kmh = max(self.max_speed_kmh, self.velocity_mps * 3.6)
        self.confidence = self.compute_confidence(state, gyro_magnitude, frame.timestamp_ms)
        self.motion_state = state
        return self.make_output(frame, filtered)

    def begin_calibration(self, timestamp_ms: int) -> None:
        self.calibration_until_ms = timestamp_ms + self.calibration_duration_ms
        self.calibration_sum = v_empty()
        self.calibration_square_sum = 0.0
        self.calibration_gyro_sum = 0.0
        self.calibration_gyro_max = 0.0
        self.calibration_max_step = 0.0
        self.calibration_last_acceleration = None
        self.calibration_samples = 0
        self.calibration_count += 1
        self.last_calibration_ms = timestamp_ms
        self.confidence = 0.35
        self.motion_state = MotionState.CALIBRATING

    def collect_calibration(self, acceleration: Vector3, gyro_magnitude: float) -> None:
        if self.calibration_last_acceleration is not None:
            self.calibration_max_step = max(
                self.calibration_max_step,
                v_mag(v_sub(acceleration, self.calibration_last_acceleration)),
            )
        self.calibration_last_acceleration = acceleration
        self.calibration_sum = v_add(self.calibration_sum, acceleration)
        self.calibration_square_sum += v_dot(acceleration, acceleration)
        self.calibration_gyro_sum += gyro_magnitude
        self.calibration_gyro_max = max(self.calibration_gyro_max, gyro_magnitude)
        self.calibration_samples += 1

    def finish_calibration_if_needed(self, timestamp_ms: int) -> None:
        if self.calibration_samples <= 0 or timestamp_ms <= self.calibration_until_ms:
            return

        stable_result = False
        gravity_candidate: Vector3 = v_empty()
        parking_window_end_velocity_mps: float = 0.0

        if self.parking_calibration_pending and len(self.pre_cal_buffer) >= 75:
            window_frames = 75
            best_start = 0
            best_rms = float("inf")
            for i in range(len(self.pre_cal_buffer) - window_frames + 1):
                s = v_empty()
                sq = 0.0
                for j in range(i, i + window_frames):
                    acc, _, _ = self.pre_cal_buffer[j]
                    s = v_add(s, acc)
                    sq += v_dot(acc, acc)
                mean = v_scale(s, 1.0 / window_frames)
                mean_sq = sq / window_frames
                mean_dot = v_dot(mean, mean)
                rms = math.sqrt(max(0.0, mean_sq - mean_dot))
                if rms < best_rms:
                    best_rms = rms
                    best_start = i

            cal_sum = v_empty()
            cal_sq_sum = 0.0
            cal_gyro_sum = 0.0
            cal_gyro_max = 0.0
            cal_max_step = 0.0
            last_acc: Optional[Vector3] = None
            for j in range(best_start, best_start + window_frames):
                acc, gyro, _ = self.pre_cal_buffer[j]
                cal_sum = v_add(cal_sum, acc)
                cal_sq_sum += v_dot(acc, acc)
                cal_gyro_sum += gyro
                cal_gyro_max = max(cal_gyro_max, gyro)
                if last_acc is not None:
                    cal_max_step = max(cal_max_step, v_mag(v_sub(acc, last_acc)))
                last_acc = acc

            gravity_candidate = v_scale(cal_sum, 1.0 / window_frames)
            mean_square = cal_sq_sum / window_frames
            gravity_square = v_dot(gravity_candidate, gravity_candidate)
            rms_deviation = math.sqrt(max(0.0, mean_square - gravity_square))
            gravity_magnitude = v_mag(gravity_candidate)
            raw_gravity_error = abs(gravity_magnitude - 9.80665)
            gyro_average = cal_gyro_sum / window_frames
            motion_during_calibration = (
                gyro_average > self.calibration_motion_gyro_mean
                or cal_gyro_max > self.calibration_motion_gyro_max
                or cal_max_step > self.calibration_motion_acc_step
            )
            stable_result = (
                rms_deviation < self.calibration_rms_threshold
                and raw_gravity_error < self.calibration_gravity_error
                and not motion_during_calibration
            )
            parking_window_end_velocity_mps = self.pre_cal_buffer[best_start + window_frames - 1][2]
        else:
            gravity_candidate = v_scale(self.calibration_sum, 1.0 / self.calibration_samples)
            mean_square = self.calibration_square_sum / self.calibration_samples
            gravity_square = v_dot(gravity_candidate, gravity_candidate)
            rms_deviation = math.sqrt(max(0.0, mean_square - gravity_square))
            gravity_magnitude = v_mag(gravity_candidate)
            raw_gravity_error = abs(gravity_magnitude - 9.80665)
            gyro_average = self.calibration_gyro_sum / self.calibration_samples
            motion_during_calibration = (
                gyro_average > self.calibration_motion_gyro_mean
                or self.calibration_gyro_max > self.calibration_motion_gyro_max
                or self.calibration_max_step > self.calibration_motion_acc_step
            )
            stable_result = (
                rms_deviation < self.calibration_rms_threshold
                and raw_gravity_error < self.calibration_gravity_error
                and not motion_during_calibration
            )

        if stable_result:
            if not self.use_sys_gravity:
                self.gravity_estimate = gravity_candidate
            self.calibration_rejected_until_ms = 0
            if self.parking_calibration_pending:
                self.apply_parking_zero(timestamp_ms, parking_window_end_velocity_mps)
                self.parking_calibration_success_until_ms = timestamp_ms + self.calibration_parking_success_ms
        else:
            self.calibration_rejected_until_ms = timestamp_ms + self.calibration_reject_cooldown_ms
            if self.parking_calibration_pending:
                self.parking_calibration_rejected_until_ms = timestamp_ms + self.calibration_parking_reject_ms
        self.parking_calibration_pending = False

        self.filtered_acceleration = v_empty()
        self.calibration_samples = 0
        self.calibration_sum = v_empty()
        self.calibration_square_sum = 0.0
        self.calibration_gyro_sum = 0.0
        self.calibration_gyro_max = 0.0
        self.calibration_max_step = 0.0
        self.calibration_last_acceleration = None

    def low_pass(self, acceleration: Vector3, alpha: float) -> Vector3:
        return v_mix(self.filtered_acceleration, acceleration, alpha)

    def compute_acc_step(self, raw_acceleration: Vector3) -> float:
        if self.last_raw_acceleration is None:
            self.last_raw_acceleration = raw_acceleration
            return 0.0
        value = v_mag(v_sub(raw_acceleration, self.last_raw_acceleration))
        self.last_raw_acceleration = raw_acceleration
        return value

    def update_gravity_from_gyro(
        self,
        gyroscope: Optional[Vector3],
        dt: float,
        gyro_magnitude: float,
    ) -> None:
        if not self.use_gyro_gravity:
            return
        if self.use_sys_gravity:
            return
        if gyroscope is not None and 0.0 < dt <= 0.08 and gyro_magnitude < 2.5:
            delta = v_scale(v_cross(gyroscope, self.gravity_estimate), self.gyro_gravity_sign * dt)
            self.gravity_estimate = v_with_magnitude(v_add(self.gravity_estimate, delta), 9.80665)

    def compute_delta_seconds(self, frame: SensorFrame) -> float:
        timestamp_ms = frame.timestamp_ms
        if frame.sensor_timestamp is not None:
            if self.last_sensor_timestamp_ns is not None and frame.sensor_timestamp > self.last_sensor_timestamp_ns:
                raw_dt = (frame.sensor_timestamp - self.last_sensor_timestamp_ns) / 1_000_000_000.0
            else:
                raw_dt = (timestamp_ms - self.last_timestamp_ms) / 1000.0
            self.last_sensor_timestamp_ns = frame.sensor_timestamp
        else:
            raw_dt = (timestamp_ms - self.last_timestamp_ms) / 1000.0
            self.last_sensor_timestamp_ns = None
        self.last_timestamp_ms = timestamp_ms
        if raw_dt <= 0:
            return self.dt_fallback
        return clamp(raw_dt, self.dt_clamp_lo, self.dt_clamp_hi)

    def update_main_axis_lock(self, timestamp_ms: int) -> None:
        if not self.main_axis_initialized:
            return
        if self.main_axis_locked and self.axis_lock_window_unstable():
            self.main_axis_locked = False
        if self.main_axis_locked:
            return
        if not self.axis_lock_window_stable():
            return
        if self.velocity_mps > self.axis_lock_speed or timestamp_ms - self.start_ms > self.axis_lock_time_ms or self.main_axis_update_count >= self.axis_lock_update_count:
            self.main_axis_locked = True

    def should_update_main_axis(self, filtered: Vector3, gyro_magnitude: float, timestamp_ms: int) -> bool:
        acceleration_magnitude = v_mag(filtered)
        if not self.main_axis_initialized:
            return acceleration_magnitude > self.axis_init_acc_threshold and gyro_magnitude < self.axis_init_gyro_threshold
        if self.motion_state in (MotionState.CURVE, MotionState.LOW_CONFIDENCE):
            return False

        lateral_average = average_lateral(self.window_frames)
        gyro_average = average_gyro(self.window_frames)

        if self.main_axis_locked:
            locked_straight = lateral_average < self.axis_locked_lateral and gyro_magnitude < self.axis_locked_gyro_instant and gyro_average < self.axis_locked_gyro_mean
            return acceleration_magnitude > self.axis_locked_acc and locked_straight and self.velocity_mps < self.axis_locked_speed

        if self.velocity_mps > self.axis_stop_update_speed:
            return False
        axis_update_likely_straight = lateral_average < self.axis_unlocked_lateral and gyro_magnitude < self.axis_unlocked_gyro_instant and gyro_average < self.axis_unlocked_gyro_mean
        speed_adjusted_threshold = self.axis_acc_high_speed if self.velocity_mps > self.axis_speed_threshold else self.axis_acc_low_speed
        return acceleration_magnitude > speed_adjusted_threshold and axis_update_likely_straight

    def update_main_axis(self, filtered: Vector3, timestamp_ms: int) -> None:
        candidate_magnitude = v_mag(filtered)
        candidate = v_norm(filtered)
        if not self.main_axis_initialized:
            self.main_axis = candidate
            self.main_axis_initialized = True
            self.main_axis_seed_magnitude = candidate_magnitude
            self.main_axis_update_count = 1
            self.main_axis_last_update_ms = timestamp_ms
            self._orthogonalize_axis()
            return

        if self.main_axis_locked:
            if v_dot(candidate, self.main_axis) < 0:
                candidate = v_scale(candidate, -1.0)
            self.main_axis = v_norm(v_mix(self.main_axis, candidate, self.axis_mix_locked))
            self._orthogonalize_axis()
            return

        alignment = v_dot(candidate, self.main_axis)
        if (
            abs(alignment) < self.axis_reset_alignment
            and self.velocity_mps < self.axis_reset_speed
            and candidate_magnitude > max(self.axis_reset_acc, self.main_axis_seed_magnitude * self.axis_reset_seed_ratio)
        ):
            self.main_axis = candidate
            self.main_axis_seed_magnitude = candidate_magnitude
            self.main_axis_update_count += 1
            self.main_axis_last_update_ms = timestamp_ms
            self._orthogonalize_axis()
            return

        if alignment < 0:
            candidate = v_scale(candidate, -1.0)
        self.main_axis = v_norm(v_mix(self.main_axis, candidate, self.axis_mix_unlocked))
        self.main_axis_seed_magnitude = max(self.main_axis_seed_magnitude, candidate_magnitude)
        self.main_axis_update_count += 1
        self.main_axis_last_update_ms = timestamp_ms
        self._orthogonalize_axis()

    def _orthogonalize_axis(self) -> None:
        grav_dir = v_norm(self.gravity_estimate)
        proj = v_dot(self.main_axis, grav_dir)
        if abs(proj) > self.axis_ortho_threshold:
            self.main_axis = v_norm(v_sub(self.main_axis, v_scale(grav_dir, proj)))

    def axis_lock_window_stable(self) -> bool:
        if len(self.window_frames) < self.axis_window_min_frames:
            return False
        return (
            average_acc_step(self.window_frames) < self.axis_stable_acc_step
            and average_gravity_deviation(self.window_frames) < self.axis_stable_gravity_dev
            and average_gyro(self.window_frames) < self.axis_stable_gyro
            and variance_forward(self.window_frames) < self.axis_stable_forward_var
        )

    def axis_lock_window_unstable(self) -> bool:
        if len(self.window_frames) < self.axis_window_min_frames:
            return False
        return average_acc_step(self.window_frames) > self.axis_unstable_acc_step or average_gravity_deviation(self.window_frames) > self.axis_unstable_gravity_dev

    def push_window_frame(
        self,
        timestamp_ms: int,
        forward: float,
        lateral: float,
        gyro_magnitude: float,
        filtered: Vector3,
        acc_step: float,
        gravity_deviation: float,
    ) -> None:
        self.window_frames.append(AccelWindowFrame(timestamp_ms, forward, lateral, gyro_magnitude, filtered, acc_step, gravity_deviation))
        while self.window_frames and (
            len(self.window_frames) > self.axis_window_max_frames or timestamp_ms - self.window_frames[0].timestamp_ms > self.axis_window_max_ms
        ):
            self.window_frames.pop(0)

    def detect_motion_state(
        self,
        forward: float,
        lateral: float,
        gyro_magnitude: float,
        timestamp_ms: int,
    ) -> MotionState:
        lateral_average = average_lateral(self.window_frames)
        gyro_average = average_gyro(self.window_frames)
        gyro_variance_value = variance_gyro(self.window_frames)
        forward_variance = variance_forward(self.window_frames)
        vibration_average = average_acc_step(self.window_frames)
        gravity_deviation_average = average_gravity_deviation(self.window_frames)
        long_since_calibration = timestamp_ms - self.last_calibration_ms > self.longcal_timeout_ms
        conduction_vibration = vibration_average > self.vibration_threshold and gyro_average <= self.vibration_conduction_gyro
        strong_vibration = (vibration_average > self.vibration_threshold and gyro_average > self.vibration_strong_gyro) or gravity_deviation_average > self.vibration_strong_gravity_dev
        curve_likely = (
            lateral_average > self.curve_a_lateral and gyro_average > self.curve_a_gyro
            and abs(forward) < lateral_average * self.curve_a_ratio
        ) or (
            gyro_average > self.curve_b_gyro and gyro_variance_value > self.curve_b_gyro_var
            and lateral_average > self.curve_b_lateral
        )

        if curve_likely:
            return MotionState.CURVE
        if conduction_vibration and self.use_vibration_guard:
            return MotionState.CONDUCTION_VIBRATION
        if strong_vibration and self.use_vibration_guard:
            return MotionState.STRONG_VIBRATION
        if long_since_calibration or forward_variance > self.low_conf_forward_var or gyro_magnitude > self.low_conf_gyro:
            return MotionState.LOW_CONFIDENCE
        if abs(forward) < self.idle_forward and self.velocity_mps < self.idle_speed:
            return MotionState.IDLE
        if forward > self.accel_forward:
            return MotionState.STRAIGHT_ACCELERATION
        if forward < self.brake_forward:
            return MotionState.BRAKING
        return MotionState.CRUISE

    def effective_forward_acceleration(self, forward: float, state: MotionState) -> float:
        if abs(forward) < self.dead_zone:
            return 0.0
        if state == MotionState.CURVE:
            return forward * (self.curve_negative_scale if forward < 0 else self.curve_positive_scale)
        if state == MotionState.STRONG_VIBRATION:
            return forward * self.vibration_scale
        if state == MotionState.CONDUCTION_VIBRATION:
            return forward * self.conduction_scale
        if state == MotionState.LOW_CONFIDENCE:
            return forward * (
                self.low_confidence_negative_scale if forward < 0 else self.low_confidence_positive_scale
            )
        if state == MotionState.BRAKING and forward < 0:
            return forward * self.braking_negative_scale
        return forward

    def apply_parking_zero(self, timestamp_ms: int, offset_velocity_mps: float) -> None:
        self.velocity_mps = max(0.0, self.velocity_mps - offset_velocity_mps)
        self.last_timestamp_ms = timestamp_ms
        self.filtered_acceleration = v_empty()
        self.window_frames = []
        self.main_axis_initialized = False
        self.main_axis_locked = False
        self.main_axis_seed_magnitude = 0.0
        self.main_axis_update_count = 0
        self.main_axis_last_update_ms = 0
        self.last_raw_acceleration = None

    def compute_confidence(self, state: MotionState, gyro_magnitude: float, timestamp_ms: int) -> float:
        value = self.confidence_base
        since_calibration = timestamp_ms - self.last_calibration_ms
        decay_rate = self.confidence_decay_rate
        if state == MotionState.STRAIGHT_ACCELERATION:
            decay_rate *= 2.0
        if state == MotionState.CURVE:
            decay_rate *= 3.0
        if state == MotionState.LOW_CONFIDENCE:
            decay_rate *= 2.5
        if state == MotionState.STRONG_VIBRATION:
            decay_rate *= 4.0
        if state == MotionState.CONDUCTION_VIBRATION:
            decay_rate *= 1.5
        value -= clamp(since_calibration * decay_rate, 0.0, 0.85)
        value -= clamp(gyro_magnitude / self.confidence_gyro_divisor, 0.0, self.confidence_gyro_max)
        return clamp(value, self.confidence_clamp_lo, self.confidence_clamp_hi)

    def make_output(self, frame: SensorFrame, filtered: Optional[Vector3] = None) -> EstimatorOutput:
        gravity_for_output = (
            frame.sys_gravity
            if (self.use_sys_gravity and frame.sys_gravity is not None)
            else self.gravity_estimate
        )
        return EstimatorOutput(
            timestamp_ms=frame.timestamp_ms,
            sensor_timestamp=frame.sensor_timestamp,
            speed_kmh=self.velocity_mps * 3.6,
            confidence=self.confidence,
            motion_state=self.motion_state,
            raw=frame.acceleration,
            filtered=filtered if filtered is not None else self.filtered_acceleration,
            gravity=gravity_for_output,
            main_axis=self.main_axis,
            calibration_count=self.calibration_count,
            calibration_rejected=(
                frame.timestamp_ms < self.calibration_rejected_until_ms
                or frame.timestamp_ms < self.parking_calibration_rejected_until_ms
            ),
        )


def average_lateral(frames: List[AccelWindowFrame]) -> float:
    return average([frame.lateral for frame in frames])


def average_gyro(frames: List[AccelWindowFrame]) -> float:
    return average([frame.gyro_magnitude for frame in frames])


def average_acc_step(frames: List[AccelWindowFrame]) -> float:
    return average([frame.acc_step for frame in frames])


def average_gravity_deviation(frames: List[AccelWindowFrame]) -> float:
    return average([frame.gravity_deviation for frame in frames])


def variance_gyro(frames: List[AccelWindowFrame]) -> float:
    return variance([frame.gyro_magnitude for frame in frames])


def variance_forward(frames: List[AccelWindowFrame]) -> float:
    return variance([frame.forward for frame in frames])


def average(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(values_list) / len(values_list)


def variance(values: Iterable[float]) -> float:
    values_list = list(values)
    if len(values_list) < 2:
        return 0.0
    avg = sum(values_list) / len(values_list)
    return sum((value - avg) * (value - avg) for value in values_list) / len(values_list)


def state_name(state: MotionState) -> str:
    return state.name.lower()


def parse_number(row: Dict[str, Any], key: str) -> Optional[float]:
    value = row.get(key)
    if value is None:
        return None
    return float(value)


def parse_vector(row: Dict[str, Any], x_key: str, y_key: str, z_key: str) -> Optional[Vector3]:
    x = parse_number(row, x_key)
    y = parse_number(row, y_key)
    z = parse_number(row, z_key)
    if x is None or y is None or z is None:
        return None
    return (x, y, z)


def make_sensor_frame(row: Dict[str, Any]) -> Optional[SensorFrame]:
    acceleration = parse_vector(row, "accX", "accY", "accZ")
    if acceleration is None:
        return None
    gyroscope = parse_vector(row, "gyroX", "gyroY", "gyroZ")
    sys_gravity = parse_vector(row, "sysGravityX", "sysGravityY", "sysGravityZ")
    mag = parse_vector(row, "magX", "magY", "magZ")
    return SensorFrame(
        timestamp_ms=int(row["timestampMs"]),
        sensor_timestamp=parse_number(row, "sensorTimestamp"),
        acceleration=acceleration,
        gyroscope=gyroscope,
        gyroscope_timestamp=parse_number(row, "gyroscopeTimestamp"),
        sys_gravity=sys_gravity,
        mag=mag,
    )


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return sorted(rows, key=lambda row: int(row.get("timestampMs", 0)))


def quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = round((len(values) - 1) * q)
    return values[index]


def event_matches(event: str, target: str) -> bool:
    return event == target or target in event


def replay(
    rows: List[Dict[str, Any]],
    strict_start: bool,
    infer_start_from_sensor: bool,
    adaptive_gravity: bool = False,
    **estimator_kwargs: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    start_event = "\u5f00\u59cb\u6d4b\u901f"
    stop_event = "\u505c\u6b62\u6d4b\u901f"
    parking_event = "\u505c\u8f66\u6821\u51c6"
    legacy_station_event = "\u5230\u7ad9\u6821\u51c6"

    estimator = SpeedEstimator(**estimator_kwargs)
    outputs: List[Dict[str, Any]] = []
    replay_events: List[Dict[str, Any]] = []
    running = False
    first_timestamp = int(rows[0]["timestampMs"]) if rows else 0

    # 磁力计场景检测器状态（分析层，不修改 SpeedEstimator 行为）
    # 前 500 帧采集磁力计 std 中位数判定一次场景，之后不再切换
    mag_window: List[float] = []
    mag_std_samples: List[float] = []
    scenario_decided = False
    scenario_is_subway = True  # 安全默认：判定前用自估重力
    if adaptive_gravity:
        estimator.use_sys_gravity = False  # 判定为驾车后才启用系统重力

    for row in rows:
        timestamp_ms = int(row.get("timestampMs", 0))
        event = str(row.get("event") or "")
        notes = str(row.get("notes") or "")

        if event_matches(event, start_event) or notes == "measurement started" or event_matches(event, "\u6d4b\u901f\u5df2\u5728\u8fd0\u884c"):
            estimator.start(timestamp_ms)
            running = True
            replay_events.append({
                "t": timestamp_ms,
                "event": "start",
                "gravity": estimator.gravity_estimate,
                "mainAxis": estimator.main_axis,
                "mainAxisInit": estimator.main_axis_initialized,
            })
            continue
        if not running and infer_start_from_sensor and should_infer_measurement_active(row):
            estimator.start(timestamp_ms)
            running = True
            replay_events.append({"t": timestamp_ms, "event": "inferred_start_from_sensor"})
        if running and (event_matches(event, parking_event) or event_matches(event, legacy_station_event)):
            speed_kmh = estimator.velocity_mps * 3.6
            replay_events.append({
                "t": timestamp_ms,
                "event": "parking_calibration_before",
                "speedKmh": speed_kmh,
                "recordedSpeedKmh": row.get("estimatedSpeedKmh"),
                "locationSpeedKmh": None if row.get("locationSpeedMps") is None else float(row["locationSpeedMps"]) * 3.6,
                "gravity": estimator.gravity_estimate,
                "mainAxis": estimator.main_axis,
                "mainAxisInit": estimator.main_axis_initialized,
                "calibrationCount": estimator.calibration_count,
            })
            estimator.calibrate_at_stop(timestamp_ms)
            replay_events.append({"t": timestamp_ms, "event": "parking_calibration_after"})
            continue
        if running and event_matches(event, "\u5165\u96a7"):
            estimator.refresh_gravity_at_entrance(timestamp_ms)
            replay_events.append({"t": timestamp_ms, "event": "tunnel_gravity_refresh"})
            continue
        if running and event_matches(event, stop_event):
            replay_events.append({"t": timestamp_ms, "event": "stop", "speedKmh": estimator.velocity_mps * 3.6})
            estimator.stop(timestamp_ms)
            running = False
            continue

        if not running or row.get("recordType") != "sensor":
            continue
        frame = make_sensor_frame(row)
        if frame is None:
            continue

        # 磁力计场景检测器：前 500 帧采集 std 中位数，判定一次场景（分析层）
        if adaptive_gravity and frame.mag is not None and not scenario_decided:
            mag_val = v_mag(frame.mag)
            mag_window.append(mag_val)
            if len(mag_window) > 50:
                mag_window.pop(0)
            if len(mag_window) >= 50:
                mean_mag = sum(mag_window) / len(mag_window)
                var_mag = sum((x - mean_mag) ** 2 for x in mag_window) / len(mag_window)
                std_mag = var_mag ** 0.5
                mag_std_samples.append(std_mag)
            if len(mag_std_samples) >= 450:
                # 450 个 std 样本（≈500 帧滑动窗），取中位数判定
                median_std = sorted(mag_std_samples)[len(mag_std_samples) // 2]
                scenario_is_subway = median_std >= 2.5
                scenario_decided = True
                estimator.use_sys_gravity = (not scenario_is_subway) and (frame.sys_gravity is not None)
                replay_events.append({
                    "t": timestamp_ms,
                    "event": "adaptive_gravity_decided",
                    "medianStd": median_std,
                    "scenario": "subway" if scenario_is_subway else "driving",
                    "useSysGravity": estimator.use_sys_gravity,
                })

        output = estimator.ingest(frame)
        if strict_start and output.calibration_count == 1 and output.calibration_rejected:
            replay_events.append({
                "t": timestamp_ms,
                "event": "start_rejected",
                "speedKmh": output.speed_kmh,
            })
            estimator.reset(timestamp_ms)
            running = False
            continue
        outputs.append({
            "timestampMs": output.timestamp_ms,
            "sensorTimestamp": output.sensor_timestamp,
            "tSec": (output.timestamp_ms - first_timestamp) / 1000.0,
            "speedKmh": output.speed_kmh,
            "recordedSpeedKmh": row.get("estimatedSpeedKmh"),
            "confidence": output.confidence,
            "motionState": state_name(output.motion_state),
            "rawAccMag": v_mag(output.raw),
            "filteredAccMag": v_mag(output.filtered),
            "gravityX": output.gravity[0],
            "gravityY": output.gravity[1],
            "gravityZ": output.gravity[2],
            "mainAxisX": output.main_axis[0],
            "mainAxisY": output.main_axis[1],
            "mainAxisZ": output.main_axis[2],
            "calibrationRejected": output.calibration_rejected,
            "secondsSinceCalibration": (output.timestamp_ms - estimator.last_calibration_ms) / 1000.0,
        })

    return outputs, replay_events


def compare_with_location(rows: List[Dict[str, Any]], outputs: List[Dict[str, Any]], lag_ms: int = 0, speed_key: str = "speedKmh") -> Dict[str, Any]:
    if not outputs:
        return {}
    samples = sorted(outputs, key=lambda item: item["timestampMs"])
    timestamps = [int(item["timestampMs"]) for item in samples]
    speeds = [float(item[speed_key]) for item in samples]
    diffs: List[float] = []
    moving_diffs: List[float] = []
    paired = 0
    for row in sorted(rows, key=lambda item: int(item.get("timestampMs", 0))):
        if row.get("recordType") != "location" or row.get("locationSpeedMps") is None:
            continue
        source_type = row.get("locationSourceType")
        if source_type is not None and int(source_type) not in (1, 4):
            continue
        speed_accuracy = row.get("locationSpeedAccuracyMps")
        if speed_accuracy is not None and float(speed_accuracy) <= 0:
            continue
        location_timestamp_ms = int(row.get("locationTimeMs") or row["timestampMs"])
        target_timestamp_ms = location_timestamp_ms - lag_ms
        estimated_speed_kmh = interpolate_output_speed_from_series(timestamps, speeds, target_timestamp_ms)
        if estimated_speed_kmh is None:
            continue
        location_speed_kmh = float(row["locationSpeedMps"]) * 3.6
        diff = estimated_speed_kmh - location_speed_kmh
        diffs.append(diff)
        if location_speed_kmh >= 3.0:
            moving_diffs.append(diff)
        paired += 1

    return {"lagMs": lag_ms, "all": diff_stats(diffs), "moving": diff_stats(moving_diffs), "pairedLocationRows": paired}


def interpolate_output_speed_from_series(timestamps: List[int], speeds: List[float], timestamp_ms: int) -> Optional[float]:
    if not timestamps:
        return None
    if timestamp_ms < timestamps[0] or timestamp_ms > timestamps[-1]:
        return None
    index = bisect.bisect_left(timestamps, timestamp_ms)
    if index < len(speeds) and timestamps[index] == timestamp_ms:
        return speeds[index]
    if index <= 0 or index >= len(speeds):
        return None
    before_timestamp = timestamps[index - 1]
    after_timestamp = timestamps[index]
    if after_timestamp == before_timestamp:
        return speeds[index]
    fraction = (timestamp_ms - before_timestamp) / (after_timestamp - before_timestamp)
    return speeds[index - 1] + (speeds[index] - speeds[index - 1]) * fraction


def diff_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    abs_values = [abs(value) for value in values]
    return {
        "count": len(values),
        "biasKmh": sum(values) / len(values),
        "maeKmh": sum(abs_values) / len(abs_values),
        "p90AbsKmh": quantile(abs_values, 0.9),
        "maxAbsKmh": max(abs_values),
    }


def compare_bucketed(
    rows: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    lag_ms: int = 0,
    speed_key: str = "speedKmh",
) -> Dict[str, Any]:
    if not outputs:
        return {}
    samples = sorted(outputs, key=lambda item: item["timestampMs"])
    timestamps = [int(item["timestampMs"]) for item in samples]
    speeds = [float(item[speed_key]) for item in samples]
    sec_since_cals = [float(item.get("secondsSinceCalibration", 0)) for item in samples]

    cal_indices_f = [0.0] * len(samples)
    current_cal = 0
    for i in range(1, len(samples)):
        if sec_since_cals[i - 1] - sec_since_cals[i] > 1.0:
            current_cal += 1
        cal_indices_f[i] = float(current_cal)

    bucket_diffs: Dict[str, List[float]] = {}
    bucket_moving_diffs: Dict[str, List[float]] = {}

    for row in sorted(rows, key=lambda item: int(item.get("timestampMs", 0))):
        if row.get("recordType") != "location" or row.get("locationSpeedMps") is None:
            continue
        source_type = row.get("locationSourceType")
        if source_type is not None and int(source_type) not in (1, 4):
            continue
        speed_accuracy = row.get("locationSpeedAccuracyMps")
        if speed_accuracy is not None and float(speed_accuracy) <= 0:
            continue
        location_timestamp_ms = int(row.get("locationTimeMs") or row["timestampMs"])
        target_timestamp_ms = location_timestamp_ms - lag_ms
        estimated_speed_kmh = interpolate_output_speed_from_series(timestamps, speeds, target_timestamp_ms)
        if estimated_speed_kmh is None:
            continue

        sec_since_cal = interpolate_output_speed_from_series(timestamps, sec_since_cals, target_timestamp_ms)
        if sec_since_cal is None or sec_since_cal > 300.0:
            continue

        cal_idx_f = interpolate_output_speed_from_series(timestamps, cal_indices_f, target_timestamp_ms)
        if cal_idx_f is None:
            continue
        cal_idx = int(round(cal_idx_f))

        label = f"cal_{cal_idx}"
        if label not in bucket_diffs:
            bucket_diffs[label] = []
            bucket_moving_diffs[label] = []

        location_speed_kmh = float(row["locationSpeedMps"]) * 3.6
        diff = estimated_speed_kmh - location_speed_kmh
        bucket_diffs[label].append(diff)
        if location_speed_kmh >= 3.0:
            bucket_moving_diffs[label].append(diff)

    result: Dict[str, Any] = {}
    for label in sorted(bucket_diffs.keys()):
        result[label] = {
            "rangeSec": "0-300",
            "all": diff_stats(bucket_diffs[label]),
            "moving": diff_stats(bucket_moving_diffs[label]),
        }
    return result


def scan_location_lag(
    rows: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    min_lag_ms: int = -30000,
    max_lag_ms: int = 30000,
    step_ms: int = 1000,
    speed_key: str = "speedKmh",
) -> Dict[str, Any]:
    if not outputs:
        return {}
    scans: List[Dict[str, Any]] = []
    for lag_ms in range(min_lag_ms, max_lag_ms + 1, step_ms):
        comparison = compare_with_location(rows, outputs, lag_ms, speed_key)
        moving = comparison.get("moving", {})
        if moving.get("count"):
            scans.append({
                "lagMs": lag_ms,
                "lagSec": lag_ms / 1000.0,
                "moving": moving,
                "all": comparison.get("all", {}),
            })
    if not scans:
        return {}
    scans.sort(key=lambda item: float(item["moving"].get("maeKmh") or 1e9))
    zero = next((item for item in scans if item["lagMs"] == 0), None)
    return {
        "definition": "positive lag means location speed is compared with an earlier estimator sample",
        "bestMoving": scans[0],
        "zeroMoving": zero,
        "topMoving": scans[:8],
    }


def should_infer_measurement_active(row: Dict[str, Any]) -> bool:
    if row.get("recordType") != "sensor" or row.get("accX") is None:
        return False
    if row.get("measurementActive") is True or row.get("estimatorActive") is True:
        return True
    motion_state = str(row.get("motionState") or "")
    calibration_status = str(row.get("calibrationStatus") or "")
    confidence = row.get("confidence")
    if motion_state or calibration_status:
        return True
    return isinstance(confidence, (int, float)) and confidence > 0


def build_anchored_outputs_v2(
    rows: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    anchor_power: float = 1.0,
    pure_zero: bool = False,
    anchor_interval_ms: int = 0,
) -> List[Dict[str, Any]]:
    """ArkTS anchor logic v2: vibration freeze + tunnel lockout + confidence blend.
    anchor_power: exponent applied to confidence before blend. >1.0 gives more anchor weight.
    pure_zero: skip blend entirely, output anchor+delta directly (matches phone).
    anchor_interval_ms: minimum ms between anchors. 0 = every valid GNSS point (phone behavior)."""
    if not outputs:
        return []

    outputs_sorted = sorted(outputs, key=lambda item: int(item["timestampMs"]))
    out_ts = [int(item["timestampMs"]) for item in outputs_sorted]
    out_spd = [float(item["speedKmh"]) / 3.6 for item in outputs_sorted]

    tunnel_inside = False
    anchors: List[Tuple[int, float, float]] = []  # (ts, gnss_mps, inertial_mps)
    last_anchor_ts: int = -999999999

    sorted_rows = sorted(rows, key=lambda item: int(item.get("timestampMs", 0)))
    for row in sorted_rows:
        t = int(row.get("timestampMs", 0))
        if row.get("recordType") == "event":
            evt = str(row.get("event") or "")
            notes = str(row.get("notes") or "")
            if "\u5165\u96a7" in evt or "\u5165\u96a7" in notes:
                tunnel_inside = True
            elif "\u51fa\u96a7" in evt or "\u51fa\u96a7" in notes:
                tunnel_inside = False
            continue

        if row.get("recordType") != "location":
            continue

        event = str(row.get("event") or "")
        if event in ("\u505c\u8f66\u6821\u51c6", "\u5230\u7ad9\u6821\u51c6"):
            anchors.append((t, 0.0, 0.0))
            continue

        if tunnel_inside:
            continue

        spd_mps = row.get("locationSpeedMps")
        src = row.get("locationSourceType")
        acc = row.get("locationSpeedAccuracyMps")
        if spd_mps is None:
            continue
        if acc is not None and float(acc) <= 0:
            continue
        if src is not None and int(src) not in (1, 4):
            continue

        loc_time_ms = int(row.get("locationTimeMs") or t)
        inertial_at_anchor = interpolate_output_speed_from_series(out_ts, out_spd, loc_time_ms)
        if inertial_at_anchor is None:
            continue

        ms = interpolate_motion_state_from_series(outputs_sorted, loc_time_ms)
        if ms in ("strong_vibration", "conduction_vibration"):
            continue

        if anchor_interval_ms > 0 and loc_time_ms - last_anchor_ts < anchor_interval_ms:
            continue

        last_anchor_ts = loc_time_ms
        anchors.append((loc_time_ms, float(spd_mps), inertial_at_anchor))

    result: List[Dict[str, Any]] = []
    anchor_idx = 0
    last_anchor_spd = 0.0
    last_anchor_inertial = 0.0
    for item in outputs_sorted:
        t = int(item["timestampMs"])
        while anchor_idx < len(anchors) and anchors[anchor_idx][0] <= t:
            _, last_anchor_spd, last_anchor_inertial = anchors[anchor_idx]
            anchor_idx += 1
        inertial_mps = float(item["speedKmh"]) / 3.6
        anchored_raw_mps = max(0.0, last_anchor_spd + (inertial_mps - last_anchor_inertial))
        if pure_zero:
            anchored_speed_kmh = anchored_raw_mps * 3.6
        else:
            conf = float(item["confidence"])
            if anchor_power != 1.0:
                conf = conf ** anchor_power
            pure_speed_kmh = float(item["speedKmh"])
            anchored_speed_kmh = conf * pure_speed_kmh + (1.0 - conf) * anchored_raw_mps * 3.6
        result.append({
            **item,
            "anchoredSpeedKmh": anchored_speed_kmh,
            "anchorSpeedKmh": last_anchor_spd * 3.6,
            "inertialDeltaKmh": (inertial_mps - last_anchor_inertial) * 3.6,
        })
    return result


def interpolate_motion_state_from_series(
    output_items: List[Dict[str, Any]],
    target_ms: int,
) -> str:
    if not output_items:
        return "unknown"
    best = output_items[0]
    best_dist = abs(int(best["timestampMs"]) - target_ms)
    for item in output_items:
        d = abs(int(item["timestampMs"]) - target_ms)
        if d < best_dist:
            best_dist = d
            best = item
    return str(best.get("motionState", "unknown"))


def summarize(
    rows: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    replay_config: Dict[str, Any],
    use_anchor_v2: bool = False,
    anchor_power: float = 1.0,
    pure_zero: bool = False,
    gnss_lag_ms: int = 0,
    anchor_interval_ms: int = 0,
) -> Dict[str, Any]:
    recorded_pairs = [
        abs(float(item["speedKmh"]) - float(item["recordedSpeedKmh"]))
        for item in outputs
        if item.get("recordedSpeedKmh") is not None
    ]
    speeds = [float(item["speedKmh"]) for item in outputs]
    confidences = [float(item["confidence"]) for item in outputs]
    states = {}
    for item in outputs:
        states[item["motionState"]] = states.get(item["motionState"], 0) + 1

    summary: Dict[str, Any] = {
        "algorithmVersion": ALGORITHM_VERSION,
        "replayConfig": {
            **replay_config,
            "appParity": replay_config_matches_app(replay_config),
            "note": "appParity=false means this replay uses experimental options not present in the ArkTS app.",
        },
        "sensorSamples": len(outputs),
        "events": events,
        "states": states,
    }
    if speeds:
        summary["speed"] = {
            "minKmh": min(speeds),
            "medianKmh": statistics.median(speeds),
            "p90Kmh": quantile(speeds, 0.9),
            "maxKmh": max(speeds),
            "lastKmh": speeds[-1],
        }
    if confidences:
        summary["confidence"] = {
            "median": statistics.median(confidences),
            "p10": quantile(confidences, 0.1),
            "min": min(confidences),
            "last": confidences[-1],
        }
    if recorded_pairs:
        summary["recordedEstimatorDiff"] = {
            "count": len(recorded_pairs),
            "maeKmh": sum(recorded_pairs) / len(recorded_pairs),
            "maxAbsKmh": max(recorded_pairs),
        }
    if use_anchor_v2:
        anchored = build_anchored_outputs_v2(rows, outputs, anchor_power, pure_zero, anchor_interval_ms)
        label = "anchoredV2"
        summary["anchorSpeed"] = {
            "minKmh": min(float(item["anchoredSpeedKmh"]) for item in anchored),
            "medianKmh": statistics.median([float(item["anchoredSpeedKmh"]) for item in anchored]),
            "p90Kmh": quantile([float(item["anchoredSpeedKmh"]) for item in anchored], 0.9),
            "maxKmh": max(float(item["anchoredSpeedKmh"]) for item in anchored),
        }
        summary["locationComparison"] = compare_with_location(rows, outputs, lag_ms=gnss_lag_ms)
        summary["anchoredComparison"] = compare_with_location(rows, anchored, lag_ms=gnss_lag_ms, speed_key="anchoredSpeedKmh")
        summary["anchoredLagScan"] = scan_location_lag(rows, anchored, speed_key="anchoredSpeedKmh")
        summary["anchoredLagScanFine"] = scan_location_lag(rows, anchored, min_lag_ms=-2000, max_lag_ms=2000, step_ms=20, speed_key="anchoredSpeedKmh")
        summary["anchoredDecay"] = compare_bucketed(rows, anchored, speed_key="anchoredSpeedKmh")
    else:
        summary["locationComparison"] = compare_with_location(rows, outputs, lag_ms=gnss_lag_ms)
        summary["locationLagScan"] = scan_location_lag(rows, outputs)
        summary["locationLagScanFine"] = scan_location_lag(rows, outputs, min_lag_ms=-2000, max_lag_ms=2000, step_ms=20)
        summary["calibrationDecay"] = compare_bucketed(rows, outputs)
    return summary


def replay_config_matches_app(config: Dict[str, Any]) -> bool:
    for key, expected in APP_PARITY_CONFIG.items():
        value = config.get(key)
        if isinstance(expected, float):
            if abs(float(value) - expected) > 1e-9:
                return False
        elif value != expected:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay MetroSpeed estimator against exported JSONL logs.")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="Optional replay JSONL output path.")
    parser.add_argument("--no-strict-start", action="store_true", help="Do not abort when current app would reject initial calibration.")
    parser.add_argument("--curve-positive-scale", type=float, default=0.35, help="Scale positive acceleration while in curve state.")
    parser.add_argument("--curve-negative-scale", type=float, default=0.35, help="Scale negative acceleration while in curve state.")
    parser.add_argument("--low-confidence-positive-scale", type=float, default=0.55, help="Scale positive acceleration while in low-confidence state.")
    parser.add_argument("--low-confidence-negative-scale", type=float, default=0.55, help="Scale negative acceleration while in low-confidence state.")
    parser.add_argument("--braking-negative-scale", type=float, default=1.0, help="Scale negative acceleration while in braking state.")
    parser.add_argument("--no-infer-start", action="store_true", help="Do not infer measurement start from estimator-bearing sensor rows.")
    parser.add_argument("--use-gyro-gravity", action="store_true", help="Experimental only: not implemented in the ArkTS app.")
    parser.add_argument("--use-sys-gravity", action="store_true", help="Experimental only: use system gravity sensor instead of estimated gravity when available.")
    parser.add_argument("--adaptive-gravity", action="store_true", help="Analysis only: magnetometer scenario detector switches gravity source per-frame (driving=system gravity, subway=self-estimated).")
    parser.add_argument("--gyro-gravity-sign", type=float, default=-1.0, help="Sign for gyro gravity propagation. Use -1 for body-frame inertial vector update.")
    parser.add_argument("--no-vibration-guard", action="store_true", help="Disable high acceleration-step vibration guarding.")
    parser.add_argument("--vibration-threshold", type=float, default=0.85, help="Mean acceleration-step threshold for vibration low-confidence handling.")
    parser.add_argument("--vibration-scale", type=float, default=0.18, help="Acceleration integration scale while vibration is above threshold.")
    # signal preprocessing
    parser.add_argument("--low-pass-alpha", type=float, default=0.22)
    parser.add_argument("--accel-clip-ceiling", type=float, default=3.5)
    parser.add_argument("--dt-fallback", type=float, default=0.02)
    parser.add_argument("--dt-clamp-lo", type=float, default=0.005)
    parser.add_argument("--dt-clamp-hi", type=float, default=0.08)
    # calibration
    parser.add_argument("--calibration-duration-ms", type=int, default=1500)
    parser.add_argument("--calibration-rms-threshold", type=float, default=0.25)
    parser.add_argument("--calibration-gravity-error", type=float, default=0.25)
    parser.add_argument("--calibration-motion-gyro-mean", type=float, default=0.08)
    parser.add_argument("--calibration-motion-gyro-max", type=float, default=0.25)
    parser.add_argument("--calibration-motion-acc-step", type=float, default=0.65)
    parser.add_argument("--calibration-reject-cooldown-ms", type=int, default=10000)
    parser.add_argument("--calibration-parking-success-ms", type=int, default=5000)
    parser.add_argument("--calibration-parking-reject-ms", type=int, default=5000)
    # axis init + tracking
    parser.add_argument("--axis-init-acc-threshold", type=float, default=0.18)
    parser.add_argument("--axis-init-gyro-threshold", type=float, default=0.18)
    parser.add_argument("--axis-locked-lateral", type=float, default=0.08)
    parser.add_argument("--axis-locked-gyro-instant", type=float, default=0.10)
    parser.add_argument("--axis-locked-gyro-mean", type=float, default=0.08)
    parser.add_argument("--axis-locked-acc", type=float, default=0.25)
    parser.add_argument("--axis-locked-speed", type=float, default=5.0)
    parser.add_argument("--axis-unlocked-lateral", type=float, default=0.18)
    parser.add_argument("--axis-unlocked-gyro-instant", type=float, default=0.16)
    parser.add_argument("--axis-unlocked-gyro-mean", type=float, default=0.14)
    parser.add_argument("--axis-stop-update-speed", type=float, default=8.0)
    parser.add_argument("--axis-speed-threshold", type=float, default=3.0)
    parser.add_argument("--axis-acc-high-speed", type=float, default=0.16)
    parser.add_argument("--axis-acc-low-speed", type=float, default=0.10)
    parser.add_argument("--axis-mix-locked", type=float, default=0.003)
    parser.add_argument("--axis-mix-unlocked", type=float, default=0.025)
    parser.add_argument("--axis-ortho-threshold", type=float, default=0.15)
    parser.add_argument("--axis-reset-alignment", type=float, default=0.35)
    parser.add_argument("--axis-reset-speed", type=float, default=2.0)
    parser.add_argument("--axis-reset-acc", type=float, default=0.18)
    parser.add_argument("--axis-reset-seed-ratio", type=float, default=1.8)
    # axis lock trigger + stability
    parser.add_argument("--axis-lock-speed", type=float, default=5.0)
    parser.add_argument("--axis-lock-time-ms", type=int, default=30000)
    parser.add_argument("--axis-lock-update-count", type=int, default=60)
    parser.add_argument("--axis-window-min-frames", type=int, default=20)
    parser.add_argument("--axis-window-max-frames", type=int, default=80)
    parser.add_argument("--axis-window-max-ms", type=int, default=1800)
    parser.add_argument("--axis-stable-acc-step", type=float, default=0.35)
    parser.add_argument("--axis-stable-gravity-dev", type=float, default=0.45)
    parser.add_argument("--axis-stable-gyro", type=float, default=0.12)
    parser.add_argument("--axis-stable-forward-var", type=float, default=0.08)
    parser.add_argument("--axis-unstable-acc-step", type=float, default=0.85)
    parser.add_argument("--axis-unstable-gravity-dev", type=float, default=1.2)
    # motion state detection
    parser.add_argument("--longcal-timeout-ms", type=int, default=360000)
    parser.add_argument("--idle-forward", type=float, default=0.035)
    parser.add_argument("--idle-speed", type=float, default=0.25)
    parser.add_argument("--accel-forward", type=float, default=0.055)
    parser.add_argument("--brake-forward", type=float, default=-0.055)
    parser.add_argument("--low-conf-forward-var", type=float, default=0.55)
    parser.add_argument("--low-conf-gyro", type=float, default=1.8)
    parser.add_argument("--vibration-conduction-gyro", type=float, default=0.06)
    parser.add_argument("--vibration-strong-gyro", type=float, default=0.06)
    parser.add_argument("--vibration-strong-gravity-dev", type=float, default=1.2)
    parser.add_argument("--curve-a-lateral", type=float, default=0.18)
    parser.add_argument("--curve-a-gyro", type=float, default=0.045)
    parser.add_argument("--curve-a-ratio", type=float, default=1.15)
    parser.add_argument("--curve-b-gyro", type=float, default=0.09)
    parser.add_argument("--curve-b-gyro-var", type=float, default=0.0018)
    parser.add_argument("--curve-b-lateral", type=float, default=0.10)
    # effective acceleration misc
    parser.add_argument("--dead-zone", type=float, default=0.025)
    parser.add_argument("--conduction-scale", type=float, default=0.45)
    # confidence
    parser.add_argument("--confidence-base", type=float, default=1.0)
    parser.add_argument("--confidence-decay-rate", type=float, default=(0.85 / 180000))
    parser.add_argument("--confidence-gyro-divisor", type=float, default=3.0)
    parser.add_argument("--confidence-gyro-max", type=float, default=0.2)
    parser.add_argument("--confidence-clamp-lo", type=float, default=0.05)
    parser.add_argument("--confidence-clamp-hi", type=float, default=0.95)
    parser.add_argument("--anchor-v2", action="store_true", help="Anchor v2: vibration freeze + tunnel lockout + confidence blend (matches ArkTS Index.ets).")
    parser.add_argument("--anchor-power", type=float, default=1.0, help="Confidence exponent for anchor v2 blend. >1.0 skews toward anchor.")
    parser.add_argument("--pure-zero", action="store_true", help="Anchor v2 without blend: pure anchor+delta. Matches phone.")
    parser.add_argument("--gnss-lag-ms", type=int, default=0, help="GNSS timestamp offset compensation in ms. Positive means GNSS data is delayed relative to sensor (negative lag in comparison sense).")
    parser.add_argument("--anchor-interval-ms", type=int, default=0, help="Minimum ms between anchors. 0 = every valid GNSS point (default phone behavior). Set to 5000+ to test inertial drift between sparse anchors.")
    args = parser.parse_args()
    curve_negative_scale = args.curve_negative_scale
    low_confidence_negative_scale = args.low_confidence_negative_scale

    rows = read_jsonl(args.jsonl)

    estimator_kwargs = {k: v for k, v in vars(args).items()
                        if k in SpeedEstimator.__init__.__code__.co_varnames}
    estimator_kwargs["curve_negative_scale"] = curve_negative_scale
    estimator_kwargs["low_confidence_negative_scale"] = low_confidence_negative_scale
    estimator_kwargs["use_vibration_guard"] = not args.no_vibration_guard

    replay_config = {
        "curve_positive_scale": args.curve_positive_scale,
        "curve_negative_scale": curve_negative_scale,
        "low_confidence_positive_scale": args.low_confidence_positive_scale,
        "low_confidence_negative_scale": low_confidence_negative_scale,
        "braking_negative_scale": args.braking_negative_scale,
        "infer_start_from_sensor": not args.no_infer_start,
        "use_gyro_gravity": args.use_gyro_gravity,
        "use_sys_gravity": args.use_sys_gravity,
        "adaptive_gravity": args.adaptive_gravity,
        "gyro_gravity_sign": args.gyro_gravity_sign,
        "use_vibration_guard": not args.no_vibration_guard,
        "vibration_threshold": args.vibration_threshold,
        "vibration_scale": args.vibration_scale,
    }

    outputs, events = replay(
        rows,
        strict_start=not args.no_strict_start,
        infer_start_from_sensor=not args.no_infer_start,
        adaptive_gravity=args.adaptive_gravity,
        **estimator_kwargs,
    )
    summary = summarize(rows, outputs, events, replay_config, use_anchor_v2=getattr(args, "anchor_v2", False), anchor_power=args.anchor_power, pure_zero=args.pure_zero, gnss_lag_ms=args.gnss_lag_ms, anchor_interval_ms=args.anchor_interval_ms)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            for item in outputs:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
