import SwiftUI

/// Per-target recalibration: every calibrated point is listed and can be
/// recaptured individually with the same 3-second countdown, without
/// redoing the whole wizard.
struct TargetsEditorView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var countdownFor: String? = nil
    @State private var countdown: Int = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Edit click targets").font(.title3).bold()
            Text("Press Recapture, move the cursor over the target in the iPhone Mirroring window, and hold still for 3 seconds.")
                .foregroundStyle(.secondary)

            List {
                Section("Share flow") {
                    ForEach(state.config.steps.indices, id: \.self) { i in
                        row(step: state.config.steps[i], isPost: false, index: i)
                    }
                }
                Section("Post steps") {
                    ForEach(state.config.postSteps.indices, id: \.self) { i in
                        row(step: state.config.postSteps[i], isPost: true, index: i)
                    }
                }
            }
            .frame(minHeight: 300)

            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(16)
        .frame(minWidth: 560, minHeight: 460)
    }

    private func row(step: FlowStep, isPost: Bool, index: Int) -> some View {
        HStack {
            Image(systemName: step.point != nil ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(step.point != nil ? .green : (step.optional ? .secondary : .red))
            VStack(alignment: .leading) {
                Text(step.name).bold()
                Text(step.prompt).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            if let p = step.point {
                Text("(\(Int(p.x)), \(Int(p.y)))").monospacedDigit().foregroundStyle(.secondary)
            } else {
                Text(step.optional ? "skipped" : "missing").foregroundStyle(.secondary)
            }
            if countdownFor == step.name {
                Text("\(countdown)...").bold().monospacedDigit()
            } else {
                Button("Recapture") { recapture(step.name, isPost: isPost, index: index) }
                    .disabled(countdownFor != nil)
                if step.optional && step.point != nil {
                    Button("Clear") { clear(isPost: isPost, index: index) }
                        .disabled(countdownFor != nil)
                }
            }
        }
    }

    private func recapture(_ name: String, isPost: Bool, index: Int) {
        countdownFor = name
        countdown = 3
        Task { @MainActor in
            for n in stride(from: 3, through: 1, by: -1) {
                countdown = n
                try? await Task.sleep(for: .seconds(1))
            }
            let cg = AccessibilityController.currentMouseCGPoint()
            let point = CGPointCodable(x: cg.x, y: cg.y)
            if isPost { state.config.postSteps[index].point = point }
            else { state.config.steps[index].point = point }
            state.saveConfig()
            state.logger.log("Recaptured \(name) at (\(Int(cg.x)), \(Int(cg.y)))")
            countdownFor = nil
        }
    }

    private func clear(isPost: Bool, index: Int) {
        if isPost { state.config.postSteps[index].point = nil }
        else { state.config.steps[index].point = nil }
        state.saveConfig()
    }
}
