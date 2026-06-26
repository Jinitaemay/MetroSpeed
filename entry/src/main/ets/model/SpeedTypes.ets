export interface Vector3 {
  x: number;
  y: number;
  z: number;
}

export enum MotionState {
  Idle,
  Stopped,
  Calibrating,
  StraightAcceleration,
  Cruise,
  Braking,
  Curve,
  LowConfidence,
  StrongVibration,
  ConductionVibration
}

export interface SpeedSample {
  timestampMs: number;
  rawAcceleration: Vector3;
  filteredAcceleration: Vector3;
  speedMps: number;
  speedKmh: number;
  confidence: number;
  motionState: MotionState;
}

export interface RunStats {
  currentSpeedKmh: number;
  maxSpeedKmh: number;
  averageSpeedKmh: number;
  elapsedMs: number;
  calibrationCount: number;
  confidence: number;
  motionState: MotionState;
  statusText: string;
  calibrationRejected: boolean;
}

export interface CalibrationState {
  gravity: Vector3;
  mainAxis: Vector3;
}

export interface SensorFrame {
  timestampMs: number;
  sensorTimestamp: number | null;
  acceleration: Vector3;
  gyroscope?: Vector3;
  gyroscopeTimestamp?: number | null;
}

export interface EstimatorFrame {
  sample: SpeedSample;
  stats: RunStats;
  calibration: CalibrationState;
}

export function motionStateText(state: MotionState): string {
  switch (state) {
    case MotionState.Idle:
      return '静止';
    case MotionState.Stopped:
      return '已停止';
    case MotionState.Calibrating:
      return '校准中';
    case MotionState.StraightAcceleration:
      return '直线加速';
    case MotionState.Cruise:
      return '直线匀速';
    case MotionState.Braking:
      return '减速';
    case MotionState.Curve:
      return '弯道中';
    case MotionState.LowConfidence:
      return '低置信度';
    case MotionState.StrongVibration:
      return '强振动';
    case MotionState.ConductionVibration:
      return '传导振动';
    default:
      return '未知';
  }
}

export interface SpeedHistoryEntry {
  t: number;
  v: number;
}

export function emptyVector(): Vector3 {
  return { x: 0, y: 0, z: 0 };
}
