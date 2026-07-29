import Foundation

/// Appends to output/log.txt and mirrors into the UI via AppState.
final class ExportLogger: @unchecked Sendable {
    private let fileURL: URL
    private let queue = DispatchQueue(label: "logger")
    private let formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    var onLine: (@Sendable (String) -> Void)?

    init(outputDir: URL) {
        fileURL = outputDir.appendingPathComponent("log.txt")
    }

    func log(_ message: String) {
        let line = "[\(formatter.string(from: Date()))] \(message)"
        queue.async { [fileURL] in
            if let data = (line + "\n").data(using: .utf8) {
                if let handle = try? FileHandle(forWritingTo: fileURL) {
                    handle.seekToEndOfFile()
                    handle.write(data)
                    try? handle.close()
                } else {
                    try? data.write(to: fileURL)
                }
            }
        }
        onLine?(line)
    }
}
