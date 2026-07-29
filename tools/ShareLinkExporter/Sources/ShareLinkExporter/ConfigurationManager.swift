import Foundation

/// Persists ExporterConfig and ExportState as JSON in the output directory,
/// so a crash or quit resumes exactly where it left off.
struct ConfigurationManager {
    let outputDir: URL
    var configURL: URL { outputDir.appendingPathComponent("config.json") }
    var stateURL: URL { outputDir.appendingPathComponent("state.json") }

    init() {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        outputDir = base.appendingPathComponent("ShareLinkExporter/output", isDirectory: true)
        try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(
            at: outputDir.appendingPathComponent("pages", isDirectory: true),
            withIntermediateDirectories: true
        )
    }

    func loadConfig() -> ExporterConfig? {
        guard let data = try? Data(contentsOf: configURL) else { return nil }
        return try? JSONDecoder().decode(ExporterConfig.self, from: data)
    }

    func save(config: ExporterConfig) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(config) {
            try? data.write(to: configURL, options: .atomic)
        }
    }

    func loadState() -> ExportState {
        guard let data = try? Data(contentsOf: stateURL),
              let state = try? JSONDecoder().decode(ExportState.self, from: data)
        else { return ExportState() }
        return state
    }

    func save(state: ExportState) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(state) {
            try? data.write(to: stateURL, options: .atomic)
        }
    }
}
