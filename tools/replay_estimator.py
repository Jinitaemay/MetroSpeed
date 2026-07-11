#!/usr/bin/env python3
import argparse
import bisect
import inspect
import json
import math
import os
import statistics
import tempfile
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALGORITHM_VERSION = "anchor-delta-20260710-r3"
PHONE_GNSS_ANCHOR_LAG_MS = 40
APP_PARITY_ESTIMATOR_CONFIG = {
    "curve_positive_scale": 0.35,
    "curve_negative_scale": 0.35,
    "low_confidence_positive_scale": 0.55,
    "low_confidence_negative_scale": 0.55,
    "braking_negative_scale": 1.0,
    "use_vibration_guard": True,
    "vibration_threshold": 0.85,
    "vibration_scale": 0.18,
    "low_pass_alpha": 0.22,
    "accel_clip_ceiling": 3.5,
    "dt_fallback": 0.02,
    "dt_clamp_lo": 0.005,
    "dt_clamp_hi": 0.08,
    "calibration_duration_ms": 1500,
    "calibration_rms_threshold": 0.25,
    "calibration_gravity_error": 0.25,
    "calibration_motion_gyro_mean": 0.08,
    "calibration_motion_gyro_max": 0.25,
    "calibration_motion_acc_step": 0.65,
    "calibration_reject_cooldown_ms": 10000,
    "calibration_parking_success_ms": 5000,
    "calibration_parking_reject_ms": 5000,
    "axis_init_acc_threshold": 0.18,
    "axis_init_gyro_threshold": 0.18,
    "axis_locked_lateral": 0.08,
    "axis_locked_gyro_instant": 0.10,
    "axis_locked_gyro_mean": 0.08,
    "axis_locked_acc": 0.25,
    "axis_locked_speed": 5.0,
    "axis_unlocked_lateral": 0.18,
    "axis_unlocked_gyro_instant": 0.16,
    "axis_unlocked_gyro_mean": 0.14,
    "axis_stop_update_speed": 8.0,
    "axis_speed_threshold": 3.0,
    "axis_acc_high_speed": 0.16,
    "axis_acc_low_speed": 0.10,
    "axis_mix_locked": 0.003,
    "axis_mix_unlocked": 0.025,
    "axis_ortho_threshold": 0.15,
    "axis_reset_alignment": 0.35,
    "axis_reset_speed": 2.0,
    "axis_reset_acc": 0.18,
    "axis_reset_seed_ratio": 1.8,
    "axis_lock_speed": 5.0,
    "axis_lock_time_ms": 30000,
    "axis_lock_update_count": 60,
    "axis_window_min_frames": 20,
    "axis_window_max_frames": 80,
    "axis_window_max_ms": 1800,
    "axis_stable_acc_step": 0.35,
    "axis_stable_gravity_dev": 0.45,
    "axis_stable_gyro": 0.12,
    "axis_stable_forward_var": 0.08,
    "axis_unstable_acc_step": 0.85,
    "axis_unstable_gravity_dev": 1.2,
    "longcal_timeout_ms": 360000,
    "idle_forward": 0.035,
    "idle_speed": 0.25,
    "accel_forward": 0.055,
    "brake_forward": -0.055,
    "low_conf_forward_var": 0.55,
    "low_conf_gyro": 1.8,
    "vibration_conduction_gyro": 0.06,
    "vibration_strong_gyro": 0.06,
    "vibration_strong_gravity_dev": 1.2,
    "curve_a_lateral": 0.18,
    "curve_a_gyro": 0.045,
    "curve_a_ratio": 1.15,
    "curve_b_gyro": 0.09,
    "curve_b_gyro_var": 0.0018,
    "curve_b_lateral": 0.10,
    "dead_zone": 0.025,
    "conduction_scale": 0.45,
    "confidence_base": 1.0,
    "confidence_decay_rate": (0.85 / 180000),
    "confidence_gyro_divisor": 3.0,
    "confidence_gyro_max": 0.2,
    "confidence_clamp_lo": 0.05,
    "confidence_clamp_hi": 0.95,
    "use_gyro_gravity": False,
    "gyro_gravity_sign": -1.0,
    "use_sys_gravity": False,
}
APP_PARITY_REPLAY_CONFIG = {
    "strict_start": True,
    "infer_start_from_sensor": True,
    "adaptive_gravity": False,
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
class PreCalFrame:
    timestamp_ms: int
    acceleration: Vector3
    gyro_magnitude: float
    acc_step: float
    delta_seconds: float
    distance_before_m: float
    integrated: bool


@dataclass
class EstimatorOutput:
    timestamp_ms: int
    sensor_timestamp: Optional[float]
    speed_kmh: float
    confidence: float
    sample_confidence: float
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
        self._constructor_kwargs = {
            key: value for key, value in locals().items() if key != "self"
        }
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
        self.calibration_first_sample_ms = 0
        self.calibration_last_sample_ms = 0
        self.calibration_rejected_until_ms = 0
        self.last_calibration_ms = 0
        self.motion_state = MotionState.IDLE
        self.confidence = 0.0
        self.pure_mode = 0
        self.window_frames: List[AccelWindowFrame] = []
        self.pre_cal_buffer: List[PreCalFrame] = []
        self.parking_calibration_pending = False
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.parking_calibration_result = 0
        self.parking_calibration_request_ms = 0
        self.parking_calibration_previous_ms = 0
        self.parking_calibration_snapshot: List[PreCalFrame] = []
        self.parking_post_request_frames: List[PreCalFrame] = []
        self.last_raw_acceleration: Optional[Vector3] = None
        self.initial_calibration_done = False

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
        self.parking_calibration_result = 0
        self.parking_calibration_request_ms = 0
        self.parking_calibration_previous_ms = 0
        self.parking_calibration_snapshot = []
        self.parking_post_request_frames = []
        self.last_raw_acceleration = None
        self.initial_calibration_done = False
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
        self.parking_calibration_result = 0
        self.parking_calibration_request_ms = 0
        self.parking_calibration_previous_ms = 0
        self.parking_calibration_snapshot = []
        self.parking_post_request_frames = []
        self.last_raw_acceleration = None
        self.pre_cal_buffer = []

    def reset(self, timestamp_ms: int) -> None:
        constructor_kwargs = dict(self._constructor_kwargs)
        pure_mode = self.pure_mode
        self.__init__(**constructor_kwargs)
        self.pure_mode = pure_mode
        self.start_ms = timestamp_ms
        self.last_timestamp_ms = timestamp_ms

    def set_pure_mode(self, pure: int) -> None:
        self.pure_mode = pure

    def consume_parking_calibration_result(self) -> int:
        result = self.parking_calibration_result
        self.parking_calibration_result = 0
        return result

    def calibrate_at_stop(self, timestamp_ms: int) -> bool:
        if not self.initial_calibration_done:
            return False
        if self.parking_calibration_pending:
            return True
        self.parking_calibration_pending = True
        self.parking_calibration_rejected_until_ms = 0
        self.parking_calibration_success_until_ms = 0
        self.parking_calibration_result = 0
        self.parking_calibration_request_ms = timestamp_ms
        self.parking_calibration_previous_ms = self.last_calibration_ms
        self.parking_calibration_snapshot = list(self.pre_cal_buffer)
        self.parking_post_request_frames = []
        self.begin_calibration(timestamp_ms)
        self.last_calibration_ms = self.parking_calibration_previous_ms
        return True

    def ingest(self, frame: SensorFrame) -> EstimatorOutput:
        if not self.running:
            return self.make_output(frame)

        dt = self.compute_delta_seconds(frame)
        raw_acceleration = frame.acceleration
        acc_step = self.compute_acc_step(raw_acceleration)

        if (
            not self.initial_calibration_done
            and not self.parking_calibration_pending
            and self.calibration_samples == 0
        ):
            self.calibration_until_ms = frame.timestamp_ms + self.calibration_duration_ms
            self.last_calibration_ms = frame.timestamp_ms

        pre_cal_gyro = v_mag(frame.gyroscope) if frame.gyroscope is not None else 0.0
        if len(self.pre_cal_buffer) >= 180:
            self.pre_cal_buffer.pop(0)
        pre_cal_frame = PreCalFrame(
            timestamp_ms=frame.timestamp_ms,
            acceleration=raw_acceleration,
            gyro_magnitude=pre_cal_gyro,
            acc_step=acc_step,
            delta_seconds=dt,
            distance_before_m=self.distance_m,
            integrated=False,
        )
        self.pre_cal_buffer.append(pre_cal_frame)
        if self.parking_calibration_pending:
            self.parking_post_request_frames.append(pre_cal_frame)

        if frame.timestamp_ms <= self.calibration_until_ms:
            calibration_gyro_magnitude = v_mag(frame.gyroscope) if frame.gyroscope is not None else 0.0
            self.collect_calibration(
                frame.timestamp_ms,
                raw_acceleration,
                calibration_gyro_magnitude,
            )
            if not self.parking_calibration_pending:
                self.motion_state = MotionState.CALIBRATING
                return self.make_output(frame, v_empty(), sample_confidence=0.35)

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
        pre_cal_frame.integrated = True
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
        self.calibration_first_sample_ms = 0
        self.calibration_last_sample_ms = 0
        self.calibration_count += 1
        self.last_calibration_ms = timestamp_ms
        self.motion_state = MotionState.CALIBRATING

    def collect_calibration(self, timestamp_ms: int, acceleration: Vector3, gyro_magnitude: float) -> None:
        if self.calibration_samples == 0:
            self.calibration_first_sample_ms = timestamp_ms
        self.calibration_last_sample_ms = timestamp_ms
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
        if (
            (not self.parking_calibration_pending and self.calibration_samples <= 0)
            or timestamp_ms <= self.calibration_until_ms
        ):
            return

        was_parking_calibration = self.parking_calibration_pending
        stable_result = False
        gravity_candidate: Vector3 = v_empty()
        parking_window_start = -1
        parking_window_end = -1

        parking_frames = (
            self.parking_calibration_snapshot + self.parking_post_request_frames
            if was_parking_calibration
            else self.pre_cal_buffer
        )
        historical_frame_count = (
            len(self.parking_calibration_snapshot)
            if was_parking_calibration
            else max(0, len(self.pre_cal_buffer) - 1)
        )
        eligible_parking_frame_count = 0
        for index in range(historical_frame_count):
            if parking_frames[index].timestamp_ms <= self.parking_calibration_request_ms:
                eligible_parking_frame_count = index + 1
            else:
                break

        if was_parking_calibration and eligible_parking_frame_count >= 75:
            window_frames = 75
            best_start = -1
            best_rms = float("inf")
            for i in range(eligible_parking_frame_count - window_frames + 1):
                window_end_timestamp_ms = parking_frames[i + window_frames - 1].timestamp_ms
                window_age_ms = self.parking_calibration_request_ms - window_end_timestamp_ms
                if window_age_ms < 0 or window_age_ms > 300:
                    continue
                s = v_empty()
                sq = 0.0
                for j in range(i, i + window_frames):
                    acc = parking_frames[j].acceleration
                    s = v_add(s, acc)
                    sq += v_dot(acc, acc)
                mean = v_scale(s, 1.0 / window_frames)
                mean_sq = sq / window_frames
                mean_dot = v_dot(mean, mean)
                rms = math.sqrt(max(0.0, mean_sq - mean_dot))
                if rms < best_rms:
                    best_rms = rms
                    best_start = i

            if best_start >= 0:
                cal_sum = v_empty()
                cal_sq_sum = 0.0
                cal_gyro_sum = 0.0
                cal_gyro_max = 0.0
                cal_max_step = 0.0
                last_acc: Optional[Vector3] = None
                for j in range(best_start, best_start + window_frames):
                    buffered_frame = parking_frames[j]
                    acc = buffered_frame.acceleration
                    gyro = buffered_frame.gyro_magnitude
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
                parking_window_start = best_start
                parking_window_end = best_start + window_frames - 1
        elif not was_parking_calibration:
            sample_coverage_ms = self.calibration_last_sample_ms - self.calibration_first_sample_ms
            has_enough_samples = self.calibration_samples >= 30 and sample_coverage_ms >= 1000
            if has_enough_samples:
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
            if was_parking_calibration:
                self.last_calibration_ms = self.parking_calibration_request_ms
                self.apply_parking_replay(
                    parking_frames, parking_window_start, parking_window_end
                )
                self.parking_calibration_success_until_ms = timestamp_ms + self.calibration_parking_success_ms
                self.parking_calibration_result = 1
            else:
                self.filtered_acceleration = v_empty()
        else:
            self.calibration_rejected_until_ms = timestamp_ms + self.calibration_reject_cooldown_ms
            if was_parking_calibration:
                self.parking_calibration_rejected_until_ms = timestamp_ms + self.calibration_parking_reject_ms
                self.parking_calibration_result = -1
            else:
                self.filtered_acceleration = v_empty()
        self.parking_calibration_pending = False
        self.parking_calibration_request_ms = 0
        self.parking_calibration_previous_ms = 0
        self.parking_calibration_snapshot = []
        self.parking_post_request_frames = []
        self.initial_calibration_done = True

        self.calibration_samples = 0
        self.calibration_sum = v_empty()
        self.calibration_square_sum = 0.0
        self.calibration_gyro_sum = 0.0
        self.calibration_gyro_max = 0.0
        self.calibration_max_step = 0.0
        self.calibration_last_acceleration = None
        self.calibration_first_sample_ms = 0
        self.calibration_last_sample_ms = 0

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

    def apply_parking_replay(
        self,
        frames: List[PreCalFrame],
        window_start: int,
        window_end: int,
    ) -> None:
        anchor_distance_m = frames[window_start].distance_before_m
        self.velocity_mps = 0.0
        self.distance_m = anchor_distance_m
        self.filtered_acceleration = v_empty()
        self.window_frames = []
        self.motion_state = MotionState.IDLE

        if self.main_axis_initialized:
            self._orthogonalize_axis()

        for index in range(window_start, window_end + 1):
            frame = frames[index]
            frame.distance_before_m = anchor_distance_m
            motion_acceleration = v_sub(frame.acceleration, self.gravity_estimate)
            clipped = clip_magnitude(motion_acceleration, self.accel_clip_ceiling)
            filtered = self.low_pass(clipped, self.low_pass_alpha)
            self.filtered_acceleration = filtered
            forward = v_dot(filtered, self.main_axis)
            lateral_vector = v_sub(filtered, v_scale(self.main_axis, forward))
            self.push_window_frame(
                frame.timestamp_ms,
                forward,
                v_mag(lateral_vector),
                frame.gyro_magnitude,
                filtered,
                frame.acc_step,
                abs(v_mag(frame.acceleration) - 9.80665),
            )

        positive_acceleration_frames = 0
        departure_detected = False
        replay_frame_limit = max(window_end + 1, len(frames) - 1)
        for index in range(window_end + 1, replay_frame_limit):
            frame = frames[index]
            frame.distance_before_m = self.distance_m
            motion_acceleration = v_sub(frame.acceleration, self.gravity_estimate)
            clipped = clip_magnitude(motion_acceleration, self.accel_clip_ceiling)
            filtered = self.low_pass(clipped, self.low_pass_alpha)
            self.filtered_acceleration = filtered
            forward = v_dot(filtered, self.main_axis)
            lateral_vector = v_sub(filtered, v_scale(self.main_axis, forward))
            lateral = v_mag(lateral_vector)
            self.push_window_frame(
                frame.timestamp_ms,
                forward,
                lateral,
                frame.gyro_magnitude,
                filtered,
                frame.acc_step,
                abs(v_mag(frame.acceleration) - 9.80665),
            )
            state = self.detect_motion_state(
                forward,
                lateral,
                frame.gyro_magnitude,
                frame.timestamp_ms,
            )
            self.motion_state = state

            if not frame.integrated:
                continue

            effective_acceleration = self.effective_forward_acceleration(forward, state)
            previous_velocity_mps = self.velocity_mps
            self.velocity_mps = max(
                0.0,
                self.velocity_mps + effective_acceleration * frame.delta_seconds,
            )
            self.distance_m += (
                (previous_velocity_mps + self.velocity_mps) / 2.0
            ) * frame.delta_seconds

            if forward > self.accel_forward and frame.gyro_magnitude < self.calibration_motion_gyro_max:
                positive_acceleration_frames += 1
            else:
                positive_acceleration_frames = 0
            if positive_acceleration_frames >= 3:
                departure_detected = True

        if not departure_detected:
            self.velocity_mps = 0.0
            self.distance_m = anchor_distance_m
            self.motion_state = MotionState.IDLE
            for index in range(window_end + 1, replay_frame_limit):
                frames[index].distance_before_m = anchor_distance_m

        if frames:
            frames[-1].distance_before_m = self.distance_m

    def compute_confidence(self, state: MotionState, gyro_magnitude: float, timestamp_ms: int) -> float:
        value = self.confidence_base
        since_calibration = timestamp_ms - self.last_calibration_ms
        decay_rate = (0.85 / 120000) if self.pure_mode > 0 else self.confidence_decay_rate
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

    def make_output(
        self,
        frame: SensorFrame,
        filtered: Optional[Vector3] = None,
        sample_confidence: Optional[float] = None,
    ) -> EstimatorOutput:
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
            sample_confidence=(
                self.confidence if sample_confidence is None else sample_confidence
            ),
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


APP_PARITY_CONFIG = {
    **APP_PARITY_ESTIMATOR_CONFIG,
    **APP_PARITY_REPLAY_CONFIG,
}


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
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{path}:{line_number}: invalid JSONL: {error.msg} "
                        f"(column {error.colno})"
                    ) from error
                if not isinstance(row, dict):
                    raise ValueError(
                        f"{path}:{line_number}: each JSONL row must be an object"
                    )
                timestamp = parse_number(row, "timestampMs")
                if timestamp is None or not timestamp.is_integer():
                    raise ValueError(
                        f"{path}:{line_number}: timestampMs must be a finite integer"
                    )
                row["timestampMs"] = int(timestamp)
                record_seq = row.get("recordSeq")
                if record_seq is not None:
                    parsed_record_seq = parse_number(row, "recordSeq")
                    if (
                        parsed_record_seq is None
                        or not parsed_record_seq.is_integer()
                        or parsed_record_seq < 0
                    ):
                        raise ValueError(
                            f"{path}:{line_number}: recordSeq must be a non-negative integer"
                        )
                    row["recordSeq"] = int(parsed_record_seq)
                rows.append(row)
    # JSONL append order (and recordSeq within a session) is the callback execution
    # order. Wall-clock timestampMs can move backwards, so globally sorting here can
    # invert stop/start and parking events across measurement runs.
    return rows


def quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = round((len(values) - 1) * q)
    return values[index]


def event_matches(event: str, target: str) -> bool:
    return event == target or target in event


def parking_calibration_event_kind(event: str) -> Optional[str]:
    """Classify parking-calibration events without treating every prefix match as success."""
    normalized = event.strip()
    if "\u505c\u8f66\u6821\u51c6\u6210\u529f" in normalized:
        return "success"
    if "\u505c\u8f66\u6821\u51c6\u62d2\u7edd" in normalized or "\u505c\u8f66\u6821\u51c6\u5931\u8d25" in normalized:
        return "rejected"
    if "\u505c\u8f66\u6821\u51c6\u8bf7\u6c42" in normalized:
        return "request"
    if normalized in ("\u505c\u8f66\u6821\u51c6", "\u5230\u7ad9\u6821\u51c6"):
        return "legacy"
    return None


def replay(
    rows: List[Dict[str, Any]],
    strict_start: bool,
    infer_start_from_sensor: bool,
    adaptive_gravity: bool = False,
    **estimator_kwargs: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    start_event = "\u5f00\u59cb\u6d4b\u901f"
    stop_event = "\u505c\u6b62\u6d4b\u901f"
    estimator = SpeedEstimator(**estimator_kwargs)
    outputs: List[Dict[str, Any]] = []
    replay_events: List[Dict[str, Any]] = []
    running = False
    tunnel_inside = False
    has_gnss_anchor = False
    latest_gnss_speed_mps = 0.0
    latest_gnss_speed_accuracy_mps = -1.0
    first_timestamp = int(rows[0]["timestampMs"]) if rows else 0
    current_run_id: Optional[str] = None
    replay_run_count = 0

    # 磁力计场景检测器状态（分析层，不修改 SpeedEstimator 行为）
    # 前 500 帧采集磁力计 std 中位数判定一次场景，之后不再切换
    mag_window: List[float] = []
    mag_std_samples: List[float] = []
    scenario_decided = False
    scenario_is_subway = True  # 安全默认：判定前用自估重力
    if adaptive_gravity:
        estimator.use_sys_gravity = False  # 判定为驾车后才启用系统重力

    def begin_replay_run(
        timestamp_ms: int,
        row: Dict[str, Any],
        event_name: str,
    ) -> None:
        nonlocal running, has_gnss_anchor, replay_run_count, current_run_id
        nonlocal scenario_decided, scenario_is_subway
        estimator.start(timestamp_ms)
        running = True
        has_gnss_anchor = False
        replay_run_count += 1
        recorded_run_id = row.get("measurementRunId")
        current_run_id = (
            str(recorded_run_id)
            if recorded_run_id not in (None, "")
            else f"replay-run-{replay_run_count}"
        )
        if adaptive_gravity:
            mag_window.clear()
            mag_std_samples.clear()
            scenario_decided = False
            scenario_is_subway = True
            estimator.use_sys_gravity = False
        replay_events.append({
            "t": timestamp_ms,
            "event": event_name,
            "measurementRunId": current_run_id,
            "gravity": estimator.gravity_estimate,
            "mainAxis": estimator.main_axis,
            "mainAxisInit": estimator.main_axis_initialized,
        })

    def end_replay_run(timestamp_ms: int, event_name: str) -> None:
        nonlocal running, current_run_id
        if not running:
            return
        replay_events.append({
            "t": timestamp_ms,
            "event": event_name,
            "measurementRunId": current_run_id,
            "speedKmh": estimator.velocity_mps * 3.6,
        })
        estimator.stop(timestamp_ms)
        running = False
        current_run_id = None

    for source_row_index, row in enumerate(rows):
        timestamp_ms = int(row.get("timestampMs", 0))
        event = str(row.get("event") or "")
        notes = str(row.get("notes") or "")

        is_start_event = event_matches(event, start_event) or notes == "measurement started"
        is_already_running_event = event_matches(event, "\u6d4b\u901f\u5df2\u5728\u8fd0\u884c")
        if is_start_event or is_already_running_event:
            recorded_run_id = row.get("measurementRunId")
            recorded_run_text = (
                str(recorded_run_id) if recorded_run_id not in (None, "") else None
            )
            starts_new_run = (
                not running
                or is_start_event
                or (
                    recorded_run_text is not None
                    and recorded_run_text != current_run_id
                )
            )
            if starts_new_run:
                if running:
                    end_replay_run(timestamp_ms, "implicit_stop_before_new_start")
                begin_replay_run(timestamp_ms, row, "start")
            else:
                replay_events.append({
                    "t": timestamp_ms,
                    "event": "measurement_already_running",
                    "measurementRunId": current_run_id,
                })
            continue

        if row.get("recordType") == "sensor" and row.get("measurementActive") is False:
            end_replay_run(timestamp_ms, "inferred_stop_from_inactive_sensor")
            continue

        if running and row.get("recordType") == "sensor":
            sensor_run_id = row.get("measurementRunId")
            if sensor_run_id not in (None, "") and str(sensor_run_id) != current_run_id:
                end_replay_run(timestamp_ms, "implicit_stop_on_run_id_change")
                begin_replay_run(timestamp_ms, row, "inferred_start_on_run_id_change")

        if not running and infer_start_from_sensor and should_infer_measurement_active(row):
            begin_replay_run(timestamp_ms, row, "inferred_start_from_sensor")
        parking_kind = parking_calibration_event_kind(event)
        if running and parking_kind in ("request", "legacy"):
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
            accepted = estimator.calibrate_at_stop(timestamp_ms)
            replay_events.append({
                "t": timestamp_ms,
                "event": "parking_calibration_requested",
                "accepted": accepted,
                "legacyEvent": parking_kind == "legacy",
            })
            if parking_kind == "legacy":
                has_gnss_anchor = True
                latest_gnss_speed_mps = 0.0
            continue
        if running and parking_kind == "success":
            has_gnss_anchor = True
            latest_gnss_speed_mps = 0.0
            replay_events.append({"t": timestamp_ms, "event": "parking_calibration_success"})
            continue
        if running and parking_kind == "rejected":
            replay_events.append({"t": timestamp_ms, "event": "parking_calibration_rejected"})
            continue
        if event_matches(event, "\u5165\u96a7"):
            tunnel_inside = True
            if running:
                replay_events.append({"t": timestamp_ms, "event": "tunnel_enter"})
            continue
        if event_matches(event, "\u51fa\u96a7"):
            tunnel_inside = False
            if running:
                replay_events.append({"t": timestamp_ms, "event": "tunnel_exit"})
            continue
        if running and event_matches(event, stop_event):
            end_replay_run(timestamp_ms, "stop")
            continue

        if running and row.get("recordType") == "location":
            speed = parse_number(row, "locationSpeedMps")
            speed_accuracy = parse_number(row, "locationSpeedAccuracyMps")
            source_type = row.get("locationSourceType")
            if speed is not None:
                latest_gnss_speed_mps = max(0.0, speed)
            latest_gnss_speed_accuracy_mps = speed_accuracy if speed_accuracy is not None else -1.0
            in_vibration = estimator.motion_state in (
                MotionState.STRONG_VIBRATION,
                MotionState.CONDUCTION_VIBRATION,
            )
            try:
                source_is_phone_anchor = source_type is not None and int(source_type) in (1, 4)
            except (TypeError, ValueError):
                source_is_phone_anchor = False
            if (
                speed is not None
                and speed_accuracy is not None
                and speed_accuracy > 0
                and source_is_phone_anchor
                and not tunnel_inside
                and not in_vibration
            ):
                has_gnss_anchor = True
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
            current_run_id = None
            continue
        parking_result = estimator.consume_parking_calibration_result()
        if parking_result > 0:
            has_gnss_anchor = True
            latest_gnss_speed_mps = 0.0
            replay_events.append({
                "t": timestamp_ms,
                "event": "parking_calibration_replay_success",
                "speedKmh": output.speed_kmh,
            })
        elif parking_result < 0:
            replay_events.append({
                "t": timestamp_ms,
                "event": "parking_calibration_replay_rejected",
                "speedKmh": output.speed_kmh,
            })
        outputs.append({
            "timestampMs": output.timestamp_ms,
            "sourceRowIndex": source_row_index,
            "recordSeq": row.get("recordSeq"),
            "sensorTimestamp": output.sensor_timestamp,
            "tSec": (output.timestamp_ms - first_timestamp) / 1000.0,
            "speedKmh": output.speed_kmh,
            "recordedSpeedKmh": row.get("estimatedSpeedKmh"),
            "confidence": output.confidence,
            "sampleConfidence": output.sample_confidence,
            "measurementRunId": current_run_id,
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
        estimator.set_pure_mode(
            1 if (
                not has_gnss_anchor
                or latest_gnss_speed_accuracy_mps <= 0
                or latest_gnss_speed_mps < latest_gnss_speed_accuracy_mps
            ) else 0
        )

    return outputs, replay_events


def build_output_runs(
    outputs: List[Dict[str, Any]],
    speed_key: str = "speedKmh",
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in outputs:
        run_value = item.get("measurementRunId")
        run_id = str(run_value) if run_value not in (None, "") else "__legacy_single_run__"
        grouped.setdefault(run_id, []).append(item)

    result: Dict[str, Dict[str, Any]] = {}
    for run_id, items in grouped.items():
        processing_items = sorted(
            items,
            key=lambda item: int(item.get("sourceRowIndex", item["timestampMs"])),
        )
        time_items = sorted(items, key=lambda item: int(item["timestampMs"]))
        result[run_id] = {
            "runId": run_id,
            "items": processing_items,
            "timeItems": time_items,
            "timestamps": [int(item["timestampMs"]) for item in time_items],
            "speeds": [float(item[speed_key]) for item in time_items],
            "sourceStart": (
                min(int(item["sourceRowIndex"]) for item in items)
                if all("sourceRowIndex" in item for item in items)
                else None
            ),
            "sourceEnd": (
                max(int(item["sourceRowIndex"]) for item in items)
                if all("sourceRowIndex" in item for item in items)
                else None
            ),
        }
    return result


def select_output_run_for_row(
    row: Dict[str, Any],
    output_runs: Dict[str, Dict[str, Any]],
    source_row_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if row.get("measurementActive") is False:
        return None
    recorded_run_id = row.get("measurementRunId")
    if recorded_run_id not in (None, ""):
        return output_runs.get(str(recorded_run_id))
    if source_row_index is not None:
        source_candidates = [
            series for series in output_runs.values()
            if series["sourceStart"] is not None
            and series["sourceEnd"] is not None
            and int(series["sourceStart"]) <= source_row_index <= int(series["sourceEnd"])
        ]
        if len(source_candidates) == 1:
            return source_candidates[0]
        future_runs = [
            series for series in output_runs.values()
            if series["sourceStart"] is not None
            and int(series["sourceStart"]) >= source_row_index
        ]
        if future_runs:
            return min(future_runs, key=lambda series: int(series["sourceStart"]))
        past_runs = [
            series for series in output_runs.values()
            if series["sourceEnd"] is not None
            and int(series["sourceEnd"]) <= source_row_index
        ]
        if past_runs:
            return max(past_runs, key=lambda series: int(series["sourceEnd"]))
    try:
        callback_timestamp_ms = int(row["timestampMs"])
    except (KeyError, TypeError, ValueError):
        return None
    candidates = [
        series for series in output_runs.values()
        if series["timestamps"]
        and series["timestamps"][0] <= callback_timestamp_ms <= series["timestamps"][-1]
    ]
    return candidates[0] if len(candidates) == 1 else None


def location_row_is_comparable(row: Dict[str, Any]) -> bool:
    if row.get("recordType") != "location" or parse_number(row, "locationSpeedMps") is None:
        return False
    source_type = row.get("locationSourceType")
    if source_type is not None:
        try:
            if int(source_type) not in (1, 4):
                return False
        except (TypeError, ValueError):
            return False
    speed_accuracy = parse_number(row, "locationSpeedAccuracyMps")
    return speed_accuracy is None or speed_accuracy > 0


def compare_with_location(rows: List[Dict[str, Any]], outputs: List[Dict[str, Any]], lag_ms: int = 0, speed_key: str = "speedKmh") -> Dict[str, Any]:
    if not outputs:
        return {}
    output_runs = build_output_runs(outputs, speed_key)
    diffs: List[float] = []
    moving_diffs: List[float] = []
    paired = 0
    for source_row_index, row in enumerate(rows):
        if not location_row_is_comparable(row):
            continue
        series = select_output_run_for_row(row, output_runs, source_row_index)
        if series is None:
            continue
        location_timestamp_ms = int(row.get("locationTimeMs") or row["timestampMs"])
        target_timestamp_ms = location_timestamp_ms - lag_ms
        estimated_speed_kmh = interpolate_output_speed_from_series(
            series["timestamps"], series["speeds"], target_timestamp_ms
        )
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
    output_runs = build_output_runs(outputs, speed_key)
    next_calibration_index = 0
    for series in sorted(
        output_runs.values(),
        key=lambda value: value["timestamps"][0] if value["timestamps"] else 0,
    ):
        samples = series["timeItems"]
        sec_since_cals = [float(item.get("secondsSinceCalibration", 0)) for item in samples]
        cal_indices_f = [float(next_calibration_index)] * len(samples)
        current_cal = next_calibration_index
        for i in range(1, len(samples)):
            if sec_since_cals[i - 1] - sec_since_cals[i] > 1.0:
                current_cal += 1
            cal_indices_f[i] = float(current_cal)
        series["secondsSinceCalibration"] = sec_since_cals
        series["calibrationIndices"] = cal_indices_f
        next_calibration_index = current_cal + 1

    bucket_diffs: Dict[str, List[float]] = {}
    bucket_moving_diffs: Dict[str, List[float]] = {}

    for source_row_index, row in enumerate(rows):
        if not location_row_is_comparable(row):
            continue
        series = select_output_run_for_row(row, output_runs, source_row_index)
        if series is None:
            continue
        location_timestamp_ms = int(row.get("locationTimeMs") or row["timestampMs"])
        target_timestamp_ms = location_timestamp_ms - lag_ms
        estimated_speed_kmh = interpolate_output_speed_from_series(
            series["timestamps"], series["speeds"], target_timestamp_ms
        )
        if estimated_speed_kmh is None:
            continue

        sec_since_cal = interpolate_output_speed_from_series(
            series["timestamps"], series["secondsSinceCalibration"], target_timestamp_ms
        )
        if sec_since_cal is None or sec_since_cal > 300.0:
            continue

        cal_idx_f = interpolate_output_speed_from_series(
            series["timestamps"], series["calibrationIndices"], target_timestamp_ms
        )
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

    output_runs = build_output_runs(outputs)
    uses_source_order = all("sourceRowIndex" in item for item in outputs)

    def item_order(item: Dict[str, Any]) -> int:
        return int(item["sourceRowIndex"] if uses_source_order else item["timestampMs"])

    def row_order(row_index: int, timestamp_ms: int) -> int:
        return row_index if uses_source_order else timestamp_ms

    tunnel_inside = False
    latest_gnss_speed_mps = 0.0
    latest_gnss_accuracy_mps = -1.0
    active_run_id: Optional[str] = None
    anchors_by_run: Dict[str, List[Tuple[int, float, float]]] = {
        run_id: [] for run_id in output_runs
    }
    reliability_by_run: Dict[str, List[Tuple[int, bool]]] = {
        run_id: [] for run_id in output_runs
    }
    has_anchor_by_run: Dict[str, bool] = {run_id: False for run_id in output_runs}
    last_anchor_ts_by_run: Dict[str, int] = {
        run_id: -999999999 for run_id in output_runs
    }
    history_resets_by_run: Dict[str, List[Tuple[int, int]]] = {
        run_id: [] for run_id in output_runs
    }

    def resolve_run_id(
        row: Dict[str, Any],
        timestamp_ms: int,
        source_row_index: int,
        prefer_active: bool = True,
    ) -> Optional[str]:
        recorded_run_id = row.get("measurementRunId")
        if recorded_run_id not in (None, ""):
            candidate = str(recorded_run_id)
            return candidate if candidate in output_runs else None
        if prefer_active and active_run_id in output_runs:
            return active_run_id
        selected = select_output_run_for_row(
            row,
            output_runs,
            source_row_index if uses_source_order else None,
        )
        if selected is not None:
            return str(selected["runId"])
        if not prefer_active and active_run_id in output_runs:
            return active_run_id
        future_runs = [
            series for series in output_runs.values()
            if series["timestamps"] and series["timestamps"][0] >= timestamp_ms
        ]
        if not future_runs:
            return None
        future_runs.sort(key=lambda series: series["timestamps"][0])
        return str(future_runs[0]["runId"])

    def activate_run(run_id: Optional[str], callback_order: int) -> None:
        nonlocal active_run_id
        if run_id is None or run_id == active_run_id:
            return
        active_run_id = run_id
        has_anchor_by_run[run_id] = False
        reliability_by_run[run_id].append((callback_order, False))

    def inertial_history_value(
        run_id: str,
        callback_ms: int,
        callback_order: int,
    ) -> float:
        # Index.ets keeps only the five most recent sensor callbacks and chooses the
        # one nearest callbackTime-40 ms. Future sensor rows must never participate.
        items = output_runs[run_id]["items"]
        reset_candidates = [
            reset for reset in history_resets_by_run[run_id]
            if reset[0] <= callback_order
        ]
        history_start_order, history_start_ms = (
            max(reset_candidates, key=lambda reset: reset[0])
            if reset_candidates
            else (-999999999, -999999999)
        )
        reset_frame: Optional[Dict[str, Any]] = None
        if reset_candidates:
            same_timestamp_frames = [
                item for item in items
                if int(item["timestampMs"]) == history_start_ms
                and (not uses_source_order or item_order(item) < history_start_order)
            ]
            if same_timestamp_frames:
                reset_frame = same_timestamp_frames[-1]
        available = [
            item for item in items
            if item_order(item) <= callback_order
            and (
                not reset_candidates
                or item is reset_frame
                or (
                    uses_source_order
                    and item_order(item) > history_start_order
                )
                or (
                    not uses_source_order
                    and int(item["timestampMs"]) > history_start_ms
                )
            )
        ]
        if not available:
            return 0.0
        recent = available[-5:]
        target_ms = callback_ms - PHONE_GNSS_ANCHOR_LAG_MS
        best = min(recent, key=lambda item: abs(int(item["timestampMs"]) - target_ms))
        return float(best["speedKmh"]) / 3.6

    for source_row_index, row in enumerate(rows):
        t = int(row.get("timestampMs", 0))
        callback_order = row_order(source_row_index, t)
        evt = str(row.get("event") or "")
        notes = str(row.get("notes") or "")
        parking_kind = parking_calibration_event_kind(evt)
        is_start_event = event_matches(evt, "\u5f00\u59cb\u6d4b\u901f") or notes == "measurement started"
        is_already_running_event = event_matches(evt, "\u6d4b\u901f\u5df2\u5728\u8fd0\u884c")
        if is_start_event:
            # An explicit start is a strong boundary even when a legacy row lacks a
            # run id. Resolve it from source order instead of inheriting the old run.
            active_run_id = None
            activate_run(
                resolve_run_id(row, t, source_row_index, prefer_active=False),
                callback_order,
            )
        elif is_already_running_event:
            activate_run(resolve_run_id(row, t, source_row_index), callback_order)

        if row.get("recordType") == "sensor":
            if row.get("measurementActive") is False:
                # Match replay(): an inactive sensor row terminates the current run
                # and must not itself infer the next one.
                active_run_id = None
                continue

            recorded_sensor_run_id = row.get("measurementRunId")
            if recorded_sensor_run_id not in (None, ""):
                sensor_run_text = str(recorded_sensor_run_id)
                if sensor_run_text != active_run_id:
                    # A changed explicit id is a strong boundary. If the run has no
                    # replayable samples, clear the old run rather than retaining it.
                    active_run_id = None
                    activate_run(
                        sensor_run_text if sensor_run_text in output_runs else None,
                        callback_order,
                    )
            elif should_infer_measurement_active(row):
                # Source order identifies the inferred run even when wall-clock
                # ranges overlap after a clock rollback.
                activate_run(
                    resolve_run_id(row, t, source_row_index, prefer_active=False),
                    callback_order,
                )
        if "\u5165\u96a7" in evt or "\u5165\u96a7" in notes:
            tunnel_inside = True
        elif "\u51fa\u96a7" in evt or "\u51fa\u96a7" in notes:
            tunnel_inside = False
        if parking_kind in ("success", "legacy"):
            run_id = resolve_run_id(row, t, source_row_index)
            if run_id is not None:
                # The app makes a successful parking result a zero anchor at the
                # result callback, not at the earlier request time.
                anchors_by_run[run_id].append((callback_order, 0.0, 0.0))
                history_resets_by_run[run_id].append((callback_order, t))
                has_anchor_by_run[run_id] = True
                latest_gnss_speed_mps = 0.0
                reliability_by_run[run_id].append((
                    callback_order,
                    latest_gnss_accuracy_mps > 0
                    and latest_gnss_speed_mps >= latest_gnss_accuracy_mps,
                ))
        if event_matches(evt, "\u505c\u6b62\u6d4b\u901f"):
            active_run_id = None
        if row.get("recordType") == "event" or parking_kind is not None:
            continue

        if row.get("recordType") != "location":
            continue

        spd_mps = parse_number(row, "locationSpeedMps")
        src = row.get("locationSourceType")
        acc = parse_number(row, "locationSpeedAccuracyMps")
        if spd_mps is None:
            continue
        latest_gnss_speed_mps = max(0.0, spd_mps)
        latest_gnss_accuracy_mps = acc if acc is not None else -1.0
        run_id = resolve_run_id(row, t, source_row_index)
        if run_id is None or row.get("measurementActive") is False:
            continue
        reliability_by_run[run_id].append((
            callback_order,
            has_anchor_by_run[run_id]
            and latest_gnss_accuracy_mps > 0
            and latest_gnss_speed_mps >= latest_gnss_accuracy_mps,
        ))

        if tunnel_inside or acc is None or acc <= 0 or src is None:
            continue
        try:
            source_type = int(src)
        except (TypeError, ValueError):
            continue
        if source_type not in (1, 4):
            continue

        ms = motion_state_at_or_before(
            output_runs[run_id]["items"], callback_order, uses_source_order
        )
        if ms in ("strong_vibration", "conduction_vibration"):
            continue

        if anchor_interval_ms > 0 and t - last_anchor_ts_by_run[run_id] < anchor_interval_ms:
            continue

        last_anchor_ts_by_run[run_id] = t
        inertial_at_anchor = inertial_history_value(run_id, t, callback_order)
        anchors_by_run[run_id].append((callback_order, spd_mps, inertial_at_anchor))
        has_anchor_by_run[run_id] = True
        reliability_by_run[run_id].append((
            callback_order,
            latest_gnss_accuracy_mps > 0
            and
            latest_gnss_speed_mps >= latest_gnss_accuracy_mps,
        ))

    result: List[Dict[str, Any]] = []
    for run_id, series in output_runs.items():
        anchors = sorted(anchors_by_run[run_id], key=lambda anchor: anchor[0])
        reliability_changes = sorted(
            reliability_by_run[run_id], key=lambda change: change[0]
        )
        anchor_idx = 0
        reliability_idx = 0
        last_anchor_spd = 0.0
        last_anchor_inertial = 0.0
        gnss_reliable = False
        for item in series["items"]:
            t = int(item["timestampMs"])
            output_order = item_order(item)
            while anchor_idx < len(anchors) and anchors[anchor_idx][0] <= output_order:
                _, last_anchor_spd, last_anchor_inertial = anchors[anchor_idx]
                anchor_idx += 1
            while (
                reliability_idx < len(reliability_changes)
                and reliability_changes[reliability_idx][0] <= output_order
            ):
                _, gnss_reliable = reliability_changes[reliability_idx]
                reliability_idx += 1
            inertial_mps = float(item["speedKmh"]) / 3.6
            anchored_raw_mps = max(
                0.0, last_anchor_spd + (inertial_mps - last_anchor_inertial)
            )
            pure_speed_kmh = float(item["speedKmh"])
            if not gnss_reliable:
                anchored_speed_kmh = pure_speed_kmh
            elif pure_zero:
                anchored_speed_kmh = anchored_raw_mps * 3.6
            else:
                conf = float(item["confidence"])
                if anchor_power != 1.0:
                    conf = conf ** anchor_power
                anchored_speed_kmh = (
                    conf * pure_speed_kmh
                    + (1.0 - conf) * anchored_raw_mps * 3.6
                )
            result.append({
                **item,
                "anchoredSpeedKmh": anchored_speed_kmh,
                "anchorSpeedKmh": last_anchor_spd * 3.6,
                "inertialDeltaKmh": (inertial_mps - last_anchor_inertial) * 3.6,
                "anchorApplied": gnss_reliable,
                "gnssReliable": gnss_reliable,
            })
    return sorted(result, key=item_order)


def motion_state_at_or_before(
    output_items: List[Dict[str, Any]],
    target_order: int,
    uses_source_order: bool = False,
) -> str:
    if not output_items:
        return "unknown"
    eligible = [
        item for item in output_items
        if int(item.get("sourceRowIndex", item["timestampMs"])) <= target_order
    ] if uses_source_order else [
        item for item in output_items if int(item["timestampMs"]) <= target_order
    ]
    if not eligible:
        return "calibrating"
    return str(eligible[-1].get("motionState", "unknown"))


def recorded_estimator_abs_diffs(
    rows: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
) -> Tuple[List[float], str]:
    """Compare replayed pure inertia only with an explicitly pure recorded field.

    Schema 13's ``estimatedSpeedKmh`` contains the GNSS-anchored display value,
    despite its historical name. Comparing that value with pure replay output
    creates false regressions, so those rows are reported as unavailable.
    """
    samples = sorted(outputs, key=lambda item: int(item["timestampMs"]))
    output_runs = build_output_runs(outputs)
    estimator_rows = [
        row for row in rows
        if row.get("recordType") == "estimator"
    ]
    pure_estimator_rows = [
        row for row in estimator_rows
        if parse_number(row, "pureInertialSpeedKmh") is not None
    ]
    if pure_estimator_rows:
        differences: List[float] = []
        for row in pure_estimator_rows:
            recorded = parse_number(row, "pureInertialSpeedKmh")
            if recorded is None:
                continue
            try:
                timestamp_ms = int(row["timestampMs"])
            except (KeyError, TypeError, ValueError):
                continue
            series = select_output_run_for_row(row, output_runs)
            if series is None:
                continue
            replayed: Optional[float] = None
            estimator_record_seq = parse_number(row, "recordSeq")
            if estimator_record_seq is not None:
                preceding = [
                    item for item in series["items"]
                    if parse_number(item, "recordSeq") is not None
                    and float(item["recordSeq"]) + 1 == estimator_record_seq
                    and int(item["timestampMs"]) == timestamp_ms
                ]
                if preceding:
                    matched = max(preceding, key=lambda item: float(item["recordSeq"]))
                    replayed = float(matched["speedKmh"])
            if replayed is None and estimator_record_seq is None:
                exact_indices = [
                    index for index, sample_timestamp in enumerate(series["timestamps"])
                    if sample_timestamp == timestamp_ms
                ]
                if len(exact_indices) == 1:
                    replayed = float(series["speeds"][exact_indices[0]])
            if replayed is not None:
                differences.append(abs(replayed - recorded))
        return (
            differences,
            "estimator_pure_inertial_rows_v14"
            if differences
            else "unavailable_no_exact_sensor_pair_v14",
        )

    if estimator_rows:
        return [], "unavailable_display_only_estimator_rows"

    legacy_differences = [
            abs(float(item["speedKmh"]) - float(item["recordedSpeedKmh"]))
            for item in samples
            if parse_number(item, "recordedSpeedKmh") is not None
    ]
    return (
        legacy_differences,
        "legacy_sensor_rows" if legacy_differences else "none",
    )


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
    recorded_pairs, recorded_pair_source = recorded_estimator_abs_diffs(rows, outputs)
    estimator_parity = replay_config_matches_app(replay_config)
    anchor_parity = (
        use_anchor_v2
        and pure_zero
        and math.isclose(anchor_power, 1.0, rel_tol=0.0, abs_tol=1e-12)
        and anchor_interval_ms == 0
    )
    speeds = [float(item["speedKmh"]) for item in outputs]
    confidences = [float(item["confidence"]) for item in outputs]
    states = {}
    for item in outputs:
        states[item["motionState"]] = states.get(item["motionState"], 0) + 1

    summary: Dict[str, Any] = {
        "algorithmVersion": ALGORITHM_VERSION,
        "replayConfig": {
            **replay_config,
            "estimatorParity": estimator_parity,
            "anchorParity": anchor_parity,
            "appParity": estimator_parity and anchor_parity,
            "anchorOptions": {
                "enabled": use_anchor_v2,
                "pureZero": pure_zero,
                "anchorPower": anchor_power,
                "anchorIntervalMs": anchor_interval_ms,
                "comparisonGnssLagMs": gnss_lag_ms,
            },
            "note": "appParity requires app estimator defaults plus --anchor-v2 --pure-zero and zero anchor interval; GNSS lag only shifts comparison targets.",
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
            "available": True,
            "count": len(recorded_pairs),
            "maeKmh": sum(recorded_pairs) / len(recorded_pairs),
            "maxAbsKmh": max(recorded_pairs),
            "source": recorded_pair_source,
        }
    elif recorded_pair_source.startswith("unavailable_"):
        summary["recordedEstimatorDiff"] = {
            "available": False,
            "count": 0,
            "source": recorded_pair_source,
            "note": (
                "schema <=13 stores anchored display speed only"
                if recorded_pair_source == "unavailable_display_only_estimator_rows"
                else "schema v14 estimator row has no exact preceding sensor-frame pair"
            ),
        }
    if use_anchor_v2:
        anchored = build_anchored_outputs_v2(rows, outputs, anchor_power, pure_zero, anchor_interval_ms)
        if anchored:
            anchored_speeds = [float(item["anchoredSpeedKmh"]) for item in anchored]
            summary["anchorSpeed"] = {
                "minKmh": min(anchored_speeds),
                "medianKmh": statistics.median(anchored_speeds),
                "p90Kmh": quantile(anchored_speeds, 0.9),
                "maxKmh": max(anchored_speeds),
                "appliedSamples": sum(1 for item in anchored if item.get("anchorApplied") is True),
            }
        else:
            summary["anchorSpeed"] = {}
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
    if set(config) != set(APP_PARITY_CONFIG):
        return False
    for key, expected in APP_PARITY_CONFIG.items():
        value = config[key]
        if isinstance(expected, float):
            if isinstance(value, bool):
                return False
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(numeric_value) or abs(numeric_value - expected) > 1e-9:
                return False
        elif value != expected:
            return False
    return True


def validate_cli_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    for name, value in vars(args).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            parser.error(f"--{name.replace('_', '-')} must be finite")

    allowed_negative = {"brake_forward", "gyro_gravity_sign", "gnss_lag_ms"}
    for name, value in vars(args).items():
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and name not in allowed_negative
            and value < 0
        ):
            parser.error(f"--{name.replace('_', '-')} must be non-negative")

    strictly_positive = {
        "accel_clip_ceiling",
        "dt_fallback",
        "dt_clamp_lo",
        "dt_clamp_hi",
        "calibration_duration_ms",
        "axis_window_min_frames",
        "axis_window_max_frames",
        "axis_window_max_ms",
        "confidence_gyro_divisor",
    }
    for name in strictly_positive:
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")

    for name in (
        "low_pass_alpha",
        "axis_mix_locked",
        "axis_mix_unlocked",
        "axis_reset_alignment",
        "confidence_clamp_lo",
        "confidence_clamp_hi",
    ):
        value = getattr(args, name)
        if value < 0 or value > 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")

    if args.dt_clamp_lo > args.dt_clamp_hi:
        parser.error("--dt-clamp-lo must not exceed --dt-clamp-hi")
    if args.axis_window_min_frames > args.axis_window_max_frames:
        parser.error("--axis-window-min-frames must not exceed --axis-window-max-frames")
    if args.confidence_clamp_lo > args.confidence_clamp_hi:
        parser.error("--confidence-clamp-lo must not exceed --confidence-clamp-hi")
    if args.brake_forward > 0:
        parser.error("--brake-forward must be zero or negative")
    if args.gyro_gravity_sign == 0:
        parser.error("--gyro-gravity-sign must be non-zero")


def paths_refer_to_same_file(first: Path, second: Path) -> bool:
    try:
        if first.exists() and second.exists():
            return first.samefile(second)
    except OSError:
        pass
    return first.resolve(strict=False) == second.resolve(strict=False)


def write_jsonl_atomic(path: Path, outputs: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            for item in outputs:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


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
    parser.add_argument("--anchor-v2", action="store_true", help="Enable anchor analysis; add --pure-zero to match the ArkTS display path.")
    parser.add_argument("--anchor-power", type=float, default=1.0, help="Confidence exponent for anchor v2 blend. >1.0 skews toward anchor.")
    parser.add_argument("--pure-zero", action="store_true", help="Anchor v2 without blend: pure anchor+delta. Matches phone.")
    parser.add_argument("--gnss-lag-ms", type=int, default=0, help="GNSS timestamp offset compensation in ms. Positive means GNSS data is delayed relative to sensor (negative lag in comparison sense).")
    parser.add_argument("--anchor-interval-ms", type=int, default=0, help="Minimum ms between anchors. 0 = every valid GNSS point (default phone behavior). Set to 5000+ to test inertial drift between sparse anchors.")
    args = parser.parse_args()
    validate_cli_args(args, parser)
    if args.out is not None and paths_refer_to_same_file(args.jsonl, args.out):
        parser.error("--out must not refer to the input JSONL file")
    curve_negative_scale = args.curve_negative_scale
    low_confidence_negative_scale = args.low_confidence_negative_scale

    try:
        rows = read_jsonl(args.jsonl)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not rows:
        parser.error("input JSONL contains no records")

    estimator_parameter_names = {
        name for name in inspect.signature(SpeedEstimator.__init__).parameters
        if name != "self"
    }
    estimator_kwargs = {
        key: value for key, value in vars(args).items()
        if key in estimator_parameter_names
    }
    estimator_kwargs["curve_negative_scale"] = curve_negative_scale
    estimator_kwargs["low_confidence_negative_scale"] = low_confidence_negative_scale
    estimator_kwargs["use_vibration_guard"] = not args.no_vibration_guard

    replay_config = {
        **estimator_kwargs,
        "strict_start": not args.no_strict_start,
        "infer_start_from_sensor": not args.no_infer_start,
        "adaptive_gravity": args.adaptive_gravity,
    }

    outputs, events = replay(
        rows,
        strict_start=not args.no_strict_start,
        infer_start_from_sensor=not args.no_infer_start,
        adaptive_gravity=args.adaptive_gravity,
        **estimator_kwargs,
    )
    if not outputs:
        parser.error("replay produced no sensor samples")
    summary = summarize(rows, outputs, events, replay_config, use_anchor_v2=getattr(args, "anchor_v2", False), anchor_power=args.anchor_power, pure_zero=args.pure_zero, gnss_lag_ms=args.gnss_lag_ms, anchor_interval_ms=args.anchor_interval_ms)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out is not None:
        write_jsonl_atomic(args.out, outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
