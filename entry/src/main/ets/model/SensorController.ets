import sensor from '@ohos.sensor';
import { BusinessError } from '@ohos.base';
import { SensorCallbackSample, SensorFrame, Vector3, Quaternion } from './SpeedTypes';

type SensorFrameCallback = (frame: SensorFrame) => void;
type SensorStatusCallback = (message: string) => void;
type SensorSampleCallback = (sample: SensorCallbackSample) => void;
const HIGH_RATE_INTERVAL_NS = 10000000;
const HIGH_RATE_HZ = 1000000000 / HIGH_RATE_INTERVAL_NS;
const MAX_SENSOR_AGE_NS = 60000000;

export class SensorController {
  private frameCallback?: SensorFrameCallback;
  private statusCallback?: SensorStatusCallback;
  private sensorSampleCallback?: SensorSampleCallback;

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
  private subscriptionGeneration: number = 0;
  private auxiliaryGeneration: number = 0;
  private auxiliaryCleanupPending: boolean = false;

  start(
    frameCallback: SensorFrameCallback,
    statusCallback: SensorStatusCallback,
    sensorSampleCallback?: SensorSampleCallback,
    requireGyroscope: boolean = true
  ): boolean {
    const coreCleanupSucceeded = this.stop();
    this.frameCallback = frameCallback;
    this.statusCallback = statusCallback;
    this.sensorSampleCallback = sensorSampleCallback;

    if (!coreCleanupSucceeded) {
      this.emitStatus('上次核心传感器退订失败，请重试');
      return false;
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
    this.frameCallback = undefined;
    this.sensorSampleCallback = undefined;
    return coreCleanupSucceeded;
  }

  private subscribeAcceleration(): boolean {
    const generation = this.subscriptionGeneration;
    try {
      sensor.on(sensor.SensorId.ACCELEROMETER, (data: sensor.AccelerometerResponse) => {
        if (!this.isCoreSubscriptionActive(generation)) {
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
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.gyroscopeSubscribed = true;
      this.gyroscopeAvailable = true;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      this.gyroscopeSubscribed = false;
      this.gyroscopeAvailable = false;
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

    const frame: SensorFrame = {
      timestampMs: Date.now(),
      sensorTimestamp,
      acceleration
    };

    const gyroAgeNs = sensorTimestamp !== null && this.latestGyroscopeTimestamp !== null
      ? sensorTimestamp - this.latestGyroscopeTimestamp
      : null;
    if (this.gyroscopeAvailable && gyroAgeNs !== null && Math.abs(gyroAgeNs) <= MAX_SENSOR_AGE_NS) {
      frame.gyroscope = this.latestGyroscope;
      frame.gyroscopeTimestamp = this.latestGyroscopeTimestamp;
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
