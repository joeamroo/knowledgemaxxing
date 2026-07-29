import AppKit
import SwiftUI

/// Simulation mode: a click-through transparent window covering the main
/// display that draws every calibrated target, numbered in execution
/// order, so calibration can be verified without touching the ChatGPT app.
@MainActor
final class SimulationOverlay {
    private var window: NSWindow?

    var isShowing: Bool { window != nil }

    func show(config: ExporterConfig) {
        hide()
        guard let screen = NSScreen.screens.first else { return }
        let panel = NSPanel(
            contentRect: screen.frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        panel.level = .statusBar
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.ignoresMouseEvents = true          // clicks pass through
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary]
        panel.contentView = NSHostingView(
            rootView: SimulationOverlayView(config: config, screenHeight: screen.frame.height)
        )
        panel.orderFrontRegardless()
        window = panel
    }

    func hide() {
        window?.orderOut(nil)
        window = nil
    }
}

struct SimulationOverlayView: View {
    let config: ExporterConfig
    let screenHeight: CGFloat

    private struct Marker: Identifiable {
        let id: Int
        let name: String
        let action: StepAction
        let point: CGPoint
        let isPost: Bool
        let tracksRow: Bool
    }

    private var markers: [Marker] {
        var out: [Marker] = []
        var order = 1
        for step in config.steps where step.point != nil {
            out.append(Marker(id: order, name: step.name, action: step.action,
                              point: step.point!.cgPoint, isPost: false,
                              tracksRow: step.tracksRowOffset))
            order += 1
        }
        for step in config.postSteps where step.point != nil {
            out.append(Marker(id: order, name: step.name, action: step.action,
                              point: step.point!.cgPoint, isPost: true,
                              tracksRow: step.tracksRowOffset))
            order += 1
        }
        return out
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
            ForEach(markers) { marker in
                markerView(marker)
                    // stored coords are CG (top-left origin); the overlay view
                    // fills the screen with the same origin, so use directly
                    .position(x: marker.point.x, y: marker.point.y)
            }
            legend
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
        }
        .ignoresSafeArea()
    }

    private func markerView(_ marker: Marker) -> some View {
        VStack(spacing: 2) {
            ZStack {
                Circle()
                    .fill((marker.isPost ? Color.purple : Color.orange).opacity(0.35))
                    .frame(width: 44, height: 44)
                Circle()
                    .stroke(marker.isPost ? Color.purple : Color.orange, lineWidth: 2)
                    .frame(width: 44, height: 44)
                Text("\(marker.id)")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
            }
            Text("\(marker.name)\(marker.action == .longPress ? " (hold)" : "")\(marker.tracksRow ? " ↓rows" : "")")
                .font(.system(size: 10, weight: .semibold))
                .padding(.horizontal, 4).padding(.vertical, 1)
                .background(.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 4))
                .foregroundStyle(.white)
        }
    }

    private var legend: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Simulation: planned clicks in execution order").bold()
            Label("share flow steps", systemImage: "circle.fill").foregroundStyle(.orange)
            Label("post steps (dismiss / back / archive)", systemImage: "circle.fill").foregroundStyle(.purple)
            Text("(hold) = long press · ↓rows = moves down the list in scroll mode")
            Text("Close this overlay from the app window; clicks pass through it.")
        }
        .font(.system(size: 12))
        .padding(12)
        .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 10))
        .foregroundStyle(.white)
    }
}
