"""Check a packaged archive for local paths or accidental private files.

Only pass/fail categories are printed; no local paths, values, or matched data.
This checks the archive, not unrelated files in the user's data directories.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import sys

from PyInstaller.archive.readers import CArchiveReader


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {
    "settings.json", "settings.json.tmp", "google-oauth-client.json",
    "google-calendar-token.dat", "calendar-receipts.json", "models_cache.json",
}
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*[A-Za-z0-9+/]{48}")
TOKEN_VALUE = re.compile(rb'''["']refresh_token["']\s*:\s*["'][A-Za-z0-9._~+/-]{20,}["']''', re.I)


def _private_name(name: str) -> bool:
    leaf = PurePosixPath(name.replace("\\", "/")).name.lower()
    return leaf in PRIVATE_NAMES or leaf.startswith("client_secret") or (
        leaf.startswith("token") and leaf.endswith(".dat")
    )


def main() -> int:
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist" / "OutlookMaximizer.exe"
    archive = CArchiveReader(str(executable))
    patterns = set()
    for value in (os.environ.get("USERNAME", ""), os.environ.get("USERPROFILE", ""), str(ROOT)):
        if len(value) < 4:
            continue
        for variant in (value, value.replace("\\", "/")):
            for encoding in ("utf-8", "utf-16le"):
                patterns.add(variant.encode(encoding).lower())

    findings = {"local_identity_or_path": False, "private_filename": False,
                "credential_material": False}

    def check(data: bytes | None) -> None:
        if not data:
            return
        folded = data.lower()
        findings["local_identity_or_path"] |= any(pattern in folded for pattern in patterns)
        findings["credential_material"] |= bool(PRIVATE_KEY.search(data) or TOKEN_VALUE.search(data))

    check(executable.read_bytes())
    pyz_names = []
    for name, entry in archive.toc.items():
        findings["private_filename"] |= _private_name(name)
        if entry[4] == "z":
            pyz_names.append(name)
        else:
            check(archive.extract(name))
    if not pyz_names:
        raise RuntimeError("Packaged Python archive was not found; privacy check incomplete")
    for name in pyz_names:
        pyz = archive.open_embedded_archive(name)
        for module in pyz.toc:
            findings["private_filename"] |= _private_name(module)
            check(pyz.extract(module, raw=True))

    for category, found in findings.items():
        print(f"{category}: {'MATCH' if found else 'clear'}")
    return 1 if any(findings.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
