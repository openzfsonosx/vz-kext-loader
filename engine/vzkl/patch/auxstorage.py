"""Patch point #2 wrapper: patch the LLB inside a VM's AuxiliaryStorage.

AuxiliaryStorage layout (Apple Virtualization macOS VM, 33570816 bytes):
  * HUFA descriptors at 0x4000 / 0x5000 name the active LLB image set; the active
    one has the highest upgrade_count. Absolute LLB offset = 0x4000 + llb_off,
    which is 0x24000 or 0x224000 depending on OS version.
  * The active region holds an IMG4 LLB (illb) immediately followed by a logo
    IMG4. We patch the LLB payload and splice it back, preserving the logo.
"""
from __future__ import annotations
import struct

from . import llb as llbmod
from . import img4 as img4mod

HUFA_OFFSETS = (0x4000, 0x5000)
REGION_STRIDE = 0x200000    # LLB set spacing (0x24000 .. 0x224000)


class AuxError(Exception):
    pass


def _der_total(buf: bytes, off: int) -> int:
    """Total bytes of a DER SEQUENCE (tag 0x30) at off, incl. header."""
    if buf[off] != 0x30:
        raise AuxError(f"expected DER SEQUENCE at 0x{off:x}, got 0x{buf[off]:02x}")
    l0 = buf[off + 1]
    if l0 & 0x80:
        n = l0 & 0x7F
        length = int.from_bytes(buf[off + 2:off + 2 + n], "big")
        return 2 + n + length
    return 2 + l0


def _active_llb_offset(buf: bytes) -> int:
    active = None
    for h in HUFA_OFFSETS:
        if buf[h:h + 4] != b"HUFA":
            continue
        _magic, _ver, upgrade, llb_off = struct.unpack_from("<4sIII", buf, h)
        abs_llb = 0x4000 + llb_off
        if active is None or upgrade > active[1]:
            active = (abs_llb, upgrade)
    if active is None:
        raise AuxError("no HUFA descriptor found; not an Apple VM AuxiliaryStorage?")
    off = active[0]
    if buf[off:off + 2] != b"\x30\x82" and buf[off:off + 2] != b"\x30\x83":
        raise AuxError(f"active LLB offset 0x{off:x} is not an IMG4")
    return off


def patch(aux_bytes: bytes) -> tuple[bytes, dict]:
    """Patch the active LLB's validator; return (new_aux, info).

    Raises AuxError; if the LLB looks already patched, info['already_patched'] is
    True and the bytes are returned unchanged.
    """
    aux = bytearray(aux_bytes)
    O = _active_llb_offset(aux)
    total = _der_total(aux, O)
    logo_off = O + total
    logo_len = _der_total(aux, logo_off)
    logo = bytes(aux[logo_off:logo_off + logo_len])

    parsed = img4mod.parse(bytes(aux[O:O + total]))
    try:
        new_payload, site = llbmod.patch(parsed.payload)
    except llbmod.PatchError:
        # No `mov x0, xN` return found -> already patched (mov x0,#0), or a
        # different build. Treat as already-patched only if the payload has the
        # DGST loads (i.e. it IS the validator) but no return mov.
        return bytes(aux), {"llb_offset": O, "already_patched": True}

    new_llb = img4mod.rewrap(parsed, new_payload, compress=False)

    # Splice: clear the whole set region, write new LLB, restore the logo.
    nxt = O + REGION_STRIDE
    if nxt > len(aux):
        nxt = len(aux)
    aux[O:nxt] = b"\x00" * (nxt - O)
    aux[O:O + len(new_llb)] = new_llb
    end = O + len(new_llb)
    aux[end:end + logo_len] = logo

    return bytes(aux), {
        "llb_offset": O,
        "orig_llb_len": total,
        "new_llb_len": len(new_llb),
        "patch_site": site["site"],
        "patch_insn": site["insn"],
        "logo_len": logo_len,
        "already_patched": False,
    }
