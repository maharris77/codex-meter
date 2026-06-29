import SwiftUI

struct MenuBarContentView: View {
    @Environment(\.openSettings) private var openSettings
    @ObservedObject var store: MeterStore

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if let error = store.loadError {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if let codex = store.summary?.codex {
                LimitCard(limit: codex, store: store)
            } else if store.loadError == nil {
                Text("No latest snapshot found.")
                    .foregroundStyle(.secondary)
            }

            if let resetCredits = store.summary?.resetCredits {
                HStack {
                    Text("Reset credits")
                    Spacer()
                    Text("\(resetCredits)")
                        .monospacedDigit()
                        .fontWeight(.semibold)
                }
                .font(.callout)
            }

            Divider()

            actions
        }
        .padding(16)
        .frame(width: 360)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Codex Meter")
                .font(.headline)
            Text(store.formattedCollectedAt())
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var actions: some View {
        HStack {
            Button("Refresh") {
                store.reload()
            }
            Button("Open Graph") {
                store.openGraph()
            }
            Button("Settings...") {
                openSettings()
            }
            Spacer()
            Button("Quit") {
                store.quit()
            }
        }
    }
}

private struct LimitCard: View {
    let limit: LimitSnapshot
    @ObservedObject var store: MeterStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(limit.displayName)
                .font(.subheadline)
                .fontWeight(.semibold)

            WindowRow(
                title: "5-hour",
                usedPercent: limit.primary?.usedPercent,
                reset: store.formattedReset(limit.primary?.resetsAt),
                tint: .blue
            )

            WindowRow(
                title: "7-day",
                usedPercent: limit.secondary?.usedPercent,
                reset: store.formattedReset(limit.secondary?.resetsAt),
                tint: .orange
            )
        }
    }
}

private struct WindowRow: View {
    let title: String
    let usedPercent: Double?
    let reset: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(formattedPercent)
                    .monospacedDigit()
                    .fontWeight(.semibold)
            }
            .font(.callout)
            ProgressView(value: percentValue, total: 100)
                .tint(tint)
            Text("Resets \(reset)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var percentValue: Double {
        usedPercent ?? 0
    }

    private var formattedPercent: String {
        guard let usedPercent else {
            return "--%"
        }
        if usedPercent.rounded() == usedPercent {
            return "\(Int(usedPercent))%"
        }
        return String(format: "%.1f%%", usedPercent)
    }
}
