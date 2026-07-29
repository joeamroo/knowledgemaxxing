import Foundation

/// The export loop. One conversation per iteration: run the calibrated
/// flow steps through the InteractionDriver, wait for the share URL on
/// the clipboard, verify it resolves, record it, then run the post steps
/// (dismiss + back in scroll mode, dismiss + archive in archive mode).
@MainActor
final class AutomationEngine {
    private let state: AppState
    private let clipboard = ClipboardWatcher()
    private var task: Task<Void, Never>?
    private var pauseRequested = false
    private var stopRequested = false
    /// scroll mode: vertical offset applied to row-anchored steps
    private var rowOffsetY: Double = 0

    init(state: AppState) {
        self.state = state
    }

    private var driver: InteractionDriver {
        if state.config.dryRun {
            let logger = state.logger
            return LoggingDriver(log: { message in logger.log(message) })
        }
        return CoordinateClickDriver()
    }

    // MARK: controls

    func start() {
        guard state.phase == .idle || state.phase == .paused || state.phase == .finished else { return }
        pauseRequested = false
        stopRequested = false
        state.phase = .running
        task = Task { await run() }
    }

    func pause() {
        pauseRequested = true
        state.currentStatus = "Pausing after current conversation..."
    }

    func stop() {
        stopRequested = true
        task?.cancel()
        state.phase = .idle
        state.currentStatus = "Stopped; progress is saved"
    }

    func retryFailures() {
        state.exportManager.removeFailures()
        state.records = state.exportManager.state.records
        start()
    }

    // MARK: main loop

    private func run() async {
        let log = state.logger
        let driver = self.driver
        if let problem = driver.readinessProblem() {
            log.log("FATAL [\(driver.name)]: \(problem)")
            state.phase = .idle
            state.currentStatus = problem
            return
        }
        await driver.focusTarget()

        let config = state.config
        let retry = RetryPolicy(maxAttempts: config.maxRetriesPerConversation, baseDelayMs: 1500)
        let verifier = LinkVerifier(timeoutSeconds: 15)
        var index = state.exportManager.state.nextIndex
        syncRowOffset(for: index, config: config)
        log.log("Run started at index \(index)/\(config.totalConversations) (mode \(config.mode.rawValue), driver \(driver.name), verify \(config.verifyLinks))")

        while index < config.totalConversations, !stopRequested, !Task.isCancelled {
            if pauseRequested {
                state.phase = .paused
                state.currentStatus = "Paused at conversation \(index + 1)"
                log.log("Paused at index \(index)")
                return
            }
            state.currentIndex = index
            let started = Date()

            let url: String? = await retry.run(
                attempt: { await exportOne(index: index, config: config, driver: driver, verifier: verifier) },
                onRetry: { attempt in
                    log.log("Conversation \(index + 1): attempt \(attempt) failed, recovering and retrying")
                    await self.runSteps(self.recoverySteps(config), config: config, driver: driver)
                }
            )

            if let url {
                let duplicate = state.exportManager.isDuplicate(url: url)
                state.exportManager.record(ExportRecord(
                    index: index, title: nil, url: url, timestamp: Date(),
                    success: true, note: duplicate ? "duplicate" : nil,
                    verified: config.verifyLinks ? true : nil
                ))
                log.log("Conversation \(index + 1): captured\(duplicate ? " DUPLICATE" : "") \(url)")
            } else {
                state.exportManager.record(ExportRecord(
                    index: index, title: nil,
                    url: "failed-\(index)-\(Int(Date().timeIntervalSince1970))",
                    timestamp: Date(), success: false,
                    note: "no verified share link after \(config.maxRetriesPerConversation) attempts"
                ))
                log.log("Conversation \(index + 1): FAILED after \(config.maxRetriesPerConversation) attempts, continuing")
            }

            await runSteps(config.postSteps, config: config, driver: driver)
            if config.mode == .scroll {
                await advanceScrollPosition(index: index, config: config, driver: driver)
            }

            state.records = state.exportManager.state.records
            state.perItemSeconds.append(Date().timeIntervalSince(started))
            index += 1
        }

        state.phase = .finished
        state.currentStatus = "Done: \(state.exportManager.successCount) exported, \(state.exportManager.failureCount) failed"
        log.log(state.currentStatus)
    }

    /// One attempt at one conversation. Returns a (verified) share URL or nil.
    private func exportOne(
        index: Int, config: ExporterConfig,
        driver: InteractionDriver, verifier: LinkVerifier
    ) async -> String? {
        clipboard.snapshot()
        let active = config.steps.filter { $0.point != nil }
        guard let lastClipboardStep = active.last(where: { $0.expectsClipboard }) else {
            state.logger.log("CONFIG ERROR: no calibrated step is marked as producing the clipboard link")
            return nil
        }
        var url: String?
        for step in active {
            state.currentStatus = "Conversation \(index + 1): \(step.name)"
            await perform(step, config: config, driver: driver)
            if step.name == lastClipboardStep.name {
                if config.dryRun {
                    state.logger.log("dry-run: would wait for clipboard now")
                    return "https://chatgpt.com/share/dry-run-\(index)"
                }
                state.currentStatus = "Conversation \(index + 1): waiting for share link..."
                url = await clipboard.waitForShareLink(
                    timeoutSeconds: config.clipboardTimeoutSeconds * config.delayMultiplier
                )
                break
            }
        }
        guard let url else { return nil }
        if config.verifyLinks {
            state.currentStatus = "Conversation \(index + 1): verifying link..."
            guard await verifier.verify(url) else {
                state.logger.log("Conversation \(index + 1): link failed verification (\(url)), will regenerate")
                return nil
            }
        }
        return url
    }

    /// Steps that close whatever UI a failed attempt left open, so the
    /// retry starts from the conversation list again.
    private func recoverySteps(_ config: ExporterConfig) -> [FlowStep] {
        config.postSteps.filter { ["dismiss_share", "back_button"].contains($0.name) }
    }

    private func runSteps(_ steps: [FlowStep], config: ExporterConfig, driver: InteractionDriver) async {
        for step in steps where step.point != nil {
            await perform(step, config: config, driver: driver)
        }
    }

    private func perform(_ step: FlowStep, config: ExporterConfig, driver: InteractionDriver) async {
        guard var point = step.point?.cgPoint else { return }
        if config.mode == .scroll, step.tracksRowOffset {
            point.y += rowOffsetY
        }
        switch step.action {
        case .tap: await driver.tap(at: point)
        case .longPress: await driver.longPress(at: point, durationMs: 800)
        }
        let wait = Double(step.waitAfterMs) * config.delayMultiplier
        try? await Task.sleep(for: .milliseconds(Int(wait)))
    }

    // MARK: scroll mode

    private func syncRowOffset(for index: Int, config: ExporterConfig) {
        guard config.mode == .scroll, config.rowsPerScreen > 0 else { return }
        rowOffsetY = Double(index % config.rowsPerScreen) * config.rowHeight
    }

    private func advanceScrollPosition(index: Int, config: ExporterConfig, driver: InteractionDriver) async {
        guard config.rowsPerScreen > 0, config.rowHeight > 0,
              let listCenter = config.listCenter?.cgPoint else { return }
        let nextPosition = (index + 1) % config.rowsPerScreen
        if nextPosition == 0 {
            state.currentStatus = "Scrolling to the next page of conversations"
            await driver.scroll(at: listCenter, byPixels: Int(config.rowHeight) * config.rowsPerScreen)
            rowOffsetY = 0
        } else {
            rowOffsetY = Double(nextPosition) * config.rowHeight
        }
    }
}
