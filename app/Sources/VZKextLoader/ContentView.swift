import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: AppModel
    @State private var showAllChecks = false

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 240, ideal: 280)
        } detail: {
            detail
        }
        .frame(minWidth: 820, minHeight: 520)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button(action: { model.refreshAll() }) {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(model.checkingHost || model.loadingVMs)
            }
        }
        .onAppear {
            if model.checks.isEmpty { model.refreshAll() }
        }
    }

    // MARK: Sidebar — VM list

    private var sidebar: some View {
        List(selection: $model.selectedVMID) {
            Section("Virtual Machines") {
                ForEach(model.vms) { vm in
                    VMRow(vm: vm).tag(vm.id)
                }
                if model.vms.isEmpty && !model.loadingVMs {
                    Text("No UTM VMs found").foregroundStyle(.secondary)
                }
            }
        }
        .overlay {
            if model.loadingVMs && model.vms.isEmpty {
                ProgressView("Scanning VMs…")
            }
        }
    }

    // MARK: Detail

    private var detail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let err = model.errorText {
                    Label(err, systemImage: "exclamationmark.octagon.fill")
                        .foregroundStyle(.white)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.85))
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }

                hostCard
                vmCard
                actionBar
                if !model.bootLog.isEmpty { bootLogCard }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var hostCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: model.hostReady ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(model.hostReady ? .green : .orange)
                    Text(model.hostSummary).fontWeight(.medium)
                    Spacer()
                    if model.checkingHost { ProgressView().controlSize(.small) }
                    Button(showAllChecks ? "Hide details" : "Show details") {
                        showAllChecks.toggle()
                    }
                    .buttonStyle(.link)
                }
                if showAllChecks {
                    Divider()
                    ForEach(model.checks) { c in
                        HStack(alignment: .top, spacing: 8) {
                            StatusDot(status: c.status)
                            VStack(alignment: .leading, spacing: 1) {
                                HStack {
                                    Text(c.label)
                                    Spacer()
                                    Text(c.detail).foregroundStyle(.secondary).font(.callout)
                                }
                                if !c.fix.isEmpty && (c.status == "fail" || c.status == "warn") {
                                    Text(c.fix).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        .padding(.vertical, 1)
                    }
                }
            }
            .padding(6)
        } label: {
            Label("Host Requirements", systemImage: "desktopcomputer")
        }
    }

    @ViewBuilder private var vmCard: some View {
        GroupBox {
            if let vm = model.selectedVM {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        StatusBadge(status: vm.status)
                        Text(vm.name).font(.title3).bold()
                        Spacer()
                        if vm.patchable {
                            Label("Patchable", systemImage: "checkmark.circle.fill")
                                .foregroundStyle(.green).font(.callout)
                        } else {
                            Label("Not patchable", systemImage: "xmark.circle.fill")
                                .foregroundStyle(.red).font(.callout)
                        }
                    }
                    if !vm.patchable && !vm.reason.isEmpty {
                        Text(vm.reason).font(.callout).foregroundStyle(.secondary)
                    }
                    Divider()
                    infoRow("UUID", vm.uuid)
                    if !vm.backend.isEmpty { infoRow("Backend", vm.backend) }
                    if !vm.os.isEmpty { infoRow("OS", "\(vm.os)  \(vm.arch)") }
                    if !vm.bundle_path.isEmpty { infoRow("Bundle", vm.bundle_path) }
                }
                .padding(6)
            } else {
                Text("Select a VM from the sidebar.")
                    .foregroundStyle(.secondary)
                    .padding(6)
            }
        } label: {
            Label("Selected VM", systemImage: "shippingbox")
        }
    }

    private var actionBar: some View {
        let vm = model.selectedVM
        let started = vm?.status == "started"
        let canBoot = model.hostReady && (vm?.patchable ?? false) && !started && !model.bootBusy
        let canStop = started && !model.bootBusy
        return VStack(alignment: .leading, spacing: 8) {
            let canPatch = (vm?.patchable ?? false) && !started && !model.bootBusy
            HStack(spacing: 12) {
                Button { model.patchSelected() } label: { Label("Patch…", systemImage: "bandage") }
                    .disabled(!canPatch)
                    .help("Patch the guest boot chain (LLB, Preboot + Recovery iBoot/kernelcache). VM must be stopped.")
                Button { model.patchSelected(unpatch: true) } label: { Label("Unpatch", systemImage: "arrow.uturn.backward") }
                    .disabled(started || model.bootBusy)
                    .help("Restore the originals from backup.")
                Button { model.bootSelected() } label: {
                    Label("Boot (overlay)", systemImage: "play.fill")
                }
                .disabled(!canBoot)
                Button { model.stopSelected() } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
                .disabled(!canStop)
                Button { } label: { Label("Verify", systemImage: "checkmark.shield") }
                    .disabled(true)
                    .help("Kext-load verification arrives in a later slice.")
                Spacer()
                if model.bootBusy { ProgressView().controlSize(.small) }
            }
            HStack(spacing: 8) {
                Circle().fill(model.overlayMounted ? Color.green : Color.secondary.opacity(0.5))
                    .frame(width: 8, height: 8)
                Text(model.overlayMounted ? "AVPBooter overlay mounted" : "overlay not mounted")
                    .font(.caption).foregroundStyle(.secondary)
                if model.overlayMounted {
                    Button("Unmount") { model.unmountOverlay() }
                        .buttonStyle(.link).font(.caption)
                }
            }
        }
    }

    private var bootLogCard: some View {
        GroupBox {
            ScrollView {
                Text(model.bootLog.joined(separator: "\n"))
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(4)
            }
            .frame(height: 140)
        } label: {
            Label("Activity", systemImage: "text.alignleft")
        }
    }

    private func infoRow(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(k).frame(width: 66, alignment: .leading).foregroundStyle(.secondary)
            Text(v).textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .font(.callout)
    }
}

// MARK: - Row / badge helpers

struct VMRow: View {
    let vm: VMItem
    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(vm.status == "started" ? Color.green : Color.secondary.opacity(0.5))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(vm.name).lineLimit(1)
                Text(vm.patchable ? "patchable" : "not patchable")
                    .font(.caption)
                    .foregroundStyle(vm.patchable ? .green : .secondary)
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }
}

struct StatusBadge: View {
    let status: String
    var body: some View {
        Text(status)
            .font(.caption).bold()
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(status == "started" ? Color.green.opacity(0.2) : Color.secondary.opacity(0.15))
            .foregroundStyle(status == "started" ? .green : .secondary)
            .clipShape(Capsule())
    }
}

struct StatusDot: View {
    let status: String
    var body: some View {
        Image(systemName: symbol)
            .foregroundStyle(color)
            .font(.system(size: 13, weight: .bold))
            .frame(width: 16)
    }
    private var symbol: String {
        switch status {
        case "ok": return "checkmark.circle.fill"
        case "warn": return "exclamationmark.triangle.fill"
        case "fail": return "xmark.circle.fill"
        default: return "circle.fill"
        }
    }
    private var color: Color {
        switch status {
        case "ok": return .green
        case "warn": return .orange
        case "fail": return .red
        default: return .secondary
        }
    }
}
