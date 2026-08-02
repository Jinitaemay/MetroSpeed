from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def test_signing_uses_project_compatible_api(self) -> None:
        source = self.read("tools/sign_app.ps1")
        self.assertIn("Get-ProjectCompatibleApiVersion", source)
        self.assertIn("与项目配置", source)
        self.assertNotIn('$compatibleVersion = "12"', source)
        self.assertIn(
            "$keystorePassword = $env:METROSPEED_KEYSTORE_PASSWORD", source
        )
        self.assertIn("$keyPassword = $env:METROSPEED_KEY_PASSWORD", source)
        self.assertIn("$keyPassword = $keystorePassword", source)
        self.assertIn("'-keyPwd', $keyPassword", source)
        self.assertIn("'-keystorePwd', $keystorePassword", source)

    def test_background_task_uses_no_extra_background_location_permission(self) -> None:
        module = self.read("entry/src/main/module.json5")
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        ability = self.read("entry/src/main/ets/entryability/EntryAbility.ets")
        self.assertNotIn("ohos.permission.LOCATION_IN_BACKGROUND", module)
        self.assertNotIn("ohos.permission.LOCATION_IN_BACKGROUND", page)
        self.assertIn("ohos.permission.KEEP_BACKGROUND_RUNNING", module)
        self.assertIn("backgroundTaskManager.BackgroundMode.LOCATION", ability)
        self.assertIn("continuousTaskCancel", ability)
        self.assertIn("后台连续采集中止", page)

        on_background = ability[
            ability.index("  onBackground(): void {"):
            ability.index("  onForeground(): void {")
        ]
        self.assertNotIn("stopSensors", on_background)

    def test_measurement_waits_for_permission_before_calibration(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        start = page[
            page.index("  private async startMeasurement(): Promise<void> {"):
            page.index("  private startLocationForMeasurement(): void {")
        ]
        permission_await = start.index(
            "const permissionResult = await this.requestLocationPermission();"
        )
        generation_check = start.index(
            "if (operationId !== this.measurementOperationId)"
        )
        estimator_start = start.index("this.stats = this.estimator.start(now);")
        sensor_start = start.index("this.sensorController.start(")
        self.assertLess(permission_await, generation_check)
        self.assertLess(generation_check, estimator_start)
        self.assertLess(estimator_start, sensor_start)
        self.assertIn("this.measurementStartPending = true;", start)
        self.assertIn("if (permissionResult.granted)", start)
        self.assertIn("stateEffect: !this.measurementStartPending", page)
        self.assertIn(".focusable(!this.measurementStartPending)", page)
        self.assertIn(
            "private locationPermissionRequest: "
            "Promise<LocationPermissionResult> | null = null;",
            page,
        )
        self.assertIn("return await this.locationPermissionRequest;", page)

        permission_helper = page[
            page.index(
                "  private async requestLocationPermission(): "
                "Promise<LocationPermissionResult> {"
            ):
            page.index("  private markResearchEventWithNotes", page.index(
                "  private async requestLocationPermission(): "
            ))
        ]
        self.assertNotIn("this.locationStatus =", permission_helper)
        self.assertIn("} finally {", permission_helper)
        self.assertIn("if (!permissionResult.granted) {", page)

    def test_disabled_controls_keep_accessible_contrast(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        index = self.read("entry/src/main/ets/pages/Index.ets")
        self.assertNotIn(".enabled(", page)
        self.assertNotIn(".enabled(", index)
        self.assertNotIn("disabledButtonStyle", page)
        self.assertNotIn("disabledButtonStyle", index)
        self.assertEqual(page.count("HitTestMode.BLOCK_DESCENDANTS"), 5)
        self.assertEqual(index.count("HitTestMode.BLOCK_DESCENDANTS"), 1)
        self.assertIn("'停车校准，不可用'", page)
        self.assertIn("'导出，不可用'", page)
        self.assertIn("if (this.researchExportInProgress) {", page)
        self.assertIn(
            "if (!this.hasResearchLog() || this.researchStatus.running || "
            "this.researchExportInProgress) {",
            page,
        )
        self.assertIn(
            ".fontColor(this.measurementStartPending ? '#CBD5E1'", page
        )
        self.assertIn(
            "this.isRunning ? '#DC2626' : '#4FD1C5'", page
        )
        self.assertNotIn("this.isRunning ? '#EF4444' : '#4FD1C5'", page)

    def test_anchor_freeze_is_labeled_as_an_experimental_control(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        self.assertIn("Text('GNSS 锚点冻结')", page)
        self.assertNotIn("Text('定位状态')", page)
        self.assertNotIn("Text('隧道模式')", page)
        self.assertNotIn("入隧时开启，出隧后关闭", page)
        self.assertIn("仅供对照实验", page)
        self.assertIn("\u4ec5\u4f9b\u5bf9\u7167\u5b9e\u9a8c\\n\u505c\u6b62\u5237\u65b0\u951a\u70b9\uff0c\u53ef\u80fd\u589e\u52a0\u6f02\u79fb", page)
        self.assertIn("已冻结：不保证提高精度", page)
        self.assertIn("关闭 GNSS 锚点冻结，恢复刷新", page)
        self.assertIn(".selectedColor('#F59E0B')", page)
        self.assertIn("Text(this.locationStatus)", page)
        self.assertNotIn("private TunnelPanel()", page)
        anchor_control = page[
            page.index("  private AnchorFreezeControl() {"):
            page.index("  private ResearchPanel() {")
        ]
        self.assertNotIn("Text(this.locationStatus)", anchor_control)
        self.assertNotIn("private LocationStatusPanel()", page)
        research_panel = page[
            page.index("  private ResearchPanel() {"):
            page.index("  build() {")
        ]
        dynamic_background = (
            ".backgroundColor(this.tunnelSwitchOn ? '#251E16' : '#0F172A')"
        )
        self.assertEqual(research_panel.count(dynamic_background), 1)
        self.assertNotIn("#3A2711", research_panel)
        self.assertIn("'#A36D22' : '#1E293B'", research_panel)
        self.assertIn("'#8E6A35' : '#475569'", research_panel)
        self.assertEqual(page.count("'#B8C2D0'"), 3)
        self.assertIn("? '#0B1020' : '#94A3B8'", research_panel)
        self.assertIn("? '#F59E0B' : '#1E293B'", research_panel)
        self.assertNotIn("'#B7791F'", research_panel)
        self.assertNotIn("'#2A958E'", research_panel)
        self.assertIn("this.AnchorFreezeControl();", research_panel)
        self.assertIn("Text(this.locationStatus)", research_panel)
        self.assertLess(research_panel.index("this.AnchorFreezeControl();"),
                        research_panel.index("Text(this.locationStatus)"))
        event_helper = page[
            page.index("  private markResearchEventWithNotes"):
            page.index("  private setTunnelStateFromSwitch")
        ]
        self.assertNotIn("this.locationStatus =", event_helper)

    def test_gyroscope_is_required_for_measurement_but_not_raw_recording(self) -> None:
        controller = self.read("entry/src/main/ets/model/SensorController.ets")
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        self.assertIn("requireGyroscope: boolean = true", controller)
        self.assertIn("private subscribeGyroscope(): boolean", controller)
        self.assertIn("if (!gyroscopeStarted && requireGyroscope)", controller)
        self.assertIn("get isGyroscopeAvailable(): boolean", controller)

        research_start = page[
            page.index("  private startResearchSensors(): boolean {"):
            page.index("  private ensureResearchSensorsOrStop(): boolean {")
        ]
        self.assertIn("}, false);", research_start)
        self.assertIn("研究记录已降级：陀螺仪不可用", research_start)
        self.assertIn("研究记录传感器降级", page)
        self.assertIn("private subscriptionGeneration: number = 0;", controller)
        self.assertIn("private auxiliaryGeneration: number = 0;", controller)
        self.assertIn("this.subscriptionGeneration += 1;", controller)
        self.assertIn("this.auxiliaryGeneration += 1;", controller)
        self.assertIn("this.isCoreSubscriptionActive(generation)", controller)
        self.assertIn(
            "this.isAuxiliarySubscriptionActive(generation, auxiliaryGeneration)",
            controller,
        )

    def test_sensor_unsubscribe_is_subscription_aware_and_idempotent(self) -> None:
        controller = self.read("entry/src/main/ets/model/SensorController.ets")
        subscriptions = (
            ("accelerometerSubscribed", "safeOffAccelerometer"),
            ("gyroscopeSubscribed", "safeOffGyroscope"),
            ("rotationVectorSubscribed", "safeOffRotationVector"),
            ("magneticFieldSubscribed", "safeOffMagneticField"),
            ("uncalibratedGyroscopeSubscribed", "safeOffUncalibratedGyroscope"),
            ("uncalibratedMagneticFieldSubscribed", "safeOffUncalibratedMagneticField"),
        )
        for subscribed, method in subscriptions:
            self.assertIn(f"private {subscribed}: boolean = false;", controller)
            self.assertIn(
                f"private {method}(): boolean {{\n"
                f"    if (!this.{subscribed})",
                controller,
            )
            self.assertIn(f"this.{subscribed} = false;", controller)

        self.assertIn("this.accelerometerSubscribed = true;", controller)
        self.assertIn("this.gyroscopeSubscribed = true;", controller)
        self.assertIn("this.rotationVectorSubscribed = true;", controller)
        self.assertIn("this.magneticFieldSubscribed = true;", controller)
        self.assertIn("this.uncalibratedGyroscopeSubscribed = true;", controller)
        self.assertIn("this.uncalibratedMagneticFieldSubscribed = true;", controller)
        self.assertIn("const coreCleanupSucceeded = this.stop();", controller)
        self.assertIn("上次核心传感器退订失败，请重试", controller)
        self.assertIn("private auxiliaryCleanupPending: boolean = false;", controller)
        self.assertIn("if (this.auxiliaryCleanupPending)", controller)
        self.assertIn("上次辅助传感器退订失败，仍记录核心传感器", controller)
        auxiliary_start = controller[
            controller.index("  startAuxiliarySensors(): void {"):
            controller.index("  stopAuxiliarySensors(): boolean {")
        ]
        self.assertNotIn("this.auxiliaryGeneration += 1;", auxiliary_start)

        stop_block = controller[
            controller.index("  stop(): boolean {"):
            controller.index("  private subscribeAcceleration(): boolean {")
        ]
        self.assertIn("let coreCleanupSucceeded = true;", stop_block)
        self.assertIn("let auxiliaryCleanupSucceeded = true;", stop_block)
        self.assertIn("this.auxiliaryCleanupPending = !auxiliaryCleanupSucceeded;", stop_block)
        self.assertIn("return coreCleanupSucceeded;", stop_block)
        self.assertNotIn("return auxiliaryCleanupSucceeded;", stop_block)

    def test_location_unsubscribe_is_owned_and_blocks_duplicate_restart(self) -> None:
        controller = self.read("entry/src/main/ets/model/LocationController.ets")
        self.assertIn("private locationChangeHandler?: LocationChangeHandler;", controller)
        self.assertIn("private satelliteStatusHandler?: SatelliteStatusHandler;", controller)
        self.assertIn("private locationSubscribed: boolean = false;", controller)
        self.assertIn("private satelliteSubscribed: boolean = false;", controller)
        self.assertIn("private subscriptionGeneration: number = 0;", controller)

        start_block = controller[
            controller.index("  start(callback: LocationCallback"):
            controller.index("  stop(): boolean {")
        ]
        self.assertIn("const cleanupSucceeded = this.stop();", start_block)
        self.assertIn("if (!cleanupSucceeded)", start_block)
        self.assertIn("上次定位退订失败，请重试", start_block)
        self.assertIn("const generation = this.subscriptionGeneration;", start_block)
        self.assertIn("this.isSubscriptionActive(generation)", start_block)
        self.assertIn("this.locationChangeHandler = locationChangeHandler;", start_block)
        self.assertIn("this.locationSubscribed = true;", start_block)

        stop_block = controller[
            controller.index("  stop(): boolean {"):
            controller.index("  private emitStatus", controller.index("  stop(): boolean {"))
        ]
        self.assertIn("this.subscriptionGeneration += 1;", stop_block)
        self.assertIn("if (!this.safeOffLocationChange()) cleanupSucceeded = false;", stop_block)
        self.assertIn("if (!this.safeOffSatelliteStatus()) cleanupSucceeded = false;", stop_block)
        self.assertIn("return cleanupSucceeded;", stop_block)

        satellite_block = controller[
            controller.index("  private subscribeSatelliteStatus"):
            controller.index("  private isSubscriptionActive")
        ]
        self.assertIn("this.isSubscriptionActive(generation)", satellite_block)
        self.assertIn("this.satelliteStatusHandler = satelliteStatusHandler;", satellite_block)
        self.assertIn("this.satelliteSubscribed = true;", satellite_block)

        location_off = controller[
            controller.index("  private safeOffLocationChange"):
            controller.index("  private safeOffSatelliteStatus")
        ]
        self.assertIn("if (!this.locationSubscribed)", location_off)
        self.assertIn(
            "geoLocationManager.off('locationChange', this.locationChangeHandler);",
            location_off,
        )
        self.assertIn("this.locationSubscribed = false;", location_off)
        self.assertIn("this.locationChangeHandler = undefined;", location_off)

        satellite_off = controller[controller.index("  private safeOffSatelliteStatus"):]
        self.assertIn("if (!this.satelliteSubscribed)", satellite_off)
        self.assertIn(
            "geoLocationManager.off('satelliteStatusChange', this.satelliteStatusHandler);",
            satellite_off,
        )
        self.assertIn("this.satelliteSubscribed = false;", satellite_off)
        self.assertIn("this.satelliteStatusHandler = undefined;", satellite_off)
        self.assertNotIn("geoLocationManager.off('locationChange');", controller)
        self.assertNotIn("geoLocationManager.off('satelliteStatusChange');", controller)

    def test_tunnel_anchor_usability_is_frozen_separately(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        self.assertIn("private gnssAnchorUsable: boolean = false;", page)
        self.assertIn("if (this.tunnelState !== 'inside')", page)
        self.assertIn("const anchorApplied = this.hasGnssAnchor && this.gnssAnchorUsable;", page)
        self.assertNotIn("const gnssReliable = this.gnssSpeedAccuracyMps", page)
        anchor_start = page.index("const reliableAnchor =")
        anchor_end = page.index("this.gnssAnchorUsable = reliableAnchor;", anchor_start)
        reliable_anchor_block = page[anchor_start:anchor_end]
        self.assertIn("location.speedMps >= speedAcc", reliable_anchor_block)
        self.assertIn("(src === 1 || src === 4)", reliable_anchor_block)
        self.assertNotIn("MotionState", reliable_anchor_block)
        self.assertNotIn("Vibration", reliable_anchor_block)

        replay = self.read("tools/replay_estimator.py")
        replay_start = replay.index('if running and row.get("recordType") == "location":')
        replay_end = replay.index("if not running or row.get", replay_start)
        replay_anchor_block = replay[replay_start:replay_end]
        self.assertNotIn("in_vibration", replay_anchor_block)
        self.assertNotIn("MotionState.", replay_anchor_block)

    def test_display_statistics_follow_the_displayed_speed(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        self.assertIn("updateDisplayStats(this.stats.currentSpeedKmh", page)
        self.assertIn("stats.maxSpeedKmh = this.displayMaxSpeedKmh;", page)
        self.assertIn("this.displayDistanceM / (this.displayElapsedMs / 1000)", page)

    def test_replay_labels_anchor_and_display_speed_separately(self) -> None:
        replay = self.read("tools/replay_estimator.py")
        baseline = self.read("tools/_baseline_all.py")
        self.assertIn('summary["anchoredDisplaySpeed"]', replay)
        self.assertIn('summary["rawEligibleAnchorSpeed"]', replay)
        self.assertIn('"deprecatedAliasFor": "anchoredDisplaySpeed"', replay)
        self.assertIn('d.get("anchoredDisplaySpeed", {})', baseline)
        self.assertNotIn('d.get("anchorSpeed", {})', baseline)

    def test_light_timing_is_monotonic_and_count_is_cumulative(self) -> None:
        page = self.read("entry/src/main/ets/pages/TunnelLight.ets")
        estimator = self.read(
            "entry/src/main/ets/model/TunnelLightSpeedEstimator.ets"
        )
        self.assertIn("systemDateTime.TimeType.STARTUP", page)
        self.assertNotIn("this.estimator.tap(Date.now())", page)
        self.assertIn("private totalTapCount: number = 0;", estimator)
        self.assertIn("tapCount: this.totalTapCount", estimator)

    def test_handheld_detection_and_warning_contract(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        self.assertIn("const currentGyroscopeTimestampMs =", page)
        self.assertIn(
            "const gyroCutoffMs = currentGyroscopeTimestampMs - "
            "this.GYRO_WINDOW_MS;",
            page,
        )
        self.assertNotIn(
            "const gyroCutoffMs = frame.timestampMs - this.GYRO_WINDOW_MS;",
            page,
        )
        self.assertIn("@State private speedStatsHeight: number = 0;", page)
        self.assertIn("Text('请稳定放置')", page)
        self.assertIn("停车时重新开始测速", page)
        self.assertIn("Text('改用灯光打点测速')", page)
        self.assertIn(".width('58%')", page)
        self.assertIn(".border({ width: 1, color: '#FECACA' })", page)
        self.assertIn(".backgroundColor('#DC2626')", page)
        self.assertNotIn("Button('留在此页'", page)


if __name__ == "__main__":
    unittest.main()
