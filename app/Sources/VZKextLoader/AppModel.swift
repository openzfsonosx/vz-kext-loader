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

                await step("Starting VM via utmctl…")
                let s = try Engine.vmStart(vm.uuid)
                guard s.ok else {
                    throw EngineError.launchFailed(s.error ?? "utmctl start failed")
                }
                await step("Start issued; watching status…")
                for _ in 0..<20 {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    if let vs = try? Engine.vmStatus(vm.uuid), vs.status == "started" {
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

    func stopSelected() {
        guard let vm = selectedVM, !bootBusy else { return }
        bootBusy = true
        log("Stopping \(vm.name)…")
        Task.detached(priority: .userInitiated) {
            let s = try? Engine.vmStop(vm.uuid)
            await MainActor.run {
                self.log(s?.ok == true ? "Stop issued." : "Stop failed: \(s?.error ?? "unknown")")
                self.bootBusy = false
                self.loadVMs()
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
            } catch {
                await MainActor.run {
                    self.errorText = error.localizedDescription
                    self.loadingVMs = false
                }
            }
        }
    }
}
