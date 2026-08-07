from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter = max(luminance(foreground), luminance(background))
    darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


class ReleaseContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def test_signing_uses_project_compatible_api(self) -> None:
        source = self.read("tools/sign_app.ps1")
        self.assertIn("Get-ProjectCompatibleApiVersion", source)
        self.assertIn("function Get-AppVersionMetadata", source)
        self.assertIn('$packInfoEntry = $archive.GetEntry("pack.info")', source)
        self.assertIn(
            '"MetroSpeed-$($appVersion.Name)-$($appVersion.Code)-release.app"',
            source,
        )
        self.assertNotIn('"MetroSpeed-release.app"', source)
        compression_load = source.index(
            "Add-Type -AssemblyName System.IO.Compression.FileSystem"
        )
        output_guard_start = source.index("if (-not $OutputPath) {")
        output_guard_end = source.index("\n}\n\n$signingDir", output_guard_start)
        output_guard = source[output_guard_start:output_guard_end]
        self.assertLess(compression_load, output_guard_start)
        self.assertIn("Get-AppVersionMetadata -Path $AppPath", output_guard)
        self.assertEqual(source.count("Get-AppVersionMetadata -Path"), 1)
        self.assertIn("与项目配置", source)
        self.assertNotIn('$compatibleVersion = "12"', source)
        self.assertIn(
            "$keystorePassword = $env:METROSPEED_KEYSTORE_PASSWORD", source
        )
        self.assertIn("$keyPassword = $env:METROSPEED_KEY_PASSWORD", source)
        self.assertIn("$keyPassword = $keystorePassword", source)
        self.assertIn("'-keyPwd', $keyPassword", source)
        self.assertIn("'-keystorePwd', $keystorePassword", source)

    def test_release_docs_distinguish_source_state_and_candidate_eligibility(self) -> None:
        readme = self.read("README.md")
        status = self.read(".trae/documents/investigation_status.md")
        rules = self.read(".trae/rules/project_rules.md")

        self.assertIn("GitHub 最新公开 Release 仍为 `v1.2.0`", readme)
        self.assertIn("AppGallery 当前已上架 `1.2.1`", readme)
        self.assertIn("不存在远端 `release/1.2.0` 分支", readme)
        self.assertNotIn("`release/1.2.0` 固定保存", readme)
        self.assertNotIn("本地正式候选", readme)
        self.assertIn("AppGallery 当前在架版本", status)
        self.assertIn("当前源码发布状态：尚无同源正式 APP", status)
        self.assertIn("不能作为当前源码的 GitHub Release 资产", status)
        self.assertNotIn("versionCode `1786008552` 不得提交", status)
        self.assertIn("`REG-001` 精确清单尚未闭环", status)
        self.assertIn(
            "1,541,653,379 字节（约 1.54 GB / 1.44 GiB）", status
        )
        self.assertNotIn("1.47 GB", status)
        self.assertNotIn("<四个显式输入>", status)
        self.assertIn("四条当前代真实记录", rules)
        self.assertIn("README 不维护真实回归记录的数据资产表", rules)
        self.assertNotIn("**数据资产表** —", rules)

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

    def test_initial_calibration_failure_preserves_rejected_snapshot(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        recorder = self.read("entry/src/main/ets/model/ResearchRecorder.ets")
        sensor_frame = page[
            page.index("  private onSensorFrame(frame: SensorFrame): void {"):
            page.index("  private resetDisplayStats(): void {")
        ]
        self.assertIn("this.abortStartCalibration(nextFrame);", sensor_frame)

        abort = page[
            page.index(
                "  private abortStartCalibration(rejectedFrame: EstimatorFrame): void {"
            ):
            page.index("  private async toggleResearchRecording(): Promise<void> {")
        ]
        notes_index = abort.index("const rejectionNotes =")
        snapshot_index = abort.index("this.researchRecorder.updateEstimator(")
        reset_index = abort.index("this.estimator.reset(now)")
        cancel_index = abort.index(
            "this.researchRecorder.cancelMeasurement(now, rejectionNotes)"
        )
        self.assertLess(notes_index, reset_index)
        self.assertLess(snapshot_index, reset_index)
        self.assertLess(reset_index, cancel_index)
        self.assertIn("reason=initial_calibration_unstable", abort)
        self.assertIn(
            "calibrationRejected=${rejectionStats.calibrationRejected}", abort
        )
        self.assertIn("status=${rejectionStats.statusText}", abort)
        self.assertIn("confidence=${rejectionStats.confidence.toFixed(2)}", abort)
        self.assertIn("state=${motionStateText(rejectionStats.motionState)}", abort)
        self.assertNotIn("resetStats.statusText", abort)

        cancel = recorder[
            recorder.index("  cancelMeasurement(timestampMs: number, notes: string)"):
            recorder.index("  clearEstimatorSnapshot(): void")
        ]
        append = recorder[
            recorder.index("  private appendRecord("):
            recorder.index("  private appendLocationFields(")
        ]
        self.assertIn("this.appendRecord(timestampMs, '开始失败', notes);", cancel)
        self.assertIn("if (recordType === 'event' && stats)", append)
        self.assertIn("this.appendEstimatorSummary(record, stats);", append)

    def test_disabled_controls_keep_accessible_contrast(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        index = self.read("entry/src/main/ets/pages/Index.ets")
        self.assertNotIn(".enabled(", page)
        self.assertNotIn(".enabled(", index)
        self.assertNotIn("disabledButtonStyle", page)
        self.assertNotIn("disabledButtonStyle", index)
        # Six guarded controls, plus the GNSS anchor-freeze control and the
        # non-interactive export overlay that lets the parent Scroll keep gestures.
        self.assertEqual(page.count("HitTestMode.BLOCK_DESCENDANTS"), 8)
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
        self.assertIn("}, false, (error: CoreSensorFatalError)", research_start)
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
        self.assertIn("this.stop();", controller)
        self.assertIn("this.accelerometerSubscribed || this.gyroscopeSubscribed", controller)
        self.assertIn("上次核心传感器退订失败，请重试", controller)
        self.assertIn("private auxiliaryCleanupPending: boolean = false;", controller)
        self.assertIn("if (this.auxiliaryCleanupPending)", controller)
        self.assertIn("上次辅助传感器退订失败，仍记录核心传感器", controller)
        self.assertIn("上次辅助传感器退订失败，仍启动核心传感器", controller)
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
        self.assertIn(
            "return coreCleanupSucceeded && auxiliaryCleanupSucceeded;",
            stop_block,
        )

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
        anchor_start = page.index("private isReliableGnssAnchor")
        anchor_end = page.index("private isCurrentGnss", anchor_start)
        reliable_anchor_block = page[anchor_start:anchor_end]
        self.assertIn("location.speedMps >= speedAccuracyMps", reliable_anchor_block)
        self.assertIn("(sourceType === 1 || sourceType === 4)", reliable_anchor_block)
        self.assertNotIn("locationAgeMs", reliable_anchor_block)
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
        self.assertIn("Number.isFinite(frame.sensorTimestamp)", page)
        self.assertIn("frame.sensorTimestamp > 0", page)
        self.assertIn("? frame.sensorTimestamp / 1000000", page)
        self.assertIn(": frame.timestampMs", page)
        self.assertIn("stats.maxSpeedKmh = this.displayMaxSpeedKmh;", page)
        self.assertIn("this.displayDistanceM / (this.displayElapsedMs / 1000)", page)

    def test_gnss_display_rejects_invalid_or_stale_updates_and_expires(self) -> None:
        page = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        location = page[
            page.index("  private startLocationForAll"):
            page.index("  private stopLocationIfUnused")
        ]
        validation = page[
            page.index("function isFiniteLocationSnapshot"):
            page.index("@Entry")
        ]
        self.assertIn("Number.isFinite(location.speedMps)", validation)
        self.assertIn("Number.isFinite(satellite.satelliteCn0Max)", validation)
        self.assertLess(
            location.index("if (!isFiniteLocationSnapshot(location))"),
            location.index("this.researchRecorder.updateLocation(location)"),
        )
        self.assertIn("this.isReliableGnssAnchor(location)", location)
        self.assertIn("this.isDisplayableGnss(location)", location)
        self.assertNotIn("reliableAnchor && this.isCurrentGnss(location)", location)
        self.assertIn("location.locationAgeMs <= this.GNSS_DISPLAY_FRESHNESS_MS", location)
        self.assertIn("sourceType === 1 || sourceType === 4", location)
        self.assertIn("this.GNSS_DISPLAY_FRESHNESS_MS - locationAgeMs", location)
        self.assertIn("}, remainingFreshnessMs);", location)
        self.assertIn("const generation = ++this.gnssDisplayGeneration;", location)
        self.assertIn("generation === this.gnssDisplayGeneration", location)
        self.assertIn("this.gnssSpeedKmh = null;", location)
        self.assertIn("Text(this.gnssSpeedKmh === null ? '—'", page)
        self.assertIn("const cleanupSucceeded = this.locationController.stop();", page)
        self.assertIn("定位退订失败，请重试", page)

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
        warning_body = page[
            page.index("Text('请将设备稳定放置"):
            page.index(".margin({ top: 14 });", page.index("Text('请将设备稳定放置"))
        ]
        self.assertIn(".fontColor('#FFFFFF')", warning_body)
        self.assertGreaterEqual(contrast_ratio("#FFFFFF", "#DC2626"), 4.5)
        self.assertNotIn("Button('留在此页'", page)

    def test_mode_pages_guard_back_navigation_and_label_light_distance(self) -> None:
        inertial = self.read("entry/src/main/ets/pages/InertialSpeed.ets")
        light = self.read("entry/src/main/ets/pages/TunnelLight.ets")
        return_bodies = (
            inertial[
                inertial.index("private returnToModeSelection"):
                inertial.index("private switchToTunnelLightSpeed")
            ],
            light[
                light.index("private returnToModeSelection"):
                light.index("private updateDistance")
            ],
        )
        for return_body in return_bodies:
            self.assertIn("if (this.navigationInProgress)", return_body)
            self.assertIn("this.navigationInProgress = true", return_body)
            self.assertIn("getRouter().back();", return_body)
            self.assertNotIn("getRouter().back().then", return_body)
            self.assertIn("this.navigationInProgress = false", return_body)
        self.assertIn(".accessibilityText('相邻灯光间距，单位米')", light)
        self.assertIn("placeholder: '例如 9.6'", light)


if __name__ == "__main__":
    unittest.main()
