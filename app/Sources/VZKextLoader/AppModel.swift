import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var checks: [CheckItem] = []
    @Published var summary: String = "Not checked yet."
    @Published var ready: Bool = false
    @Published var running: Bool = false
    @Published var errorText: String?

    func runChecks() {
        running = true
        errorText = nil
        Task.detached(priority: .userInitiated) {
            do {
                let result = try Engine.check()
                await MainActor.run {
                    self.checks = result.checks
                    self.summary = result.summary
                    self.ready = result.ready
                    self.running = false
                }
            } catch {
                await MainActor.run {
                    self.errorText = error.localizedDescription
                    self.summary = "Host check failed."
                    self.ready = false
                    self.running = false
                }
            }
        }
    }
}
