"""Copied by the maintainer's Windows diagnostic harness, never shipped to apps."""

import asyncio
import json
import os
import struct
import sys
from pathlib import Path

from pyyaki import App

root = Path(__file__).parent
scenario = json.loads((root / "probe.json").read_text(encoding="utf-8"))["scenario"]

if scenario == "import-timeout":
    import time

    time.sleep(15)
if scenario.startswith("worker-frame-"):
    # Fault only this worker's inherited pipe before it completes app import.
    import ctypes
    from ctypes import wintypes

    handle = int(sys.argv[sys.argv.index("--write-handle") + 1])
    payload = {
        "worker-frame-zero": struct.pack("<I", 0),
        "worker-frame-large": struct.pack("<I", 1048577),
        "worker-frame-json": struct.pack("<I", 1) + b"{",
    }[scenario]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel.WriteFile.restype = wintypes.BOOL
    written = wintypes.DWORD()
    if not kernel.WriteFile(handle, payload, len(payload), ctypes.byref(written), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if written.value != len(payload):
        raise RuntimeError("Incomplete injected frame")
    print(f"Injected {scenario}", flush=True)
    # Keep the pipe open: host rejection must precede EOF or worker shutdown.
    import time

    time.sleep(30)

app = App()


@app.command("probe.echo")
async def echo(value: str) -> str:
    return value


@app.command("probe.crash")
async def crash() -> None:
    os._exit(17)


@app.command("probe.pipe-close")
async def pipe_close() -> None:
    # Close only our own worker's write fd; keep the process alive for recovery.
    import msvcrt

    handle = int(sys.argv[sys.argv.index("--write-handle") + 1])
    fd = next(fd for fd in range(3, 64) if _matches_handle(msvcrt, fd, handle))
    os.close(fd)
    await asyncio.sleep(30)


def _matches_handle(msvcrt, fd, handle):
    try:
        return msvcrt.get_osfhandle(fd) == handle
    except OSError:
        return False
