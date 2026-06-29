import Foundation

enum ViewPreset: String, CaseIterable, Codable, Identifiable {
    case fiveHours = "five_hours"
    case oneDay = "one_day"
    case sevenDays = "seven_days"
    case thirtyDays = "thirty_days"
    case all

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fiveHours:
            "Last 5 hours"
        case .oneDay:
            "Last 24 hours"
        case .sevenDays:
            "Last 7 days"
        case .thirtyDays:
            "Last 30 days"
        case .all:
            "All data"
        }
    }
}

struct MeterSettings: Codable, Equatable {
    var defaultViewPreset: ViewPreset

    static let defaultValue = MeterSettings(defaultViewPreset: .sevenDays)

    enum CodingKeys: String, CodingKey {
        case defaultViewPreset
    }

    init(defaultViewPreset: ViewPreset) {
        self.defaultViewPreset = defaultViewPreset
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let rawPreset = try container.decodeIfPresent(String.self, forKey: .defaultViewPreset)
        defaultViewPreset = rawPreset.flatMap(ViewPreset.init(rawValue:)) ?? .sevenDays
    }
}

struct LatestSnapshot: Decodable {
    let collectedAt: String
    let collectedAtEpoch: Int?
    let result: RateLimitResult
}

struct RateLimitResult: Decodable {
    let rateLimits: LimitSnapshot?
    let rateLimitsByLimitId: [String: LimitSnapshot]
    let rateLimitResetCredits: ResetCredits?
}

struct LimitSnapshot: Decodable, Identifiable {
    let limitId: String?
    let limitName: String?
    let planType: String?
    let primary: WindowSnapshot?
    let secondary: WindowSnapshot?

    var id: String { limitId ?? limitName ?? "codex" }

    var displayName: String {
        if let limitName, !limitName.isEmpty {
            return limitName
        }
        if limitId == "codex" {
            return "Codex"
        }
        return limitId ?? "Codex"
    }
}

struct WindowSnapshot: Decodable {
    let usedPercent: Double?
    let resetsAt: Int?
    let windowDurationMins: Int?
}

struct ResetCredits: Decodable {
    let availableCount: Int?
}

struct MeterSummary {
    let collectedAt: Date?
    let codex: LimitSnapshot?
    let limits: [LimitSnapshot]
    let resetCredits: Int?
}
