import subprocess
import webbrowser
import time
import os
import sys

URL = "http://127.0.0.1:8000"


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    os.makedirs("logs", exist_ok=True)

    print("Starting Tomoko Brain API...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api:app",
         "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    time.sleep(3)
    print(f"Opening {URL}")
    webbrowser.open(URL)

    print("Tomoko running. Keep this window open. Close to stop.")
    print("CSV logging to logs/brain_signals.csv")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
