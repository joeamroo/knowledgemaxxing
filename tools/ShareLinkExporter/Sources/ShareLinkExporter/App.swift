import SwiftUI

@main
struct ShareLinkExporterApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup {
            MainView()
                .environmentObject(state)
        }
        .windowResizability(.contentSize)
    }
}
