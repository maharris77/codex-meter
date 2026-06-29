// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "CodexMeter",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "CodexMeter", targets: ["CodexMeterApp"])
    ],
    targets: [
        .executableTarget(
            name: "CodexMeterApp",
            path: "Sources/CodexMeterApp"
        )
    ]
)
