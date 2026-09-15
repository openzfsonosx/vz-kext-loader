import Foundation

/// Controls UTM by scripting it directly (AppleEvents) from *this app*, so macOS
/// attributes the Automation permission to vz-kext-loader (which declares
/// NSAppleEventsUsageDescription). Spawning utmctl as a child does not attribute
/// the grant reliably, which is why that path failed with "not found".
enum UTMScript {

    struct Outcome {
        let ok: Bool
        let value: String     // status text, or ""
        let error: String?
    }

    @MainActor
    private static func runAppleScript(_ source: String) -> Outcome {
        var errorDict: NSDictionary?
        guard let script = NSAppleScript(source: source) else {
            return Outcome(ok: false, value: "", error: "could not build AppleScript")
        }
        let result = script.executeAndReturnError(&errorDict)
        if let err = errorDict {
            let msg = (err[NSAppleScript.errorMessage] as? String) ?? "AppleScript error"
            return Outcome(ok: false, value: "", error: msg)
        }
        return Outcome(ok: true, value: result.stringValue ?? "", error: nil)
    }

    @MainActor
    static func start(_ uuid: String) -> Outcome {
        runAppleScript("tell application \"UTM\" to start virtual machine id \"\(uuid)\"")
    }

    @MainActor
    static func stop(_ uuid: String) -> Outcome {
        runAppleScript("tell application \"UTM\" to stop virtual machine id \"\(uuid)\"")
    }

    /// Returns a normalized status string: started | starting | stopped | stopping | unknown.
    @MainActor
    static func status(_ uuid: String) -> Outcome {
        let src = """
        tell application "UTM"
          set vm to virtual machine id "\(uuid)"
          set s to status of vm
          if s is started then
            return "started"
          else if s is starting then
            return "starting"
          else if s is stopping then
            return "stopping"
          else if s is stopped then
            return "stopped"
          else
            return "unknown"
          end if
        end tell
        """
        return runAppleScript(src)
    }
}
