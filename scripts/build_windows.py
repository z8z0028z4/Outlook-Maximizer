"""Build a single-file Windows executable without touching existing build trees."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Build this Windows executable on Windows.")
    for package in ("PyInstaller", "PyQt6", "pywin32", "tzdata"):
        try:
            print(f"{package} {importlib.metadata.version(package)}", flush=True)
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit(f"Build dependency missing: {package}") from None
    destination = ROOT / "dist" / "OutlookMaximizer.exe"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="outlook-maximizer-build-") as temp:
        temp_root = Path(temp)
        env = os.environ.copy()
        env["PYINSTALLER_CONFIG_DIR"] = str(temp_root / "config")
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        # Unrelated tools on the developer's PATH may contain DLLs with the
        # same name but incompatible exports (notably Windows' icuuc.dll).
        # Keep dependency detection on Windows and the build interpreter.
        windows = Path(env.get("SystemRoot", r"C:\Windows"))
        python_dir = Path(sys.executable).resolve().parent
        env["PATH"] = os.pathsep.join(str(path) for path in (
            windows / "System32", windows, windows / "System32" / "Wbem",
            windows / "System32" / "WindowsPowerShell" / "v1.0",
            python_dir, python_dir / "Scripts",
        ))
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                   "--distpath", str(temp_root / "dist"), "--workpath", str(temp_root / "work"),
                   str(ROOT / "build_windows.spec")]
        subprocess.run(command, cwd=ROOT, env=env, check=True)
        built = temp_root / "dist" / "OutlookMaximizer.exe"
        if not built.is_file():
            raise RuntimeError("PyInstaller did not create the expected executable")
        staging = destination.parent / f".outlook-maximizer-{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(built, staging)
            os.replace(staging, destination)
        finally:
            staging.unlink(missing_ok=True)
    print(f"Built: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
