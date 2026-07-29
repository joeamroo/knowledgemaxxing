import Foundation

/// A point on screen in CoreGraphics coordinates (origin top-left of main display).
struct CGPointCodable: Codable, Equatable {
    var x: Double
    var y: Double
    var cgPoint: CGPoint { CGPoint(x: x, y: y) }
}

enum StepAction: String, Codable {
    case tap
    case longPress
}

/// One replayable interaction in the per-conversation flow.
struct FlowStep: Codable, Identifiable {
    var id: String { name }
    var name: String
    var prompt: String              // shown during calibration
    var action: StepAction
    var point: CGPointCodable?
    /// Settle delay after the step, in milliseconds. These are floors, not
    /// the completion signal; the clipboard is the real completion signal.
    var waitAfterMs: Int
    /// The step after which the share URL is expected to land in the clipboard.
    /// The engine waits after the LAST calibrated step that sets this.
    var expectsClipboard: Bool
    /// Optional steps may be skipped during calibration (point stays nil)
    /// and are then not performed. Covers UI variants like a separate
    /// "Copy link" confirmation tap.
    var optional: Bool = false
    /// In scroll mode, steps anchored to the conversation row move down as
    /// the loop advances through the visible rows.
    var tracksRowOffset: Bool = false

    enum CodingKeys: String, CodingKey {
        case name, prompt, action, point, waitAfterMs, expectsClipboard, optional, tracksRowOffset
    }

    init(name: String, prompt: String, action: StepAction, point: CGPointCodable?,
         waitAfterMs: Int, expectsClipboard: Bool, optional: Bool = false,
         tracksRowOffset: Bool = false) {
        self.name = name
        self.prompt = prompt
        self.action = action
        self.point = point
        self.waitAfterMs = waitAfterMs
        self.expectsClipboard = expectsClipboard
        self.optional = optional
        self.tracksRowOffset = tracksRowOffset
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        prompt = try c.decode(String.self, forKey: .prompt)
        action = try c.decode(StepAction.self, forKey: .action)
        point = try c.decodeIfPresent(CGPointCodable.self, forKey: .point)
        waitAfterMs = try c.decode(Int.self, forKey: .waitAfterMs)
        expectsClipboard = try c.decode(Bool.self, forKey: .expectsClipboard)
        optional = try c.decodeIfPresent(Bool.self, forKey: .optional) ?? false
        tracksRowOffset = try c.decodeIfPresent(Bool.self, forKey: .tracksRowOffset) ?? false
    }
}

enum NavigationMode: String, Codable, CaseIterable {
    /// Default: conversations stay where they are; the loop taps each row,
    /// exports from inside the open conversation (whose header controls sit
    /// at fixed coordinates), goes Back, and scrolls the list page by page.
    case scroll
    /// Optional optimization: archive each conversation after export so the
    /// next one is always the top row and no scrolling is ever needed.
    /// Depends on current archive behavior; validate on the first item.
    case archive
}

struct ExporterConfig: Codable {
    var mode: NavigationMode = .scroll
    var steps: [FlowStep] = []
    var postSteps: [FlowStep] = []
    /// Scroll mode geometry
    var rowHeight: Double = 0
    var rowsPerScreen: Int = 0
    var listCenter: CGPointCodable?
    /// Global pacing multiplier; raise it if the phone or network is slow.
    var delayMultiplier: Double = 1.0
    var clipboardTimeoutSeconds: Double = 25
    var maxRetriesPerConversation: Int = 3
    var totalConversations: Int = 100
    var dryRun: Bool = false
    /// GET each captured URL and require HTTP 200 before accepting it;
    /// failures regenerate the link (bounded by maxRetriesPerConversation).
    var verifyLinks: Bool = true

    enum CodingKeys: String, CodingKey {
        case mode, steps, postSteps, rowHeight, rowsPerScreen, listCenter
        case delayMultiplier, clipboardTimeoutSeconds, maxRetriesPerConversation
        case totalConversations, dryRun, verifyLinks
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = try c.decodeIfPresent(NavigationMode.self, forKey: .mode) ?? .scroll
        steps = try c.decodeIfPresent([FlowStep].self, forKey: .steps) ?? []
        postSteps = try c.decodeIfPresent([FlowStep].self, forKey: .postSteps) ?? []
        rowHeight = try c.decodeIfPresent(Double.self, forKey: .rowHeight) ?? 0
        rowsPerScreen = try c.decodeIfPresent(Int.self, forKey: .rowsPerScreen) ?? 0
        listCenter = try c.decodeIfPresent(CGPointCodable.self, forKey: .listCenter)
        delayMultiplier = try c.decodeIfPresent(Double.self, forKey: .delayMultiplier) ?? 1.0
        clipboardTimeoutSeconds = try c.decodeIfPresent(Double.self, forKey: .clipboardTimeoutSeconds) ?? 25
        maxRetriesPerConversation = try c.decodeIfPresent(Int.self, forKey: .maxRetriesPerConversation) ?? 3
        totalConversations = try c.decodeIfPresent(Int.self, forKey: .totalConversations) ?? 100
        dryRun = try c.decodeIfPresent(Bool.self, forKey: .dryRun) ?? false
        verifyLinks = try c.decodeIfPresent(Bool.self, forKey: .verifyLinks) ?? true
    }

    /// The calibratable step flows per mode.
    ///
    /// Scroll mode exports from INSIDE the conversation because the header
    /// share control sits at a fixed position regardless of which row was
    /// tapped; only the row tap itself moves. Archive mode uses the sidebar
    /// long-press menu because the pressed row is always the top one.
    static func defaultSteps(mode: NavigationMode) -> (steps: [FlowStep], post: [FlowStep]) {
        switch mode {
        case .scroll:
            let steps: [FlowStep] = [
                FlowStep(name: "conversation_row",
                         prompt: "the FIRST conversation row in the sidebar (a plain tap opens it)",
                         action: .tap, point: nil, waitAfterMs: 1600, expectsClipboard: false,
                         tracksRowOffset: true),
                FlowStep(name: "share_button",
                         prompt: "the share / ... control in the OPEN conversation's header",
                         action: .tap, point: nil, waitAfterMs: 1000, expectsClipboard: false),
                FlowStep(name: "share_menu_item",
                         prompt: "the Share item if a menu appeared (skip if the share dialog opened directly)",
                         action: .tap, point: nil, waitAfterMs: 1000, expectsClipboard: false,
                         optional: true),
                FlowStep(name: "create_link_button",
                         prompt: "the Create link / Share link button in the share dialog",
                         action: .tap, point: nil, waitAfterMs: 900, expectsClipboard: true),
                FlowStep(name: "copy_link_confirm",
                         prompt: "the Copy link button IF a second tap is needed to copy (skip if the link copies on creation)",
                         action: .tap, point: nil, waitAfterMs: 700, expectsClipboard: true,
                         optional: true),
            ]
            let post: [FlowStep] = [
                FlowStep(name: "dismiss_share",
                         prompt: "the X / close control of the share dialog (or a safe spot that dismisses it)",
                         action: .tap, point: nil, waitAfterMs: 800, expectsClipboard: false),
                FlowStep(name: "back_button",
                         prompt: "the Back / sidebar control that returns to the conversation list",
                         action: .tap, point: nil, waitAfterMs: 1100, expectsClipboard: false),
            ]
            return (steps, post)
        case .archive:
            let steps: [FlowStep] = [
                FlowStep(name: "conversation_row",
                         prompt: "the FIRST conversation row in the sidebar (we long-press it)",
                         action: .longPress, point: nil, waitAfterMs: 900, expectsClipboard: false),
                FlowStep(name: "share_menu_item",
                         prompt: "the Share item in the long-press context menu",
                         action: .tap, point: nil, waitAfterMs: 1200, expectsClipboard: false),
                FlowStep(name: "create_link_button",
                         prompt: "the Create link / Share link button in the share dialog",
                         action: .tap, point: nil, waitAfterMs: 900, expectsClipboard: true),
                FlowStep(name: "copy_link_confirm",
                         prompt: "the Copy link button IF a second tap is needed to copy (skip if the link copies on creation)",
                         action: .tap, point: nil, waitAfterMs: 700, expectsClipboard: true,
                         optional: true),
            ]
            let post: [FlowStep] = [
                FlowStep(name: "dismiss_share",
                         prompt: "the X / close control of the share dialog (or a safe spot that dismisses it)",
                         action: .tap, point: nil, waitAfterMs: 800, expectsClipboard: false),
                FlowStep(name: "row_for_archive",
                         prompt: "the FIRST conversation row again (long-press to open its menu)",
                         action: .longPress, point: nil, waitAfterMs: 900, expectsClipboard: false),
                FlowStep(name: "archive_menu_item",
                         prompt: "the Archive item in the long-press context menu",
                         action: .tap, point: nil, waitAfterMs: 1100, expectsClipboard: false),
            ]
            return (steps, post)
        }
    }
}

struct ExportRecord: Codable, Identifiable {
    var id: String { url }
    var index: Int
    var title: String?
    var url: String
    var timestamp: Date
    var success: Bool
    var note: String?
    var verified: Bool? = nil
}

struct ExportState: Codable {
    var records: [ExportRecord] = []
    var nextIndex: Int = 0
    var seenURLs: Set<String> = []
}
