import sensor from '@ohos.sensor';
import { BusinessError } from '@ohos.base';
import { systemDateTime } from '@kit.BasicServicesKit';
import { SensorCallbackSample, SensorFrame, Vector3, Quaternion } from './SpeedTypes';

type SensorFrameCallback = (frame: SensorFrame) => void;
type SensorStatusCallback = (message: string) => void;
type SensorSampleCallback = (sample: SensorCallbackSample) => void;
export type CoreSensorFailureSensor = 'accelerometer' | 'gyroscope';
export type CoreSensorFailurePhase = 'initial' | 'stale';
export interface CoreSensorFatalError {
  sensor: CoreSensorFailureSensor;
  phase: CoreSensorFailurePhase;
  timestampMs: number;
  message: string;
}
export type CoreSensorFatalCallback = (error: CoreSensorFatalError) => void;
const HIGH_RATE_INTERVAL_NS = 10000000;
const HIGH_RATE_HZ = 1000000000 / HIGH_RATE_INTERVAL_NS;
const MAX_SENSOR_AGE_NS = 60000000;
const GYROSCOPE_FRESHNESS_TIMEOUT_NS = 1000000000;
const GYROSCOPE_WATCHDOG_MAX_ACCEL_GAP_NS = 200000000;
const CORE_SENSOR_WATCHDOG_INTERVAL_MS = 250;
const ACCELEROMETER_INITIAL_TIMEOUT_MS = 3000;
const ACCELEROMETER_STALE_TIMEOUT_MS = 2000;
const ACCELEROMETER_STALL_CONFIRM_MS = 500;

function isFiniteVector3(x: number, y: number, z: number): boolean {
  return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z);
}

function isFiniteSensorTimestamp(timestamp: number): boolean {
  return Number.isFinite(timestamp);
}

export class SensorController {
  private frameCallback?: SensorFrameCallback;
  private statusCallback?: SensorStatusCallback;
  private sensorSampleCallback?: SensorSampleCallback;
  private fatalCallback?: CoreSensorFatalCallback;

  private latestGyroscope?: Vector3;
  private latestGyroscopeTimestamp: number | null = null;
  private gyroscopeAvailable: boolean = false;

  private latestRotationVector?: Quaternion;
  private latestRotationVectorTimestamp: number | null = null;
  private rotationVectorAvailable: boolean = false;

  private latestMagneticField?: Vector3;
  private latestMagneticFieldTimestamp: number | null = null;
  private magneticFieldAvailable: boolean = false;

  private uncalibratedGyroscopeAvailable: boolean = false;
  private uncalibratedMagneticFieldAvailable: boolean = false;

  private accelerometerSubscribed: boolean = false;
  private gyroscopeSubscribed: boolean = false;
  private rotationVectorSubscribed: boolean = false;
  private magneticFieldSubscribed: boolean = false;
  private uncalibratedGyroscopeSubscribed: boolean = false;
  private uncalibratedMagneticFieldSubscribed: boolean = false;

  private running: boolean = false;
  private requireGyroscope: boolean = false;
  private coreFatalReported: boolean = false;
  private optionalGyroscopeDegraded: boolean = false;
  private gyroscopeFreshFrameSeen: boolean = false;
  private accelerometerFrameSeen: boolean = false;
  private coreStartMonotonicMs: number = 0;
  private accelerometerStallSuspectSinceMonotonicMs: number = 0;
  private coreSensorWatchdogTimer: number = -1;
  private lastAccelerationTimestamp: number | null = null;
  private lastAccelerationMonotonicMs: number = 0;
  private gyroscopeMissingSinceTimestamp: number | null = null;
  private gyroscopeMissingSinceMonotonicMs: number = 0;
  private lastMonotonicMs: number = 0;
  private lastMonotonicFallbackWallMs: number = 0;
  private subscriptionGeneration: number = 0;
  private auxiliaryGeneration: number = 0;
  private auxiliaryCleanupPending: boolean = false;

  start(
    frameCallback: SensorFrameCallback,
    statusCallback: SensorStatusCallback,
    sensorSampleCallback?: SensorSampleCallback,
    requireGyroscope: boolean = true,
    fatalCallback?: CoreSensorFatalCallback
  ): boolean {
    this.stop();
    this.frameCallback = frameCallback;
    this.statusCallback = statusCallback;
    this.sensorSampleCallback = sensorSampleCallback;
    this.fatalCallback = fatalCallback;

    if (this.accelerometerSubscribed || this.gyroscopeSubscribed) {
      this.emitStatus('上次核心传感器退订失败，请重试');
      return false;
    }
    if (this.auxiliaryCleanupPending) {
      this.emitStatus('上次辅助传感器退订失败，仍启动核心传感器');
    }

    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;
    this.latestMagneticField = undefined;
    this.latestMagneticFieldTimestamp = null;
    this.magneticFieldAvailable = false;
    this.uncalibratedGyroscopeAvailable = false;
    this.uncalibratedMagneticFieldAvailable = false;
    this.requireGyroscope = requireGyroscope;
    this.coreFatalReported = false;
    this.optionalGyroscopeDegraded = false;
    this.gyroscopeFreshFrameSeen = false;
    this.accelerometerFrameSeen = false;
    this.coreStartMonotonicMs = this.readMonotonicNowMs();
    this.accelerometerStallSuspectSinceMonotonicMs = 0;
    this.lastAccelerationTimestamp = null;
    this.lastAccelerationMonotonicMs = 0;
    this.clearGyroscopeMissingWindow();

    this.running = true;

    const gyroscopeStarted = this.subscribeGyroscope();
    if (!gyroscopeStarted && requireGyroscope) {
      this.stop();
      return false;
    }

    const accelerationStarted = this.subscribeAcceleration();
    if (!accelerationStarted) {
      this.stop();
      return false;
    }

    this.scheduleCoreSensorWatchdog(this.subscriptionGeneration);
    this.emitActiveSensorStatus(false);

    return true;
  }

  get isGyroscopeAvailable(): boolean {
    return this.gyroscopeAvailable;
  }

  startAuxiliarySensors(): void {
    if (this.auxiliaryCleanupPending) {
      let cleanupSucceeded = true;
      if (!this.safeOffRotationVector()) cleanupSucceeded = false;
      if (!this.safeOffMagneticField()) cleanupSucceeded = false;
      if (!this.safeOffUncalibratedGyroscope()) cleanupSucceeded = false;
      if (!this.safeOffUncalibratedMagneticField()) cleanupSucceeded = false;
      this.auxiliaryCleanupPending = !cleanupSucceeded;
      if (!cleanupSucceeded) {
        this.emitStatus('上次辅助传感器退订失败，仍记录核心传感器');
        return;
      }
    }
    if (!this.rotationVectorSubscribed) {
      this.subscribeRotationVector();
    }
    if (!this.magneticFieldSubscribed) {
      this.subscribeMagneticField();
    }
    if (!this.uncalibratedGyroscopeSubscribed) {
      this.subscribeUncalibratedGyroscope();
    }
    if (!this.uncalibratedMagneticFieldSubscribed) {
      this.subscribeUncalibratedMagneticField();
    }

    this.emitActiveSensorStatus(true);
  }

  stopAuxiliarySensors(): boolean {
    this.auxiliaryGeneration += 1;
    let cleanupSucceeded = true;
    if (!this.safeOffRotationVector()) cleanupSucceeded = false;
    if (!this.safeOffMagneticField()) cleanupSucceeded = false;
    if (!this.safeOffUncalibratedGyroscope()) cleanupSucceeded = false;
    if (!this.safeOffUncalibratedMagneticField()) cleanupSucceeded = false;
    this.auxiliaryCleanupPending = !cleanupSucceeded;

    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;
    this.latestMagneticField = undefined;
    this.latestMagneticFieldTimestamp = null;
    this.magneticFieldAvailable = false;
    this.uncalibratedGyroscopeAvailable = false;
    this.uncalibratedMagneticFieldAvailable = false;

    if (this.running) {
      this.emitActiveSensorStatus(false);
    }
    return cleanupSucceeded;
  }

  stop(): boolean {
    this.subscriptionGeneration += 1;
    this.auxiliaryGeneration += 1;
    this.running = false;
    this.stopCoreSensorWatchdog();
    let coreCleanupSucceeded = true;
    if (!this.safeOffAccelerometer()) coreCleanupSucceeded = false;
    if (!this.safeOffGyroscope()) coreCleanupSucceeded = false;

    let auxiliaryCleanupSucceeded = true;
    if (!this.safeOffRotationVector()) auxiliaryCleanupSucceeded = false;
    if (!this.safeOffMagneticField()) auxiliaryCleanupSucceeded = false;
    if (!this.safeOffUncalibratedGyroscope()) auxiliaryCleanupSucceeded = false;
    if (!this.safeOffUncalibratedMagneticField()) auxiliaryCleanupSucceeded = false;
    this.auxiliaryCleanupPending = !auxiliaryCleanupSucceeded;

    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;
    this.latestMagneticField = undefined;
    this.latestMagneticFieldTimestamp = null;
    this.magneticFieldAvailable = false;
    this.uncalibratedGyroscopeAvailable = false;
    this.uncalibratedMagneticFieldAvailable = false;
    this.requireGyroscope = false;
    this.coreFatalReported = false;
    this.optionalGyroscopeDegraded = false;
    this.gyroscopeFreshFrameSeen = false;
    this.accelerometerFrameSeen = false;
    this.coreStartMonotonicMs = 0;
    this.accelerometerStallSuspectSinceMonotonicMs = 0;
    this.lastAccelerationTimestamp = null;
    this.lastAccelerationMonotonicMs = 0;
    this.clearGyroscopeMissingWindow();
    this.frameCallback = undefined;
    this.sensorSampleCallback = undefined;
    this.fatalCallback = undefined;
    return coreCleanupSucceeded && auxiliaryCleanupSucceeded;
  }

  private subscribeAcceleration(): boolean {
    const generation = this.subscriptionGeneration;
    try {
      sensor.on(sensor.SensorId.ACCELEROMETER, (data: sensor.AccelerometerResponse) => {
        if (!this.isCoreSubscriptionActive(generation)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) || !isFiniteVector3(data.x, data.y, data.z)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'accelerometer',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z
        });
        this.emitFrame({ x: data.x, y: data.y, z: data.z }, data.timestamp);
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.accelerometerSubscribed = true;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      this.accelerometerSubscribed = false;
      this.emitStatus(`无法读取加速度传感器：${err.message}`);
      return false;
    }
  }

  private subscribeGyroscope(): boolean {
    const generation = this.subscriptionGeneration;
    try {
      sensor.on(sensor.SensorId.GYROSCOPE, (data: sensor.GyroscopeResponse) => {
        if (!this.isCoreSubscriptionActive(generation)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) || !isFiniteVector3(data.x, data.y, data.z)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'gyroscope',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z
        });
        this.latestGyroscope = { x: data.x, y: data.y, z: data.z };
        this.latestGyroscopeTimestamp = data.timestamp;
        this.gyroscopeAvailable = true;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.gyroscopeSubscribed = true;
      this.gyroscopeAvailable = true;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      this.gyroscopeSubscribed = false;
      this.gyroscopeAvailable = false;
      this.optionalGyroscopeDegraded = !this.requireGyroscope;
      this.emitStatus(`陀螺仪不可用：${err.message}`);
      return false;
    }
  }

  private subscribeRotationVector(): void {
    const generation = this.subscriptionGeneration;
    const auxiliaryGeneration = this.auxiliaryGeneration;
    try {
      sensor.on(sensor.SensorId.ROTATION_VECTOR, (data: sensor.RotationVectorResponse) => {
        if (!this.isAuxiliarySubscriptionActive(generation, auxiliaryGeneration)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) ||
          !isFiniteVector3(data.x, data.y, data.z) || !Number.isFinite(data.w)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'rotation_vector',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z,
          w: data.w
        });
        this.latestRotationVector = { x: data.x, y: data.y, z: data.z, w: data.w };
        this.latestRotationVectorTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.rotationVectorSubscribed = true;
      this.rotationVectorAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.rotationVectorSubscribed = false;
      this.rotationVectorAvailable = false;
      console.info(`Rotation vector sensor unavailable: ${err.message}`);
    }
  }

  private subscribeMagneticField(): void {
    const generation = this.subscriptionGeneration;
    const auxiliaryGeneration = this.auxiliaryGeneration;
    try {
      sensor.on(sensor.SensorId.MAGNETIC_FIELD, (data: sensor.MagneticFieldResponse) => {
        if (!this.isAuxiliarySubscriptionActive(generation, auxiliaryGeneration)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) || !isFiniteVector3(data.x, data.y, data.z)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'magnetic_field',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z
        });
        this.latestMagneticField = { x: data.x, y: data.y, z: data.z };
        this.latestMagneticFieldTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.magneticFieldSubscribed = true;
      this.magneticFieldAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.magneticFieldSubscribed = false;
      this.magneticFieldAvailable = false;
      console.info(`Magnetic field sensor unavailable: ${err.message}`);
    }
  }

  private subscribeUncalibratedGyroscope(): void {
    const generation = this.subscriptionGeneration;
    const auxiliaryGeneration = this.auxiliaryGeneration;
    try {
      sensor.on(sensor.SensorId.GYROSCOPE_UNCALIBRATED, (data: sensor.GyroscopeUncalibratedResponse) => {
        if (!this.isAuxiliarySubscriptionActive(generation, auxiliaryGeneration)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) || !isFiniteVector3(data.x, data.y, data.z) ||
          !isFiniteVector3(data.biasX, data.biasY, data.biasZ)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'gyroscope_uncalibrated',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z,
          biasX: data.biasX,
          biasY: data.biasY,
          biasZ: data.biasZ
        });
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.uncalibratedGyroscopeSubscribed = true;
      this.uncalibratedGyroscopeAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.uncalibratedGyroscopeSubscribed = false;
      this.uncalibratedGyroscopeAvailable = false;
      console.info(`Uncalibrated gyroscope unavailable: ${err.message}`);
    }
  }

  private subscribeUncalibratedMagneticField(): void {
    const generation = this.subscriptionGeneration;
    const auxiliaryGeneration = this.auxiliaryGeneration;
    try {
      sensor.on(sensor.SensorId.MAGNETIC_FIELD_UNCALIBRATED, (data: sensor.MagneticFieldUncalibratedResponse) => {
        if (!this.isAuxiliarySubscriptionActive(generation, auxiliaryGeneration)) {
          return;
        }
        if (!isFiniteSensorTimestamp(data.timestamp) || !isFiniteVector3(data.x, data.y, data.z) ||
          !isFiniteVector3(data.biasX, data.biasY, data.biasZ)) {
          return;
        }
        this.emitSensorSample({
          sensorType: 'magnetic_field_uncalibrated',
          callbackTimestampMs: Date.now(),
          sensorTimestamp: data.timestamp,
          requestedIntervalNs: HIGH_RATE_INTERVAL_NS,
          x: data.x,
          y: data.y,
          z: data.z,
          biasX: data.biasX,
          biasY: data.biasY,
          biasZ: data.biasZ
        });
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.uncalibratedMagneticFieldSubscribed = true;
      this.uncalibratedMagneticFieldAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.uncalibratedMagneticFieldSubscribed = false;
      this.uncalibratedMagneticFieldAvailable = false;
      console.info(`Uncalibrated magnetic field unavailable: ${err.message}`);
    }
  }

  private emitSensorSample(sample: SensorCallbackSample): void {
    if (this.running && this.sensorSampleCallback) {
      this.sensorSampleCallback(sample);
    }
  }

  private isCoreSubscriptionActive(generation: number): boolean {
    return this.running && generation === this.subscriptionGeneration;
  }

  private isAuxiliarySubscriptionActive(generation: number, auxiliaryGeneration: number): boolean {
    return this.isCoreSubscriptionActive(generation) &&
      auxiliaryGeneration === this.auxiliaryGeneration;
  }

  private emitFrame(acceleration: Vector3, sensorTimestamp: number | null): void {
    if (!this.running || !this.frameCallback) {
      return;
    }
    if (!isFiniteVector3(acceleration.x, acceleration.y, acceleration.z) ||
      (sensorTimestamp !== null && !Number.isFinite(sensorTimestamp))) {
      return;
    }

    const wallNowMs = Date.now();
    const monotonicNowMs = this.readMonotonicNowMs();
    this.observeAccelerationContinuity(sensorTimestamp, monotonicNowMs);

    const frame: SensorFrame = {
      timestampMs: wallNowMs,
      sensorTimestamp,
      acceleration
    };

    const gyroAgeNs = sensorTimestamp !== null && this.latestGyroscopeTimestamp !== null
      ? sensorTimestamp - this.latestGyroscopeTimestamp
      : null;
    const gyroscopeFresh = this.gyroscopeAvailable &&
      gyroAgeNs !== null &&
      Math.abs(gyroAgeNs) <= MAX_SENSOR_AGE_NS;
    if (gyroscopeFresh) {
      frame.gyroscope = this.latestGyroscope;
      frame.gyroscopeTimestamp = this.latestGyroscopeTimestamp;
      this.gyroscopeFreshFrameSeen = true;
      const recoveredFromOptionalDegradation = this.optionalGyroscopeDegraded;
      this.optionalGyroscopeDegraded = false;
      this.clearGyroscopeMissingWindow();
      if (recoveredFromOptionalDegradation) {
        this.emitStatus('陀螺仪回调已恢复，研究记录恢复完整传感器采集');
      }
    } else {
      this.handleMissingGyroscope(sensorTimestamp, monotonicNowMs);
      if (this.requireGyroscope) {
        // Required-gyro measurement frames must never reach the estimator with
        // an implicit zero gyro. A short skew can recover; a sustained gap is
        // converted into one fatal status and deferred subscription cleanup.
        return;
      }
    }

    const rotVecAgeNs = sensorTimestamp !== null && this.latestRotationVectorTimestamp !== null
      ? sensorTimestamp - this.latestRotationVectorTimestamp
      : null;
    if (this.rotationVectorAvailable && rotVecAgeNs !== null && Math.abs(rotVecAgeNs) <= MAX_SENSOR_AGE_NS) {
      frame.rotationVector = this.latestRotationVector;
      frame.rotationVectorTimestamp = this.latestRotationVectorTimestamp;
    }

    const magAgeNs = sensorTimestamp !== null && this.latestMagneticFieldTimestamp !== null
      ? sensorTimestamp - this.latestMagneticFieldTimestamp
      : null;
    if (this.magneticFieldAvailable && magAgeNs !== null && Math.abs(magAgeNs) <= MAX_SENSOR_AGE_NS) {
      frame.magneticField = this.latestMagneticField;
      frame.magneticFieldTimestamp = this.latestMagneticFieldTimestamp;
    }

    this.frameCallback(frame);
  }

  private observeAccelerationContinuity(sensorTimestamp: number | null, monotonicNowMs: number): void {
    let continuous = true;
    if (sensorTimestamp !== null && this.lastAccelerationTimestamp !== null) {
      const sensorGapNs = sensorTimestamp - this.lastAccelerationTimestamp;
      continuous = sensorGapNs > 0 && sensorGapNs <= GYROSCOPE_WATCHDOG_MAX_ACCEL_GAP_NS;
    } else if (this.lastAccelerationMonotonicMs > 0) {
      const callbackGapMs = monotonicNowMs - this.lastAccelerationMonotonicMs;
      continuous = callbackGapMs > 0 &&
        callbackGapMs * 1000000 <= GYROSCOPE_WATCHDOG_MAX_ACCEL_GAP_NS;
    }
    if (!continuous) {
      this.clearGyroscopeMissingWindow();
    }
    this.accelerometerFrameSeen = true;
    this.accelerometerStallSuspectSinceMonotonicMs = 0;
    this.lastAccelerationTimestamp = sensorTimestamp;
    this.lastAccelerationMonotonicMs = monotonicNowMs;
  }

  private handleMissingGyroscope(sensorTimestamp: number | null, monotonicNowMs: number): void {
    if (this.gyroscopeMissingSinceMonotonicMs <= 0) {
      this.gyroscopeMissingSinceTimestamp = sensorTimestamp;
      this.gyroscopeMissingSinceMonotonicMs = monotonicNowMs;
      return;
    }

    let missingDurationNs = Math.max(0,
      monotonicNowMs - this.gyroscopeMissingSinceMonotonicMs) * 1000000;
    if (sensorTimestamp !== null && this.gyroscopeMissingSinceTimestamp !== null &&
      sensorTimestamp >= this.gyroscopeMissingSinceTimestamp) {
      missingDurationNs = sensorTimestamp - this.gyroscopeMissingSinceTimestamp;
    }
    if (missingDurationNs < GYROSCOPE_FRESHNESS_TIMEOUT_NS) {
      return;
    }

    if (this.requireGyroscope) {
      const phase: CoreSensorFailurePhase = this.gyroscopeFreshFrameSeen ? 'stale' : 'initial';
      const reason = phase === 'initial' ? '陀螺仪首帧超时' : '陀螺仪数据中断';
      this.failCoreSensor('gyroscope', phase, reason);
      return;
    }

    if (!this.optionalGyroscopeDegraded) {
      this.optionalGyroscopeDegraded = true;
      this.gyroscopeAvailable = false;
      this.emitStatus('研究记录已降级：陀螺仪回调超时，仅记录加速度');
    }
  }

  private clearGyroscopeMissingWindow(): void {
    this.gyroscopeMissingSinceTimestamp = null;
    this.gyroscopeMissingSinceMonotonicMs = 0;
  }

  private readMonotonicNowMs(): number {
    try {
      const uptimeMs = systemDateTime.getUptime(systemDateTime.TimeType.STARTUP);
      const normalizedUptimeMs = Math.max(1, uptimeMs);
      if (Number.isFinite(normalizedUptimeMs) && normalizedUptimeMs >= this.lastMonotonicMs) {
        this.lastMonotonicMs = normalizedUptimeMs;
        this.lastMonotonicFallbackWallMs = Date.now();
        return normalizedUptimeMs;
      }
    } catch (error) {
      // Fall through to a clamped wall-clock delta. It may delay a watchdog,
      // but a wall-clock jump must never create a false core-sensor failure.
    }

    const wallNowMs = Date.now();
    if (this.lastMonotonicMs <= 0) {
      this.lastMonotonicMs = 1;
      this.lastMonotonicFallbackWallMs = wallNowMs;
      return this.lastMonotonicMs;
    }
    if (Number.isFinite(wallNowMs) && Number.isFinite(this.lastMonotonicFallbackWallMs)) {
      const wallDeltaMs = wallNowMs - this.lastMonotonicFallbackWallMs;
      if (wallDeltaMs > 0) {
        this.lastMonotonicMs += Math.min(wallDeltaMs, CORE_SENSOR_WATCHDOG_INTERVAL_MS * 2);
      }
      this.lastMonotonicFallbackWallMs = wallNowMs;
    }
    return this.lastMonotonicMs;
  }

  private scheduleCoreSensorWatchdog(generation: number): void {
    if (!this.isCoreSubscriptionActive(generation) || this.coreSensorWatchdogTimer >= 0) {
      return;
    }
    this.coreSensorWatchdogTimer = setTimeout(() => {
      this.coreSensorWatchdogTimer = -1;
      if (!this.isCoreSubscriptionActive(generation)) {
        return;
      }
      this.checkAccelerometerWatchdog();
      if (this.isCoreSubscriptionActive(generation)) {
        this.scheduleCoreSensorWatchdog(generation);
      }
    }, CORE_SENSOR_WATCHDOG_INTERVAL_MS);
  }

  private stopCoreSensorWatchdog(): void {
    if (this.coreSensorWatchdogTimer < 0) {
      return;
    }
    clearTimeout(this.coreSensorWatchdogTimer);
    this.coreSensorWatchdogTimer = -1;
  }

  private checkAccelerometerWatchdog(): void {
    const now = this.readMonotonicNowMs();
    const referenceMs = this.accelerometerFrameSeen
      ? this.lastAccelerationMonotonicMs
      : this.coreStartMonotonicMs;
    const timeoutMs = this.accelerometerFrameSeen
      ? ACCELEROMETER_STALE_TIMEOUT_MS
      : ACCELEROMETER_INITIAL_TIMEOUT_MS;
    if (referenceMs <= 0 || now - referenceMs < timeoutMs) {
      this.accelerometerStallSuspectSinceMonotonicMs = 0;
      return;
    }
    if (this.accelerometerStallSuspectSinceMonotonicMs <= 0) {
      this.accelerometerStallSuspectSinceMonotonicMs = now;
      return;
    }
    if (now - this.accelerometerStallSuspectSinceMonotonicMs < ACCELEROMETER_STALL_CONFIRM_MS) {
      return;
    }

    const phase: CoreSensorFailurePhase = this.accelerometerFrameSeen ? 'stale' : 'initial';
    const reason = phase === 'initial' ? '加速度计首帧超时' : '加速度计数据中断';
    this.failCoreSensor('accelerometer', phase, reason);
  }

  private failCoreSensor(
    sensorType: CoreSensorFailureSensor,
    phase: CoreSensorFailurePhase,
    reason: string
  ): void {
    if (!this.running || this.coreFatalReported) {
      return;
    }
    this.coreFatalReported = true;
    if (sensorType === 'gyroscope') {
      this.gyroscopeAvailable = false;
    }
    const failedGeneration = this.subscriptionGeneration;
    this.running = false;
    this.stopCoreSensorWatchdog();
    const operation = this.requireGyroscope ? '测速已中止' : '研究记录传感器已中止';
    this.emitStatus(`${operation}：${reason}，请重新开始`);
    if (this.fatalCallback) {
      try {
        this.fatalCallback({
          sensor: sensorType,
          phase,
          timestampMs: Date.now(),
          message: reason
        });
      } catch (error) {
        // A UI observer must not prevent core sensor teardown.
      }
    }
    setTimeout(() => {
      if (failedGeneration !== this.subscriptionGeneration || !this.coreFatalReported) {
        return;
      }
      this.stop();
    }, 0);
  }

  private emitStatus(message: string): void {
    if (this.statusCallback) {
      this.statusCallback(message);
    }
  }

  private emitActiveSensorStatus(includeAuxiliary: boolean): void {
    const highRate: string[] = ['加速度'];

    if (this.gyroscopeAvailable) highRate.push('陀螺仪');
    if (includeAuxiliary) {
      if (this.rotationVectorAvailable) highRate.push('旋转矢量');
      if (this.magneticFieldAvailable) highRate.push('校准磁场');
      if (this.uncalibratedGyroscopeAvailable) highRate.push('未校准陀螺仪');
      if (this.uncalibratedMagneticFieldAvailable) highRate.push('未校准磁场');
    }

    this.emitStatus(`已启动传感器：${HIGH_RATE_HZ}Hz（${highRate.join('、')}），为请求频率`);
  }

  private safeOffAccelerometer(): boolean {
    if (!this.accelerometerSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.ACCELEROMETER);
      this.accelerometerSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`accelerometer off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffGyroscope(): boolean {
    if (!this.gyroscopeSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.GYROSCOPE);
      this.gyroscopeSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`gyroscope off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffRotationVector(): boolean {
    if (!this.rotationVectorSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.ROTATION_VECTOR);
      this.rotationVectorSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`rotation vector off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffMagneticField(): boolean {
    if (!this.magneticFieldSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.MAGNETIC_FIELD);
      this.magneticFieldSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`magnetic field off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffUncalibratedGyroscope(): boolean {
    if (!this.uncalibratedGyroscopeSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.GYROSCOPE_UNCALIBRATED);
      this.uncalibratedGyroscopeSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`uncalibrated gyroscope off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffUncalibratedMagneticField(): boolean {
    if (!this.uncalibratedMagneticFieldSubscribed) {
      return true;
    }
    try {
      sensor.off(sensor.SensorId.MAGNETIC_FIELD_UNCALIBRATED);
      this.uncalibratedMagneticFieldSubscribed = false;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`uncalibrated magnetic field off failed: ${err.message}`);
      return false;
    }
  }

}
