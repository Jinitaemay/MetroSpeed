import sensor from '@ohos.sensor';
import { BusinessError } from '@ohos.base';
import { SensorFrame, Vector3 } from './SpeedTypes';

type SensorFrameCallback = (frame: SensorFrame) => void;
type SensorStatusCallback = (message: string) => void;
const HIGH_RATE_INTERVAL_NS = 20000000;
const HIGH_RATE_HZ = 1000000000 / HIGH_RATE_INTERVAL_NS;
const MAX_GYROSCOPE_AGE_NS = 60000000;

export class SensorController {
  private frameCallback?: SensorFrameCallback;
  private statusCallback?: SensorStatusCallback;
  private latestGyroscope?: Vector3;
  private latestGyroscopeTimestamp: number | null = null;
  private gyroscopeAvailable: boolean = false;
  private running: boolean = false;

  start(frameCallback: SensorFrameCallback, statusCallback: SensorStatusCallback): boolean {
    this.stop();
    this.frameCallback = frameCallback;
    this.statusCallback = statusCallback;
    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
    this.running = true;
    this.subscribeGyroscope();
    const accelerationStarted = this.subscribeAcceleration();
    if (!accelerationStarted) {
      this.stop();
    }
    return accelerationStarted;
  }

  stop(): void {
    this.running = false;
    this.safeOffAccelerometer();
    this.safeOffGyroscope();
    this.latestGyroscope = undefined;
    this.latestGyroscopeTimestamp = null;
    this.gyroscopeAvailable = false;
  }

  private subscribeAcceleration(): boolean {
    try {
      sensor.on(sensor.SensorId.ACCELEROMETER, (data: sensor.AccelerometerResponse) => {
        this.emitFrame({ x: data.x, y: data.y, z: data.z }, data.timestamp);
      }, { interval: HIGH_RATE_INTERVAL_NS });
      this.emitStatus(this.withGyroscopeStatus(`已使用加速度传感器（含重力），请求 ${HIGH_RATE_HZ}Hz`));
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
      this.emitStatus(`陀螺仪不可用，弯道识别将降级：${err.message}`);
    }
  }

  private emitFrame(acceleration: Vector3, sensorTimestamp: number | null): void {
    if (!this.running || !this.frameCallback) {
      return;
    }
    const gyroAgeNs = sensorTimestamp !== null && this.latestGyroscopeTimestamp !== null
      ? sensorTimestamp - this.latestGyroscopeTimestamp
      : null;
    const gyroFresh = gyroAgeNs !== null && Math.abs(gyroAgeNs) <= MAX_GYROSCOPE_AGE_NS;
    this.frameCallback({
      timestampMs: Date.now(),
      sensorTimestamp,
      acceleration,
      gyroscope: gyroFresh ? this.latestGyroscope : undefined,
      gyroscopeTimestamp: gyroFresh ? this.latestGyroscopeTimestamp : null
    });
  }

  private emitStatus(message: string): void {
    if (this.statusCallback) {
      this.statusCallback(message);
    }
  }

  private withGyroscopeStatus(message: string): string {
    if (this.gyroscopeAvailable) {
      return message;
    }
    return `${message}；陀螺仪不可用，弯道识别降级`;
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
}
