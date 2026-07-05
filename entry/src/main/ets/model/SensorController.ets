import sensor from '@ohos.sensor';
import { BusinessError } from '@ohos.base';
import { SensorFrame, Vector3, Quaternion } from './SpeedTypes';

type SensorFrameCallback = (frame: SensorFrame) => void;
type SensorStatusCallback = (message: string) => void;
const HIGH_RATE_INTERVAL_NS = 20000000;
const HIGH_RATE_HZ = 1000000000 / HIGH_RATE_INTERVAL_NS;
const MAX_SENSOR_AGE_NS = 60000000;

export class SensorController {
  private frameCallback?: SensorFrameCallback;
  private statusCallback?: SensorStatusCallback;

  private latestGyroscope?: Vector3;
  private latestGyroscopeTimestamp: number | null = null;
  private gyroscopeAvailable: boolean = false;

  private latestGravity?: Vector3;
  private latestGravityTimestamp: number | null = null;
  private gravityAvailable: boolean = false;

  private latestLinearAcceleration?: Vector3;
  private latestLinearAccelerationTimestamp: number | null = null;
  private linearAccelerationAvailable: boolean = false;

  private latestRotationVector?: Quaternion;
  private latestRotationVectorTimestamp: number | null = null;
  private rotationVectorAvailable: boolean = false;

  private latestMagneticField?: Vector3;
  private latestMagneticFieldTimestamp: number | null = null;
  private magneticFieldAvailable: boolean = false;

  private running: boolean = false;

  start(frameCallback: SensorFrameCallback, statusCallback: SensorStatusCallback): boolean {
    this.stop();
    this.frameCallback = frameCallback;
    this.statusCallback = statusCallback;

    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
    this.latestGravity = undefined;
    this.latestGravityTimestamp = null;
    this.gravityAvailable = false;
    this.latestLinearAcceleration = undefined;
    this.latestLinearAccelerationTimestamp = null;
    this.linearAccelerationAvailable = false;
    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;
    this.latestMagneticField = undefined;
    this.latestMagneticFieldTimestamp = null;
    this.magneticFieldAvailable = false;

    this.running = true;

    this.subscribeGyroscope();
    this.subscribeGravity();
    this.subscribeMagneticField();

    const accelerationStarted = this.subscribeAcceleration();
    if (!accelerationStarted) {
      this.stop();
    }

    const availableSensors: string[] = ['加速度'];
    if (this.gyroscopeAvailable) availableSensors.push('陀螺仪');
    if (this.gravityAvailable) availableSensors.push('重力');
    if (this.magneticFieldAvailable) availableSensors.push('磁场');
    this.emitStatus(`已启动传感器：${availableSensors.join('、')}，请求 ${HIGH_RATE_HZ}Hz`);

    return accelerationStarted;
  }

  startAuxiliarySensors(): void {
    this.subscribeLinearAcceleration();
    this.subscribeRotationVector();

    const all: string[] = ['加速度'];
    if (this.gyroscopeAvailable) all.push('陀螺仪');
    if (this.gravityAvailable) all.push('重力');
    if (this.linearAccelerationAvailable) all.push('线性加速度');
    if (this.rotationVectorAvailable) all.push('旋转矢量');
    if (this.magneticFieldAvailable) all.push('磁场');
    if (all.length > 0) {
      this.emitStatus(`已启动传感器：${all.join('、')}，请求 ${HIGH_RATE_HZ}Hz`);
    }
  }

  stopAuxiliarySensors(): void {
    this.safeOffLinearAcceleration();
    this.safeOffRotationVector();

    this.latestLinearAcceleration = undefined;
    this.latestLinearAccelerationTimestamp = null;
    this.linearAccelerationAvailable = false;
    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;

    if (this.running) {
      const core: string[] = ['加速度'];
      if (this.gyroscopeAvailable) core.push('陀螺仪');
      if (this.gravityAvailable) core.push('重力');
      if (this.magneticFieldAvailable) core.push('磁场');
      this.emitStatus(`已启动传感器：${core.join('、')}，请求 ${HIGH_RATE_HZ}Hz`);
    }
  }

  stop(): void {
    this.running = false;
    this.safeOffAccelerometer();
    this.safeOffGyroscope();
    this.safeOffGravity();
    this.safeOffLinearAcceleration();
    this.safeOffRotationVector();
    this.safeOffMagneticField();

    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
    this.latestGravity = undefined;
    this.latestGravityTimestamp = null;
    this.gravityAvailable = false;
    this.latestLinearAcceleration = undefined;
    this.latestLinearAccelerationTimestamp = null;
    this.linearAccelerationAvailable = false;
    this.latestRotationVector = undefined;
    this.latestRotationVectorTimestamp = null;
    this.rotationVectorAvailable = false;
    this.latestMagneticField = undefined;
    this.latestMagneticFieldTimestamp = null;
    this.magneticFieldAvailable = false;
  }

  private subscribeAcceleration(): boolean {
    try {
      sensor.on(sensor.SensorId.ACCELEROMETER, (data: sensor.AccelerometerResponse) => {
        this.emitFrame({ x: data.x, y: data.y, z: data.z }, data.timestamp);
      }, { interval: HIGH_RATE_INTERVAL_NS });
      return true;
    } catch (error) {
      const err = error as BusinessError;
      this.emitStatus(`无法读取加速度传感器：${err.message}`);
      return false;
    }
  }

  private subscribeGyroscope(): void {
    try {
      sensor.on(sensor.SensorId.GYROSCOPE, (data: sensor.GyroscopeResponse) => {
        this.latestGyroscope = { x: data.x, y: data.y, z: data.z };
        this.latestGyroscopeTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.gyroscopeAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.gyroscopeAvailable = false;
      this.emitStatus(`陀螺仪不可用：${err.message}`);
    }
  }

  private subscribeGravity(): void {
    try {
      sensor.on(sensor.SensorId.GRAVITY, (data: sensor.GravityResponse) => {
        this.latestGravity = { x: data.x, y: data.y, z: data.z };
        this.latestGravityTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.gravityAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.gravityAvailable = false;
      console.info(`Gravity sensor unavailable: ${err.message}`);
    }
  }

  private subscribeLinearAcceleration(): void {
    try {
      sensor.on(sensor.SensorId.LINEAR_ACCELEROMETER, (data: sensor.LinearAccelerometerResponse) => {
        this.latestLinearAcceleration = { x: data.x, y: data.y, z: data.z };
        this.latestLinearAccelerationTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.linearAccelerationAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.linearAccelerationAvailable = false;
      console.info(`Linear acceleration sensor unavailable: ${err.message}`);
    }
  }

  private subscribeRotationVector(): void {
    try {
      sensor.on(sensor.SensorId.ROTATION_VECTOR, (data: sensor.RotationVectorResponse) => {
        this.latestRotationVector = { x: data.x, y: data.y, z: data.z, w: data.w };
        this.latestRotationVectorTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.rotationVectorAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.rotationVectorAvailable = false;
      console.info(`Rotation vector sensor unavailable: ${err.message}`);
    }
  }

  private subscribeMagneticField(): void {
    try {
      sensor.on(sensor.SensorId.MAGNETIC_FIELD, (data: sensor.MagneticFieldResponse) => {
        this.latestMagneticField = { x: data.x, y: data.y, z: data.z };
        this.latestMagneticFieldTimestamp = data.timestamp;
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.magneticFieldAvailable = true;
    } catch (error) {
      const err = error as BusinessError;
      this.magneticFieldAvailable = false;
      console.info(`Magnetic field sensor unavailable: ${err.message}`);
    }
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

    const gravityAgeNs = sensorTimestamp !== null && this.latestGravityTimestamp !== null
      ? sensorTimestamp - this.latestGravityTimestamp
      : null;
    if (this.gravityAvailable && gravityAgeNs !== null && Math.abs(gravityAgeNs) <= MAX_SENSOR_AGE_NS) {
      frame.gravity = this.latestGravity;
      frame.gravityTimestamp = this.latestGravityTimestamp;
    }

    const linearAccAgeNs = sensorTimestamp !== null && this.latestLinearAccelerationTimestamp !== null
      ? sensorTimestamp - this.latestLinearAccelerationTimestamp
      : null;
    if (this.linearAccelerationAvailable && linearAccAgeNs !== null && Math.abs(linearAccAgeNs) <= MAX_SENSOR_AGE_NS) {
      frame.linearAcceleration = this.latestLinearAcceleration;
      frame.linearAccelerationTimestamp = this.latestLinearAccelerationTimestamp;
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

  private safeOffAccelerometer(): void {
    try {
      sensor.off(sensor.SensorId.ACCELEROMETER);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`accelerometer off failed: ${err.message}`);
    }
  }

  private safeOffGyroscope(): void {
    try {
      sensor.off(sensor.SensorId.GYROSCOPE);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`gyroscope off failed: ${err.message}`);
    }
  }

  private safeOffGravity(): void {
    try {
      sensor.off(sensor.SensorId.GRAVITY);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`gravity off failed: ${err.message}`);
    }
  }

  private safeOffLinearAcceleration(): void {
    try {
      sensor.off(sensor.SensorId.LINEAR_ACCELEROMETER);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`linear acceleration off failed: ${err.message}`);
    }
  }

  private safeOffRotationVector(): void {
    try {
      sensor.off(sensor.SensorId.ROTATION_VECTOR);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`rotation vector off failed: ${err.message}`);
    }
  }

  private safeOffMagneticField(): void {
    try {
      sensor.off(sensor.SensorId.MAGNETIC_FIELD);
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`magnetic field off failed: ${err.message}`);
    }
  }
}
