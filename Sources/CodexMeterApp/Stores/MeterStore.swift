import AppKit
import Combine
import Foundation

@MainActor
final class MeterStore: ObservableObject {
    @Published private(set) var summary: MeterSummary?
    @Published private(set) var loadError: String?
    @Published var settings: MeterSettings

    private let archiveDirectory: URL
    private let latestURL: URL
    private let settingsURL: URL
    private let graphURL: URL
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(
        archiveDirectory: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Documents/Archives/Codex Meter")
    ) {
        self.archiveDirectory = archiveDirectory
        latestURL = archiveDirectory.appendingPathComponent("latest.json")
        settingsURL = archiveDirectory.appendingPathComponent("settings.json")
        graphURL = archiveDirectory.appendingPathComponent("usage.svg")
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        settings = Self.loadSettings(from: settingsURL)
        reload()
    }

    var menuBarTitle: String {
        guard let codex = summary?.codex else {
            return "Codex Meter"
        }
        let primary = formattedPercent(codex.primary?.usedPercent)
        let weekly = formattedPercent(codex.secondary?.usedPercent)
        return "\(primary) / \(weekly)"
    }

    var menuBarSymbol: String {
        guard let percent = summary?.codex?.primary?.usedPercent else {
            return "gauge.with.dots.needle.0percent"
        }
        if percent >= 90 {
            return "gauge.with.dots.needle.100percent"
        }
        if percent >= 50 {
            return "gauge.with.dots.needle.67percent"
        }
        return "gauge.with.dots.needle.33percent"
    }

    var archivePath: String {
        archiveDirectory.path
    }

    func reload() {
        do {
            let data = try Data(contentsOf: latestURL)
            let snapshot = try decoder.decode(LatestSnapshot.self, from: data)
            let limits = snapshot.result.rateLimitsByLimitId.values.sorted {
                $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending
            }
            let collectedAt = Self.isoDateFormatter.date(from: snapshot.collectedAt)
            summary = MeterSummary(
                collectedAt: collectedAt,
                codex: snapshot.result.rateLimits ?? snapshot.result.rateLimitsByLimitId["codex"],
                limits: limits,
                resetCredits: snapshot.result.rateLimitResetCredits?.availableCount
            )
            loadError = nil
        } catch {
            summary = nil
            loadError = error.localizedDescription
        }
    }

    func updateDefaultViewPreset(_ preset: ViewPreset) {
        settings.defaultViewPreset = preset
        saveSettings()
    }

    func openGraph() {
        NSWorkspace.shared.open(graphURL)
    }

    func openArchiveFolder() {
        NSWorkspace.shared.open(archiveDirectory)
    }

    func quit() {
        NSApplication.shared.terminate(nil)
    }

    func formattedPercent(_ value: Double?) -> String {
        guard let value else {
            return "--%"
        }
        if value.rounded() == value {
            return "\(Int(value))%"
        }
        return String(format: "%.1f%%", value)
    }

    func formattedReset(_ epoch: Int?) -> String {
        guard let epoch else {
            return "unknown"
        }
        return Self.dateFormatter.string(from: Date(timeIntervalSince1970: TimeInterval(epoch)))
    }

    func formattedCollectedAt() -> String {
        guard let collectedAt = summary?.collectedAt else {
            return "No snapshot loaded"
        }
        return Self.dateFormatter.string(from: collectedAt)
    }

    private func saveSettings() {
        do {
            try FileManager.default.createDirectory(
                at: archiveDirectory,
                withIntermediateDirectories: true
            )
            let data = try encoder.encode(settings)
            try data.write(to: settingsURL, options: .atomic)
            loadError = nil
        } catch {
            loadError = "Could not save settings: \(error.localizedDescription)"
        }
    }

    private static func loadSettings(from url: URL) -> MeterSettings {
        do {
            let data = try Data(contentsOf: url)
            return try JSONDecoder().decode(MeterSettings.self, from: data)
        } catch {
            return .defaultValue
        }
    }

    private static let isoDateFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter
    }()
}
