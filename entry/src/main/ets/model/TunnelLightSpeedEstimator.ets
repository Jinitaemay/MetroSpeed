export interface LightSpeedResult {
  intervalCount: number;
  elapsedMs: number;
  speedKmh: number;
}

export interface LightTapOutcome {
  message: string;
  tapCount: number;
  results: LightSpeedResult[];
}

/**
 * Calculates train speed from equally spaced tunnel lights.
 *
 * A result spanning N intervals uses the timestamps of the newest tap and the
 * tap N positions before it. This is more stable than averaging rounded
 * one-interval speeds and naturally provides estimates for the latest 1–4
 * intervals.
 */
export class TunnelLightSpeedEstimator {
  private lightDistanceM: number = 0;
  private tapTimesMs: number[] = [];
  private totalTapCount: number = 0;
  private readonly MAX_INTERVALS: number = 4;

  setLightDistance(distanceM: number): boolean {
    if (!Number.isFinite(distanceM) || distanceM <= 0) {
      return false;
    }
    if (distanceM !== this.lightDistanceM) {
      this.lightDistanceM = distanceM;
      this.reset();
    }
    return true;
  }

  reset(): void {
    this.tapTimesMs = [];
    this.totalTapCount = 0;
  }

  tap(timestampMs: number): LightTapOutcome {
    if (this.lightDistanceM <= 0) {
      return this.outcome('请先输入灯光间距');
    }
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      return this.outcome('打点时间无效');
    }
    if (this.tapTimesMs.length > 0) {
      const previous = this.tapTimesMs[this.tapTimesMs.length - 1];
      if (timestampMs <= previous) {
        return this.outcome('打点时间必须递增');
      }
    }

    this.tapTimesMs.push(timestampMs);
    this.totalTapCount += 1;
    while (this.tapTimesMs.length > this.MAX_INTERVALS + 1) {
      this.tapTimesMs.shift();
    }
    if (this.tapTimesMs.length < 2) {
      return this.outcome('已记录起始灯光');
    }
    return this.outcome('已更新速度区间');
  }

  results(): LightSpeedResult[] {
    const result: LightSpeedResult[] = [];
    if (this.tapTimesMs.length < 2 || this.lightDistanceM <= 0) {
      return result;
    }

    const endIndex = this.tapTimesMs.length - 1;
    const availableIntervals = Math.min(this.MAX_INTERVALS, endIndex);
    for (let intervalCount = 1; intervalCount <= availableIntervals; intervalCount++) {
      const elapsedMs = this.tapTimesMs[endIndex] - this.tapTimesMs[endIndex - intervalCount];
      if (elapsedMs <= 0) {
        continue;
      }
      const speedKmh = this.lightDistanceM * intervalCount * 3600 / elapsedMs;
      result.push({ intervalCount, elapsedMs, speedKmh });
    }
    return result;
  }

  private outcome(message: string): LightTapOutcome {
    return {
      message,
      tapCount: this.totalTapCount,
      results: this.results()
    };
  }
}
