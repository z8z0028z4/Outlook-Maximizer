"""Exercise a built app with isolated settings and harmless native fake CLIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FAKE_CLI = r'''
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class FakeCli {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetDllDirectory(uint size, StringBuilder path);
    public static int Main(string[] args) {
        var path = new StringBuilder(32768);
        GetDllDirectory((uint)path.Capacity, path);
        var bundle = Environment.GetEnvironmentVariable("OUTLOOK_TEST_BUNDLE") ?? "";
        bool leaked = false;
        foreach (var name in new[] { "PATH", "PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH" }) {
            var value = Environment.GetEnvironmentVariable(name) ?? "";
            if (bundle.Length > 0 && value.IndexOf(bundle, StringComparison.OrdinalIgnoreCase) >= 0)
                leaked = true;
        }
        Console.WriteLine("DLL_DIRECTORY=" + path.ToString());
        Console.WriteLine("BUNDLE_PATH_LEAK=" + leaked.ToString());
        Console.WriteLine("PYI_HOME_PRESENT=" + (Environment.GetEnvironmentVariable("_PYI_APPLICATION_HOME_DIR") != null).ToString());
        Console.WriteLine("INPUT=" + (Console.ReadLine() ?? ""));
        return 0;
    }
}
'''


def _compiler(system_root: Path) -> Path:
    framework = system_root / "Microsoft.NET"
    for variant in ("Framework64", "Framework"):
        candidate = framework / variant / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    raise RuntimeError("Windows .NET Framework C# compiler is needed for the fake CLI test")


def _fake_clis(test_root: Path, system_root: Path) -> Path:
    fake_bin = test_root / "fake-cli"
    fake_bin.mkdir()
    source = test_root / "fake-cli.cs"
    source.write_text(FAKE_CLI, encoding="utf-8")
    first = fake_bin / "codex.exe"
    subprocess.run([str(_compiler(system_root)), "/nologo", "/target:exe", f"/out:{first}", str(source)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    shutil.copy2(first, fake_bin / "agy.exe")
    shutil.copy2(first, fake_bin / "claude.exe")
    return fake_bin


def _environment(test_root: Path, system_root: Path, fake_bin: Path) -> dict[str, str]:
    profile = test_root / "profile"
    local = profile / "local"
    roaming = profile / "roaming"
    temp = test_root / "temp"
    for directory in (profile, local, roaming, temp):
        directory.mkdir(parents=True, exist_ok=True)
    drive, tail = os.path.splitdrive(str(profile))
    system32 = system_root / "System32"
    return {
        "SystemRoot": str(system_root), "WINDIR": str(system_root),
        "COMSPEC": str(system32 / "cmd.exe"),
        "PATH": os.pathsep.join((str(fake_bin), str(system32))),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD", "USERPROFILE": str(profile),
        "HOMEDRIVE": drive, "HOMEPATH": tail, "HOME": str(profile),
        "LOCALAPPDATA": str(local), "APPDATA": str(roaming),
        "CODEX_HOME": str(profile / "codex"), "CODEX_INSTALL_DIR": str(profile / "codex-install"),
        "TEMP": str(temp), "TMP": str(temp), "QT_QPA_PLATFORM": "windows",
    }


def main() -> int:
    if os.name != "nt":
        raise SystemExit("Run packaged verification on Windows.")
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist" / "OutlookMaximizer.exe"
    if not executable.is_file():
        raise SystemExit(f"Executable not found: {executable}")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    # Use a unique directory under the writable workspace. Some Windows
    # sandboxes permit ordinary writes in the system temp directory but deny
    # the atomic rename used to persist the isolated onboarding settings.
    verification_dir = ROOT / ".local" / "packaged-verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=verification_dir) as temp:
        test_root = Path(temp)
        fake_bin = _fake_clis(test_root, system_root)
        env = _environment(test_root, system_root, fake_bin)
        isolated_executable = test_root / "OutlookMaximizer.exe"
        shutil.copy2(executable, isolated_executable)
        result_path = test_root / "packaged-self-test.json"
        result = subprocess.run([str(isolated_executable), "--packaged-self-test", str(test_root)],
                                cwd=test_root, env=env, stdin=subprocess.DEVNULL, timeout=90)
        if not result_path.is_file():
            raise RuntimeError(f"Packaged self-test did not produce a result (exit {result.returncode})")
        report = json.loads(result_path.read_text(encoding="utf-8"))
        print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
        if result.returncode or report.get("status") != "ok":
            raise RuntimeError(f"Packaged self-test failed (exit {result.returncode})")
        smoke = subprocess.run([str(isolated_executable), "--smoke-test", "--data-dir", str(test_root / "data")],
                               cwd=test_root, env=env, stdin=subprocess.DEVNULL, timeout=90)
        if smoke.returncode:
            raise RuntimeError(f"Packaged demo smoke test failed (exit {smoke.returncode})")
        print("Packaged demo smoke test: ok", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
