import Foundation
import AppKit

/// Controls UTM by scripting it directly (AppleEvents) from *this app*, so macOS
/// attributes any Automation permission to vz-kext-loader (which declares
/// NSAppleEventsUsageDescription + the apple-events entitlement).
///
/// NB: on some systems macOS silently denies the Automation prompt for this app
/// even though signature, entitlement, usage string and activation policy are all
/// correct (errAEEventNotPermitted with no consent prompt and no Settings entry).
/// The boot flow therefore treats a scripting failure as non-fatal and falls back
/// to opening UTM for a manual start — see AppModel.bootSelected.
enum UTMScript {

    static let utmBundleID = "com.utmapp.UTM"

    struct Outcome {
        let ok: Bool
        let value: String     // status text, or ""
        let error: String?
    }

    /// Launch UTM (the manual-start fallback when Automation isn't available).
    @MainActor
    static func openUTM() {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: utmBundleID)
        else { return }
        NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
    }

    /// Map an NSAppleScript error dict to a user-facing message.
    private static func permissionMessage(for err: NSDictionary) -> String {
        let code = (err[NSAppleScript.errorNumber] as? Int) ?? 0
        switch code {
        case -1743:   // errAEEventNotPermitted — Automation denied
            return "macOS did not grant Automation permission to control UTM."
        case -600, -1728:   // procNotFound / app isn't running
            return "UTM does not appear to be running."
        default:
            return (err[NSAppleScript.errorMessage] as? String)
                ?? "AppleScript error (\(code))."
        }
    }

    @MainActor
    private static func runAppleScript(_ source: String) -> Outcome {
        var errorDict: NSDictionary?
        guard let script = NSAppleScript(source: source) else {
            return Outcome(ok: false, value: "", error: "could not build AppleScript")
        }
        let result = script.executeAndReturnError(&errorDict)
        if let err = errorDict {
            return Outcome(ok: false, value: "", error: permissionMessage(for: err))
        }
        return Outcome(ok: true, value: result.stringValue ?? "", error: nil)
    }

    @MainActor
    static func start(_ uuid: String, recovery: Bool = false) -> Outcome {
        let rec = recovery ? " with recovery" : ""
        return runAppleScript(
            "tell application \"UTM\" to start virtual machine id \"\(uuid)\"\(rec)")
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
