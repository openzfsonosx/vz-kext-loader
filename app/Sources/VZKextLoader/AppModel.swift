import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    // Host checks
    @Published var checks: [CheckItem] = []
    @Published var hostSummary: String = "Not checked yet."
    @Published var hostReady: Bool = false
    @Published var checkingHost: Bool = false

    // VMs
    @Published var vms: [VMItem] = []
    @Published var selectedVMID: String?
    @Published var loadingVMs: Bool = false
    @Published var searchPaths: [String] = []

    // Boot / overlay
    @Published var overlayMounted: Bool = false
    @Published var bootBusy: Bool = false
    @Published var bootLog: [String] = []

    @Published var errorText: String?

    var selectedVM: VMItem? {
        guard let id = selectedVMID else { return nil }
        return vms.first { $0.id == id }
    }

    func refreshAll() {
        runChecks()
        loadVMs()
        refreshOverlay()
    }

    private func log(_ s: String) {
        let ts = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        bootLog.append("[\(ts)] \(s)")
    }

    func refreshOverlay() {
        Task.detached(priority: .utility) {
            let st = try? Engine.overlayStatus()
            await MainActor.run { self.overlayMounted = st?.mounted ?? false }
        }
    }

    /// Ensure the patched-AVPBooter overlay is mounted, then start the VM.
    func bootSelected() {
        guard let vm = selectedVM, vm.patchable, !bootBusy else { return }
        bootBusy = true
        log("Boot requested: \(vm.name)")

        Task.detached(priority: .userInitiated) {
            func step(_ s: String) async { await MainActor.run { self.log(s) } }
            do {
                let st = try Engine.overlayStatus()
                if !st.mounted {
                    await step("Overlay not mounted; building patched AVPBooter overlay…")
                    let b = try Engine.overlayBuild()
                    guard b.ok, let argv = b.mount_argv else {
                        throw EngineError.badOutput(b.error ?? "overlay build failed")
                    }
                    await step("Built overlay (AVPBooter \(b.avpbooter_state ?? "?")), device \(b.device ?? "?").")
                    await step("Requesting administrator authorization to mount overlay…")
                    let r = await MainActor.run { Privileged.run(argv) }
                    guard r.ok else {
                        throw EngineError.launchFailed(r.error ?? "mount cancelled")
                    }
                    await step("Overlay mounted over framework Resources.")
                } else {
                    await step("Overlay already mounted.")
                }
                await MainActor.run { self.overlayMounted = true }

                await step("Starting VM (scripting UTM)…")
                let s = await MainActor.run { UTMScript.start(vm.uuid) }
                guard s.ok else {
                    throw EngineError.launchFailed(s.error ?? "UTM start failed")
                }
                await step("Start issued; watching status…")
                for _ in 0..<20 {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    let vs = await MainActor.run { UTMScript.status(vm.uuid) }
                    if vs.ok && vs.value == "started" {
                        await step("VM is running. (Expect a double-boot if a kext rebuild is pending.)")
                        break
                    }
                }
                await MainActor.run { self.bootBusy = false; self.loadVMs() }
            } catch {
                await step("Boot failed: \(error.localizedDescription)")
                await MainActor.run { self.bootBusy = false }
            }
        }
    }

    /// Boot the selected VM into Recovery (for the one-time security setup:
    /// Permissive + disable SIP & Authenticated Root). No overlay needed — this
    /// is the pre-patch step on a stock VM.
    func bootRecovery() {
        guard let vm = selectedVM, !bootBusy else { return }
        guard vm.status != "started" else { log("Stop \(vm.name) first."); return }
        bootBusy = true
        log("Booting \(vm.name) into Recovery…")
        Task.detached(priority: .userInitiated) {
            let s = await MainActor.run { UTMScript.start(vm.uuid, recovery: true) }
            await MainActor.run {
                self.log(s.ok ? "Recovery boot issued. In Startup Security Utility: set Permissive, then disable SIP and Authenticated Root."
                              : "Recovery boot failed: \(s.error ?? "unknown")")
                self.bootBusy = false
                self.loadVMs()
            }
        }
    }

    // Extra VM search directories (for VMs outside ~/Library/.../Documents,
    // e.g. on an external volume). Persisted; passed to the engine as
    // VZKL_VM_SEARCH_PATHS.
    var extraSearchPaths: [String] {
        UserDefaults.standard.stringArray(forKey: "extraSearchPaths") ?? []
    }

    func addSearchPath(_ path: String) {
        var paths = extraSearchPaths
        guard !path.isEmpty, !paths.contains(path) else { return }
        paths.append(path)
        UserDefaults.standard.set(paths, forKey: "extraSearchPaths")
        log("Added VM search path: \(path)")
        loadVMs()
    }

    func stopSelected() {
        guard let vm = selectedVM, !bootBusy else { return }
        bootBusy = true
        log("Stopping \(vm.name)…")
        Task.detached(priority: .userInitiated) {
            let s = await MainActor.run { UTMScript.stop(vm.uuid) }
            await MainActor.run {
                self.log(s.ok ? "Stop issued." : "Stop failed: \(s.error ?? "unknown")")
                self.bootBusy = false
                self.loadVMs()
            }
        }
    }

    /// Patch (or unpatch) the selected VM's guest boot chain, as root via one
    /// admin prompt. The VM must be stopped.
    func patchSelected(unpatch: Bool = false) {
        guard let vm = selectedVM, !bootBusy else { return }
        guard !unpatch ? vm.patchable : true else { return }
        if vm.status == "started" {
            log("Stop \(vm.name) before \(unpatch ? "unpatching" : "patching") its disk.")
            return
        }
        bootBusy = true
        log("\(unpatch ? "Unpatching" : "Patching") \(vm.name)… (administrator required)")
        let progressFile = Engine.progressPath(uuid: vm.uuid)
        try? "".write(toFile: progressFile, atomically: true, encoding: .utf8)
        let poller = startProgressTail(progressFile)
        Task.detached(priority: .userInitiated) {
            let argv = await MainActor.run { Engine.patchVMArgv(uuid: vm.uuid, unpatch: unpatch) }
            // Subprocess (not NSAppleScript) so the main thread stays free and
            // the progress tailer can stream while this runs.
            let r = Privileged.runViaSubprocess(argv)
            await MainActor.run {
                poller.cancel()
                self.handlePatchOutput(r, unpatch: unpatch)
                self.bootBusy = false
                self.loadVMs()
            }
        }
    }

    /// Tail the engine's progress file and log new lines live while the single
    /// privileged patch call is blocked. Returns a cancellable task.
    private func startProgressTail(_ path: String) -> Task<Void, Never> {
        Task { [weak self] in
            var shown = 0
            while !Task.isCancelled {
                if let text = try? String(contentsOfFile: path, encoding: .utf8) {
                    let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
                    if lines.count > shown {
                        for line in lines[shown...] {
                            self?.log("  " + line)
                        }
                        shown = lines.count
                    }
                }
                try? await Task.sleep(nanoseconds: 400_000_000)
            }
        }
    }

    private func handlePatchOutput(_ r: Privileged.Result, unpatch: Bool) {
        guard r.ok, let data = r.output.data(using: .utf8),
              let res = try? JSONDecoder().decode(PatchVMResult.self, from: data) else {
            log("\(unpatch ? "Unpatch" : "Patch") failed: \(r.error ?? "no/invalid engine output")")
            return
        }
        if !res.ok {
            log("\(unpatch ? "Unpatch" : "Patch") failed: \(res.error ?? "unknown")")
            return
        }
        if unpatch {
            log("Restored \(res.restored?.count ?? 0) file(s) from backup.")
        } else {
            log("Patched \(res.patched ?? 0) file(s); backups + manifest saved.")
            for e in res.entries ?? [] {
                log("  \(e.role ?? "?") — \(e.state ?? "?")")
            }
        }
    }

    /// Unmount the overlay (admin prompt). The overlay reverts on reboot anyway.
    func unmountOverlay() {
        Task.detached(priority: .userInitiated) {
            guard let st = try? Engine.overlayStatus(),
                  let resources = st.framework_resources, !resources.isEmpty else {
                await MainActor.run { self.log("Could not determine overlay mount point.") }
                return
            }
            let r = await MainActor.run { Privileged.run(["/sbin/umount", resources]) }
            await MainActor.run {
                self.log(r.ok ? "Overlay unmounted." : "Unmount failed: \(r.error ?? "unknown")")
                self.overlayMounted = !r.ok
            }
        }
    }

    func runChecks() {
        checkingHost = true
        errorText = nil
        Task.detached(priority: .userInitiated) {
            do {
                let r = try Engine.check()
                await MainActor.run {
                    self.checks = r.checks
                    self.hostSummary = r.summary
                    self.hostReady = r.ready
                    self.checkingHost = false
                }
            } catch {
                await MainActor.run {
                    self.errorText = error.localizedDescription
                    self.hostSummary = "Host check failed."
                    self.hostReady = false
                    self.checkingHost = false
                }
            }
        }
    }

    func loadVMs() {
        loadingVMs = true
        Task.detached(priority: .userInitiated) {
            do {
                let r = try Engine.listVMs()
                await MainActor.run {
                    self.vms = r.vms
                    self.searchPaths = r.search_paths
                    if self.selectedVMID == nil {
                        self.selectedVMID = r.vms.first(where: { $0.patchable })?.id
                            ?? r.vms.first?.id
                    }
                    self.loadingVMs = false
                }
                await self.enrichStatuses()
            } catch {
                await MainActor.run {
                    self.errorText = error.localizedDescription
                    self.loadingVMs = false
                }
            }
        }
    }

    /// utmctl's status is Automation-gated and unreliable from a spawned child,
    /// so refresh each VM's run status by scripting UTM from the app directly.
    /// The first such call also triggers the Automation permission prompt.
    private func enrichStatuses() async {
        let ids = await MainActor.run { self.vms.map { $0.uuid } }
        var statusByID: [String: String] = [:]
        for id in ids {
            let outcome = await MainActor.run { UTMScript.status(id) }
            if outcome.ok, !outcome.value.isEmpty {
                statusByID[id] = outcome.value
            }
        }
        await MainActor.run {
            self.vms = self.vms.map { vm in
                var v = vm
                if let s = statusByID[vm.uuid] { v.status = s }
                return v
            }
        }
    }
}
