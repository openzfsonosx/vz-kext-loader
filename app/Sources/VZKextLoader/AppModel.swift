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

    @Published var errorText: String?

    var selectedVM: VMItem? {
        guard let id = selectedVMID else { return nil }
        return vms.first { $0.id == id }
    }

    func refreshAll() {
        runChecks()
        loadVMs()
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
