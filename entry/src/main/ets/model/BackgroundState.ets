export class BackgroundState {
  static measurementActive: boolean = false;
  static recordingActive: boolean = false;

  static isCollectionActive(): boolean {
    return BackgroundState.measurementActive || BackgroundState.recordingActive;
  }
}
