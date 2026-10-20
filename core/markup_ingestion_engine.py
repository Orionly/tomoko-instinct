import os
import sys
import json
import base64
import shutil
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config_local

MYT = timezone(timedelta(hours=8))
MARKUPS_DIR = os.path.join(ROOT, "markups")
ARCHIVE_DIR = os.path.join(MARKUPS_DIR, "archive")
DATA_DIR = os.path.join(ROOT, "data")
PROFILE_FILE = os.path.join(DATA_DIR, "trader_profile.json")

def _get_api_key() -> str:
    return getattr(config_local, "GEMINI_API_KEY", "")

def _ensure_dirs():
    os.makedirs(MARKUPS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

def load_trader_profile() -> Dict[str, Any]:
    _ensure_dirs()
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "version": "1.0",
        "trader_style": {
            "primary_symbol": "XAUUSD",
            "box_height_atr_ratio_range": [0.40, 0.95],
            "min_displacement_atr": 1.2,
            "midline_type": "CONSEQUENT_ENCROACHMENT_50",
            "anchor_rule": "WICK_TO_BODY_CLUSTERS"
        },
        "active_session": {},
        "history": []
    }

def save_trader_profile(profile: Dict[str, Any]):
    _ensure_dirs()
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

def parse_chart_image(img_path: str) -> Optional[Dict[str, Any]]:
    api_key = _get_api_key()
    if not api_key:
        print("[ERROR] GEMINI_API_KEY is missing from config_local.py")
        return None

    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(img_path) as im:
            max_dim = 1440
            if max(im.size) > max_dim:
                scale = max_dim / max(im.size)
                new_size = (int(im.size[0] * scale), int(im.size[1] * scale))
                im = im.resize(new_size, Image.Resampling.LANCZOS)
            buf = BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=88)
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            mime_type = "image/jpeg"
    except Exception as e:
        try:
            with open(img_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
            ext = os.path.splitext(img_path)[1].lower().replace(".", "")
            mime_type = "image/png" if ext == "png" else "image/jpeg"
        except Exception as e2:
            print(f"[ERROR] Failed to read {img_path}: {e2}")
            return None

    # File modification time in MYT
    mtime = os.path.getmtime(img_path)
    file_dt = datetime.fromtimestamp(mtime, tz=MYT)
    file_dt_str = file_dt.strftime("%Y-%m-%d %H:%M:%S MYT")

    prompt = f"""You are an expert institutional trading chart analyst.
Analyze this TradingView chart screenshot and extract the trader's marked zones.
File saved timestamp: {file_dt_str}

Look carefully at:
1. The symbol (e.g. XAUUSD) and timeframe (e.g. 15m, 1h, 4h).
2. The current market price displayed in the blue badge on the right axis.
3. The horizontal rectangular zones marked on the chart.
   For each zone:
   - Identify whether it sits ABOVE or BELOW the current market price (Resistance/Supply vs Support/Demand).
   - Read the exact top price boundary (high) and bottom price boundary (low) using the right price axis numbers.
   - Note the dashed purple midline (50% Consequent Encroachment) if present.
   - Order the zones strictly from highest price to lowest price.

Respond ONLY with valid JSON in this exact structure:
{{
  "symbol": "XAUUSD",
  "timeframe": "M15",
  "live_price_on_chart": 4177.55,
  "chart_date": "{file_dt.strftime('%Y-%m-%d')}",
  "zones": [
    {{
      "name": "Upper Zone 2",
      "type": "RESISTANCE",
      "high": 4198.0,
      "low": 4197.0,
      "midline": 4197.5,
      "notes": "Upper sweep resistance"
    }},
    {{
      "name": "Upper Zone 1",
      "type": "RESISTANCE",
      "high": 4187.0,
      "low": 4184.0,
      "midline": 4185.5,
      "notes": "Immediate intraday resistance shelf"
    }},
    {{
      "name": "Lower Zone 1",
      "type": "SUPPORT",
      "high": 4166.0,
      "low": 4163.5,
      "midline": 4164.75,
      "notes": "Immediate intraday demand shelf"
    }},
    {{
      "name": "Lower Zone 2",
      "type": "SUPPORT",
      "high": 4158.0,
      "low": 4155.5,
      "midline": 4156.75,
      "notes": "Session base launchpad"
    }}
  ]
}}"""

    candidate_models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-flash"]
    payload = json.dumps({
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": img_b64}}
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"}
    }).encode("utf-8")

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                parsed["file_timestamp"] = file_dt_str
                parsed["source_file"] = os.path.basename(img_path)
                parsed["model_used"] = model_name
                return parsed
        except Exception as e:
            print(f"[!] Model {model_name} returned error: {e}. Trying fallback...")
            continue

    print(f"[ERROR] All Gemini Vision candidate models failed to parse {img_path}.")
    return None

def ingest_pending_markups() -> List[Dict[str, Any]]:
    """Scan markups/ folder, parse new images, log to trader profile, and archive them."""
    _ensure_dirs()
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    candidates = []

    for f in os.listdir(MARKUPS_DIR):
        full_path = os.path.join(MARKUPS_DIR, f)
        if os.path.isfile(full_path):
            ext = os.path.splitext(f)[1].lower()
            if ext in image_exts:
                candidates.append(full_path)

    if not candidates:
        return []

    profile = load_trader_profile()
    today_str = datetime.now(MYT).strftime("%Y-%m-%d")
    today_archive_dir = os.path.join(ARCHIVE_DIR, today_str)
    os.makedirs(today_archive_dir, exist_ok=True)

    results = []
    for img_path in candidates:
        fname = os.path.basename(img_path)
        print(f"[*] Ingesting chart markup: {fname}...")
        parsed = parse_chart_image(img_path)
        if parsed:
            results.append(parsed)
            # Update active session by timeframe
            tf = parsed.get("timeframe", "M15").upper()
            if "active_session" not in profile:
                profile["active_session"] = {}
            profile["active_session"][tf] = parsed
            profile["history"].append(parsed)

            # Archive file
            dest_path = os.path.join(today_archive_dir, fname)
            # Avoid overwriting existing archive
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(fname)
                timestamp = int(datetime.now(MYT).timestamp())
                dest_path = os.path.join(today_archive_dir, f"{base}_{timestamp}{ext}")
            shutil.move(img_path, dest_path)
            print(f"[+] Successfully archived {fname} -> {dest_path}")
        else:
            print(f"[-] Could not parse {fname}. Leaving in markups/ for inspection.")

    save_trader_profile(profile)
    return results

if __name__ == "__main__":
    res = ingest_pending_markups()
    if not res:
        print("No pending chart markups found in markups/ folder.")
    else:
        print(f"Successfully ingested {len(res)} chart markup(s)!")
        for r in res:
            print(f"  {r.get('symbol')} {r.get('timeframe')}: {len(r.get('zones', []))} zones mapped.")
