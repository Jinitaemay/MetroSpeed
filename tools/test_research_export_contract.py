from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDER_SOURCE = (
    REPO_ROOT / "entry" / "src" / "main" / "ets" / "model" / "ResearchRecorder.ets"
)
PAGE_SOURCE = (
    REPO_ROOT / "entry" / "src" / "main" / "ets" / "pages" / "InertialSpeed.ets"
)


class ResearchExportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RECORDER_SOURCE.read_text(encoding="utf-8")
        cls.page_source = PAGE_SOURCE.read_text(encoding="utf-8")

    def test_successful_export_keeps_the_local_source(self) -> None:
        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]

        self.assertIn("await this.copyTempLogToUri", export_body)
        self.assertIn("本地记录已保留", export_body)
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

    def test_failed_export_clears_partial_picker_target(self) -> None:
        export_start = self.source.index("async exportJsonl")
        export_end = self.source.index("\n  status():", export_start)
        export_body = self.source[export_start:export_end]
        cleanup_begin = self.source.index("private cleanupFailedExportTarget")
        cleanup_end = self.source.index("\n  private closeTempLogFile", cleanup_begin)
        cleanup_body = self.source[cleanup_begin:cleanup_end]

        self.assertGreaterEqual(export_body.count("cleanupFailedExportTarget"), 4)
        self.assertIn("fs.OpenMode.TRUNC", cleanup_body)
        self.assertIn("fs.unlinkSync(uri)", cleanup_body)
        self.assertIn("uri === sourcePath", cleanup_body)

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
