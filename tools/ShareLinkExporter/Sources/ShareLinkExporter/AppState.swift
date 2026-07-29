import Foundation
import SwiftUI

enum RunPhase: Equatable {
    case needsCalibration
    case idle
    case running
    case paused
    case fetchingTitles
    case finished
}

@MainActor
final class AppState: ObservableObject {
    @Published var phase: RunPhase = .needsCalibration
    @Published var config: ExporterConfig
    @Published var currentIndex: Int = 0
    @Published var currentStatus: String = "Waiting"
    @Published var logLines: [String] = []
    @Published var records: [ExportRecord] = []
    @Published var perItemSeconds: [Double] = []
    @Published var permissionGranted: Bool = false
    @Published var mirroringRunning: Bool = false

    let configManager = ConfigurationManager()
    let logger: ExportLogger
    lazy var exportManager = ExportManager(configManager: configManager)
    let overlay = SimulationOverlay()
    @Published var simulating = false

    init() {
        logger = ExportLogger(outputDir: configManager.outputDir)
        if let saved = configManager.loadConfig(), !saved.steps.isEmpty {
            config = saved
            phase = .idle
        } else {
            config = ExporterConfig()
        }
        records = exportManager.state.records
        logger.onLine = { [weak self] line in
            Task { @MainActor in
                self?.logLines.append(line)
                if self?.logLines.count ?? 0 > 400 { self?.logLines.removeFirst(100) }
            }
        }
        refreshEnvironment()
    }

    func refreshEnvironment() {
        permissionGranted = AccessibilityController.ensurePermission(promptIfNeeded: false)
        mirroringRunning = AccessibilityController.mirroringRunning()
    }

    var progressFraction: Double {
        guard config.totalConversations > 0 else { return 0 }
        return min(1.0, Double(exportManager.successCount) / Double(config.totalConversations))
    }

    var estimatedRemaining: String {
        guard !perItemSeconds.isEmpty else { return "estimating..." }
        let avg = perItemSeconds.reduce(0, +) / Double(perItemSeconds.count)
        let remaining = max(0, config.totalConversations - exportManager.successCount)
        let seconds = Int(avg * Double(remaining))
        return "~\(seconds / 60)m \(seconds % 60)s remaining"
    }

    func saveConfig() {
        configManager.save(config: config)
    }

    func toggleSimulation() {
        if simulating {
            overlay.hide()
            simulating = false
        } else {
            overlay.show(config: config)
            simulating = true
        }
    }
}
