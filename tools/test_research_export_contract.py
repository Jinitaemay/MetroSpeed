from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDER_SOURCE = (
    REPO_ROOT / "entry" / "src" / "main" / "ets" / "model" / "ResearchRecorder.ets"
)
PAGE_SOURCE = (
    REPO_ROOT / "entry" / "src" / "main" / "ets" / "pages" / "InertialSpeed.ets"
)
BACKGROUND_STATE_SOURCE = (
    REPO_ROOT / "entry" / "src" / "main" / "ets" / "model" / "BackgroundState.ets"
)
ENTRY_ABILITY_SOURCE = (
    REPO_ROOT
    / "entry"
    / "src"
    / "main"
    / "ets"
    / "entryability"
    / "EntryAbility.ets"
)


class ResearchExportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RECORDER_SOURCE.read_text(encoding="utf-8")
        cls.page_source = PAGE_SOURCE.read_text(encoding="utf-8")
        cls.background_source = BACKGROUND_STATE_SOURCE.read_text(encoding="utf-8")
        cls.ability_source = ENTRY_ABILITY_SOURCE.read_text(encoding="utf-8")

    def test_successful_export_keeps_the_local_source(self) -> None:
        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]

        self.assertIn("await this.copyTempLogToUri", export_body)
        self.assertIn("仍可再次导出", export_body)
        self.assertNotIn("unlinkSync(sourcePath)", export_body)
        self.assertNotIn("removeExportedTempLog", export_body)
        self.assertNotIn("private removeExportedTempLog", self.source)

    def test_new_session_is_persisted_before_old_logs_are_retired(self) -> None:
        start_begin = self.source.index("start(timestampMs:")
        start_end = self.source.index("\n  stop(timestampMs:", start_begin)
        start_body = self.source[start_begin:start_end]

        open_index = start_body.index("openTempLogForNewSession()")
        flush_index = start_body.index("flushTempLogFile()", open_index)
        refresh_index = start_body.index("refreshExportableLogState()", flush_index)
        cleanup_index = start_body.index("clearSupersededPendingLogs", flush_index)
        self.assertLess(open_index, flush_index)
        self.assertLess(flush_index, refresh_index)
        self.assertLess(refresh_index, cleanup_index)
        self.assertGreaterEqual(start_body.count("rollbackFailedSessionStart"), 2)

        target_begin = self.source.index("private newSessionTarget")
        target_end = self.source.index("\n  private clearSupersededPendingLogs", target_begin)
        target_body = self.source[target_begin:target_end]
        self.assertIn("SESSION_LOG_PREFIX", target_body)
        self.assertIn("SESSION_LOG_SUFFIX", target_body)
        self.assertNotIn("LEGACY_TEMP_LOG_FILE_NAME", target_body)

    def test_failed_session_start_restores_the_previous_exportable_log(self) -> None:
        rollback_begin = self.source.index("private rollbackFailedSessionStart")
        rollback_end = self.source.index("\n  private integrityMarkerPath", rollback_begin)
        rollback_body = self.source[rollback_begin:rollback_end]

        self.assertIn("fs.unlinkSync(failedPath)", rollback_body)
        self.assertIn("selectNewestPendingLog(failedPath)", rollback_body)
        self.assertIn("refreshExportableLogState()", rollback_body)
        self.assertIn("restoreExportableLogSummary()", rollback_body)
        self.assertIn("start_record_failed", rollback_body)
        self.assertIn("if (path === excludedPath)", self.source)

    def test_failed_session_residue_is_quarantined_across_restart(self) -> None:
        self.assertIn("DISCARDED_LOG_MARKER_SUFFIX", self.source)
        self.assertIn("this.quarantineFailedSession(failedPath);", self.source)
        self.assertIn("if (this.hasDiscardedMarker(path))", self.source)

    def test_export_copy_is_chunked_async_and_periodically_yields(self) -> None:
        copy_begin = self.source.index("private async copyTempLogToUri")
        copy_end = self.source.index("\n  private closeTempLogFile", copy_begin)
        copy_body = self.source[copy_begin:copy_end]

        self.assertIn("EXPORT_COPY_BUFFER_BYTES", copy_body)
        self.assertIn("EXPORT_YIELD_EVERY_CHUNKS", copy_body)
        self.assertIn("await fs.read", copy_body)
        self.assertIn("await fs.write", copy_body)
        self.assertIn("await this.yieldExportLoop()", copy_body)
        self.assertIn("copiedBytes === expectedBytes", copy_body)
        self.assertIn("let copySucceeded = false;", copy_body)
        self.assertIn("return copySucceeded;", copy_body)

    def test_export_retries_valid_partial_chunk_writes(self) -> None:
        copy_begin = self.source.index("private async copyTempLogToUri")
        copy_end = self.source.index("\n  private closeTempLogFile", copy_begin)
        copy_body = self.source[copy_begin:copy_end]

        self.assertIn("let chunkOffset = 0;", copy_body)
        self.assertIn("while (chunkOffset < bytesRead)", copy_body)
        self.assertIn("buffer.slice(chunkOffset, bytesRead)", copy_body)
        self.assertIn("bytesWritten <= 0 || bytesWritten > remainingBytes", copy_body)
        self.assertIn("chunkOffset += bytesWritten;", copy_body)
        self.assertNotIn("bytesWritten !== bytesRead", copy_body)

    def test_export_progress_success_phases_are_ordered(self) -> None:
        self.assertIn(
            "export type ResearchExportPhase = "
            "'choosing' | 'copying' | 'syncing' | 'completed' | 'failed';",
            self.source,
        )

        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]
        choosing_index = export_body.index("phase: 'choosing'")
        picker_index = export_body.index("await documentPicker.save", choosing_index)
        copy_index = export_body.index("await this.copyTempLogToUri", picker_index)
        completed_index = export_body.index("phase: 'completed'", copy_index)
        self.assertLess(choosing_index, picker_index)
        self.assertLess(picker_index, copy_index)
        self.assertLess(copy_index, completed_index)

        copy_begin = self.source.index("private async copyTempLogToUri")
        copy_end = self.source.index("\n  private closeTempLogFile", copy_begin)
        copy_body = self.source[copy_begin:copy_end]
        copying_index = copy_body.index("phase: 'copying'")
        read_loop_index = copy_body.index("while (true)", copying_index)
        syncing_index = copy_body.index("phase: 'syncing'", read_loop_index)
        fsync_index = copy_body.index("await fs.fsync(targetFile.fd)", syncing_index)
        self.assertLess(copying_index, read_loop_index)
        self.assertLess(read_loop_index, syncing_index)
        self.assertLess(syncing_index, fsync_index)
        self.assertNotIn("phase: 'completed'", copy_body)

    def test_export_progress_bytes_only_advance_and_keep_one_total(self) -> None:
        copy_begin = self.source.index("private async copyTempLogToUri")
        copy_end = self.source.index("\n  private closeTempLogFile", copy_begin)
        copy_body = self.source[copy_begin:copy_end]

        self.assertEqual(copy_body.count("let copiedBytes = 0;"), 1)
        self.assertEqual(copy_body.count("copiedBytes += bytesWritten;"), 1)
        copied_mutations = re.findall(
            r"^\s*copiedBytes\s*([+\-]?=)", copy_body, flags=re.MULTILINE
        )
        self.assertEqual(copied_mutations, ["+="])

        progress_blocks = re.findall(
            r"onProgress\(\{(?P<body>.*?)\}\);", copy_body, flags=re.DOTALL
        )
        self.assertGreaterEqual(len(progress_blocks), 4)
        for progress_body in progress_blocks:
            self.assertIn("totalBytes: expectedBytes", progress_body)
            if "phase: 'copying'" in progress_body and "copiedBytes: 0" not in progress_body:
                self.assertRegex(progress_body, r"\bcopiedBytes\b")

    def test_completed_progress_is_emitted_only_after_copy_handles_close(self) -> None:
        copy_begin = self.source.index("private async copyTempLogToUri")
        copy_end = self.source.index("\n  private closeTempLogFile", copy_begin)
        copy_body = self.source[copy_begin:copy_end]
        source_close_index = copy_body.index("await fs.close(sourceFile)")
        target_close_index = copy_body.index("await fs.close(targetFile)")
        return_index = copy_body.index("return copySucceeded;")
        self.assertLess(source_close_index, return_index)
        self.assertLess(target_close_index, return_index)
        self.assertEqual(copy_body.count("return copySucceeded;"), 1)

        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]
        copy_index = export_body.index("await this.copyTempLogToUri")
        completed_index = export_body.index("phase: 'completed'", copy_index)
        self.assertLess(copy_index, completed_index)

    def test_candidate_stat_failure_skips_only_that_log(self) -> None:
        select_begin = self.source.index("private selectNewestPendingLog")
        select_end = self.source.index("\n  private openTempLogForNewSession", select_begin)
        select_body = self.source[select_begin:select_end]
        helper_begin = self.source.index("private isNonEmptyLogCandidate")
        helper_end = self.source.index("\n  private openTempLogForNewSession", helper_begin)
        helper_body = self.source[helper_begin:helper_end]

        self.assertIn("if (!this.isNonEmptyLogCandidate(path))", select_body)
        self.assertIn("continue;", select_body)
        self.assertIn("this.isNonEmptyLogCandidate(legacyPath)", select_body)
        self.assertIn("return fs.statSync(path).size > 0;", helper_body)
        self.assertIn("catch (error)", helper_body)
        self.assertIn("return false;", helper_body)

    def test_failed_export_removes_partial_picker_target_without_leaving_zero_bytes(self) -> None:
        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]
        cleanup_begin = self.source.index("private async cleanupFailedExportTarget")
        cleanup_end = self.source.index("\n  private closeTempLogFile", cleanup_begin)
        cleanup_body = self.source[cleanup_begin:cleanup_end]

        self.assertGreaterEqual(export_body.count("cleanupFailedExportTarget"), 4)
        self.assertGreaterEqual(
            export_body.count("await this.cleanupFailedExportTarget"), 4
        )
        self.assertIn("if (deviceInfo.sdkApiVersion >= 23)", export_body)
        self.assertIn("saveOptions.autoCreateEmptyFile = false;", export_body)
        self.assertIn("documentPicker.save(saveOptions)", export_body)
        self.assertIn("-export-${exportAttemptMs}.jsonl", export_body)
        self.assertIn("fs.OpenMode.CREATE | fs.OpenMode.TRUNC", self.source)
        self.assertIn("Promise<boolean>", cleanup_body)
        self.assertIn("fileManagerService.deleteToTrash(uri)", cleanup_body)
        self.assertIn("deleteError.code === 1014000002", cleanup_body)
        self.assertNotIn("fs.unlink(uri)", cleanup_body)
        self.assertNotIn("fs.stat(uri)", cleanup_body)
        self.assertNotIn("fs.OpenMode.TRUNC", cleanup_body)
        self.assertNotIn("unlinkSync(uri)", cleanup_body)
        self.assertIn("uri === sourcePath", cleanup_body)
        self.assertIn("保存位置可能残留未完成文件，请手动删除", self.source)

    def test_export_keeps_screen_on_without_expanding_location_long_task_scope(self) -> None:
        collection_begin = self.background_source.index("static isCollectionActive")
        collection_end = self.background_source.index(
            "\n  static shouldKeepScreenOn", collection_begin
        )
        collection_body = self.background_source[collection_begin:collection_end]
        keep_screen_body = self.background_source[collection_end:]
        self.assertIn("static exportActive: boolean = false;", self.background_source)
        self.assertNotIn("exportActive", collection_body)
        self.assertIn("BackgroundState.exportActive", keep_screen_body)

        keep_begin = self.ability_source.index("private updateKeepScreenOn")
        keep_end = self.ability_source.index("\n  onWindowStageCreate", keep_begin)
        keep_body = self.ability_source[keep_begin:keep_end]
        self.assertIn("BackgroundState.shouldKeepScreenOn()", keep_body)

        long_task_begin = self.ability_source.index("private startLongTask")
        long_task_end = self.ability_source.index("\n  private stopLongTask", long_task_begin)
        long_task_body = self.ability_source[long_task_begin:long_task_end]
        self.assertIn("BackgroundMode.LOCATION", long_task_body)
        self.assertIn("BackgroundState.isCollectionActive()", long_task_body)
        self.assertNotIn("shouldKeepScreenOn", long_task_body)
        self.assertNotIn("exportActive", long_task_body)

        export_begin = self.page_source.index("private async exportResearchLog")
        export_end = self.page_source.index(
            "\n  private applyResearchExportProgress", export_begin
        )
        export_body = self.page_source[export_begin:export_end]
        self.assertIn("this.setResearchExportActive(true);", export_body)
        finally_index = export_body.index("finally")
        clear_index = export_body.index("this.setResearchExportActive(false);", finally_index)
        self.assertLess(finally_index, clear_index)

        state_begin = self.page_source.index("private setResearchExportActive")
        state_end = self.page_source.index(
            "\n  private applyResearchExportProgress", state_begin
        )
        state_body = self.page_source[state_begin:state_end]
        self.assertIn("BackgroundState.exportActive = active;", state_body)
        self.assertIn("this.notifyCollectionStateChanged();", state_body)

    def test_same_percent_copy_bytes_are_refreshed_with_time_throttling(self) -> None:
        self.assertIn(
            "RESEARCH_EXPORT_UI_MIN_INTERVAL_MS: number = 250",
            self.page_source,
        )
        progress_begin = self.page_source.index("private applyResearchExportProgress")
        progress_end = self.page_source.index("\n  private exportOverlayTitle", progress_begin)
        progress_body = self.page_source[progress_begin:progress_end]
        self.assertIn("const copiedBytesChanged", progress_body)
        self.assertIn("const samePercentByteRefreshDue", progress_body)
        self.assertIn("now - this.researchExportLastUiUpdateMs", progress_body)
        self.assertIn("this.RESEARCH_EXPORT_UI_MIN_INTERVAL_MS", progress_body)
        self.assertIn("this.researchExportCopiedBytes = progress.copiedBytes;", progress_body)
        self.assertIn("this.researchExportLastUiUpdateMs = now;", progress_body)
        self.assertNotIn(
            "if (!phaseChanged && nextPercent === this.researchExportPercent)",
            progress_body,
        )

    def test_page_blocks_navigation_while_an_export_is_active(self) -> None:
        back_begin = self.page_source.index("onBackPress(): boolean")
        back_end = self.page_source.index("\n  aboutToDisappear():", back_begin)
        back_body = self.page_source[back_begin:back_end]
        self.assertIn("if (!this.researchExportInProgress)", back_body)
        self.assertLess(
            back_body.index("return false;"),
            back_body.index("return true;"),
        )

        return_begin = self.page_source.index("private returnToModeSelection")
        return_end = self.page_source.index("\n  private switchToTunnelLightSpeed", return_begin)
        return_body = self.page_source[return_begin:return_end]
        export_guard_index = return_body.index("if (this.researchExportInProgress)")
        guard_return_index = return_body.index("return;", export_guard_index)
        router_back_index = return_body.index("getRouter().back()", guard_return_index)
        self.assertLess(export_guard_index, guard_return_index)
        self.assertLess(guard_return_index, router_back_index)

        switch_begin = self.page_source.index("private switchToTunnelLightSpeed")
        switch_end = self.page_source.index("\n  private confidenceText", switch_begin)
        switch_body = self.page_source[switch_begin:switch_end]
        switch_guard_index = switch_body.index(
            "if (this.navigationInProgress || this.researchExportInProgress)"
        )
        replace_index = switch_body.index("getRouter().replaceUrl", switch_guard_index)
        self.assertLess(switch_guard_index, replace_index)

        overlay_begin = self.page_source.index("private ExportOverlay()")
        overlay_end = self.page_source.index("\n  @Builder\n  private ResearchPanel", overlay_begin)
        overlay_body = self.page_source[overlay_begin:overlay_end]
        self.assertIn(
            ".hitTestBehavior(HitTestMode.BLOCK_DESCENDANTS)", overlay_body
        )
        self.assertNotIn(".hitTestBehavior(HitTestMode.Block)", overlay_body)
        self.assertIn("导出完成后仍可再次导出", overlay_body)
        self.assertIn(".height(this.researchPanelHeight)", overlay_body)
        self.assertNotIn(".height('100%')", overlay_body)
        panel_begin = self.page_source.index("private ResearchPanel()")
        panel_end = self.page_source.index("\n  build()", panel_begin)
        panel_body = self.page_source[panel_begin:panel_end]
        self.assertIn("Stack({ alignContent: Alignment.TopStart })", panel_body)
        self.assertIn(
            "if (this.researchExportInProgress && this.researchPanelHeight > 0)",
            panel_body,
        )
        self.assertIn("this.ExportOverlay();", panel_body)
        self.assertIn("const height = newArea.height as number;", panel_body)
        self.assertIn("if (height > 0 && height !== this.researchPanelHeight)", panel_body)
        self.assertIn("this.researchPanelHeight = height;", panel_body)
        anchor_begin = self.page_source.index("private AnchorFreezeControl()")
        anchor_end = self.page_source.index("\n  @Builder\n  private ExportOverlay", anchor_begin)
        anchor_body = self.page_source[anchor_begin:anchor_end]
        self.assertIn(".focusable(!this.researchExportInProgress)", anchor_body)
        self.assertIn("HitTestMode.BLOCK_DESCENDANTS", anchor_body)
        self.assertIn("if (this.researchExportInProgress)", anchor_body)
        build_begin = self.page_source.index("build()")
        build_body = self.page_source[build_begin:]
        self.assertNotIn("this.ExportOverlay();", build_body)

    def test_page_starts_sensors_before_opening_the_record_file(self) -> None:
        toggle_begin = self.page_source.index("private async toggleResearchRecording")
        toggle_end = self.page_source.index("\n  private startResearchSensors", toggle_begin)
        toggle_body = self.page_source[toggle_begin:toggle_end]

        sensor_start_index = toggle_body.index("if (!this.startResearchSensors())")
        recorder_start_index = toggle_body.index("this.researchRecorder.start")
        file_failure_index = toggle_body.index("if (!this.researchStatus.running)", recorder_start_index)
        sensor_cleanup_index = toggle_body.index("this.stopResearchSensorsIfIdle()", file_failure_index)
        self.assertLess(sensor_start_index, recorder_start_index)
        self.assertLess(recorder_start_index, sensor_cleanup_index)


if __name__ == "__main__":
    unittest.main()
