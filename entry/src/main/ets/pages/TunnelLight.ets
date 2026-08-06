import { LightSpeedResult, TunnelLightSpeedEstimator } from '../model/TunnelLightSpeedEstimator';
import { systemDateTime } from '@kit.BasicServicesKit';

@Entry
@Component
struct TunnelLight {
  private estimator: TunnelLightSpeedEstimator = new TunnelLightSpeedEstimator();

  @State private distanceText: string = '';
  @State private results: LightSpeedResult[] = [];
  @State private tapCount: number = 0;
  @State private statusText: string = '输入相邻灯光的实际间距，然后在每盏灯经过同一参照位置时打点';
  private navigationInProgress: boolean = false;

  private returnToModeSelection(): void {
    if (this.navigationInProgress) {
      return;
    }
    this.navigationInProgress = true;
    try {
      this.getUIContext().getRouter().back();
    } catch {
      this.navigationInProgress = false;
      this.statusText = '无法返回测速方式首页';
    }
  }

  private updateDistance(): boolean {
    const distance = Number(this.distanceText.trim());
    if (!this.estimator.setLightDistance(distance)) {
      this.statusText = '请输入大于 0 的灯光间距';
      return false;
    }
    return true;
  }

  private changeDistance(value: string): void {
    if (value === this.distanceText) {
      return;
    }
    const hadTaps = this.tapCount > 0 || this.results.length > 0;
    this.distanceText = value;
    this.estimator.reset();
    this.results = [];
    this.tapCount = 0;
    if (hadTaps) {
      this.statusText = value.trim().length > 0 ?
        '间距已改变，请从下一盏灯重新开始打点' :
        '间距已清空，请重新输入后开始打点';
    }
  }

  private recordLight(): void {
    if (!this.updateDistance()) {
      return;
    }
    let timestampMs: number;
    try {
      timestampMs = systemDateTime.getUptime(systemDateTime.TimeType.STARTUP);
    } catch {
      this.statusText = '无法读取单调时钟，请重试';
      return;
    }
    const outcome = this.estimator.tap(timestampMs);
    this.results = outcome.results;
    this.tapCount = outcome.tapCount;
    this.statusText = outcome.message;
  }

  private resetMeasurement(): void {
    this.estimator.reset();
    this.results = [];
    this.tapCount = 0;
    this.statusText = '已清空，请从下一盏灯重新开始打点';
  }

  private resultFor(intervalCount: number): LightSpeedResult | undefined {
    return this.results.find((item: LightSpeedResult) => item.intervalCount === intervalCount);
  }

  @Builder
  private ResultRow(intervalCount: number) {
    Row() {
      Column() {
        Text(`最近 ${intervalCount} 个间隔`)
          .fontSize(14)
          .fontColor('#CBD5E1');
        Text(this.resultFor(intervalCount) ?
          `${(this.resultFor(intervalCount) as LightSpeedResult).elapsedMs} ms` : '等待更多打点')
          .fontSize(11)
          .fontColor('#94A3B8')
          .margin({ top: 4 });
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1);

      Text(this.resultFor(intervalCount) ?
        `${(this.resultFor(intervalCount) as LightSpeedResult).speedKmh.toFixed(1)} km/h` : '—')
        .fontSize(22)
        .fontWeight(FontWeight.Bold)
        .fontColor(intervalCount === 4 ? '#4FD1C5' : '#F8FAFC');
    }
    .width('100%')
    .height(68)
    .padding({ left: 14, right: 14 })
    .backgroundColor('#111827')
    .borderRadius(8)
    .alignItems(VerticalAlign.Center);
  }

  build() {
    Scroll() {
      Column({ space: 12 }) {
        Row() {
          Button('‹', { type: ButtonType.Normal, stateEffect: true })
            .width(44)
            .height(40)
            .fontSize(28)
            .fontColor('#F8FAFC')
            .backgroundColor('#1E293B')
            .borderRadius(8)
            .accessibilityText('返回测速方式首页')
            .onClick(() => {
              this.returnToModeSelection();
            });
          Column() {
            Text('灯光打点测速')
              .fontSize(22)
              .fontWeight(FontWeight.Bold)
              .fontColor('#F8FAFC');
            Text('手持操作 · 等距灯光')
              .fontSize(12)
              .fontColor('#94A3B8')
              .margin({ top: 3 });
          }
          .alignItems(HorizontalAlign.Start)
          .margin({ left: 12 });
        }
        .width('100%');

        Column({ space: 10 }) {
          Text('相邻灯光间距（米）')
            .fontSize(14)
            .fontColor('#CBD5E1')
            .width('100%');
          TextInput({ text: this.distanceText, placeholder: '例如 9.6' })
            .type(InputType.NUMBER_DECIMAL)
            .accessibilityText('相邻灯光间距，单位米')
            .fontSize(18)
            .fontColor('#F8FAFC')
            .placeholderColor('#94A3B8')
            .backgroundColor('#1E293B')
            .borderRadius(8)
            .height(48)
            .onChange((value: string) => {
              this.changeDistance(value);
            });
          Text('间距改变后会自动清空旧打点，避免混用不同距离。')
            .fontSize(11)
            .fontColor('#94A3B8')
            .width('100%');
        }
        .width('100%')
        .padding(14)
        .backgroundColor('#0F172A')
        .borderRadius(8);

        Button({ type: ButtonType.Normal, stateEffect: true }) {
          Row() {
            Stack() {
              Text('●')
                .fontSize(22)
                .fontColor('#5EEAD4');
            }
            .width(50)
            .height(50)
            .backgroundColor('#134E4A')
            .borderRadius(25);

            Column() {
              Text('点击记录灯光')
                .fontSize(21)
                .fontWeight(FontWeight.Bold)
                .fontColor('#F8FAFC');
              Text(this.tapCount === 0 ? '首次点击确定起点' : `已记录 ${this.tapCount} 个灯光 · 继续点击`)
                .fontSize(12)
                .fontColor('#CCFBF1')
                .margin({ top: 4 });
            }
            .alignItems(HorizontalAlign.Start)
            .layoutWeight(1)
            .margin({ left: 14 });
          }
          .width('100%')
          .alignItems(VerticalAlign.Center);
        }
        .width('100%')
        .height(100)
        .padding({ left: 18, right: 18 })
        .backgroundColor('#0F766E')
        .border({ width: 1, color: '#2DD4BF' })
        .borderRadius(24)
        .onClick(() => {
          this.recordLight();
        });

        Row() {
          Text(this.statusText)
            .fontSize(12)
            .fontColor('#94A3B8')
            .layoutWeight(1)
            .maxLines(2);
          Button('清空', { type: ButtonType.Capsule, stateEffect: true })
            .height(34)
            .fontSize(13)
            .fontColor('#F8FAFC')
            .backgroundColor('#334155')
            .onClick(() => {
              this.resetMeasurement();
            });
        }
        .width('100%')
        .alignItems(VerticalAlign.Center);

        Column({ space: 8 }) {
          this.ResultRow(1);
          this.ResultRow(2);
          this.ResultRow(3);
          this.ResultRow(4);
        }
        .width('100%');

        Text('计算公式：速度 = 灯光间距 × 间隔数 ÷ 用时 × 3.6。选择车窗上的固定参照位置，在每盏灯经过同一位置时打点；优先参考 3～4 个间隔，可减小单次打点时机误差。')
          .fontSize(12)
          .fontColor('#94A3B8')
          .lineHeight(19)
          .padding(12)
          .backgroundColor('#0F172A')
          .borderRadius(8);
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 18, bottom: 18 });
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#0B1020')
    .edgeEffect(EdgeEffect.Spring)
    .expandSafeArea([SafeAreaType.SYSTEM], [SafeAreaEdge.TOP, SafeAreaEdge.BOTTOM]);
  }
}
