# TOMOKO INSTINCT V4.4 BLOCK 3 — FIX REPORT
**Fix Agent — single mission: make Main.py the one true main engine**
**Date:** 2 October 2026 17:52 (local)
**Workspace:** `C:\Users\USER\Documents\Tomoko`

---

## TL;DR

The old architecture scattered the real engine across `Tomoko.bat` + two separate `cmd /k` windows with **no supervisor** (any single crash left the other serving stale `demoObj(...)` data). Mission complete:

- **`Main.py` is now the ONE true main engine** (10,034 bytes) — pre-flight protected, supervised, fail-fast, graceful Ctrl+C. **Verified live in production mode.**
- Legacy mains **renamed, not deleted**: `desktop_legacy.py`, `web_legacy.py`, `START_HERE_OLD.bat`.
- `.gitignore` added; **all 21 tracked `__pycache__/*.pyc` and `logs/brain_signals.csv` removed from the git index** (file kept on disk, ignored).
- `scripts/guard.py` created with `safe_write()` (undersized writes blocked).
- **Nothing pushed.** Staged locally; user pushes after manual verification.

---

## 1. Old architecture vs new

### OLD (no supervisor, stale-data risk)
```
start_tomoko.bat ──► Tomoko.bat
                      ├─ terminal64.exe (MT5)
                      ├─ cmd /k python core\mt5_bridge.py --port 18001   (EA bridge)
                      └─ cmd /k uvicorn backend.api:app --port 8000 --reload  (dashboard)
                              # independent console windows, no parent supervision.
                              # If the bridge died, uvicorn kept serving stale demo data.
```

### NEW (single supervised process tree)
```
python Main.py
 ├─ pre-flight size checks (core/mt5_bridge.py>500B, backend/api.py>1000B, ui/web_dashboard.html>1000B)
 ├─ [MT5_BRIDGE] core\mt5_bridge.py --port 18001        (sys.executable, CREATE_NO_WINDOW)
 ├─ [BRAIN_API]  python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000   (no --reload unless --dev)
 ├─ fail-fast health check (3s): die right after launch  ──► FATAL, both stopped, exit 1
 ├─ supervisor loop (poll 3s): ANY child death           ──► FATAL, both stopped, exit 1
 ├─ auto-open http://localhost:8000 on success
 └─ Ctrl+C ──► terminate both gracefully, exit 0
```
Processes share the orchestrator as parent; log output streamed with prefixes; no orphaned windows.

---

## 2. Files renamed / created

### Renamed (git mv — original content preserved byte-for-byte, reversible)
| From | To | Size |
|---|---|---|
| Main.py | desktop_legacy.py | 461 B |
| Tomoko.py | web_legacy.py | 834 B |
| Tomoko.bat | START_HERE_OLD.bat | 1,463 B |

`run_tomoko.bat` and `start_tomoko.bat` kept **as-is** for reference (note: `start_tomoko.bat` calls the old `Tomoko.bat`, which no longer exists — reference only, superseded by `python Main.py`).

### Created
| File | Size | Purpose |
|---|---|---|
| Main.py | **10,034 B** | True orchestrator (see §4) |
| .gitignore | — | `__pycache__/`, `*.pyc`, `venv/`, `logs/`, `*.log`, `*.csv`, `.DS_Store`, `.vscode/.idea/`, `*.recovered`, `*.backup-*`, etc. |
| scripts/guard.py | 2,493 B | `safe_write(path, content)` blocks writes below `MIN_SIZES` |

**guard.py `safe_write` behavior (verified):**
```
MIN_SIZES = { Main.py:500, backend/api.py:1000, core/mt5_bridge.py:500, ui/web_dashboard.html:1000, config.py:100 }
> python scripts/guard.py Main.py "SHORT"
BLOCKED: write to 'Main.py' has 5 chars, below the minimum 500 chars. File was NOT modified.  (exit 1)
```
Unknown paths are rejected outright; correct path + sufficient length writes successfully.

---

## 3. Git housekeeping

- `git rm -r --cached` removed from index (files remain on disk, now ignored):
  - 21 `.pyc` files across `__pycache__/`, `backend/`, `core/`, `ui/`, `strategies/`
  - `logs/brain_signals.csv` — **still on disk, 10,871,529 bytes and growing**, confirmed `git check-ignore` applies
- Staged: `.gitignore`, `Main.py`, `scripts/guard.py`, `desktop_legacy.py`, `web_legacy.py` (+ rename of `Tomoko.bat` → `START_HERE_OLD.bat`)
- Working tree still dirty by design (`backend/static/web_dashboard.html`, `web_dashboard.html` — pre-existing uncommitted content; `RECOVERY_REPORT.md` untracked). Left for user.
- **No commit, no push performed.**

---

## 4. New Main.py summary (10,034 B, 284 lines)

| Requirement | Implementation |
|---|---|
| Pre-flight | `preflight_check()` — verifies 3 engine files exist and exceed min size; FATAL + `exit(1)` on damage |
| Launch MT5 bridge | `Popen([sys.executable, "core/mt5_bridge.py", "--port", "18001"])` |
| Launch API | `Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", "8000"])` — no `--reload`; append `--reload` only if `--dev` |
| Log streaming | Reader threads tagged `[MT5_BRIDGE]` / `[BRAIN_API]` |
| Fail-fast health | 3s grace, then any dead child → FATAL + shutdown + exit 1 |
| Supervisor | Poll every 3s; any death → FATAL + shutdown + exit 1 |
| Ctrl+C | `KeyboardInterrupt` → `stop_all()` → exit 0 (plus Windows `SetConsoleCtrlHandler(None, False)` so Ctrl+C works even under new-process-group launches) |
| Browser | Auto-opens `http://localhost:8000/` |
| Interpreter | `sys.executable` everywhere (Python 3.13.6) |

---

## 5. Test results

### TEST A — Happy path (production), `python Main.py` (bg process, live observation)
```
[INFO] TOMOKO INSTINCT V4.4 BLOCK 3 - ONE TRUE MAIN ENGINE
[INFO] OK: core/mt5_bridge.py (32052B)
[INFO] OK: backend/api.py (15826B)
[INFO] OK: ui/web_dashboard.html (38807B)
[INFO] Launching EA Bridge: [... 'core/mt5_bridge.py', '--port', '18001']
[INFO] Launching Brain API: ...python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
[INFO] MT5 BRIDGE LIVE (pid 2916, port 18001)
[INFO] BRAIN LIVE: http://localhost:8000/ (pid 21544)
[INFO] Browser opened: http://localhost:8000/
[BRAIN_API] [MT5] Connected to VTMarkets-Live 5 Balance 24.44 USD ...
[BRAIN_API] [MARKET_PULSE] USD final score=4.46 bull=4/4   # matches screenshots' "USD Strong +4.43"
```
- HTTP check: `GET /` → **200**, len 38,687, contains `TOMOKO`, `EURUSD`, `Market Pulse`, `Heatmap`.
- `logs/brain_signals.csv` actively growing under the new orchestrator.
- Browser auto-opened.

### TEST B — Fail-fast supervision (killed EA Bridge child pid 2916)
```
[FATAL] EA Bridge (pid 2916) DIED, exit code 4294967295. Stopping dashboard to avoid stale data.
[INFO]  Stopping children...
[INFO]  All children stopped.
```
→ Orchestrator exited (code 1), **both** children gone, **ports 8000 + 18001 freed**, no survivors. -> no stale demo data possible.

### TEST C — Ctrl+C graceful shutdown (real `CTRL_C_EVENT` via console harness)
```
[INFO] Ctrl+C received. Shutting down gracefully...
[INFO]  Stopping children...
[INFO]  All children stopped.
[INFO]  Tomoko stopped cleanly. Goodbye.
HARNESS: orchestrator exit code = 0
```
→ Both processes terminated, exit code 0, ports freed, no orphans.

### TEST D — `--dev` mode
```
uvicorn backend.api:app --host 127.0.0.1 --port 8000 --reload     # --reload present ✓
```

### TEST E — guard (see §2) — undersized write blocked with exit 1.

---

## 6. How to run / verify after this report

```bat
:: Production (recommended):
python Main.py

:: Development (hot reload):
python Main.py --dev

:: Verify from git at any time:
git show HEAD:Main.py | Measure-Object -Character
```

**Next manual steps (user):** review `git status`/`git diff --cached`, then commit and push at your own discretion. Nothing was committed or pushed by this Fix Agent.

---

## 7. Safety compliance checklist

- [x] Integration & renames done via `git mv` (reversible)
- [x] `logs/brain_signals.csv` **not deleted** — removed from index only, confirmed on disk
- [x] `__pycache__` not deleted — removed from index only
- [x] Main.py = 10,034 B (min safe 500 B) ✓
- [x] Nothing pushed
- [x] Test processes cleaned up (no engine processes running, ports free at handoff)
- [x] Recovery artifacts from the previous mission (`RECOVERY_REPORT.md`) left untouched