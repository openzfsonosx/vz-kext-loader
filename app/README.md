# vz-kext-loader app

Native macOS (SwiftUI) front-end for the patch/boot workflow. The UI is a thin
shell; all real work lives in the Python engine at `../engine` (invoked as
`python3 -m vzkl <command> --json`).

## Build & run

```sh
cd app
./build-app.sh          # release build -> ./VZKextLoader.app
open VZKextLoader.app
```

Or for console logs during development:

```sh
swift build
.build/debug/VZKextLoader
```

## Automation permission (UTM control) — important for development

The app controls UTM via AppleEvents (start/stop/status). macOS gates that behind
the **Automation** privacy permission. On current macOS the consent prompt is
only presented for a **notarized** app — a Developer-ID-signed, hardened-runtime
app with the `com.apple.security.automation.apple-events` entitlement (which this
build has) still gets denied *without* a prompt.

Until the app is notarized, run it from a terminal that already holds the UTM
Automation grant, so the process inherits it:

```sh
cd app && swift run          # inherits the terminal's UTM automation grant
```

`open VZKextLoader.app` will work once the app is **notarized** (future).
Mount/unmount use a separate mechanism (authorization) and already work from the
bundle.

## Engine location

The app runs the engine from `~/src/vz-kext-loader/engine` by default. Override
with an environment variable if the repo lives elsewhere:

```sh
VZKL_ENGINE_DIR=/path/to/vz-kext-loader/engine open VZKextLoader.app
```

## Status

- **Host Requirements**: runs `vzkl check`, renders each requirement as a
  green/amber/red row with a remediation hint. Implemented.
- **VM sidebar**: runs `vzkl list-vms`, lists UTM VMs with run status and a
  patchable/not-patchable classification; detail pane shows the selected VM's
  backend/OS/arch/bundle. Implemented. External VMs (e.g. on `/Volumes/NVMe`)
  are found by setting `VZKL_VM_SEARCH_PATHS=/Volumes/NVMe/VMs` (colon-separated).
- **Boot (with overlay)**: the app checks the overlay, and if absent builds a
  patched-AVPBooter overlay (`vzkl overlay-build`, non-privileged) and mounts it
  over the framework Resources via a macOS admin prompt (option 1), then starts
  the VM (`utmctl`) and watches status. A **Stop** button and an **Unmount**
  link (also admin-prompted) are provided, with a live Activity log. Implemented.
- **Patch / Unpatch**: runs `vzkl patch-vm [--unpatch]` as root via one admin
  prompt (PYTHONPATH set so pyimg4/capstone import under root). Patch applies the
  guest chain (LLB in AuxiliaryStorage, main-OS Preboot iBoot+kernelcache, and
  Recovery iBoot+kernelcache) to the *stopped* VM, keeping backups + a manifest;
  Unpatch restores the originals. Implemented; full live run is the fresh-clone
  replication test.
- Verify (kext loaded): upcoming slice.

## Layout

- `Sources/VZKextLoader/Bootstrap.swift` — NSApplication + window bootstrap.
- `Sources/VZKextLoader/Engine.swift` — runs the Python engine, decodes JSON.
- `Sources/VZKextLoader/AppModel.swift` — observable state.
- `Sources/VZKextLoader/ContentView.swift` — the UI.
