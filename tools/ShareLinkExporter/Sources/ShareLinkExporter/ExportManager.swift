import Foundation

/// Owns the export state, dedupe, and the CSV/JSON outputs.
/// State is flushed after every successful export so interruption at any
/// point resumes cleanly.
final class ExportManager {
    private let configManager: ConfigurationManager
    private(set) var state: ExportState

    init(configManager: ConfigurationManager) {
        self.configManager = configManager
        self.state = configManager.loadState()
    }

    var successCount: Int { state.records.filter(\.success).count }
    var failureCount: Int { state.records.filter { !$0.success }.count }

    func isDuplicate(url: String) -> Bool { state.seenURLs.contains(url) }

    func record(_ record: ExportRecord) {
        state.records.append(record)
        if record.success { state.seenURLs.insert(record.url) }
        state.nextIndex = record.index + 1
        persist()
    }

    func setTitle(_ title: String, forURL url: String) {
        if let i = state.records.firstIndex(where: { $0.url == url }) {
            state.records[i].title = title
        }
        persist()
    }

    func failedRecords() -> [ExportRecord] { state.records.filter { !$0.success } }

    func removeFailures() {
        state.records.removeAll { !$0.success }
        persist()
    }

    func persist() {
        configManager.save(state: state)
        writeOutputs()
    }

    /// links.csv and links.json are regenerated wholesale on every flush,
    /// so they are always consistent with state.json.
    private func writeOutputs() {
        let dir = configManager.outputDir
        var csv = "Title,ShareURL,Timestamp,Success\n"
        for r in state.records {
            let title = (r.title ?? "").replacingOccurrences(of: "\"", with: "\"\"")
            csv += "\"\(title)\",\(r.url),\(ISO8601DateFormatter().string(from: r.timestamp)),\(r.success)\n"
        }
        try? csv.data(using: .utf8)?.write(to: dir.appendingPathComponent("links.csv"), options: .atomic)

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(state.records) {
            try? data.write(to: dir.appendingPathComponent("links.json"), options: .atomic)
        }
    }
}
