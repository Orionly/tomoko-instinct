"""
Tomoko Audit Generator - creates a .txt file you CAN upload here
Run: python Tomoko-Audit-Generator.py
Creates: audit_for_meta.txt
"""
import os, glob, pathlib
from datetime import datetime
import subprocess

out = []
def log(s): out.append(s); print(s)

log("="*70)
log("TOMOKO INSTINCT FULL AUDIT - FOR META AI REVIEW")
log(f"Generated: {datetime.now()}")
log("="*70)

log("\n[FILE TREE - all files, size]")
for root, dirs, files in os.walk("."):
    if "__pycache__" in root or ".git" in root or "logs" in root or "venv" in root or ".venv" in root:
        continue
    level = root.count(os.sep)
    indent = " " * 2 * level
    log(f"{indent}{os.path.basename(root)}/")
    subindent = " " * 2 * (level + 1)
    for f in files[:50]:
        fp = os.path.join(root, f)
        try:
            sz = os.path.getsize(fp)
            log(f"{subindent}{f} - {sz} bytes")
        except:
            pass

log("\n[MAIN FILES - first 100 lines each]")
for fname in ["Main.py", "config.py", "Requirements.txt", "backend/api.py", "core/mt5_bridge.py", "ui/web_dashboard.html", "strategies/__init__.py"]:
    if os.path.exists(fname):
        log(f"\n--- {fname} ({os.path.getsize(fname)} bytes) ---")
        try:
            with open(fname, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = fh.readlines()[:100]
                for i, line in enumerate(lines, 1):
                    log(f"{i:3}: {line.rstrip()[:200]}")
        except Exception as e:
            log(f"Error reading {fname}: {e}")
    else:
        matches = glob.glob(f"**/{os.path.basename(fname)}", recursive=True)
        if matches:
            log(f"\n--- {fname} NOT at root, found at {matches[:3]} ---")
            for m in matches[:1]:
                log(f"Trying {m} ({os.path.getsize(m)} bytes)")
                try:
                    with open(m, 'r', encoding='utf-8', errors='ignore') as fh:
                        lines = fh.readlines()[:60]
                        for i, line in enumerate(lines, 1):
                            log(f"{i:3}: {line.rstrip()[:200]}")
                except: pass

log("\n[STRATEGIES FOLDER]")
if os.path.exists("strategies"):
    for f in glob.glob("strategies/*.py"):
        log(f"\n--- {f} ({os.path.getsize(f)} bytes) ---")
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read(2000)
                log(content[:2000])
        except: pass
else:
    log("strategies/ DOES NOT EXIST")

log("\n[BACKEND FOLDER]")
if os.path.exists("backend"):
    for f in glob.glob("backend/*.py"):
        log(f"  {f}: {os.path.getsize(f)} bytes")
        if "api" in f:
            try:
                with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
                    if "demoObj" in txt:
                        idx = txt.find("demoObj")
                        log(f"    demoObj found near: {txt[max(0,idx-100):idx+200][:300]}")
                    if "score" in txt.lower():
                        log(f"    Contains scoring logic")
            except: pass

log("\n[UI - web_dashboard.html analysis]")
for path in ["ui/web_dashboard.html", "web_dashboard.html", "backend/static/web_dashboard.html"]:
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                html = fh.read()
                log(f"\n{path}: {len(html)} chars")
                log(f"  Has EURUSD: {'EURUSD' in html}")
                log(f"  Has demoObj: {'demoObj' in html}")
                log(f"  Has Market Pulse: {'Market Pulse' in html}")
                log(f"  Preview: {html[:500][:200]}")
        except Exception as e:
            log(f"Error {path}: {e}")

log("\n[GIT LOG]")
try:
    r = subprocess.run("git log --oneline -n 10", shell=True, capture_output=True, text=True, timeout=5)
    log(r.stdout)
    r2 = subprocess.run("git status --short", shell=True, capture_output=True, text=True, timeout=5)
    log("git status:")
    log(r2.stdout)
except Exception as e:
    log(f"git error: {e}")

log("\n" + "="*70)
log("END OF AUDIT")
log("="*70)

with open("audit_for_meta.txt", "w", encoding="utf-8", errors="ignore") as f:
    f.write("\n".join(out))

print(f"\nCreated audit_for_meta.txt - {os.path.getsize('audit_for_meta.txt')} bytes - UPLOAD THIS FILE HERE")
