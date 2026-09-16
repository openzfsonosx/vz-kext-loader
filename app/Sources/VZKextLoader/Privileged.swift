import Foundation

/// Runs a single command with administrator privileges via a native macOS
/// password prompt (AppleScript `do shell script … with administrator
/// privileges`). Option 1: prompt per privileged action, no bundled helper.
enum Privileged {

    struct Result {
        let ok: Bool
        let output: String
        let error: String?
    }

    /// Escape a string to sit inside an AppleScript double-quoted literal.
    private static func asEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
    }

    /// Quote one argv element for /bin/sh.
    private static func shQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Run `argv` as root via an `osascript` SUBPROCESS. Unlike `run`, this does
    /// not block the main thread, so callers can stream progress while it runs.
    /// Shows the same system auth dialog. Safe to call off the main actor.
    static func runViaSubprocess(_ argv: [String]) -> Result {
        let shellCommand = argv.map(shQuote).joined(separator: " ")
        let script = "do shell script \"\(asEscape(shellCommand))\" with administrator privileges"
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        proc.arguments = ["-e", script]
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do {
            try proc.run()
        } catch {
            return Result(ok: false, output: "", error: error.localizedDescription)
        }
        let od = out.fileHandleForReading.readDataToEndOfFile()
        let ed = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        let output = String(data: od, encoding: .utf8) ?? ""
        if proc.terminationStatus != 0 {
            let msg = String(data: ed, encoding: .utf8) ?? "authorization failed or was cancelled"
            return Result(ok: false, output: output, error: msg.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return Result(ok: true, output: output, error: nil)
    }

    /// Run `argv` as root. Shows the system auth dialog; returns the outcome.
    /// Must be called on the main thread (AppleScript UI).
    @MainActor
    static func run(_ argv: [String]) -> Result {
        let shellCommand = argv.map(shQuote).joined(separator: " ")
        let script = "do shell script \"\(asEscape(shellCommand))\" with administrator privileges"

        var errorDict: NSDictionary?
        let scriptObject = NSAppleScript(source: script)
        let out = scriptObject?.executeAndReturnError(&errorDict)

        if let err = errorDict {
            let msg = (err[NSAppleScript.errorMessage] as? String) ?? "authorization failed or was cancelled"
            return Result(ok: false, output: "", error: msg)
        }
        return Result(ok: true, output: out?.stringValue ?? "", error: nil)
    }
}
