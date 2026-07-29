import Foundation

/// Downloads every collected share page. Three jobs:
/// 1. Extract the conversation title (the phone UI has no accessibility
///    tree to read titles from, but the public share page does).
/// 2. Archive the raw HTML to output/pages/.
/// 3. Parse each page into clean Markdown with speaker labels at
///    output/markdown/<slug>.md (see ConversationParser).
struct TitleFetcher {
    let outputDir: URL

    struct FetchResult {
        let url: String
        let title: String?
        let parsedMessages: Bool
    }

    func fetchAll(
        records: [ExportRecord],
        progress: @escaping (Int, Int, String) -> Void
    ) async -> [FetchResult] {
        var results: [FetchResult] = []
        let session = URLSession(configuration: .ephemeral)
        let pagesDir = outputDir.appendingPathComponent("pages", isDirectory: true)
        let markdownDir = outputDir.appendingPathComponent("markdown", isDirectory: true)
        try? FileManager.default.createDirectory(at: markdownDir, withIntermediateDirectories: true)
        let successRecords = records.filter(\.success)
        for (i, record) in successRecords.enumerated() {
            guard let url = URL(string: record.url) else { continue }
            progress(i + 1, successRecords.count, record.url)
            do {
                var request = URLRequest(url: url)
                request.setValue(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
                    forHTTPHeaderField: "User-Agent"
                )
                let (data, _) = try await session.data(for: request)
                let html = String(data: data, encoding: .utf8) ?? ""
                let slug = url.lastPathComponent
                try? data.write(to: pagesDir.appendingPathComponent("\(slug).html"), options: .atomic)

                let title = Self.title(fromHTML: html)
                let markdown = ConversationParser.markdown(fromHTML: html, url: record.url, title: title)
                try? markdown.data(using: .utf8)?.write(
                    to: markdownDir.appendingPathComponent("\(slug).md"), options: .atomic
                )
                let parsed = !ConversationParser.messagesFromEmbeddedJSON(html).isEmpty
                results.append(FetchResult(url: record.url, title: title, parsedMessages: parsed))
            } catch {
                results.append(FetchResult(url: record.url, title: nil, parsedMessages: false))
            }
            // be polite; these are ~100 requests against a public endpoint
            try? await Task.sleep(for: .milliseconds(800))
        }
        return results
    }

    static func title(fromHTML html: String) -> String? {
        for pattern in [
            #"<meta property="og:title" content="([^"]+)""#,
            #"<title>([^<]+)</title>"#,
        ] {
            if let match = html.range(of: pattern, options: .regularExpression) {
                let matched = String(html[match])
                if let inner = matched.range(of: #"(?<=content=")[^"]+|(?<=<title>)[^<]+"#,
                                             options: .regularExpression) {
                    let title = String(matched[inner])
                        .replacingOccurrences(of: " | ChatGPT", with: "")
                        .replacingOccurrences(of: "&amp;", with: "&")
                        .replacingOccurrences(of: "&#39;", with: "'")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !title.isEmpty, title != "ChatGPT" { return title }
                }
            }
        }
        return nil
    }
}
