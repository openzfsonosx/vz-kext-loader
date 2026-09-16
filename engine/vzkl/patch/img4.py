"""img4 helpers built on pyimg4.

Reliability notes learned in the field:
  * pyimg4's IM4PData accessors are unreliable; use the CLI (`im4p extract`) to
    read the decompressed payload.
  * The im4p carries PAYP **properties** (e.g. the kernelcache's kcep/kclo/… and
    iBoot's iocv) that the bootloader needs; they MUST be preserved on rewrap or
    the object loads but the OS won't boot ("needs reinstall"). We keep the
    original im4p and copy its properties onto the new one.
  * Reuse the ORIGINAL manifest (im4m) when rewrapping — no re-signing is needed
    because the parent stage's patched validator ignores the stale digest.
"""
from __future__ import annotations
import os
import subprocess
import tempfile

import pyimg4


def _cli(*args) -> None:
    subprocess.run(["python3", "-m", "pyimg4", *args],
                   check=True, capture_output=True)


class Img4:
    """A parsed IMG4: fourcc/desc/payload/manifest plus the original im4p bytes
    (kept so rewrap can copy the PAYP properties)."""

    def __init__(self, fourcc: str, description: str, payload: bytes,
                 im4m: bytes, im4p_bytes: bytes, compression):
        self.fourcc = fourcc
        self.description = description
        self.payload = payload
        self.im4m = im4m
        self.im4p_bytes = im4p_bytes
        self.compression = compression


def parse(img4_bytes: bytes) -> Img4:
    """Split an IMG4 into fourcc/desc/payload/manifest, keeping the im4p bytes.

    Accepts either a full IMG4 (IM4P+IM4M) — the Preboot/Recovery boot objects —
    or a bare IM4P with no manifest — the restore-bundle copies
    (iBoot.vma2.RELEASE.im4p, kernelcache.release.vma2). For a bare IM4P, im4m
    is None and rewrap() re-emits a bare IM4P.
    """
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "x.in")
        im4p = os.path.join(td, "x.im4p")
        im4m = os.path.join(td, "x.im4m")
        payload = os.path.join(td, "x.bin")
        with open(src, "wb") as f:
            f.write(img4_bytes)
        try:
            _cli("img4", "extract", "-i", src, "-p", im4p, "-m", im4m)
            im4m_bytes = open(im4m, "rb").read()
        except subprocess.CalledProcessError:
            im4p, im4m_bytes = src, None          # bare IM4P: input is the im4p
        _cli("im4p", "extract", "-i", im4p, "-o", payload)
        im4p_bytes = open(im4p, "rb").read()
        im4p_obj = pyimg4.IM4P(im4p_bytes)
        comp = getattr(im4p_obj.payload, "compression", None)
        return Img4(
            fourcc=im4p_obj.fourcc,
            description=im4p_obj.description or "",
            payload=open(payload, "rb").read(),
            im4m=im4m_bytes,
            im4p_bytes=im4p_bytes,
            compression=comp,
        )


def rewrap(orig: Img4, new_payload: bytes, compress) -> bytes:
    """Rebuild an IMG4 from a patched payload, preserving the original im4p's
    fourcc/description AND its PAYP properties, and reusing the manifest.

    compress=False -> uncompressed im4p.
    compress=True  -> LZFSE (kernelcache).
    compress=None  -> preserve the original payload's compression. macOS 27
      (Golden Gate) renamed Stage-2 iBoot to "mBoot" and ships it LZFSE-compressed
      where every earlier iBoot was uncompressed; re-emitting it uncompressed is an
      unfaithful object, so iBoot/LLB now preserve whatever the original used.
    """
    orig_im4p = pyimg4.IM4P(orig.im4p_bytes)

    if compress is None:
        compress = orig.compression == pyimg4.Compression.LZFSE

    data = pyimg4.IM4PData(new_payload)
    if compress:
        data.compress(pyimg4.Compression.LZFSE)

    new_im4p = pyimg4.IM4P(fourcc=orig.fourcc,
                           description=orig.description,
                           payload=data)
    # Preserve the boot properties (kernelcache kcep/kclo/…, iBoot iocv, …).
    for prop in (orig_im4p.properties or ()):
        new_im4p.add_property(prop)

    im4p_out = new_im4p.output()
    if orig.im4m is None:
        return im4p_out                     # bare IM4P (restore-bundle copies)
    img4_obj = pyimg4.IMG4(im4p=pyimg4.IM4P(im4p_out),
                           im4m=pyimg4.IM4M(orig.im4m))
    return img4_obj.output()
