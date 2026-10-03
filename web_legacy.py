import subprocess
import webbrowser
import time
import os
import sys

import config

URL = f"http://{config.API_HOST}:{config.API_PORT}"


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    os.makedirs("logs", exist_ok=True)

    print("Starting Tomoko Brain API...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.api:app",
         "--host", config.API_HOST, "--port", str(config.API_PORT), "--log-level", "warning"],
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
