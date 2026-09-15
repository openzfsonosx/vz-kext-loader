// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VZKextLoader",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "VZKextLoader",
            path: "Sources/VZKextLoader"
        )
    ]
)
