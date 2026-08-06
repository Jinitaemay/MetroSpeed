export class BackgroundState {
  static measurementActive: boolean = false;
  static recordingActive: boolean = false;
  static exportActive: boolean = false;

  static isCollectionActive(): boolean {
    return BackgroundState.measurementActive || BackgroundState.recordingActive;
  }

  static shouldKeepScreenOn(): boolean {
    return BackgroundState.isCollectionActive() || BackgroundState.exportActive;
  }
}
