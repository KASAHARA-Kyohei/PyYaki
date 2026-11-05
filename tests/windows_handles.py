"""Read-only sentinel identity checks for owned Windows diagnostic processes."""

import ctypes
from ctypes import wintypes as w


class FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("creation", w.FILETIME),
        ("access", w.FILETIME),
        ("write", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


def file_identity(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.GetFileInformationByHandle.restype = w.BOOL
    info = FileInfo()
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        return {"error": ctypes.get_last_error()}
    return {"volume": info.volume, "index": (info.index_high << 32) | info.index_low}


def identity_in_child(child, handle):
    """Duplicate from our own Popen child without closing or changing its handle."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.DuplicateHandle.argtypes = [
        w.HANDLE,
        w.HANDLE,
        w.HANDLE,
        ctypes.POINTER(w.HANDLE),
        w.DWORD,
        w.BOOL,
        w.DWORD,
    ]
    kernel.DuplicateHandle.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    duplicate = w.HANDLE()
    if not kernel.DuplicateHandle(
        int(child._handle),
        handle,
        kernel.GetCurrentProcess(),
        ctypes.byref(duplicate),
        0,
        False,
        2,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return file_identity(duplicate)
    finally:
        kernel.CloseHandle(duplicate)
