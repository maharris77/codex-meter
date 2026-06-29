import SwiftUI

@main
struct CodexMeterApp: App {
    @StateObject private var store = MeterStore()

    var body: some Scene {
        MenuBarExtra {
            MenuBarContentView(store: store)
        } label: {
            Label(store.menuBarTitle, systemImage: store.menuBarSymbol)
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView(store: store)
        }
    }
}
