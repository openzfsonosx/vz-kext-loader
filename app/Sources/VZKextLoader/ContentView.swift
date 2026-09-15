import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            if let err = model.errorText {
                errorBanner(err)
            }
            checkList
            Divider()
            footer
        }
        .frame(minWidth: 560, minHeight: 460)
        .onAppear { if model.checks.isEmpty { model.runChecks() } }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("vz-kext-loader").font(.title2).bold()
                Text("Host Requirements").font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer()
            Button(action: { model.runChecks() }) {
                Label("Re-check", systemImage: "arrow.clockwise")
            }
            .disabled(model.running)
        }
        .padding()
    }

    private func errorBanner(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.white)
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.85))
    }

    private var checkList: some View {
        List(model.checks) { c in
            HStack(alignment: .top, spacing: 10) {
                StatusDot(status: c.status)
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(c.label).fontWeight(.medium)
                        Spacer()
                        Text(c.detail).foregroundStyle(.secondary).font(.callout)
                            .multilineTextAlignment(.trailing)
                    }
                    if !c.fix.isEmpty && (c.status == "fail" || c.status == "warn") {
                        Text(c.fix).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.vertical, 2)
        }
        .listStyle(.inset)
        .overlay {
            if model.running && model.checks.isEmpty {
                ProgressView("Checking host…")
            }
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Image(systemName: model.ready ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(model.ready ? .green : .orange)
            Text(model.summary).font(.callout)
            Spacer()
            if model.running { ProgressView().controlSize(.small) }
        }
        .padding()
    }
}

struct StatusDot: View {
    let status: String
    var body: some View {
        Image(systemName: symbol)
            .foregroundStyle(color)
            .font(.system(size: 14, weight: .bold))
            .frame(width: 18)
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
