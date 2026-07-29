import SwiftUI

/// First-launch wizard. For each flow step: press "Capture in 3s", move the
/// cursor over the target inside the iPhone Mirroring window, and hold it
/// there until the countdown fires. Countdown capture avoids focus and
/// hotkey problems entirely. Optional steps can be skipped; individual
/// targets can be recaptured later via the targets editor.
struct CalibrationView: View {
    @EnvironmentObject var state: AppState
    @State private var mode: NavigationMode = .scroll
    @State private var steps: [FlowStep] = []
    @State private var postSteps: [FlowStep] = []
    @State private var stepIndex = 0
    @State private var countdown: Int? = nil
    @State private var capturing = false
    @State private var totalText = "100"
    // scroll-mode extras
    @State private var secondRowPoint: CGPointCodable? = nil
    @State private var rowsPerScreenText = "10"
    @State private var scrollExtraIndex = 0  // 0 = second row, 1 = list center

    private var allSteps: [FlowStep] { steps + postSteps }
    private var scrollExtrasNeeded: Bool { mode == .scroll }
    private var scrollExtrasDone: Bool { !scrollExtrasNeeded || scrollExtraIndex >= 2 }
    private var done: Bool { stepIndex >= allSteps.count && scrollExtrasDone }
    private var currentIsOptional: Bool {
        stepIndex < allSteps.count && allSteps[stepIndex].optional
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Calibration").font(.title2).bold()
            Text("Get the phone ready first: iPhone Mirroring open, ChatGPT open, sidebar visible with your conversations. Then capture each target below.")
                .foregroundStyle(.secondary)

            Picker("Navigation mode", selection: $mode) {
                Text("Scroll the list (default, chats stay put)").tag(NavigationMode.scroll)
                Text("Archive after export (optional optimization)").tag(NavigationMode.archive)
            }
            .onChange(of: mode) { rebuildSteps() }
            Text(mode == .scroll
                 ? "Scroll mode opens each conversation and shares from its header (fixed position), so only the row tap moves as the list advances."
                 : "Archive mode archives each chat after export so the next one is always the top row. Chats are NOT deleted (Settings > Archived chats), but verify archive behavior on your first conversation.")
                .font(.caption).foregroundStyle(.secondary)

            HStack(spacing: 18) {
                HStack {
                    Text("Conversations:")
                    TextField("100", text: $totalText).frame(width: 60)
                }
                if mode == .scroll {
                    HStack {
                        Text("Rows visible per screen:")
                        TextField("10", text: $rowsPerScreenText).frame(width: 50)
                    }
                }
            }

            Divider()

            if !done {
                currentPrompt
                HStack {
                    Button(capturing ? "Move cursor to the target..." : "Capture in 3s") {
                        beginCountdown()
                    }
                    .disabled(capturing)
                    .keyboardShortcut(.defaultAction)
                    if currentIsOptional {
                        Button("Skip (not needed on my UI)") { skipCurrent() }
                            .disabled(capturing)
                    }
                    if let countdown { Text("\(countdown)...").font(.title3).monospacedDigit() }
                }
                if stepIndex > 0 || scrollExtraIndex > 0 {
                    Button("Redo previous") { goBack() }.disabled(capturing)
                }
            } else {
                Label("All targets captured", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Button("Save and continue") { finish() }
                    .keyboardShortcut(.defaultAction)
            }

            Divider()
            capturedList
        }
        .padding(20)
        .frame(minWidth: 560)
        .onAppear { rebuildSteps() }
    }

    @ViewBuilder private var currentPrompt: some View {
        if stepIndex < allSteps.count {
            let step = allSteps[stepIndex]
            (Text("Step \(stepIndex + 1) of \(allSteps.count): hover over ") +
             Text(step.prompt).bold() +
             Text(step.optional ? "  (optional)" : ""))
                .font(.headline)
        } else if scrollExtraIndex == 0 {
            Text("Scroll setup: hover over the SECOND conversation row (measures row height)")
                .font(.headline)
        } else {
            Text("Scroll setup: hover over the middle of the conversation list (scroll anchor)")
                .font(.headline)
        }
    }

    private var capturedList: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(allSteps.enumerated()), id: \.offset) { i, step in
                HStack {
                    Image(systemName: step.point != nil ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(step.point != nil ? .green : .secondary)
                    Text(step.name)
                    if step.optional { Text("optional").font(.caption2).foregroundStyle(.secondary) }
                    if let p = step.point {
                        Text("(\(Int(p.x)), \(Int(p.y)))").foregroundStyle(.secondary)
                    }
                    if i == stepIndex && !done { Text("← next").foregroundStyle(.orange) }
                }
                .font(.callout)
            }
        }
    }

    private func rebuildSteps() {
        let defaults = ExporterConfig.defaultSteps(mode: mode)
        steps = defaults.steps
        postSteps = defaults.post
        stepIndex = 0
        scrollExtraIndex = 0
        secondRowPoint = nil
    }

    private func beginCountdown() {
        capturing = true
        countdown = 3
        Task { @MainActor in
            for n in stride(from: 3, through: 1, by: -1) {
                countdown = n
                try? await Task.sleep(for: .seconds(1))
            }
            countdown = nil
            capturing = false
            capture(AccessibilityController.currentMouseCGPoint())
        }
    }

    private func capture(_ cg: CGPoint) {
        let point = CGPointCodable(x: cg.x, y: cg.y)
        if stepIndex < allSteps.count {
            if stepIndex < steps.count {
                steps[stepIndex].point = point
            } else {
                postSteps[stepIndex - steps.count].point = point
            }
            stepIndex += 1
        } else if scrollExtraIndex == 0 {
            secondRowPoint = point
            scrollExtraIndex = 1
        } else {
            state.config.listCenter = point
            scrollExtraIndex = 2
        }
    }

    private func skipCurrent() {
        guard currentIsOptional else { return }
        if stepIndex < steps.count {
            steps[stepIndex].point = nil
        } else {
            postSteps[stepIndex - steps.count].point = nil
        }
        stepIndex += 1
    }

    private func goBack() {
        if scrollExtraIndex > 0 { scrollExtraIndex -= 1 }
        else if stepIndex > 0 { stepIndex -= 1 }
    }

    private func finish() {
        state.config.mode = mode
        state.config.steps = steps
        state.config.postSteps = postSteps
        state.config.totalConversations = Int(totalText) ?? 100
        if mode == .scroll,
           let first = steps.first(where: { $0.name == "conversation_row" })?.point,
           let second = secondRowPoint {
            state.config.rowHeight = abs(second.y - first.y)
            state.config.rowsPerScreen = Int(rowsPerScreenText) ?? 10
        }
        state.saveConfig()
        state.logger.log("Calibration saved (mode \(mode.rawValue), \(state.config.totalConversations) conversations)")
        state.phase = .idle
    }
}
