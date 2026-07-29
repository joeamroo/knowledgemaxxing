import Foundation

/// Turns a fetched share page into clean Markdown with speaker labels.
///
/// Strategy, best fidelity first:
/// 1. The share page embeds the conversation as JSON in a <script> tag
///    (same message schema family as the official export: author.role +
///    content.parts). Message parts are already Markdown, so extracting
///    them preserves code blocks, tables, and lists exactly.
/// 2. Fallback: crude HTML-to-text extraction of the page body, clearly
///    marked as low fidelity.
/// The raw HTML is always saved alongside, so a parser fix can be re-run
/// offline later without re-fetching.
enum ConversationParser {

    struct Message {
        let role: String
        let text: String
    }

    static func markdown(fromHTML html: String, url: String, title: String?) -> String {
        let messages = messagesFromEmbeddedJSON(html)
        var lines: [String] = []
        lines.append("# \(title ?? "ChatGPT conversation")")
        lines.append("")
        lines.append("Source: \(url)")
        lines.append("")
        if !messages.isEmpty {
            for message in messages {
                lines.append("## \(label(for: message.role))")
                lines.append("")
                lines.append(message.text.trimmingCharacters(in: .whitespacesAndNewlines))
                lines.append("")
            }
        } else {
            lines.append("_Structured extraction failed for this page; text-only fallback below. The raw HTML is saved next to this file for a future parser pass._")
            lines.append("")
            lines.append(plainTextFallback(html))
        }
        return lines.joined(separator: "\n")
    }

    static func label(for role: String) -> String {
        switch role.lowercased() {
        case "user": return "User"
        case "assistant": return "ChatGPT"
        case "tool", "system": return role.capitalized
        default: return role.capitalized
        }
    }

    // MARK: strategy 1, embedded JSON

    static func messagesFromEmbeddedJSON(_ html: String) -> [Message] {
        for json in candidateJSONBlobs(in: html) {
            guard let data = json.data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data)
            else { continue }
            var found: [Message] = []
            walk(object, into: &found)
            if !found.isEmpty { return dedupe(found) }
        }
        return []
    }

    /// Script bodies that look like they contain conversation data. Handles
    /// both plain JSON scripts and JSON escaped inside JS string literals
    /// (streamed remix/next payloads).
    private static func candidateJSONBlobs(in html: String) -> [String] {
        var blobs: [String] = []
        let scriptPattern = #"<script[^>]*>([\s\S]*?)</script>"#
        guard let regex = try? NSRegularExpression(pattern: scriptPattern) else { return [] }
        let ns = html as NSString
        for match in regex.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
            let body = ns.substring(with: match.range(at: 1))
            guard body.contains("author") || body.contains("\\\"author\\\"") else { continue }
            // plain JSON object in the script
            if let start = body.firstIndex(of: "{"), let blob = balancedJSON(from: body, startingAt: start) {
                blobs.append(blob)
            }
            // JSON escaped inside a string literal: unescape and retry
            if body.contains("\\\"author\\\"") {
                let unescaped = body
                    .replacingOccurrences(of: "\\\"", with: "\"")
                    .replacingOccurrences(of: "\\\\", with: "\\")
                    .replacingOccurrences(of: "\\n", with: "\n")
                if let start = unescaped.firstIndex(of: "{"),
                   let blob = balancedJSON(from: unescaped, startingAt: start) {
                    blobs.append(blob)
                }
            }
        }
        return blobs
    }

    /// Extracts one balanced {...} object starting at `start`, respecting strings.
    private static func balancedJSON(from text: String, startingAt start: String.Index) -> String? {
        var depth = 0
        var inString = false
        var escaped = false
        var index = start
        while index < text.endIndex {
            let ch = text[index]
            if escaped {
                escaped = false
            } else if ch == "\\" {
                escaped = true
            } else if ch == "\"" {
                inString.toggle()
            } else if !inString {
                if ch == "{" { depth += 1 }
                if ch == "}" {
                    depth -= 1
                    if depth == 0 { return String(text[start...index]) }
                }
            }
            index = text.index(after: index)
        }
        return nil
    }

    /// Recursively finds message-shaped dicts anywhere in the decoded JSON:
    /// {author: {role}, content: {parts: [...]}} or {role, content: string}.
    private static func walk(_ node: Any, into out: inout [Message]) {
        if let dict = node as? [String: Any] {
            if let message = messageFrom(dict) {
                out.append(message)
            }
            for value in dict.values { walk(value, into: &out) }
        } else if let array = node as? [Any] {
            for value in array { walk(value, into: &out) }
        }
    }

    private static func messageFrom(_ dict: [String: Any]) -> Message? {
        var role: String?
        if let author = dict["author"] as? [String: Any] {
            role = author["role"] as? String
        } else if let r = dict["role"] as? String {
            role = r
        }
        guard let role, ["user", "assistant"].contains(role) else { return nil }

        var text = ""
        if let content = dict["content"] as? [String: Any] {
            if let parts = content["parts"] as? [Any] {
                text = parts.compactMap { $0 as? String }.joined(separator: "\n")
            } else if let t = content["text"] as? String {
                text = t
            }
        } else if let content = dict["content"] as? String {
            text = content
        }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return Message(role: role, text: trimmed)
    }

    private static func dedupe(_ messages: [Message]) -> [Message] {
        var seen = Set<String>()
        var out: [Message] = []
        for m in messages {
            let key = "\(m.role)|\(m.text.prefix(200))"
            if seen.insert(key).inserted { out.append(m) }
        }
        return out
    }

    // MARK: strategy 2, crude fallback

    static func plainTextFallback(_ html: String) -> String {
        var text = html
        // keep code block boundaries before stripping tags
        text = text.replacingOccurrences(of: "<pre", with: "\n```\n<pre")
        text = text.replacingOccurrences(of: "</pre>", with: "</pre>\n```\n")
        for pattern in [#"<script[\s\S]*?</script>"#, #"<style[\s\S]*?</style>"#, #"<[^>]+>"#] {
            text = text.replacingOccurrences(of: pattern, with: " ", options: .regularExpression)
        }
        text = text
            .replacingOccurrences(of: "&amp;", with: "&")
            .replacingOccurrences(of: "&lt;", with: "<")
            .replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&quot;", with: "\"")
        // collapse whitespace runs
        text = text.replacingOccurrences(of: #"[ \t]+"#, with: " ", options: .regularExpression)
        text = text.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
