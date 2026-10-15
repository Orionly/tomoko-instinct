import os

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 1. Promote Main Dashboard
with open(os.path.join(root, "ui", "dashboard_preview.html"), "r", encoding="utf-8") as f:
    dash = f.read()

# Production adjustments
dash = dash.replace("V5.0 PREVIEW", "V5.0")
dash = dash.replace("Tomoko // Brain V5.0 Preview (Macro Intelligence)", "Tomoko // Brain V5.0 (Macro Intelligence)")
dash = dash.replace("Tomoko Brain <b>V5.0 Preview</b>", "Tomoko Brain <b>V5.0</b>")
dash = dash.replace("/api/preview/macro_intel", "/api/macro_intel")
dash = dash.replace("/preview/pair/${d.symbol}", "/pair/${d.symbol}")

prod_dash_path = os.path.join(root, "ui", "web_dashboard.html")
with open(prod_dash_path, "w", encoding="utf-8") as f:
    f.write(dash)

# Also sync duplicates if they exist
for dup in [os.path.join(root, "backend", "static", "web_dashboard.html"), os.path.join(root, "web_dashboard.html")]:
    if os.path.exists(os.path.dirname(dup)):
        with open(dup, "w", encoding="utf-8") as f:
            f.write(dash)

# 2. Promote Pair Deep Dive
with open(os.path.join(root, "ui", "pair_preview.html"), "r", encoding="utf-8") as f:
    pair = f.read()

pair = pair.replace("Tomoko // Pair Deep Dive Preview", "Tomoko // Pair Deep Dive V5.0")
pair = pair.replace('href="/preview"', 'href="/"')
pair = pair.replace("/api/preview/pair/${symbol}/macro_confluence", "/api/pair/${symbol}/macro_confluence")
pair = pair.replace("Tomoko Brain <b>V5.0 Preview</b>", "Tomoko Brain <b>V5.0</b>")

prod_pair_path = os.path.join(root, "ui", "pair.html")
with open(prod_pair_path, "w", encoding="utf-8") as f:
    f.write(pair)

# Also sync duplicate if exists
dup_pair = os.path.join(root, "backend", "static", "pair.html")
if os.path.exists(os.path.dirname(dup_pair)):
    with open(dup_pair, "w", encoding="utf-8") as f:
        f.write(pair)

print("Promotion to production successfully completed!")
