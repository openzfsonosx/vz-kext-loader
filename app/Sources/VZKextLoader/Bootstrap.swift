import AppKit
import SwiftUI

// SPM executable bootstrap: create a real, activating window that hosts the
// SwiftUI view. Using NSApplication directly (rather than the SwiftUI `App`
// lifecycle) keeps a CLI-launched binary showing a normal foreground window
// without a full .app bundle; a bundle can wrap this later.

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let model = AppModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        let content = ContentView().environmentObject(model)
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 680),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "vz-kext-loader"
        window.center()
        window.contentView = NSHostingView(rootView: content)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // MARK: Menu bar (App + Edit), built programmatically (no xib).

    private func setupMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About vz-kext-loader",
                        action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit vz-kext-loader",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editItem.submenu = editMenu
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        NSApp.mainMenu = main
    }

    @objc private func showAbout() {
        let version = (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0.1.0"
        let credits = NSMutableAttributedString()
        func line(_ s: String, bold: Bool = false, size: CGFloat = 11) {
            let font = bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size)
            let para = NSMutableParagraphStyle(); para.alignment = .center
            credits.append(NSAttributedString(string: s + "\n",
                attributes: [.font: font, .paragraphStyle: para,
                             .foregroundColor: NSColor.labelColor]))
        }
        line("Load third-party kexts in UTM / Virtualization.framework")
        line("macOS guests, for per-version testing.")
        line(" ")
        line("Method by Steven Michaud and the utmapp/UTM #4026", bold: true)
        line("discussion (with thanks to dariaphoebe).")
        line(" ")
        line("Built by Joergen Lundman with Claude (Anthropic).")
        line(" ")
        line("github.com/openzfsonosx/vz-kext-loader", size: 10)

        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "vz-kext-loader",
            .applicationVersion: version,
            .credits: credits,
        ])
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
enum Bootstrap {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
