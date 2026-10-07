"""Patch point #3: iBoot-family Stage 2 (img4-wrapped).

Two regimes, told apart by the im4p description:

* macOS 12-26 ("iBoot-…"): Stage 2 shares AVPBooter's `validate_boot_object`;
  nop every `bl` to it so the stage accepts the (modified) next object. Same
  unique-signature caller-nulling as avpbooter, on the decompressed payload.

* macOS 27+ ("mBoot-…"): do NOT touch `validate_boot_object`. Patching it out
  there breaks the Stage 2 module and forces a "re-sign" on reboot that erases
  every guest patch (iBoot Stage 2 + AppleVPBootPolicy.kext) — the rollback we
  hit on Golden Gate. The Apple bug that the `validate_boot_object` nop worked
  around is gone on 27, so Stage 2 is patched exactly like Stage 1 (LLB):
  `image4_validate_property_callback` (the DGST validator) return -> mov x0,#0.
  (Ref: Steven Michaud's gist, comment 6410984, 2026-10-06.)

Either way the IMG4 is rewrapped preserving the original payload compression
(uncompressed on 12-26 iBoot, LZFSE on 27 mBoot) and reusing the manifest.
"""
from __future__ import annotations

from .. import avpbooter
from . import img4 as img4mod
from . import llb


def _is_mboot(description: str | None) -> bool:
    """macOS 27+ renamed Stage 2 from iBoot to mBoot; that's our regime marker."""
    return (description or "").lower().startswith("mboot")


def patch(img4_bytes: bytes, expected_callers: int | None = None) -> tuple[bytes, dict]:
    """Return (new_img4, info). Idempotent: an already-patched Stage 2 is
    returned unchanged with info['already_patched']=True."""
    parsed = img4mod.parse(img4_bytes)

    # macOS 27+ (mBoot): LLB-style image4_validate_property_callback patch.
    if _is_mboot(parsed.description):
        new_payload, site = llb.patch(parsed.payload)
        if site.get("already_patched"):
            return img4_bytes, {"already_patched": True,
                                "method": "image4_validate_property_callback",
                                "description": parsed.description}
        new_img4 = img4mod.rewrap(parsed, new_payload, compress=None)
        return new_img4, {
            "already_patched": False,
            "method": "image4_validate_property_callback",
            "site": hex(site["site"]),
            "fourcc": parsed.fourcc,
            "description": parsed.description,
        }

    # macOS 12-26 (iBoot): validate_boot_object caller-nulling.
    new_payload, callers, state = avpbooter.ensure_patched(parsed.payload)
    if state == "already-patched":
        return img4_bytes, {"already_patched": True,
                            "method": "validate_boot_object", "callers": []}
    if expected_callers is not None and len(callers) != expected_callers:
        raise avpbooter.PatchError(
            f"iBoot: expected {expected_callers} callers, found {len(callers)}")
    new_img4 = img4mod.rewrap(parsed, new_payload, compress=None)
    return new_img4, {
        "already_patched": False,
        "method": "validate_boot_object",
        "callers": callers,
        "fourcc": parsed.fourcc,
        "description": parsed.description,
    }
