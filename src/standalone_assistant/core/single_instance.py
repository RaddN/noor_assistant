from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass


ERROR_ALREADY_EXISTS = 183


@dataclass
class SingleInstanceLock:
    handle: int | None = None

    def release(self) -> None:
        if os.name != "nt" or not self.handle:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.ReleaseMutex(self.handle)
        kernel32.CloseHandle(self.handle)
        self.handle = None


def acquire_single_instance(name: str) -> SingleInstanceLock | None:
    if os.name != "nt":
        return SingleInstanceLock()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.CreateMutexW(None, True, f"Local\\{name}")
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return SingleInstanceLock(int(handle))
