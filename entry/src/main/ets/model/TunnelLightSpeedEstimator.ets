export interface LightSpeedResult {
  intervalCount: number;
  elapsedMs: number;
  speedKmh: number;
}

export interface LightTapOutcome {
  accepted: boolean;
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
  private readonly MAX_INTERVALS: number = 4;
  private readonly MIN_TAP_GAP_MS: number = 120;

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

  getLightDistance(): number {
    return this.lightDistanceM;
  }

  reset(): void {
    this.tapTimesMs = [];
  }

  tap(timestampMs: number): LightTapOutcome {
    if (this.lightDistanceM <= 0) {
      return this.outcome(false, '请先输入灯光间距');
    }
    if (!Number.isFinite(timestampMs) || timestampMs < 0) {
      return this.outcome(false, '打点时间无效');
    }
    if (this.tapTimesMs.length > 0) {
      const previous = this.tapTimesMs[this.tapTimesMs.length - 1];
      if (timestampMs <= previous) {
        return this.outcome(false, '打点时间必须递增');
      }
      if (timestampMs - previous < this.MIN_TAP_GAP_MS) {
        return this.outcome(false, '打点过快，已忽略误触');
      }
    }

    this.tapTimesMs.push(timestampMs);
    while (this.tapTimesMs.length > this.MAX_INTERVALS + 1) {
      this.tapTimesMs.shift();
    }
    if (this.tapTimesMs.length < 2) {
      return this.outcome(true, '已记录起始灯光');
    }
    return this.outcome(true, '已更新速度区间');
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

  private outcome(accepted: boolean, message: string): LightTapOutcome {
    return {
      accepted,
      message,
      tapCount: this.tapTimesMs.length,
      results: this.results()
    };
  }
}
