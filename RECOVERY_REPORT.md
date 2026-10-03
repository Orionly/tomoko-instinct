# TOMOKO INSTINCT V4.4 BLOCK 3 — FORENSIC RECOVERY REPORT

**Recovery Agent — Read-only forensic mode**
**Date:** 2 October 2026 16:45 (local)
**Workspace:** `C:\Users\USER\Documents\Tomoko`
**Repo:** `Orionly/tomoko-instinct` (origin), branch `main`

---

## TL;DR

- **Main.py is NOT 0 bytes.** It is **461 bytes** on disk, **461 bytes** in git `HEAD`, and **461 bytes** on GitHub (API-verified sha `bf109da…`). Nothing was lost.
- The running dashboard does **not** run from `Main.py` at all. It runs from **`backend/api.py` (FastAPI/uvicorn)** and **`core/mt5_bridge.py`**, launched by **`Tomoko.bat`** — a process tree that never imports `Main.py`.
- 4 live Python processes verified (`uvicorn` on :8000, EA bridge on :18001), started **2/10/2026 4:26 PM**, and `logs/brain_signals.csv` was **growing during this investigation** (10.7 MB, last write 4:44 PM).
- **Root cause of the "0 bytes but still running" paradox:** the screenshotted web dashboard is a separate app (FastAPI + static HTML) that does not depend on `Main.py`. In this workspace, `Main.py` was never empty; the empty-file observation is most likely a stale editor view, a different directory (`Tomoko_V2\main.py` exists in your editor history, not this `Documents\Tomoko` folder), or a cache artifact.
- **No recovery files were created** because there was nothing to recover. See "Non-destructive recovery" below for the exact rule evaluation.

---

## 1. File sizes and dates

| File | Size | LastWriteTime |
|---|---|---|
| Main.py | **461** | 25/8/2026 10:44 PM |
| Tomoko.py | **834** | 27/8/2026 11:31 AM |
| config.py | 1,969 | 5/9/2026 8:02 PM |
| Tomoko.bat | 1,463 | 12/9/2026 11:41 PM |
| run_tomoko.bat | 181 | 25/8/2026 10:44 PM |
| start_tomoko.bat | 32 | 31/8/2026 12:24 AM |
| Tomoko.sh | 253 | 27/8/2026 11:31 AM |
| launch_hidden.vbs | 92 | 25/8/2026 10:44 PM |
| backend/api.py | 15,826 | 24/9/2026 5:24 PM |
| ui/dashboard.py | 26,614 | 26/8/2026 11:38 PM |
| ui/web_dashboard.html | 38,807 | 24/9/2026 5:25 PM (identical copy in `backend/static/` and root — SHA-256 match) |
| logs/brain_signals.csv | **10,714,376** | **2/10/2026 4:44 PM (live)** |

`venv` directory: **absent** — system Python 3.13.6 (`C:\Users\USER\AppData\Local\Programs\Python\Python313`) is used.

---

## 2. What actually launches the dashboard — THE REAL ENTRY POINT

| File | Launches | Purpose |
|---|---|---|
| **Tomoko.bat** | `core\mt5_bridge.py --port 18001` **and** `uvicorn backend.api:app --port 8000 --reload` | **THE real launcher. Never touches Main.py/Tomoko.py.** |
| start_tomoko.bat | `call Tomoko.bat` | Wrapper for Tomoko.bat |
| Tomoko.sh | `uvicorn backend.api:app` | Linux equivalent |
| Tomoko.py | `uvicorn backend.api:app` (via `-m uvicorn`) | Older standalone web launcher (not used by Tomoko.bat) |
| run_tomoko.bat | `python main.py` | Tkinter **desktop** app (TomokoBrainApp from `ui/dashboard.py`) |
| launch_hidden.vbs | `run_tomoko.bat` hidden | Hidden desktop-app launch — **not** the web dashboard |

**The web dashboard** (screenshots: TOMOKO BRAIN, EURUSD 68, XAUUSD 80, Market Pulse, Heatmap, pair pages) is `FastAPI` app `backend.api:app` serving `/api/...` endpoints plus `ui/web_dashboard.html`. That HTML file contains embedded **demo data** (`demoObj('EURUSD',…68…)`, `demoObj('XAUUSD',…82…)`, `DEMO_PULSE`) which is exactly what the screenshots show — so the page renders even on API hiccups. The dashboard is **fully independent of Main.py**.

---

## 3. Ghost runner / __pycache__ forensics

| __pycache__ location | Contents (key items) | Timestamp |
|---|---|---|
| root | `config.cpython-313.pyc` only (1,494 B) | 5/9/2026 8:02 PM |
| backend | `api.cpython-313.pyc` | 24/9/2026 5:26 PM |
| core | 14 .pyc incl. `mt5_bridge` (41,285 B) | 30/8–24/9/2026 |
| ui | `dashboard.cpython-313.pyc` (39,199 B) | 30/8/2026 |
| strategies | 4 .pyc | 30/8/2026 |

**Verdict:** The dashboard is **not** running from cache-only. `backend/api.py` source exists (15,826 B) and the `.pyc` files are ordinary CPython 3.13 bytecode caches created when those modules were last imported. The running worker (PID 22672) is a **uvicorn `--reload` worker** importing `backend.api` from source via `--reload` (source exists → cache irrelevant).

**Confirmed live processes (2/10/2026 4:26 PM start, all still alive):**
- PID 20172 — `uvicorn backend.api:app --host 127.0.0.1 --port 8000 --reload --log-level info` (child of cmd PID 12128)
- PID 22672 — uvicorn reload worker, listening on `127.0.0.1:8000`
- PID 21912 — `python core\mt5_bridge.py --port 18001` (child of cmd PID 10756)
- PID 22468 — multiprocessing spawn child

These command lines **exactly match the two `start ... cmd /k` lines in Tomoko.bat**, confirming Tomoko.bat was the launcher at 4:26 PM today.

---

## 4. Git forensics

- `git status -u`: on branch main, **up to date with origin/main**. Working-tree modifications **only** in `backend/static/web_dashboard.html`, `logs/brain_signals.csv`, `web_dashboard.html`. **`Main.py` and `Tomoko.py` are UNMODIFIED** (working tree == HEAD).
- `git log --oneline -n 20` → **single commit**: `702a378 Tomoko initial - half build`
- `git log --follow -- Main.py` and `-- Tomoko.py` → both show only `702a378`; **no file ever deleted/recreated in git**.
- `git rev-list --count HEAD` = **1** → **there is no `HEAD~1`** (`git show HEAD~1:Main.py` fails with exit 128, "invalid object name 'HEAD~1'").
- `git reflog -n 20` → only `702a378` initial commit + a branch rename. **No reset, checkout, or amend events.**
- `git show HEAD:Main.py` → **446 characters** (461 bytes with CRLF) — a valid 15-line launcher (imports `ui.dashboard`, `core.*`, `config`; starts `TomokoBrainApp`).
- `git show HEAD:backend/api.py` → 15,388 chars (365 lines) — full FastAPI app present.
- Remote API check (`api.github.com/…/contents/Main.py`): **size = 461 bytes**, sha `bf109da4…`, content = the same launcher. **GitHub copy is NOT 0 bytes.**
- No `.gitignore`; all 67 tracks include `__pycache__/*.pyc` (they were committed). Do not delete them without a decision — they're tracked artifacts.

---

## 5. Non-destructive recovery — rule evaluation

**Mission rule:** `If Tomoko.py > 500 bytes and Main.py < 100 bytes → cp Tomoko.py Main.py.recovered`
- Tomoko.py = 834 B (> 500) ✓ but **Main.py = 461 B, NOT < 100 B** ✗ → **condition FAILS, no copy made.**

**`git show HEAD~1:Main.py > Main.py.from_git.recovered`**: attempted; **fails** — only one commit exists, no `HEAD~1`. Nothing to extract.

**Timeline / Local History:** scanned `%APPDATA%\Code\User\History` and `%APPDATA%\antigravity\User\History`. The only `main.py` entries belong to a **different project** — `C:\Users\USER\Documents\Tomoko_V2\main.py` (50+ snapshots) and `Tomoko_V2\main_backup.py`. **No editor history exists for `C:\Users\USER\Documents\Tomoko\Main.py`**, consistent with the file never having been changed since creation.

**Conclusion:** No `.recovered` files were created because **there was nothing to recover**. Creating 0-byte or redundant stubs would violate the spirit of the 500-byte safety rule. Both entry points are intact in three independent stores (disk, git HEAD, GitHub).

---

## 6. Root cause: why "Main.py empty" but app still running?

> **Main.py is not empty.** In this workspace it is 461 bytes on disk, in git, and on GitHub.

The dashboard you are looking at (TOMOKO BRAIN web UI with Market Pulse / Heatmap / pair pages) is **not launched by Main.py**. It is launched by **Tomoko.bat**, which starts:

1. EA Bridge: `python core\mt5_bridge.py --port 18001`
2. Web API: `uvicorn backend.api:app --port 8000 --reload`

…in separate `cmd /k` windows, then opens a browser to `http://localhost:8000`. Neither step imports `Main.py` or `Tomoko.py`. So:

- Even if `Main.py` (desktop Tkinter launcher) were 0 bytes or deleted, the running uvicorn/MT5 processes keep serving the dashboard — they are already in memory with a live worker on port 8000.
- Therefore "dashboard still running" is **expected behavior**, not a ghost.

The "0 bytes" report most likely originated from one of:
- A stale/mis-rendered editor or GitHub cache view of `Main.py`,
- A different copy of the project (your editor history shows active work in `Documents\Tomoko_V2\main.py`, a different folder),
- Confusing `run_tomoko.bat`'s desktop app path with the web dashboard path.

**The real entry points, for the record:**
- **Web dashboard (what you see in screenshots):** `Tomoko.bat` → `uvicorn backend.api:app` (port 8000) + `core/mt5_bridge.py` (port 18001).
- **Desktop Tkinter app:** `run_tomoko.bat` / `launch_hidden.vbs` → `python Main.py` → `ui.dashboard.TomokoBrainApp`.

---

## 7. Recommended actions

**Recommended restore command: NONE REQUIRED.** Both files are complete.

To verify yourself (will print "OK" if intact):
```
git status --porcelain -- Main.py Tomoko.py          # expect empty output
git show HEAD:Main.py | Measure-Object -Character    # expect ~446 chars / 15 lines
```

If you ever need to restore from git (e.g., a future accidental truncation):
```
git checkout HEAD -- Main.py Tomoko.py
```
This restores the 461 B / 834 B versions — git will never hand back a <500-byte file for these paths since HEAD holds full copies.

Additional safeguards (optional, do not alter runtime):
1. Keep the current uvicorn/MT5 processes untouched — they are healthy and logging.
2. If you want a safety snapshot: `Copy-Item Main.py Main.py.backup-20261002` (this is a *backup*, not a recovery overwrite).
3. Worktree hygiene: `logs/brain_signals.csv` (10.7 MB) and `backend/static/web_dashboard.html` have uncommitted changes; commit them if you want the live HTML/log in git.

---

## Appendix — evidence trail

- `Get-Item Main.py` → Length 461, no link type (real file).
- `git ls-files` → 67 tracked files including `Main.py`, `Tomoko.py`, `backend/api.py`, `ui/dashboard.py`, `ui/web_dashboard.html`, and committed `.pyc` caches.
- Running processes matched to `Tomoko.bat` exact command lines (2/10/2026 4:26 PM).
- `netstat -ano` → `127.0.0.1:8000` LISTENING (PID 22672), `127.0.0.1:18001` LISTENING (PIDs 22468/21912).
- `logs/brain_signals.csv` grew from 10,709,505 → 10,714,376 bytes during the investigation (live writer).
- `web_dashboard.html` three copies (ui/, backend/static/, root) identical SHA-256 `AF2FED9E…`.
- GitHub Contents API: `Main.py` size 461, sha `bf109da4…`, raw content = 15-line launcher.