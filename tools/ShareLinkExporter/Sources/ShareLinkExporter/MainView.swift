import SwiftUI

struct MainView: View {
    @EnvironmentObject var state: AppState
    @State private var engine: AutomationEngine?
    @State private var showingTargetsEditor = false

    var body: some View {
        Group {
            if state.phase == .needsCalibration {
                CalibrationView()
            } else {
                dashboard
            }
        }
        .onAppear {
            if engine == nil { engine = AutomationEngine(state: state) }
            state.refreshEnvironment()
        }
    }

    private var dashboard: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            environmentWarnings
            progressSection
            controls
            Divider()
            recordsTable
            Divider()
            logView
        }
        .padding(16)
        .frame(minWidth: 640, minHeight: 560)
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading) {
                Text("ChatGPT Share Link Exporter").font(.title2).bold()
                Text(state.currentStatus).foregroundStyle(.secondary)
            }
            Spacer()
            Button(state.simulating ? "Hide simulation" : "Simulate clicks") {
                state.toggleSimulation()
            }
            Button("Edit targets") { showingTargetsEditor = true }
                .sheet(isPresented: $showingTargetsEditor) {
                    TargetsEditorView().environmentObject(state)
                }
            Button("Full recalibration") { state.phase = .needsCalibration }
        }
    }

    @ViewBuilder private var environmentWarnings: some View {
        if !state.permissionGranted {
            Label("Accessibility permission missing: System Settings > Privacy & Security > Accessibility, enable your terminal (or the built app), then relaunch.",
                  systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        }
        if !state.mirroringRunning {
            Label("iPhone Mirroring is not running. Open it and unlock the phone before starting.",
                  systemImage: "iphone.slash")
                .foregroundStyle(.orange)
        }
    }

    private var progressSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            ProgressView(value: state.progressFraction)
            HStack {
                Text("\(state.exportManager.successCount) / \(state.config.totalConversations) exported")
                if state.exportManager.failureCount > 0 {
                    Text("· \(state.exportManager.failureCount) failed").foregroundStyle(.red)
                }
                Spacer()
                Text(state.estimatedRemaining).foregroundStyle(.secondary)
            }
            .font(.callout)
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            switch state.phase {
            case .running:
                Button("Pause") { engine?.pause() }
                Button("Stop") { engine?.stop() }
            case .paused, .idle, .finished:
                Button(state.phase == .paused ? "Resume" : "Start") {
                    state.refreshEnvironment()
                    engine?.start()
                }
                .keyboardShortcut(.defaultAction)
                if state.exportManager.failureCount > 0 {
                    Button("Retry \(state.exportManager.failureCount) failures") { engine?.retryFailures() }
                }
                Button("Fetch titles + archive pages") { fetchTitles() }
                    .disabled(state.exportManager.successCount == 0)
            case .fetchingTitles:
                ProgressView().controlSize(.small)
                Text("Fetching pages...")
            case .needsCalibration:
                EmptyView()
            }
            Spacer()
            Toggle("Verify links", isOn: $state.config.verifyLinks)
                .onChange(of: state.config.verifyLinks) { state.saveConfig() }
            Toggle("Dry run", isOn: $state.config.dryRun)
                .onChange(of: state.config.dryRun) { state.saveConfig() }
            HStack {
                Text("Pace")
                Slider(value: $state.config.delayMultiplier, in: 0.5...3.0, step: 0.25)
                    .frame(width: 120)
                    .onChange(of: state.config.delayMultiplier) { state.saveConfig() }
                Text(String(format: "%.2fx", state.config.delayMultiplier))
                    .monospacedDigit().foregroundStyle(.secondary)
            }
        }
    }

    private var recordsTable: some View {
        List(state.records.reversed()) { record in
            HStack {
                Image(systemName: record.success ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(record.success ? .green : .red)
                Text("#\(record.index + 1)")
                    .frame(width: 40, alignment: .leading)
                    .foregroundStyle(.secondary)
                Text(record.title ?? record.url).lineLimit(1)
                Spacer()
                if record.success, let url = URL(string: record.url) {
                    Link("open", destination: url)
                }
            }
            .font(.callout)
        }
        .frame(minHeight: 180)
    }

    private var logView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(state.logLines.enumerated()), id: \.offset) { i, line in
                        Text(line).font(.system(size: 11, design: .monospaced)).id(i)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: 120)
            .onChange(of: state.logLines.count) {
                proxy.scrollTo(state.logLines.count - 1, anchor: .bottom)
            }
        }
    }

    private func fetchTitles() {
        state.phase = .fetchingTitles
        let fetcher = TitleFetcher(outputDir: state.configManager.outputDir)
        let records = state.exportManager.state.records
        Task {
            let results = await fetcher.fetchAll(records: records) { done, total, url in
                Task { @MainActor in
                    state.currentStatus = "Fetching page \(done)/\(total): \(url)"
                }
            }
            await MainActor.run {
                for result in results {
                    if let title = result.title {
                        state.exportManager.setTitle(title, forURL: result.url)
                    }
                }
                state.records = state.exportManager.state.records
                state.phase = .idle
                let parsed = results.filter(\.parsedMessages).count
                state.currentStatus = "Fetched \(results.count) pages: \(parsed) parsed to Markdown, HTML archived for all"
                state.logger.log("Fetched \(results.count) pages, \(results.filter { $0.title != nil }.count) titles, \(parsed) parsed to structured Markdown (see output/markdown/)")
            }
        }
    }
}
