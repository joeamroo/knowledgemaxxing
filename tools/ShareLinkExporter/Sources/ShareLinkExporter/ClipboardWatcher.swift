import AppKit
import Foundation

/// Watches the macOS pasteboard for the share URL arriving from the phone
/// via Universal Clipboard. This is the reliable completion signal that
/// replaces fixed sleeps for link generation.
final class ClipboardWatcher {
    private var lastChangeCount: Int = NSPasteboard.general.changeCount

    /// Remember the current state so only NEW clipboard content counts.
    func snapshot() {
        lastChangeCount = NSPasteboard.general.changeCount
    }

    /// Poll until a chatgpt.com/share link newer than the snapshot appears.
    /// Returns nil on timeout (caller retries or records a failure).
    func waitForShareLink(timeoutSeconds: Double) async -> String? {
        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while Date() < deadline {
            let pasteboard = NSPasteboard.general
            if pasteboard.changeCount != lastChangeCount,
               let text = pasteboard.string(forType: .string)?
                   .trimmingCharacters(in: .whitespacesAndNewlines),
               let url = Self.extractShareURL(from: text) {
                lastChangeCount = pasteboard.changeCount
                return url
            }
            try? await Task.sleep(for: .milliseconds(200))
        }
        return nil
    }

    static func extractShareURL(from text: String) -> String? {
        // share links look like https://chatgpt.com/share/<uuid>
        // (older ones used chat.openai.com/share/...)
        let pattern = #"https://(chatgpt\.com|chat\.openai\.com)/share/[A-Za-z0-9\-]+"#
        guard let range = text.range(of: pattern, options: .regularExpression) else { return nil }
        return String(text[range])
    }
}
