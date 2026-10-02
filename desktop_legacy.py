import sys, os
sys.path.append(os.path.dirname(__file__))
from ui.dashboard import TomokoBrainApp
from core.mt5_bridge import MT5Bridge
from core.regime_engine import RegimeEngine
from core.context_feed import ContextFeed
from core.levels_engine import LevelsEngine
from core.journal import Journal
import config

if __name__ == "__main__":
    # Ensure journal dir exists
    os.makedirs("journal", exist_ok=True)
    app = TomokoBrainApp()
    app.mainloop()
