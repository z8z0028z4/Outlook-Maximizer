# Build with: python scripts/build_windows.py
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)

analysis = Analysis(
    [str(root / "scripts" / "windows_entry.py")],
    pathex=[str(root), str(root / "scripts")],
    binaries=[],
    datas=[
        (str(root / "outlook_maximizer" / "analysis.schema.json"), "outlook_maximizer"),
        (str(root / "outlook_maximizer" / "reply.schema.json"), "outlook_maximizer"),
        *collect_data_files("tzdata"),
    ],
    hiddenimports=[
        "pythoncom", "pywintypes", "win32gui", "win32process",
        "win32com.client.dynamic", "win32com.server.util", *collect_submodules("tzdata"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

archive = PYZ(analysis.pure)
exe = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="OutlookMaximizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
