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
}
