import SwiftUI

@main
struct AstraCradleCompanionApp: App {
    @StateObject private var client = AstraBeamClient()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(client)
                .preferredColorScheme(.dark)
        }
    }
}
