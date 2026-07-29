import AppKit
import ApplicationServices
import CoreGraphics

/// Synthetic input against the iPhone Mirroring window.
///
/// iPhone Mirroring exposes NO accessibility tree for the mirrored iOS UI
/// (it is a video stream), so element-based automation is impossible.
/// Everything here is coordinate-based CGEvents, which is exactly why the
/// engine leans on the clipboard for completion detection and on the
/// archive trick for constant coordinates.
enum AccessibilityController {

    /// CGEvent posting silently no-ops without the Accessibility permission,
    /// so surface it loudly instead.
    static func ensurePermission(promptIfNeeded: Bool = true) -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: promptIfNeeded]
        return AXIsProcessTrustedWithOptions(options as CFDictionary)
    }

    /// Frontmost-window check: warn if iPhone Mirroring is not running.
    static func mirroringRunning() -> Bool {
        NSWorkspace.shared.runningApplications.contains {
            $0.bundleIdentifier == "com.apple.ScreenContinuity"
        }
    }

    static func activateMirroring() {
        if let app = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == "com.apple.ScreenContinuity"
        }) {
            app.activate()
        }
    }

    private static func post(_ event: CGEvent?) {
        event?.post(tap: .cghidEventTap)
    }

    static func moveMouse(to point: CGPoint) {
        post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved,
                     mouseCursorPosition: point, mouseButton: .left))
    }

    static func tap(at point: CGPoint) async {
        moveMouse(to: point)
        try? await Task.sleep(for: .milliseconds(120))
        post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                     mouseCursorPosition: point, mouseButton: .left))
        try? await Task.sleep(for: .milliseconds(70))
        post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                     mouseCursorPosition: point, mouseButton: .left))
    }

    /// iOS long-press: hold the button down well past the recognizer threshold.
    static func longPress(at point: CGPoint, durationMs: Int = 800) async {
        moveMouse(to: point)
        try? await Task.sleep(for: .milliseconds(120))
        post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                     mouseCursorPosition: point, mouseButton: .left))
        try? await Task.sleep(for: .milliseconds(durationMs))
        post(CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                     mouseCursorPosition: point, mouseButton: .left))
    }

    /// Small discrete scroll steps avoid iOS momentum overshoot.
    static func scroll(at point: CGPoint, byPixels pixels: Int) async {
        moveMouse(to: point)
        try? await Task.sleep(for: .milliseconds(150))
        let stepSize = 40
        var remaining = abs(pixels)
        let direction: Int32 = pixels > 0 ? -1 : 1  // positive = content up (next rows)
        while remaining > 0 {
            let step = min(stepSize, remaining)
            let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
                                wheelCount: 1, wheel1: direction * Int32(step),
                                wheel2: 0, wheel3: 0)
            post(event)
            remaining -= step
            try? await Task.sleep(for: .milliseconds(60))
        }
        // let the list settle before the next tap
        try? await Task.sleep(for: .milliseconds(700))
    }

    /// Current mouse position converted to CG (top-left origin) coordinates,
    /// used by the calibration wizard's countdown capture.
    static func currentMouseCGPoint() -> CGPoint {
        let cocoa = NSEvent.mouseLocation  // bottom-left origin
        let screenHeight = NSScreen.screens.first?.frame.height ?? 0
        return CGPoint(x: cocoa.x, y: screenHeight - cocoa.y)
    }
}
