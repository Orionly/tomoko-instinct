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
CURRENT_WEEK_ARCHIVE = os.path.join(ARCHIVE_DIR, "current_week")
DATA_DIR = os.path.join(ROOT, "data")
PROFILE_FILE = os.path.join(DATA_DIR, "trader_profile.json")

def _get_api_key() -> str:
    return getattr(config_local, "GEMINI_API_KEY", "")

def _ensure_dirs():
    os.makedirs(MARKUPS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(CURRENT_WEEK_ARCHIVE, exist_ok=True)
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
Analyze this TradingView chart screenshot and extract the trader's marked zones along with the underlying characteristics of WHY each zone was drawn.
File saved timestamp: {file_dt_str}

Look carefully at:
1. The symbol (e.g. XAUUSD) and timeframe. Use the candle countdown timer displayed immediately below the blue market price pill on the right price axis:
   - If the countdown timer shows hours (e.g. "03:36:01" or > 60 minutes), the timeframe is strictly "H4".
   - If the countdown timer is between 15:01 and 60:00 (e.g. "33:24"), the timeframe is strictly "H1".
   - If the countdown timer is <= 15:00 (e.g. "02:06"), the timeframe is strictly "M15".
   - If no timer is visible, determine timeframe from the candle density and interval.
2. The current market price displayed in the blue badge on the right axis.
3. The horizontal rectangular zones marked on the chart.
   For each zone:
   - Identify whether it sits ABOVE or BELOW the current market price (Resistance/Supply vs Support/Demand).
   - Read the exact top price boundary (high) and bottom price boundary (low) using the right price axis numbers.
   - Note the dashed purple midline (50% Consequent Encroachment) if present.
   - Order the zones strictly from highest price to lowest price.
   - Determine WHY the zone was marked (Zone Marking Characteristics):
     * anchor_type: Choose best fit: "WICK_CLUSTER" (multiple rejection wicks), "BODY_EXTREME" (candle close edge), "ORDER_BLOCK" (last opposite candle before aggressive move), "IMBALANCE_FVG" (fair value gap/unmitigated imbalance), or "FRACTAL_SWING_TIP".
     * structural_role: Choose best fit: "LIQUIDITY_SWEEP_ORIGIN", "BREAK_OF_STRUCTURE_ORIGIN", "SWAP_FLIP" (broken S/R flipped), "SESSION_HIGH_LOW", or "RANGE_BOUNDARY".
     * prior_retest_count: Estimated retests before this snapshot (0 = fresh/untested, 1, 2, or 3+).
     * why_marked: A concise, precise institutional rationale explaining why the trader placed this box here.

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
      "box_height_pts": 1.0,
      "anchor_type": "WICK_CLUSTER",
      "structural_role": "LIQUIDITY_SWEEP_ORIGIN",
      "prior_retest_count": 0,
      "why_marked": "Asian session high sweep origin prior to aggressive London drop",
      "notes": "Upper sweep resistance"
    }},
    {{
      "name": "Upper Zone 1",
      "type": "RESISTANCE",
      "high": 4187.0,
      "low": 4184.0,
      "midline": 4185.5,
      "box_height_pts": 3.0,
      "anchor_type": "ORDER_BLOCK",
      "structural_role": "BREAK_OF_STRUCTURE_ORIGIN",
      "prior_retest_count": 1,
      "why_marked": "Bearish displacement origin that broke internal structure",
      "notes": "Immediate intraday resistance shelf"
    }},
    {{
      "name": "Lower Zone 1",
      "type": "SUPPORT",
      "high": 4166.0,
      "low": 4163.5,
      "midline": 4164.75,
      "box_height_pts": 2.5,
      "anchor_type": "BODY_EXTREME",
      "structural_role": "SWAP_FLIP",
      "prior_retest_count": 1,
      "why_marked": "Prior ceiling broken during Asia open now acting as support",
      "notes": "Immediate intraday demand shelf"
    }},
    {{
      "name": "Lower Zone 2",
      "type": "SUPPORT",
      "high": 4158.0,
      "low": 4155.5,
      "midline": 4156.75,
      "box_height_pts": 2.5,
      "anchor_type": "ORDER_BLOCK",
      "structural_role": "RANGE_BOUNDARY",
      "prior_retest_count": 0,
      "why_marked": "Clean demand base launchpad from previous session accumulation",
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

def purge_weekly_archive() -> int:
    """
    Purges all archived screenshots in `markups/archive/current_week/` and legacy date folders.
    Triggered after weekend fine-tuning is completed so disk space is reclaimed,
    while `data/trader_profile.json` retains all cumulative numerical and characteristic data.
    """
    _ensure_dirs()
    purged_count = 0
    if os.path.exists(CURRENT_WEEK_ARCHIVE):
        for f in os.listdir(CURRENT_WEEK_ARCHIVE):
            fp = os.path.join(CURRENT_WEEK_ARCHIVE, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    purged_count += 1
                elif os.path.isdir(fp):
                    shutil.rmtree(fp)
                    purged_count += 1
            except Exception as e:
                print(f"[!] Error deleting {fp}: {e}")
        print(f"[PURGE] Weekly archive cleared: {purged_count} files removed from {CURRENT_WEEK_ARCHIVE}.")

    # Clean legacy YYYY-MM-DD subfolders in ARCHIVE_DIR
    for item in os.listdir(ARCHIVE_DIR):
        if item == "current_week":
            continue
        item_path = os.path.join(ARCHIVE_DIR, item)
        if os.path.isdir(item_path):
            try:
                shutil.rmtree(item_path)
                purged_count += 1
            except Exception:
                pass
    return purged_count

def get_pending_markups(max_files: int = 9) -> List[str]:
    """Return chart images in markups/ folder waiting to be ingested (up to max_files, newest first)."""
    _ensure_dirs()
    image_exts = {".png", ".jpg", ".jpeg", ".webp"}
    candidates = []
    for f in os.listdir(MARKUPS_DIR):
        full_path = os.path.join(MARKUPS_DIR, f)
        if os.path.isfile(full_path) and os.path.splitext(f)[1].lower() in image_exts:
            candidates.append(full_path)
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[:max_files]


def record_session_zones(
    session_data: Dict[str, Any],
    file_paths: Optional[List[str]] = None,
    archive_files: bool = True
) -> Dict[str, Any]:
    """
    Direct local recording of trader zones and characteristics (0 external API calls used).
    Updates active_session, appends to history in data/trader_profile.json,
    and moves processed files to markups/archive/current_week/.
    Files remain preserved in current_week until purged on the weekend post-fine-tune.
    """
    _ensure_dirs()
    profile = load_trader_profile()
    today_str = datetime.now(MYT).strftime("%Y-%m-%d")

    if "active_session" not in profile:
        profile["active_session"] = {}

    for tf, data in session_data.items():
        tf_key = tf.upper()
        data["chart_date"] = today_str
        profile["active_session"][tf_key] = data
        profile["history"].append(data)

    save_trader_profile(profile)

    # Move processed files to current_week archive
    if archive_files and file_paths:
        for img_path in file_paths:
            if os.path.exists(img_path):
                fname = os.path.basename(img_path)
                dest_path = os.path.join(CURRENT_WEEK_ARCHIVE, fname)
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(fname)
                    timestamp = int(datetime.now(MYT).timestamp())
                    dest_path = os.path.join(CURRENT_WEEK_ARCHIVE, f"{base}_{timestamp}{ext}")
                shutil.move(img_path, dest_path)

    return profile


def ingest_pending_markups(max_files: int = 9) -> List[Dict[str, Any]]:
    """
    Scan markups/ folder, sort by creation time (newest first), take pending snapshots,
    parse them with characteristics, log to trader profile, and move to markups/archive/current_week/.
    Does NOT purge archives during the week; all week's snapshots are preserved until weekend fine-tuning.
    """
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

    # Sort descending by Windows creation / modification time (newest first)
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    if len(candidates) > max_files:
        print(f"[*] Found {len(candidates)} snapshots in markups/. Taking {max_files} most recent...")
        candidates = candidates[:max_files]

    profile = load_trader_profile()

    results = []
    for img_path in candidates:
        fname = os.path.basename(img_path)
        print(f"[*] Ingesting chart markup: {fname}...")
        parsed = parse_chart_image(img_path)
        if parsed:
            results.append(parsed)
            # Update active session by timeframe (H4, H1, M15)
            tf = parsed.get("timeframe", "M15").upper()
            if "active_session" not in profile:
                profile["active_session"] = {}
            profile["active_session"][tf] = parsed
            profile["history"].append(parsed)

            # Move out of markups/ into current_week archive immediately so dropzone is clean
            dest_path = os.path.join(CURRENT_WEEK_ARCHIVE, fname)
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(fname)
                timestamp = int(datetime.now(MYT).timestamp())
                dest_path = os.path.join(CURRENT_WEEK_ARCHIVE, f"{base}_{timestamp}{ext}")
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
