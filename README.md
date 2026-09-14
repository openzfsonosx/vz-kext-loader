# vz-kext-loader

Loading third-party (Developer-ID-signed, or unsigned) kernel extensions inside
**Apple Virtualization.framework** macOS guests — as run by UTM's "Apple" backend —
on Apple Silicon, without buying hardware for every macOS release.

The motivating use case is smoke-testing the [OpenZFS on macOS](https://github.com/openzfsonosx/openzfs-fork)
`zfs.kext` on each new macOS version. The technique is general: it applies to any
kext you control.

> **Status:** Working end-to-end on **macOS 26 (Tahoe)**. A clone VM boots the
> patched chain, builds and blesses the auxiliary kernel collection (auxKC), and
> loads `org.openzfsonosx.zfs`. Verified with a full `zpool create` / write / read /
> `zpool destroy` smoke test, and confirmed to survive kext version upgrades
> (2.3.1 → 2.4.3-rc3) through the normal `.pkg` installer flow.

> **Safety:** This deliberately defeats Secure Boot digest checks in a *virtual
> machine you own, for testing*. Do it on throwaway clones. It does **not** touch,
> and does not require modifying, any host system file — the one host-side action
> is an ephemeral union mount that reverts on unmount/reboot.

This document is the canonical writeup of the findings. Credit to
[Steven Michaud's gist](https://gist.github.com/steven-michaud/fda019a4ae2df3a9295409053a53a65c)
and the discussion in [utmapp/UTM#4026](https://github.com/utmapp/UTM/issues/4026),
which established the method; this repo adds a from-scratch reproduction, the
precise anchoring strategy for each patch, and the **recovery-boot-chain** insight
that makes the auxKC bless succeed.

---

## Table of contents

- [Why Apple blocks this](#why-apple-blocks-this)
- [The Apple Silicon boot chain](#the-apple-silicon-boot-chain)
- [The five patch points](#the-five-patch-points)
- [How each location is found (robustness)](#how-each-location-is-found-robustness)
- [The kcgen build environment — the key insight](#the-kcgen-build-environment--the-key-insight)
- [Full working recipe (Tahoe)](#full-working-recipe-tahoe)
- [What persists vs. what is ephemeral](#what-persists-vs-what-is-ephemeral)
- [Tooling](#tooling)
- [Cross-version caveats](#cross-version-caveats)
- [Verification](#verification)
- [Open items / future work](#open-items--future-work)

---

## Why Apple blocks this

On Apple Silicon, third-party kexts load only from the **Auxiliary Kernel
Collection (auxKC)**, which is built and cryptographically blessed at install
time. In a VM, Apple deliberately fails the SEP path that authorizes the auxKC
build (historically reported as "command 33" / `_validate_acm_context`).
Feedback FB17890643 was closed "works as designed." So a stock VM simply cannot
build an auxKC, and the kext never loads.

The workaround is to defeat the boot-object digest checks at each stage of the
boot chain, and to defeat the ACM check inside the kernel that gates the auxKC
bless. With those defeated, the machinery runs to completion and produces a
valid-enough auxKC that the (now permissive) boot chain accepts.

Required guest security state (set in Recovery, **Permissive first** — see the
recipe): SIP disabled, Authenticated Root disabled, and Security Mode
**Permissive** ("allow booting unsigned OS"). Reduced Security is *not* enough —
it triggers a "verify startup disk" re-sign that reverts the patches.

---

## The Apple Silicon boot chain

```
AVPBooter (Stage 0)         host file, fed to the VM by Virtualization.framework
   └─ LLB (Stage 1)         in the guest AuxiliaryStorage (NVRAM blob)
        └─ iBoot (Stage 2)  in the guest Preboot volume, per-NSIH boot dir
             └─ kernelcache  the OS kernel + boot kexts
                  └─ auxKC   third-party kexts (what we want)
```

Two facts that matter:

1. **UTM's Apple backend** runs the VM in an XPC service
   (`com.apple.Virtualization.VirtualMachine`) that opens AVPBooter via a plain
   `open()` on
   `…/Virtualization.framework/Versions/A/Resources/AVPBooter.vmapple2.bin`.
   That path can be shadowed with an ephemeral union mount — no host file is
   modified.
2. There are **two** copies of iBoot + kernelcache: the main-OS set on the
   Preboot volume, **and a recovery set on the Recovery volume**. The auxKC is
   built in a special "kcgen" boot environment that boots the **recovery** set.
   Patching only the main-OS set is not enough (this was the missing piece).

---

## The five patch points

All patches follow the same principle: make each stage accept the next object
regardless of its digest, and make the kernel's ACM check pass.

| # | Object | Function | Patch |
|---|--------|----------|-------|
| 1 | AVPBooter (Stage 0, host) | `validate_boot_object` | rewrite each `bl` caller → `mov x0,#0` |
| 2 | LLB (Stage 1, AuxiliaryStorage) | `image4_validate_property_callback` (DGST validator) | rewrite the return `mov x0,<reg>` → `mov x0,#0` |
| 3 | iBoot (Stage 2, main-OS Preboot) | `validate_boot_object` | rewrite each `bl` caller → `mov x0,#0` |
| 4 | kernelcache (main-OS Preboot) | `_validate_acm_context` callers | `nop` the `bl` in `_command_create_linked_manifest` and `_command_update_local_policy_for_kcos` |
| 5 | **Recovery iBoot + kernelcache (Recovery volume)** | same as #3 and #4 | same patches |

Patch bytes (AArch64, little-endian):

- `mov x0,#0` = `00 00 80 d2`
- `nop` = `1f 20 03 d5`

On our build (macOS 26.x, `iBoot-13822.*`, `KernelManagement_host-487.0.4`) the
observed offsets were, **as build-specific examples only**:

- AVPBooter: `validate_boot_object` @ `0x27c48`, caller `bl` @ `0x16e8`.
- LLB (`iBoot-13822.40.85`): return `mov x0,x27` @ `0x676c` → `mov x0,#0`.
- iBoot (Stage 2): `validate_boot_object` callers `bl` @ `0x6438`, `0x14774`.
- kernelcache: `bl _validate_acm_context` @ `0x206963c` and `0x206a770` → `nop`.
- Recovery iBoot + kernelcache: byte-identical to the main-OS originals on this
  build, so the same offsets applied. (We simply copied the already-patched
  main-OS files into the recovery boot dir.)

**Do not hardcode these.** See the next section for how to *find* them.

---

## How each location is found (robustness)

Four of the five anchors are deterministic; one needs real disassembly.

### Robust — safe to automate with a refuse-on-mismatch guard

- **Active LLB set in AuxiliaryStorage.** Structural parse, not pattern-matching:
  two `HUFA` headers at `0x4000` / `0x5000`; pick the set with the higher
  `upgrade_count`; walk the img4 DER length to locate the logo image that
  follows. Verified across macOS 12–27. The active set alternates between
  `0x24000` and `0x224000` by version.
- **AVPBooter and iBoot `validate_boot_object`.** Anchored on the unique 8-byte
  prologue `e5 03 04 aa 04 00 80 52` (`mov x5,x4; mov w4,#0`), which occurs
  **exactly once** in each of AVPBooter and iBoot. Decode its `bl` callers by
  relative arithmetic and rewrite each to `mov x0,#0`. A tool must assert the
  caller count matches expectation (e.g. 2 for iBoot) and refuse otherwise.
- **Kernelcache ACM defeat.** Symbol-anchored, not offset-anchored: resolve
  `_validate_acm_context`, then `nop` the `bl` to it inside the two functions
  that call it in range (`_command_create_linked_manifest`,
  `_command_update_local_policy_for_kcos`). There are ~11 `bl` sites to that
  symbol; only these two are relevant. Deterministic while the symbol table is
  present.

### Needs fuzz — disassembly plus a human eyeball

- **The LLB digest gate.** This is the fragile one. The `validate_boot_object`
  signature *also* appears once in LLB, but patching there **breaks boot** — LLB
  enforces the digest in a different function, `image4_validate_property_callback`.
  Locate it by scanning for the `DGST` constant `0x44475354`
  (`mov w8,#0x5354; movk w8,#0x4447,lsl16`, which appears twice in the function),
  then patch the **return**: the `mov x0,<reg>` immediately before the shared
  `retab` epilogue, rewritten to `mov x0,#0`. The source register varies
  (`x27` for us, `x20` in Michaud's notes), so you cannot hardcode the
  instruction bytes — a tool must disassemble and pick the return `mov` by
  position, and it is worth a human check.

---

## The kcgen build environment — the key insight

When you install/approve a kext, `kmutil` stages it and sets an nvram variable:

```
one-time-boot-command = kcgen
```

On the next boot, iBoot honors that and boots a special **kcgen** environment (a
recovery-like ramdisk). There, `kcgend` builds, signs, and blesses the auxKC,
clears the command, and reboots to the normal OS. From the outside this looks
like: boot progresses to ~70%, pauses, the VM reboots, and progress restarts
from 0% — a **double boot**. nvram `IASInstallPhaseList` enumerates the phases:
`kcgend` → `validating extensions` → `building auxiliary kernel collection` →
`signing collection` → `rebooting`.

The trap: **the kcgen environment boots the Recovery volume's iBoot and
kernelcache**, not the main-OS Preboot copies. If only the main-OS set is
patched, then inside kcgen the kernel still has the original
`_validate_acm_context`; the bless's ACM check fails; no linked manifest is
written; and at the next normal boot `bootpolicy_get_linked_manifest` returns
`file I/O (4)` with `SEP command 61` → `tag not found (14)`, so
`kernelmanagerd` reports "No auxKC path found" and the kext never loads.

**Patching the recovery boot chain (patch point #5) is what makes the bless
succeed.** After that, kcgen writes the linked manifest and the `auxi` hash into
the local policy, and the normal boot loads the auxKC.

Note: the kcgen phase logs to memory and reboots before flushing to the
persistent store, so its log is effectively a black box without a serial console
(which is why Michaud built one into his private UTM patch). We did not need to
see inside once patch #5 was in place — but if you are debugging a *new* failure
there, a serial console is the tool.

---

## Full working recipe (Tahoe)

Prerequisites on the host: `pyimg4` (pip), `capstone` (pip), `radare2` (brew).
Guest access: SSH with passwordless `sudo` is convenient for automation.

0. **Clone** a stock Tahoe VM (throwaway). Boot to Recovery. In Startup Security
   Utility / `bputil`, set **Permissive** ("allow booting unsigned OS") **first**,
   then `csrutil disable` and `csrutil authenticated-root disable`. Order matters:
   Permissive-first prevents the "verify startup disk" re-sign that otherwise
   reverts the patches.

1. **Host — AVPBooter overlay.** Build a small HFS image containing the patched
   `AVPBooter.vmapple2.bin`; attach it; unmount the volume but keep the device
   node; then union-mount it over the framework Resources directory:

   ```sh
   sudo mount -t hfs -o union,nobrowse /dev/diskNs1 \
     "/System/Library/Frameworks/Virtualization.framework/Versions/A/Resources"
   ```

   Sealed SSV allows this because Authenticated Root is disabled. The VM opens the
   genuine path and gets our bytes. This reverts on unmount/reboot.

2. **LLB** — patch `image4_validate_property_callback`'s return in the active LLB
   inside the clone's `AuxiliaryStorage`, re-wrap the im4p (reusing the original
   manifest), and splice it back at the active LLB offset, preserving the logo
   image that follows.

3. **Main-OS Preboot** — in `boot/<NSIH>/`, patch `iBoot.img4`
   (`validate_boot_object` callers) and `kernelcache` (ACM `bl` nops, then
   LZFSE-recompress and re-wrap reusing the original manifest).

4. **Recovery volume** — mount it and apply the *same* iBoot + kernelcache
   patches to its `boot/<NSIH>/`. On Tahoe these files were byte-identical to the
   main-OS originals, so the simplest path is to copy the already-patched main-OS
   files in:

   ```sh
   sudo mount_apfs -o rdonly /dev/disk5s3 /tmp/rec
   sudo mount -u -o rw /tmp/rec       # Recovery is not snapshot-sealed
   # back up *.orig-rec, then copy patched main-OS iBoot.img4 + kernelcache in
   sudo umount /tmp/rec
   ```

5. **Guest — trigger the build.** Ensure the kext is in `/Library/Extensions`.
   Then either install via the normal `.pkg` (the password prompt is the
   approval), **or** headless:

   ```sh
   sudo kmutil load -z -p /Library/Extensions/zfs.kext   # -z skips the approval gate
   ```

   This stages the auxKC and sets `one-time-boot-command=kcgen`.

6. **Reboot.** The VM double-boots through kcgen, builds and blesses the auxKC,
   and comes back with the kext loaded.

Confirm:

```sh
kextstat | grep zfs                 # org.openzfsonosx.zfs (…)
sudo bputil -d | grep -i auxi       # Auxiliary Kernel Cache Image4 Hash: <set>
/usr/local/zfs/bin/zpool version    # kmod version matches userland
```

**Updating the kext later** needs nothing new: install the pkg, approve, reboot.
The double-boot rebuild happens automatically. Confirmed 2.3.1 → 2.4.3-rc3.

---

## What persists vs. what is ephemeral

- **Durable (survives guest reboots):** the LLB patch in AuxiliaryStorage, the
  main-OS Preboot iBoot + kernelcache, the Recovery-volume iBoot + kernelcache,
  and the security state (Permissive, SIP off, Authenticated Root off). Once
  `auxi` is blessed, normal boots load the auxKC with no kcgen cycle unless the
  kext changes.
- **Ephemeral:** the host-side AVPBooter union overlay. It reverts on host reboot
  or unmount. It must be mounted whenever the VM boots — including the kcgen
  double-boot. Re-mount it before starting the VM after any host reboot.

---

## Tooling

- `pyimg4` (pip, pure-python) — img4/im4p/im4m extract, create, LZFSE compress,
  add properties, re-wrap. Reliable via its CLI for extract; use the Python API
  for reading fourcc/desc/properties and for `IMG4(im4p, im4m).output()`.
  LZFSE round-trips the ~78 MB kernelcache byte-exact.
- `capstone` (pip) — AArch64 disassembly for the signature/constant scans; strip
  the leading `#` from `adrp` operands before parsing the immediate.
- `radare2` (brew) — symbol resolution and xrefs for the kernelcache ACM sites.
- `bputil -d` — read the local policy (security mode, `nsih`, `auxi`, `auxp`).
- `kmutil load -z` — stage the auxKC and arm `one-time-boot-command=kcgen`.
- Reused the **original** manifest (im4m) at each stage — no re-signing needed,
  because the parent stage's patched check ignores the stale digest.

---

## Cross-version caveats

- Every raw offset here is **per-build**. The *anchoring methods*
  (unique-signature scan, DGST-constant scan, symbol xref, structural parse) are
  what carry across builds; the numbers do not.
- **macOS 27 (Golden Gate):** the ACM functions were renamed into a `boop_*`
  namespace, so a symbol-name match must be updated; and the LLB reportedly lacks
  a plaintext `iBoot-NNNN` version string, so any string sanity check must fall
  back to reading the version out of the DER `IM4P`.
- The recovery iBoot/kernelcache were byte-identical to the main-OS originals on
  Tahoe; that may not hold on every release. A tool should re-derive the recovery
  offsets rather than assume, even though copying worked here.
- Determining the **active NSIH** (from the current local policy / `bputil -d`)
  and patching *that* boot dir is a required step — a clone can carry more than
  one.

---

## Verification

On macOS 26 (Tahoe), PLAY2 clone:

```
kextstat:            org.openzfsonosx.zfs (2.4.3) loaded
/dev/zfs:            present
bputil -d:           auxi set; SIP off; Permissive; 3rd-party kexts enabled
zpool version:       zfs-2.4.3-rc3 / zfs-kmod-2.4.3-rc3
smoke test:          zpool create (file vdev) → ONLINE → mount → write → read → destroy   ✓
boot log:            bootpolicy_get_linked_manifest: success; loaded auxiliary kext collection
```

---

## Open items / future work

- Package the host + guest steps into a one-shot tool so a fresh clone is
  turnkey. Everything except two classifier-sensitive actions (the framework
  union mount, and setting the `amfi_get_out_of_my_way=1` boot-arg if you ever
  drive `kcgend` manually) is host + SSH automatable.
- Confirm the offsets/anchors on macOS 27 (Golden Gate); update the ACM symbol
  match for the `boop_*` rename and the LLB DER version read.
- Optional: a serial console (custom Virtualization.framework launcher, or
  Michaud's UTM patch) for visibility into the kcgen phase when a new failure
  appears.

---

## Credits

- Steven Michaud — the original method and changelog
  (<https://gist.github.com/steven-michaud/fda019a4ae2df3a9295409053a53a65c>).
- dariaphoebe and others in
  [utmapp/UTM#4026](https://github.com/utmapp/UTM/issues/4026).
- This repo: from-scratch reproduction, per-anchor robustness analysis, and the
  recovery-boot-chain finding, developed against the OpenZFS on macOS `zfs.kext`.
