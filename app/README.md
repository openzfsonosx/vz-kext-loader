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
- Patch, Boot (with overlay), Verify: buttons present, wired in upcoming slices.

## Layout

- `Sources/VZKextLoader/Bootstrap.swift` — NSApplication + window bootstrap.
- `Sources/VZKextLoader/Engine.swift` — runs the Python engine, decodes JSON.
- `Sources/VZKextLoader/AppModel.swift` — observable state.
- `Sources/VZKextLoader/ContentView.swift` — the UI.
