import CoreGraphics
import Foundation

/// Abstraction over how we drive the phone UI. The engine only talks to
/// this protocol, so the coordinate-click implementation can be swapped
/// for an Accessibility-tree or browser-automation driver later without
/// touching the engine, the calibration data model, or the UI.
protocol InteractionDriver: Sendable {
    var name: String { get }
    /// Preconditions (permissions, target app running). Returns a
    /// user-readable problem description, or nil when ready.
    func readinessProblem() -> String?
    /// Bring the automation target to the foreground.
    func focusTarget() async
    func tap(at point: CGPoint) async
    func longPress(at point: CGPoint, durationMs: Int) async
    func scroll(at point: CGPoint, byPixels: Int) async
}

/// The only viable driver today: synthetic CGEvents against the iPhone
/// Mirroring window. iPhone Mirroring exposes no accessibility tree for
/// the mirrored iOS UI, so element-based drivers are impossible until
/// Apple ships an API; this type isolates that constraint.
struct CoordinateClickDriver: InteractionDriver {
    let name = "coordinate-click (iPhone Mirroring)"

    func readinessProblem() -> String? {
        if !AccessibilityController.ensurePermission(promptIfNeeded: true) {
            return "Accessibility permission missing: System Settings > Privacy & Security > Accessibility (grant it to your terminal if launched from one), then relaunch."
        }
        if !AccessibilityController.mirroringRunning() {
            return "iPhone Mirroring is not running. Open it, unlock the phone, open ChatGPT with the sidebar visible."
        }
        return nil
    }

    func focusTarget() async {
        AccessibilityController.activateMirroring()
        try? await Task.sleep(for: .seconds(1))
    }

    func tap(at point: CGPoint) async {
        await AccessibilityController.tap(at: point)
    }

    func longPress(at point: CGPoint, durationMs: Int) async {
        await AccessibilityController.longPress(at: point, durationMs: durationMs)
    }

    func scroll(at point: CGPoint, byPixels pixels: Int) async {
        await AccessibilityController.scroll(at: point, byPixels: pixels)
    }
}

/// Dry-run driver: performs nothing, used so the engine code path stays
/// identical while only logging intended actions.
struct LoggingDriver: InteractionDriver {
    let name = "dry-run (no input posted)"
    let log: @Sendable (String) -> Void

    func readinessProblem() -> String? { nil }
    func focusTarget() async {}
    func tap(at point: CGPoint) async {
        log("dry-run: tap at (\(Int(point.x)), \(Int(point.y)))")
    }
    func longPress(at point: CGPoint, durationMs: Int) async {
        log("dry-run: long-press \(durationMs)ms at (\(Int(point.x)), \(Int(point.y)))")
    }
    func scroll(at point: CGPoint, byPixels pixels: Int) async {
        log("dry-run: scroll \(pixels)px at (\(Int(point.x)), \(Int(point.y)))")
    }
}
