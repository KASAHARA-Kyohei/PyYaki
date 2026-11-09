"""Fixed native-host entry point; configure logs before importing PyYaki."""

import sys


def main():
    # Only the owned worker's redirected text streams change. Keep locale/file I/O
    # defaults and -I isolation; native/binary writes remain the caller's bytes.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

    import sysconfig

    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 14)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        raise RuntimeError("PyYaki requires CPython 3.14 with the normal GIL")

    import runpy

    runpy.run_module("pyyaki.worker", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
