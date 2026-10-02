import os
from pathlib import Path

# For Windows - creates Tomoko.lnk on Desktop.
# Requires: pip install pywin32  (winshell optional)
try:
    import win32com.client

    # Resolve Desktop without winshell (falls back to USERPROFILE\Desktop)
    try:
        import winshell
        desktop = Path(winshell.desktop())
    except Exception:
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"

    target = Path.cwd() / "Tomoko.bat"
    wsh = win32com.client.Dispatch("WScript.Shell")
    shortcut = wsh.CreateShortcut(str(desktop / "Tomoko Brain.lnk"))
    shortcut.TargetPath = str(target)
    shortcut.WorkingDirectory = str(Path.cwd())
    shortcut.Description = "Tomoko Brain - Double click to launch"
    # Optional icon - set once you have icon.ico:
    # shortcut.IconLocation = str(Path.cwd() / "icon.ico")
    shortcut.Save()
    print(f"Shortcut created at {desktop / 'Tomoko Brain.lnk'}")
except Exception as e:
    print(f"Error: {e}")
    print("Manual: Right-click Tomoko.bat -> Send to -> Desktop (create shortcut)")
