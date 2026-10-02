#!/usr/bin/env python3
"""
Tomoko Instinct V4.4 Block 3 - ONE TRUE MAIN ENGINE

This is the single orchestrating entry point. It replaces the old Tomoko.bat
process chain (which had no supervisor and could serve stale demo data if one
side crashed). Main.py launches and supervises exactly two child processes:

  1) core/mt5_bridge.py        -> EA Bridge   on port 18001
  2) backend.api:app / uvicorn -> Web Dashboard on port 8000

Behavior:
  * Pre-flight: refuses to start if any engine file is missing or undersized.
  * Streams both children's output to this console with [MT5_BRIDGE]/[BRAIN_API]
    prefixes so failures are visible.
  * Fail-fast health check: any child that dies right after launch => FATAL exit.
  * Supervisor loop: every 3 seconds, if either child has exited => FATAL exit
    (never keep serving stale data).
  * Ctrl+C terminates both children gracefully and exits 0.
  * Auto-opens http://localhost:8000 once both are alive.

Usage:
  python Main.py            # production (no --reload)
  python Main.py --dev      # development (uvicorn --reload)

Legacy mains were renamed (not deleted):
  Main.py    -> desktop_legacy.py   (old Tkinter desktop launcher)
  Tomoko.py  -> web_legacy.py       (old simple uvicorn launcher)
  Tomoko.bat -> START_HERE_OLD.bat  (old no-supervisor batch launcher)
"""

import os
import sys
import time
import ctypes
import threading
import subprocess
import webbrowser

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Pre-flight manifest: path -> minimum acceptable size in bytes.
# Smaller files mean a bad overwrite (e.g. rescued 0-byte file), so abort.
REQUIRED_FILES = {
    "core/mt5_bridge.py": 500,
    "backend/api.py": 1000,
    "ui/web_dashboard.html": 1000,
}

HOST = "127.0.0.1"
PORT = 8000
BRIDGE_PORT = 18001
SUPERVISOR_INTERVAL = 3  # seconds
HEALTH_CHECK_WAIT = 3    # seconds granted to a child to prove it is alive
PREFIX_MT5 = "MT5_BRIDGE"
PREFIX_BRAIN = "BRAIN_API"


# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #

def log(level, message):
    print(f"[{level}] {message}", flush=True)


def log_stream(prefix, stream):
    """Read lines from a child's stdout and reprint with a prefix."""
    try:
        for line in iter(stream.readline, ""):
            line = line.rstrip("\r\n")
            if line:
                print(f"[{prefix}] {line}", flush=True)
    except Exception as exc:  # stream closed unexpectedly
        log("WARN", f"[{prefix}] log pipe closed: {exc}")
    finally:
        try:
            stream.close()
        except Exception:
            pass


def spawn_reader(proc, prefix):
    """Read a child's piped output into the console in a daemon thread."""
    threading.Thread(
        target=log_stream,
        args=(prefix, proc.stdout),
        daemon=True,
        name=f"reader-{prefix}",
    ).start()


# --------------------------------------------------------------------------- #
# Pre-flight
# --------------------------------------------------------------------------- #

def preflight_check():
    """Verify every engine file exists and exceeds its minimum size."""
    ok = True
    for path, minimum in REQUIRED_FILES.items():
        if not os.path.isfile(path):
            log("FATAL", f"Pre-flight FAILED: {path} is MISSING.")
            ok = False
            continue
        size = os.path.getsize(path)
        if size < minimum:
            log("FATAL",
                f"Pre-flight FAILED: {path} is too small ({size}B < {minimum}B). "
                "Refusing to start with a damaged engine.")
            ok = False
            continue
        log("INFO", f"OK: {path} ({size}B)")
    if not ok:
        log("FATAL", "ABORTING: engine pre-flight failed. Nothing was started.")
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Process launch
# --------------------------------------------------------------------------- #

def popen_no_window(cmd):
    """Popen helper that never pops a console window on Windows."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )


def launch_children(dev_mode):
    """Start the EA bridge and the web API."""
    # 1) MT5 EA bridge
    bridge_cmd = [sys.executable, "core/mt5_bridge.py", "--port", str(BRIDGE_PORT)]
    log("INFO", f"Launching EA Bridge: {bridge_cmd}")
    proc_bridge = popen_no_window(bridge_cmd)

    # 2) Web dashboard (FastAPI/uvicorn)
    api_cmd = [
        sys.executable, "-m", "uvicorn", "backend.api:app",
        "--host", HOST, "--port", str(PORT),
    ]
    if dev_mode:
        api_cmd.append("--reload")
    log("INFO", f"Launching Brain API: {' '.join(api_cmd)}")
    proc_api = popen_no_window(api_cmd)

    spawn_reader(proc_bridge, PREFIX_MT5)
    spawn_reader(proc_api, PREFIX_BRAIN)
    return proc_bridge, proc_api


# --------------------------------------------------------------------------- #
# Health check + supervisor
# --------------------------------------------------------------------------- #

def health_check(proc_bridge, proc_api):
    """Give children HEALTH_CHECK_WAIT seconds to prove they stay alive."""
    time.sleep(HEALTH_CHECK_WAIT)
    failed = False
    if proc_bridge.poll() is not None:
        log("FATAL", f"EA Bridge (pid {proc_bridge.pid}) died immediately, "
                     f"exit code {proc_bridge.returncode}.")
        failed = True
    if proc_api.poll() is not None:
        log("FATAL", f"Brain API (pid {proc_api.pid}) died immediately, "
                     f"exit code {proc_api.returncode}.")
        failed = True
    return not failed


def supervisor_loop(proc_bridge, proc_api):
    """Poll every interval; if either child dies, stop everything (fail fast)."""
    while True:
        time.sleep(SUPERVISOR_INTERVAL)
        if proc_bridge.poll() is not None:
            log("FATAL", f"EA Bridge (pid {proc_bridge.pid}) DIED, "
                         f"exit code {proc_bridge.returncode}. "
                         f"Stopping dashboard to avoid stale data.")
            stop_all(proc_bridge, proc_api)
            sys.exit(1)
        if proc_api.poll() is not None:
            log("FATAL", f"Brain API (pid {proc_api.pid}) DIED, "
                         f"exit code {proc_api.returncode}. "
                         f"Stopping bridge to avoid stale data.")
            stop_all(proc_bridge, proc_api)
            sys.exit(1)


def stop_all(proc_bridge, proc_api):
    """Terminate both children gracefully, then hard-kill if needed."""
    log("INFO", "Stopping children...")
    for proc in (proc_bridge, proc_api):
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    for proc in (proc_bridge, proc_api):
        if proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    log("INFO", "All children stopped.")


# --------------------------------------------------------------------------- #
# Browser auto-open
# --------------------------------------------------------------------------- #

def open_browser():
    time.sleep(1)  # give uvicorn a moment to finish binding
    try:
        webbrowser.open(f"http://localhost:{PORT}/")
        log("INFO", f"Browser opened: http://localhost:{PORT}/")
    except Exception as exc:
        log("WARN", f"Could not open browser automatically: {exc}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    dev_mode = "--dev" in sys.argv[1:]

    # Windows: if this process was created in a new process group
    # (CREATE_NEW_PROCESS_GROUP), CTRL+C is disabled by default, which would
    # defeat the graceful KeyboardInterrupt shutdown below. Re-enable it so
    # Ctrl+C always reaches our handler regardless of launch context.
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        except Exception as exc:
            log("WARN", f"Could not re-enable Ctrl+C handling: {exc}")

    print()
    log("INFO", "=" * 60)
    log("INFO", "TOMOKO INSTINCT V4.4 BLOCK 3 - ONE TRUE MAIN ENGINE")
    log("INFO", "=" * 60)

    # 1) Pre-flight protects against empty/undersized engine overwrites.
    preflight_check()

    # 2) Launch both children.
    proc_bridge, proc_api = launch_children(dev_mode=dev_mode)

    # 3) Fail-fast: any child dying right after launch => FATAL.
    if not health_check(proc_bridge, proc_api):
        stop_all(proc_bridge, proc_api)
        log("FATAL", "ABORTING: a child process failed immediately after launch.")
        sys.exit(1)

    log("INFO", f"BRAIN LIVE: http://localhost:{PORT}/ (pid {proc_api.pid})")
    log("INFO", f"MT5 BRIDGE LIVE (pid {proc_bridge.pid}, port {BRIDGE_PORT})")
    log("INFO", f"Supervisor active (poll every {SUPERVISOR_INTERVAL}s). "
                "Ctrl+C to stop both.")
    threading.Thread(target=open_browser, daemon=True).start()

    # 4) Supervisor loop with graceful Ctrl+C.
    try:
        supervisor_loop(proc_bridge, proc_api)
    except KeyboardInterrupt:
        log("INFO", "Ctrl+C received. Shutting down gracefully...")
        stop_all(proc_bridge, proc_api)
        log("INFO", "Tomoko stopped cleanly. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()