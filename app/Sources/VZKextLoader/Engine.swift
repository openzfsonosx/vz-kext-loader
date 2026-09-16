import Foundation

// MARK: - Models mirroring the engine's JSON

struct CheckItem: Codable, Identifiable, Hashable {
    var id: String
    var label: String
    var status: String      // "ok" | "warn" | "fail" | "info"
    var detail: String
    var fix: String
    var blocking: Bool
}

struct CheckResult: Codable {
    var checks: [CheckItem]
    var ready: Bool
    var summary: String
}

struct VMItem: Codable, Identifiable, Hashable {
    var uuid: String
    var name: String
    var status: String        // started | stopped | unknown
    var backend: String
    var os: String
    var arch: String
    var bundle_path: String
    var aux_path: String
    var patchable: Bool
    var reason: String
    var has_backup: Bool?

    var id: String { uuid }
}

struct VMListResult: Codable {
    var vms: [VMItem]
    var patchable_count: Int
    var search_paths: [String]
}

struct OverlayStatus: Codable {
    var mounted: Bool
    var device: String
    var dmg: String
    var framework_resources: String?
}

struct OverlayBuild: Codable {
    var ok: Bool
    var dmg: String?
    var device: String?
    var avpbooter_state: String?
    var avpbooter_sha: String?
    var mount_argv: [String]?
    var unmount_argv: [String]?
    var error: String?
}

struct VMOp: Codable {
    var uuid: String?
    var status: String?
    var ok: Bool
    var output: String?
    var error: String?
}

struct PatchEntry: Codable, Hashable {
    var role: String?
    var state: String?
    var path: String?
}

struct PatchVMResult: Codable {
    var ok: Bool
    var patched: Int?
    var manifest: String?
    var entries: [PatchEntry]?
    var restored: [String]?
    var missing_backups: [String]?
    var error: String?
}

enum EngineError: LocalizedError {
    case engineNotFound(String)
    case launchFailed(String)
    case badOutput(String)

    var errorDescription: String? {
        switch self {
        case .engineNotFound(let p): return "Engine not found at \(p)"
        case .launchFailed(let m): return "Could not run the engine: \(m)"
        case .badOutput(let m): return "Engine returned unexpected output: \(m)"
        }
    }
}

/// Locates and runs the Python engine (`python3 -m vzkl ...`).
enum Engine {

    static let python = "/usr/bin/python3"

    /// Directory containing the `vzkl` package. Overridable via VZKL_ENGINE_DIR;
    /// defaults to the repo layout under the user's home.
    static var engineDir: String {
        if let env = ProcessInfo.processInfo.environment["VZKL_ENGINE_DIR"], !env.isEmpty {
            return env
        }
        return NSHomeDirectory() + "/src/vz-kext-loader/engine"
    }

    /// Run `vzkl <args> --json`, decoding the JSON into `T`.
    static func runJSON<T: Decodable>(_ args: [String], as type: T.Type) throws -> T {
        let dir = engineDir
        guard FileManager.default.fileExists(atPath: dir + "/vzkl/__main__.py") else {
            throw EngineError.engineNotFound(dir)
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = ["-m", "vzkl"] + args + ["--json"]
        proc.currentDirectoryURL = URL(fileURLWithPath: dir)

        // Clean, predictable environment. Keep HOME (user site-packages) and a
        // sane PATH so r2/utmctl resolve.
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
        // Extra VM search directories the user added (persisted).
        let extra = UserDefaults.standard.stringArray(forKey: "extraSearchPaths") ?? []
        if !extra.isEmpty {
            env["VZKL_VM_SEARCH_PATHS"] = extra.joined(separator: ":")
        }
        proc.environment = env

        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err

        do {
            try proc.run()
        } catch {
            throw EngineError.launchFailed(error.localizedDescription)
        }
        let outData = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()

        guard !outData.isEmpty else {
            let msg = String(data: errData, encoding: .utf8) ?? "no output"
            throw EngineError.badOutput(msg)
        }
        do {
            return try JSONDecoder().decode(T.self, from: outData)
        } catch {
            let raw = String(data: outData, encoding: .utf8) ?? "<binary>"
            throw EngineError.badOutput(raw)
        }
    }

    static func check() throws -> CheckResult {
        try runJSON(["check"], as: CheckResult.self)
    }

    static func listVMs() throws -> VMListResult {
        try runJSON(["list-vms"], as: VMListResult.self)
    }

    static func overlayStatus() throws -> OverlayStatus {
        try runJSON(["overlay-status"], as: OverlayStatus.self)
    }

    static func overlayBuild() throws -> OverlayBuild {
        try runJSON(["overlay-build"], as: OverlayBuild.self)
    }

    static func vmStart(_ uuid: String) throws -> VMOp {
        try runJSON(["vm", "start", uuid], as: VMOp.self)
    }

    static func vmStop(_ uuid: String) throws -> VMOp {
        try runJSON(["vm", "stop", uuid], as: VMOp.self)
    }

    static func vmStatus(_ uuid: String) throws -> VMOp {
        try runJSON(["vm", "status", uuid], as: VMOp.self)
    }

    /// The user site-packages dir, so pyimg4/capstone import when the engine runs
    /// as root (from an admin prompt). Empty string if it can't be determined.
    static func userSitePackages() -> String {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = ["-m", "site", "--user-site"]
        let out = Pipe()
        proc.standardOutput = out
        proc.standardError = Pipe()
        do { try proc.run() } catch { return "" }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return (String(data: data, encoding: .utf8) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// argv for running `vzkl patch-vm` (or --unpatch) as root, with PYTHONPATH
    /// covering both the engine and the user site-packages. Run via Privileged.run.
    static func patchVMArgv(uuid: String, unpatch: Bool) -> [String] {
        let pyPath = engineDir + ":" + userSitePackages()
        var args = ["/usr/bin/env", "PYTHONPATH=\(pyPath)", python,
                    "-m", "vzkl", "patch-vm", uuid]
        if unpatch { args.append("--unpatch") }
        args.append("--json")
        return args
    }
}
