@Entry
@Component
struct Index {
  @State private navigationStatus: string = '';
  @State private navigationInProgress: boolean = false;

  private openMode(url: string): void {
    if (this.navigationInProgress) {
      return;
    }
    this.navigationInProgress = true;
    try {
      this.getUIContext().getRouter().pushUrl({ url }).then(() => {
        this.navigationInProgress = false;
      }).catch(() => {
        this.navigationInProgress = false;
        this.navigationStatus = '无法打开测速页面';
      });
    } catch {
      this.navigationInProgress = false;
      this.navigationStatus = '无法打开测速页面';
    }
  }

  @Builder
  private ModeCard(
    title: string,
    subtitle: string,
    description: string,
    actionText: string,
    accentColor: string,
    url: string
  ) {
    Button({ type: ButtonType.Normal, stateEffect: !this.navigationInProgress }) {
      Column() {
        Row() {
          Column() {
            Text(title)
              .fontSize(24)
              .fontWeight(FontWeight.Bold)
              .fontColor('#F8FAFC');
            Text(subtitle)
              .fontSize(13)
              .fontColor(accentColor)
              .margin({ top: 5 });
          }
          .alignItems(HorizontalAlign.Start)
          .layoutWeight(1);

          Text('→')
            .fontSize(28)
            .fontColor(accentColor)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center);

        Text(description)
          .width('100%')
          .fontSize(14)
          .fontColor('#CBD5E1')
          .lineHeight(22)
          .constraintSize({ minHeight: 66 })
          .margin({ top: 18 });

        Text(actionText)
          .width('100%')
          .fontSize(13)
          .fontWeight(FontWeight.Medium)
          .fontColor(accentColor)
          .margin({ top: 18 });
      }
      .width('100%')
      .alignItems(HorizontalAlign.Start);
    }
    .width('100%')
    .padding(20)
    .backgroundColor('#111827')
    .border({ width: 1, color: accentColor })
    .borderRadius(12)
    .focusable(!this.navigationInProgress)
    .hitTestBehavior(this.navigationInProgress ? HitTestMode.BLOCK_DESCENDANTS : HitTestMode.Default)
    .accessibilityText(this.navigationInProgress ? `${actionText}，不可用` : actionText)
    .onClick(() => {
      this.openMode(url);
    });
  }

  build() {
    Scroll() {
      Column({ space: 16 }) {
        Column() {
          Text('地铁测速')
            .fontSize(30)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC');
          Text('选择测速方式')
            .fontSize(15)
            .fontColor('#94A3B8')
            .margin({ top: 6 });
        }
        .width('100%')
        .alignItems(HorizontalAlign.Start)
        .margin({ bottom: 8 });

        this.ModeCard(
          '惯性测速',
          '稳定放置 · 连续估算',
          '将手机稳定平放，通过惯性传感器持续估算速度，并在 GNSS 可用时使用速度锚点；当前未适配明显上下坡。',
          '进入惯性测速',
          '#60A5FA',
          'pages/InertialSpeed'
        );

        this.ModeCard(
          '灯光打点测速',
          '手持操作 · 等距灯光',
          '输入相邻灯光的实际间距，在每盏灯经过车窗固定参照位置时打点。',
          '进入灯光打点测速',
          '#4FD1C5',
          'pages/TunnelLight'
        );

        if (this.navigationStatus.length > 0) {
          Text(this.navigationStatus)
            .width('100%')
            .fontSize(13)
            .fontColor('#F87171')
            .textAlign(TextAlign.Center);
        }

        Text('请根据手机放置方式和可见参照物选择。')
          .width('100%')
          .fontSize(12)
          .fontColor('#94A3B8')
          .lineHeight(19)
          .padding(12)
          .backgroundColor('#0F172A')
          .borderRadius(8);
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 28, bottom: 18 });
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#0B1020')
    .edgeEffect(EdgeEffect.Spring)
    .expandSafeArea([SafeAreaType.SYSTEM], [SafeAreaEdge.TOP, SafeAreaEdge.BOTTOM]);
  }
}
