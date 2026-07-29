// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ShareLinkExporter",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "ShareLinkExporter",
            path: "Sources/ShareLinkExporter"
        )
    ]
)
