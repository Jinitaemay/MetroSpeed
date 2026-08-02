import geoLocationManager from '@ohos.geoLocationManager';
import { BusinessError } from '@ohos.base';

type LocationCallback = (location: LocationSnapshot) => void;
type SatelliteCallback = (satellite: SatelliteSnapshot) => void;
type LocationStatusCallback = (message: string) => void;
type LocationChangeHandler = (location: geoLocationManager.Location) => void;
type SatelliteStatusHandler = (status: geoLocationManager.SatelliteStatusInfo) => void;

export interface LocationSnapshot {
  timestampMs: number;
  latitude: number;
  longitude: number;
  altitude: number;
  accuracy: number;
  altitudeAccuracy: number | null;
  speedMps: number;
  speedAccuracyMps: number | null;
  directionDeg: number;
  directionAccuracyDeg: number | null;
  locationTimeMs: number;
  timeSinceBootNs: number | null;
  uncertaintyOfTimeSinceBootNs: number | null;
  locationSourceType: number | null;
  locationAgeMs: number;
  satelliteCount: number | null;
  satelliteFixCount: number | null;
  satelliteCn0Avg: number | null;
  satelliteCn0Max: number | null;
  satelliteCn0PositiveCount: number | null;
  satelliteCn0PositiveAvg: number | null;
  satelliteCn0UsedAvg: number | null;
}

export interface SatelliteSnapshot {
  timestampMs: number;
  satelliteCount: number;
  satelliteFixCount: number;
  satelliteCn0Avg: number;
  satelliteCn0Max: number;
  satelliteCn0PositiveCount: number;
  satelliteCn0PositiveAvg: number;
  satelliteCn0UsedAvg: number;
}

export class LocationController {
  private callback?: LocationCallback;
  private satelliteCallback?: SatelliteCallback;
  private statusCallback?: LocationStatusCallback;
  private running: boolean = false;
  private latestSatellite?: SatelliteSnapshot;
  private locationChangeHandler?: LocationChangeHandler;
  private satelliteStatusHandler?: SatelliteStatusHandler;
  private locationSubscribed: boolean = false;
  private satelliteSubscribed: boolean = false;
  private subscriptionGeneration: number = 0;

  get isRunning(): boolean {
    return this.running;
  }

  start(callback: LocationCallback, satelliteCallback: SatelliteCallback, statusCallback: LocationStatusCallback, forRecording?: boolean): boolean {
    const cleanupSucceeded = this.stop();
    this.callback = callback;
    this.satelliteCallback = satelliteCallback;
    this.statusCallback = statusCallback;

    if (!cleanupSucceeded) {
      this.emitStatus('上次定位退订失败，请重试');
      return false;
    }

    this.running = true;
    this.latestSatellite = undefined;
    const generation = this.subscriptionGeneration;

    const request: geoLocationManager.LocationRequest = {
      priority: geoLocationManager.LocationRequestPriority.ACCURACY,
      scenario: geoLocationManager.LocationRequestScenario.TRAJECTORY_TRACKING,
      timeInterval: 1,
      distanceInterval: 0
    };

    const locationChangeHandler: LocationChangeHandler = (location: geoLocationManager.Location) => {
      if (!this.isSubscriptionActive(generation)) {
        return;
      }
      const now = Date.now();
      const snapshot: LocationSnapshot = {
        timestampMs: now,
        latitude: location.latitude,
        longitude: location.longitude,
        altitude: location.altitude,
        accuracy: location.accuracy,
        altitudeAccuracy: location.altitudeAccuracy === undefined ? null : location.altitudeAccuracy,
        speedMps: location.speed,
        speedAccuracyMps: location.speedAccuracy === undefined ? null : location.speedAccuracy,
        directionDeg: location.direction,
        directionAccuracyDeg: location.directionAccuracy === undefined ? null : location.directionAccuracy,
        locationTimeMs: location.timeStamp,
        timeSinceBootNs: location.timeSinceBoot === undefined ? null : location.timeSinceBoot,
        uncertaintyOfTimeSinceBootNs: location.uncertaintyOfTimeSinceBoot === undefined ? null : location.uncertaintyOfTimeSinceBoot,
        locationSourceType: location.sourceType === undefined ? null : location.sourceType,
        locationAgeMs: now - location.timeStamp,
        satelliteCount: this.latestSatellite ? this.latestSatellite.satelliteCount : null,
        satelliteFixCount: this.latestSatellite ? this.latestSatellite.satelliteFixCount : null,
        satelliteCn0Avg: this.latestSatellite ? this.latestSatellite.satelliteCn0Avg : null,
        satelliteCn0Max: this.latestSatellite ? this.latestSatellite.satelliteCn0Max : null,
        satelliteCn0PositiveCount: this.latestSatellite ? this.latestSatellite.satelliteCn0PositiveCount : null,
        satelliteCn0PositiveAvg: this.latestSatellite ? this.latestSatellite.satelliteCn0PositiveAvg : null,
        satelliteCn0UsedAvg: this.latestSatellite ? this.latestSatellite.satelliteCn0UsedAvg : null
      };
      if (this.callback) {
        this.callback(snapshot);
      }
    };

    try {
      geoLocationManager.on('locationChange', request, locationChangeHandler);
      this.locationChangeHandler = locationChangeHandler;
      this.locationSubscribed = true;
      this.subscribeSatelliteStatus(generation);
      this.emitStatus(forRecording ? '卫星记录已启动' : '定位已启动');
      return true;
    } catch (error) {
      const err = error as BusinessError;
      this.running = false;
      this.emitStatus(`定位不可用：${err.message}`);
      return false;
    }
  }

  stop(): boolean {
    this.subscriptionGeneration += 1;
    this.running = false;
    let cleanupSucceeded = true;
    if (!this.safeOffLocationChange()) cleanupSucceeded = false;
    if (!this.safeOffSatelliteStatus()) cleanupSucceeded = false;
    this.latestSatellite = undefined;
    this.callback = undefined;
    this.satelliteCallback = undefined;
    return cleanupSucceeded;
  }

  private emitStatus(message: string): void {
    if (this.statusCallback) {
      this.statusCallback(message);
    }
  }

  private subscribeSatelliteStatus(generation: number): void {
    const satelliteStatusHandler: SatelliteStatusHandler = (status: geoLocationManager.SatelliteStatusInfo) => {
      if (!this.isSubscriptionActive(generation)) {
        return;
      }
      let fixCount = 0;
      let usedCn0Sum = 0;
      let usedCn0Count = 0;
      if (status.satelliteAdditionalInfo) {
        for (let i = 0; i < status.satelliteAdditionalInfo.length; i++) {
          if ((status.satelliteAdditionalInfo[i] & geoLocationManager.SatelliteAdditionalInfo.SATELLITES_ADDITIONAL_INFO_USED_IN_FIX) !== 0) {
            fixCount++;
            if (i < status.carrierToNoiseDensitys.length) {
              usedCn0Sum += status.carrierToNoiseDensitys[i];
              usedCn0Count++;
            }
          }
        }
      }

      let cn0Sum = 0;
      let cn0Max = 0;
      let positiveCn0Sum = 0;
      let positiveCn0Count = 0;
      for (let j = 0; j < status.carrierToNoiseDensitys.length; j++) {
        const cn0 = status.carrierToNoiseDensitys[j];
        cn0Sum += cn0;
        cn0Max = Math.max(cn0Max, cn0);
        if (cn0 > 0) {
          positiveCn0Sum += cn0;
          positiveCn0Count++;
        }
      }

      const snapshot: SatelliteSnapshot = {
        timestampMs: Date.now(),
        satelliteCount: status.satellitesNumber,
        satelliteFixCount: fixCount,
        satelliteCn0Avg: status.carrierToNoiseDensitys.length > 0 ? cn0Sum / status.carrierToNoiseDensitys.length : 0,
        satelliteCn0Max: cn0Max,
        satelliteCn0PositiveCount: positiveCn0Count,
        satelliteCn0PositiveAvg: positiveCn0Count > 0 ? positiveCn0Sum / positiveCn0Count : 0,
        satelliteCn0UsedAvg: usedCn0Count > 0 ? usedCn0Sum / usedCn0Count : 0
      };
      this.latestSatellite = snapshot;
      if (this.satelliteCallback) {
        this.satelliteCallback(snapshot);
      }
    };

    try {
      geoLocationManager.on('satelliteStatusChange', satelliteStatusHandler);
      this.satelliteStatusHandler = satelliteStatusHandler;
      this.satelliteSubscribed = true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`satellite status unavailable: ${err.message}`);
    }
  }

  private isSubscriptionActive(generation: number): boolean {
    return this.running && generation === this.subscriptionGeneration;
  }

  private safeOffLocationChange(): boolean {
    if (!this.locationSubscribed) {
      return true;
    }
    if (!this.locationChangeHandler) {
      console.warn('location off failed: missing owned callback');
      return false;
    }
    try {
      geoLocationManager.off('locationChange', this.locationChangeHandler);
      this.locationSubscribed = false;
      this.locationChangeHandler = undefined;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`location off failed: ${err.message}`);
      return false;
    }
  }

  private safeOffSatelliteStatus(): boolean {
    if (!this.satelliteSubscribed) {
      return true;
    }
    if (!this.satelliteStatusHandler) {
      console.warn('satellite off failed: missing owned callback');
      return false;
    }
    try {
      geoLocationManager.off('satelliteStatusChange', this.satelliteStatusHandler);
      this.satelliteSubscribed = false;
      this.satelliteStatusHandler = undefined;
      return true;
    } catch (error) {
      const err = error as BusinessError;
      console.warn(`satellite off failed: ${err.message}`);
      return false;
    }
  }
}
