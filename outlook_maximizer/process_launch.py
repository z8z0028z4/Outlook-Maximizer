"""Launch native programs without passing a frozen app's private DLL paths."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from typing import Mapping


_launch_lock = threading.Lock()
_PATH_VARIABLES = {
    "PATH", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH", "QML_IMPORT_PATH",
}


def _outside_bundle(path: str, bundle: str) -> bool:
    """Keep an environment path unless it points into this extracted bundle."""
    candidate = path.strip().strip('"')
    if not candidate or not os.path.isabs(candidate):
        return True
    try:
        root = os.path.normcase(os.path.abspath(bundle))
        target = os.path.normcase(os.path.abspath(candidate))
        return os.path.commonpath((root, target)) != root
    except (OSError, ValueError):
        return True


def _external_environment(env: Mapping[str, str], bundle: str) -> dict[str, str]:
    clean = {}
    for name, value in env.items():
        upper = name.upper()
        # These private bootloader variables describe this executable, not a
        # separately launched executable that may itself use PyInstaller.
        if upper.startswith("_PYI_"):
            continue
        if upper in _PATH_VARIABLES:
            parts = [part for part in value.split(os.pathsep) if _outside_bundle(part, bundle)]
            if not parts:
                continue
            value = os.pathsep.join(parts)
        clean[name] = value
    return clean


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = (ctypes.c_uint32, ctypes.c_wchar_p)
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = (ctypes.c_wchar_p,)
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    return kernel


def _get_dll_directory(kernel) -> str | None:
    ctypes.set_last_error(0)
    size = kernel.GetDllDirectoryW(0, None)
    if not size:
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
        return None
    buffer = ctypes.create_unicode_buffer(size + 1)
    ctypes.set_last_error(0)
    copied = kernel.GetDllDirectoryW(len(buffer), buffer)
    if not copied and not ctypes.get_last_error():
        return None
    if not copied or copied >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _set_dll_directory(kernel, path: str | None) -> None:
    if not kernel.SetDllDirectoryW(path):
        raise ctypes.WinError(ctypes.get_last_error())


def popen_external(*popenargs, **kwargs) -> subprocess.Popen:
    """Start an external process and immediately restore the parent DLL search.

    Windows' DLL search setting is process-wide. The lock serializes our own
    process launches; unrelated native DLL loads in other threads cannot be
    synchronized here, so the setting is held only across process creation.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if os.name != "nt" or not getattr(sys, "frozen", False) or not bundle:
        return subprocess.Popen(*popenargs, **kwargs)

    supplied = kwargs.get("env")
    kwargs["env"] = _external_environment(os.environ if supplied is None else supplied, str(bundle))
    with _launch_lock:
        kernel = _kernel32()
        previous = _get_dll_directory(kernel)
        _set_dll_directory(kernel, None)
        process = None
        try:
            process = subprocess.Popen(*popenargs, **kwargs)
        finally:
            try:
                _set_dll_directory(kernel, previous)
            except BaseException:
                # Do not lose an untracked child if restoring the parent fails.
                if process is not None:
                    try:
                        process.terminate()
                        process.wait(timeout=3)
                    except (OSError, subprocess.SubprocessError):
                        pass
                raise
        return process


def run_external(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """Equivalent to subprocess.run, without holding the DLL lock while waiting."""
    bundle = getattr(sys, "_MEIPASS", None)
    if os.name != "nt" or not getattr(sys, "frozen", False) or not bundle:
        return subprocess.run(*popenargs, input=input, capture_output=capture_output,
                              timeout=timeout, check=check, **kwargs)
    if input is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    with popen_external(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            exc.stdout, exc.stderr = process.communicate()
            raise
        except BaseException:
            process.kill()
            raise
        code = process.poll()
        if check and code:
            raise subprocess.CalledProcessError(code, process.args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(process.args, code, stdout, stderr)
