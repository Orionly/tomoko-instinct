#!/usr/bin/env python3
"""
Tomoko Instinct V4.4 Block 3 - ONE TRUE MAIN ENGINE

This is the single orchestrating entry point. Main.py launches and supervises
the unified Brain Engine (FastAPI Web Dashboard on port 8000 + embedded
TomokoDataPump EA Socket Bridge on port 18001).

Behavior:
  * Pre-flight: refuses to start if any engine file is missing or undersized.
  * Streams child output to this console with [BRAIN_API] prefix.
  * Fail-fast health check: child dying right after launch => FATAL exit.
  * Supervisor loop: every 3 seconds, if child exits => FATAL exit.
  * Ctrl+C terminates child gracefully and exits 0.
  * Auto-opens http://localhost:8000 once alive.

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
import shutil
import threading
import subprocess
import webbrowser

import config

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

# Host/ports centralized in config.py (single source of truth - no hardcoding)
HOST = config.API_HOST
PORT = config.API_PORT
BRIDGE_PORT = config.EA_SOCKET_PORT
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


def sync_static():
    """Keep legacy backend/static/ copies in sync with ui/ (single source of truth).

    ui/ holds the canonical HTML files. Old launchers that read
    backend/static/*.html directly still find fresh byte-identical copies, so
    the copies can never drift from the source.
    """
    pairs = {
        "ui/web_dashboard.html": os.path.join("backend", "static", "web_dashboard.html"),
        "ui/pair.html": os.path.join("backend", "static", "pair.html"),
    }
    for src, dst in pairs.items():
        if not os.path.isfile(src):
            log("WARN", f"sync skipped: {src} missing")
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            log("INFO", f"synced {src} -> {dst}")
        except Exception as exc:
            log("WARN", f"sync failed {src} -> {dst}: {exc}")
    log("INFO", "synced ui -> backend/static")


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


def launch_engine(dev_mode):
    """Start the unified Brain Engine (Web API + embedded EA Socket Bridge)."""
    api_cmd = [
        sys.executable, "-u", "-m", "uvicorn", "backend.api:app",
        "--host", HOST, "--port", str(PORT),
    ]
    if dev_mode:
        api_cmd.append("--reload")
    log("INFO", f"Launching Brain Engine (API:{PORT} + EA Bridge:{BRIDGE_PORT}): {' '.join(api_cmd)}")
    proc_api = popen_no_window(api_cmd)
    spawn_reader(proc_api, PREFIX_BRAIN)
    return proc_api


# --------------------------------------------------------------------------- #
# Health check + supervisor
# --------------------------------------------------------------------------- #

def health_check(proc_api):
    """Give engine HEALTH_CHECK_WAIT seconds to prove it stays alive."""
    time.sleep(HEALTH_CHECK_WAIT)
    if proc_api.poll() is not None:
        log("FATAL", f"Brain API (pid {proc_api.pid}) died immediately, "
                     f"exit code {proc_api.returncode}.")
        return False
    return True


def supervisor_loop(proc_api):
    """Poll every interval; if engine dies, stop everything (fail fast)."""
    while True:
        time.sleep(SUPERVISOR_INTERVAL)
        if proc_api.poll() is not None:
            log("FATAL", f"Brain API (pid {proc_api.pid}) DIED, "
                         f"exit code {proc_api.returncode}.")
            stop_all(proc_api)
            sys.exit(1)


def stop_all(proc_api):
    """Terminate engine gracefully, then hard-kill if needed."""
    log("INFO", "Stopping Brain Engine...")
    if proc_api.poll() is None:
        try:
            proc_api.terminate()
        except Exception:
            pass
    if proc_api.poll() is None:
        try:
            proc_api.wait(timeout=5)
        except Exception:
            try:
                proc_api.kill()
            except Exception:
                pass
    log("INFO", "Brain Engine stopped.")


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

    # 2) Mirror ui/ HTML into backend/static/ so old launchers keep working.
    sync_static()

    # 3) Launch unified engine.
    proc_api = launch_engine(dev_mode=dev_mode)

    # 4) Fail-fast: engine dying right after launch => FATAL.
    if not health_check(proc_api):
        stop_all(proc_api)
        log("FATAL", "ABORTING: Brain engine failed immediately after launch.")
        sys.exit(1)

    log("INFO", f"BRAIN LIVE: http://localhost:{PORT}/ (pid {proc_api.pid})")
    log("INFO", f"EA SOCKET BRIDGE ACTIVE (port {BRIDGE_PORT}, embedded in Brain engine)")
    log("INFO", f"Supervisor active (poll every {SUPERVISOR_INTERVAL}s). "
                "Ctrl+C to stop.")
    threading.Thread(target=open_browser, daemon=True).start()

    # 5) Supervisor loop with graceful Ctrl+C.
    try:
        supervisor_loop(proc_api)
    except KeyboardInterrupt:
        log("INFO", "Ctrl+C received. Shutting down gracefully...")
        stop_all(proc_api)
        log("INFO", "Tomoko stopped cleanly. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()