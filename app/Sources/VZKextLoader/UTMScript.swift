import Foundation
import CoreServices

/// Controls UTM by scripting it directly (AppleEvents) from *this app*, so macOS
/// attributes the Automation permission to vz-kext-loader (which declares
/// NSAppleEventsUsageDescription). Spawning utmctl as a child does not attribute
/// the grant reliably, which is why that path failed with "not found".
enum UTMScript {

    static let utmBundleID = "com.utmapp.UTM"

    struct Outcome {
        let ok: Bool
        let value: String     // status text, or ""
        let error: String?
    }

    /// Explicitly request Automation permission for UTM, showing the consent
    /// dialog if needed. `NSAppleScript` alone can return "not permitted"
    /// WITHOUT prompting; this API reliably triggers the prompt.
    /// Returns nil if permitted, otherwise a human-readable reason.
    @MainActor
    @discardableResult
    static func ensurePermission() -> String? {
        var target = AEAddressDesc()
        guard let data = utmBundleID.data(using: .utf8) else {
            return "could not build target"
        }
        let createErr = data.withUnsafeBytes { raw -> OSErr in
            AECreateDesc(typeApplicationBundleID, raw.baseAddress, data.count, &target)
        }
        if createErr != noErr { return "AECreateDesc failed (\(createErr))" }
        defer { AEDisposeDesc(&target) }

        let status = AEDeterminePermissionToAutomateTarget(
            &target, typeWildCard, typeWildCard, true /* ask user if needed */)
        switch status {
        case noErr:
            return nil
        case OSStatus(errAEEventNotPermitted):
            return "Automation permission for UTM was denied. Enable it in System "
                 + "Settings › Privacy & Security › Automation."
        case OSStatus(procNotFound):
            return "UTM does not appear to be running."
        default:
            return "Automation permission check failed (\(status))."
        }
    }

    @MainActor
    private static func runAppleScript(_ source: String) -> Outcome {
        if let denied = ensurePermission() {
            return Outcome(ok: false, value: "", error: denied)
        }
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
