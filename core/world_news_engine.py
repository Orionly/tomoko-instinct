# Autonomous Decoupled World News & Geopolitical Intelligence Engine
#
# Dedicated to unscheduled macro narrative drivers:
# - Geopolitics, military actions, sanctions, wars, maritime chokepoints
# - Government policy changes, tariffs, trade restrictions
# - Central bank & political speech intelligence / executive briefs
# - Severity classification & direct correlation to Tomoko's 7 pairs
# - 100% open RSS / Google News syndication (Zero Finnhub calls)

import os
import re
import json
import time
import html
import logging
import threading
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Any

log = logging.getLogger(__name__)

# Safe import of FinBERT ONNX news classifier
try:
    from core.news_classifier import analyze_news_item
except Exception as _nc_err:
    analyze_news_item = None
    log.warning("[WorldNewsEngine] FinBERT AI News Classifier unavailable: %s", _nc_err)

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "world_news_cache.json"
)

# Open syndicated feeds (100% free, no API keys, no quotas)
RSS_FEEDS = [
    {
        "name": "Reuters / AP Macro (Google News)",
        "category": "geopolitics",
        "url": "https://news.google.com/rss/search?q=geopolitics+OR+war+OR+missile+OR+sanctions+OR+military+OR+airstrike&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Trade, Tariffs & Energy (Google News)",
        "category": "policy",
        "url": "https://news.google.com/rss/search?q=tariff+OR+%22trade+war%22+OR+OPEC+OR+%22crude+oil%22+OR+sanctions+OR+%22oil+pipeline%22+OR+%22gas+pipeline%22&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Central Bank & Leader Speeches (Google News)",
        "category": "speeches",
        "url": "https://news.google.com/rss/search?q=(speech+OR+remarks+OR+%22press+conference%22)+AND+(Warsh+OR+Powell+OR+FOMC+OR+Trump+OR+Bessent+OR+Ueda+OR+Lagarde+OR+%22Bank+of+Japan%22+OR+%22Bank+of+England%22)&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "BBC World News",
        "category": "geopolitics",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
    },
    {
        "name": "BBC Business & Trade",
        "category": "policy",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
    },
    {
        "name": "Al Jazeera English (Middle East / Chokepoints)",
        "category": "geopolitics",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
    },
    {
        "name": "Truth Social Wire (Google News)",
        "category": "policy",
        "url": "https://news.google.com/rss/search?q=%22Truth+Social%22+AND+(Trump+OR+tariff+OR+sanctions+OR+dollar+OR+Fed)&hl=en-US&gl=US&ceid=US:en",
    },
]

# Tracked pair universe
TRACKED_PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]

# Noise / Fluff exclusion filter
EXCLUDED_KEYWORDS = [
    "wnba", "nba", "nfl", "premier league", "champions league", "football",
    "soccer", "baseball", "cricket", "box office", "hollywood", "celebrity",
    "red carpet", "dating", "grammy", "oscar", "pop star", "fashion week",
    "culture war", "gang war", "soap opera", "quarterback", "touchdown",
    "patriots", "packers", "chiefs", "arsenal", "chelsea", "liverpool",
    "manchester", "barcelona", "real madrid", "ufc", "wwe", "boxing"
]

# Keyword definitions for Severity
CRITICAL_KEYWORDS = [
    "declares war", "missile strike", "airstrike", "invasion", "military attack",
    "strait of hormuz", "red sea attack", "drone strike", "nuclear alert",
    "currency intervention", "emergency rate hike", "sovereign default",
    "martial law", "hostage", "blockade", "bank run", "black swan", "all-out war"
]

HIGH_KEYWORDS = [
    "tariff", "tariffs", "trade war", "sanction", "sanctions", "opec cut",
    "oil pipeline", "gas pipeline", "pipeline sabotage", "debt ceiling", "emergency meeting", "embargo",
    "export ban", "mobilization", "retaliatory", "unilateral",
    "warning on yen", "market intervention", "taiwan strait", "south china sea",
    "retaliatory tariff"
]

MEDIUM_KEYWORDS = [
    "central bank", "interest rate", "fed chair", "warsh", "powell", "bessent", "lagarde", "ueda",
    "summit", "bilateral talks", "g7", "g20", "treaty", "legislation",
    "fiscal deficit", "bond yields", "inflation report", "treasury", "budget deficit",
    "presidential race", "white house policy"
]

# Directional action / sentiment modifiers
DE_ESCALATION_PHRASES = [
    "waive", "waives", "waiving", "waived", "waiver",
    "lift sanction", "lifts sanction", "lifting sanction", "lifted sanction", "lift sanctions", "lifts sanctions", "lifting sanctions", "lifted sanctions",
    "suspend", "suspends", "suspending", "suspended", "suspension",
    "ease sanction", "eases sanction", "easing sanction", "eased sanction", "ease sanctions", "eases sanctions", "easing sanctions",
    "delay tariff", "delays tariff", "delaying tariff",
    "pause", "pauses", "pausing", "paused",
    "freeze", "freezes", "shelve", "shelves",
    "exempt", "exemption",
    "on hold", "puts on hold", "put on hold", "halts", "halting",
    "ceasefire", "peace deal", "peace talk", "peace talks", "truce", "thaw", "thaws",
    "lessen strain", "lessens strain", "de-escalat", "end war", "ends war", "sanctions relief",
    "normalize ties", "normalizing ties", "diplomatic breakthrough", "aid deal"
]

ESCALATION_PHRASES = [
    "impose sanction", "imposes sanction", "imposing sanction", "imposed sanction",
    "impose sanctions", "imposes sanctions", "imposing sanctions", "imposed sanctions",
    "fresh sanction", "new sanction", "tighten sanction", "tightens sanction",
    "airstrike", "missile strike", "drone strike", "invasion", "invade", "military attack",
    "slaps tariff", "slap tariff", "impose tariff", "imposes tariff", "tariff threat",
    "retaliatory tariff", "blockade", "nuclear threat", "war declaration", "mobilization"
]

# Speaker identification dictionary
KNOWN_SPEAKERS = {
    "kevin warsh": {"name": "Kevin Warsh", "title": "Federal Reserve Chair", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "warsh": {"name": "Kevin Warsh", "title": "Federal Reserve Chair", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "scott bessent": {"name": "Scott Bessent", "title": "US Treasury Secretary", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "bessent": {"name": "Scott Bessent", "title": "US Treasury Secretary", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "donald trump": {"name": "Donald Trump", "title": "US President", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "trump": {"name": "Donald Trump", "title": "US President", "currency": "USD", "is_former": False, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "jerome powell": {"name": "Jerome Powell", "title": "Former Fed Chair / Fed Governor", "currency": "USD", "is_former": True, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "powell": {"name": "Jerome Powell", "title": "Former Fed Chair / Fed Governor", "currency": "USD", "is_former": True, "pairs": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]},
    "kazuo ueda": {"name": "Kazuo Ueda", "title": "Governor, Bank of Japan", "currency": "JPY", "is_former": False, "pairs": ["USDJPY", "GBPJPY", "EURJPY"]},
    "ueda": {"name": "Kazuo Ueda", "title": "Governor, Bank of Japan", "currency": "JPY", "is_former": False, "pairs": ["USDJPY", "GBPJPY", "EURJPY"]},
    "christine lagarde": {"name": "Christine Lagarde", "title": "President, European Central Bank", "currency": "EUR", "is_former": False, "pairs": ["EURUSD", "EURJPY", "EURGBP"]},
    "lagarde": {"name": "Christine Lagarde", "title": "President, European Central Bank", "currency": "EUR", "is_former": False, "pairs": ["EURUSD", "EURJPY", "EURGBP"]},
    "andrew bailey": {"name": "Andrew Bailey", "title": "Governor, Bank of England", "currency": "GBP", "is_former": False, "pairs": ["GBPUSD", "GBPJPY", "EURGBP"]},
    "bailey": {"name": "Andrew Bailey", "title": "Governor, Bank of England", "currency": "GBP", "is_former": False, "pairs": ["GBPUSD", "GBPJPY", "EURGBP"]},
}


def clean_html_text(raw_html: str) -> str:
    """Strip HTML tags and unescape entities."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    text = text.replace('\xa0', ' ').replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"').replace('\ufffd', "'")
    return re.sub(r"\s+", " ", text).strip()


def parse_rfc_date(date_str: str) -> Optional[datetime]:
    """Parse RFC 822/2822 or ISO date string into UTC datetime."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class WorldNewsEngine:
    """
    Autonomous Decoupled World News, Geopolitical & Speech Intelligence Engine.
    Provides real-time narrative sentiment, severity grading, pair attribution,
    and speech briefs with zero Finnhub dependence.
    """

    def __init__(self, cache_file: str = CACHE_FILE, poll_interval: int = 180):
        self.cache_file = cache_file
        self.poll_interval = poll_interval
        self.items: List[Dict[str, Any]] = []
        self.speeches: List[Dict[str, Any]] = []
        self.last_fetch_time: Optional[datetime] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Load cached state if available
        self._load_cache()

        # Background polling worker
        self._bg_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._bg_thread.start()

    def _load_cache(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            try:
                data = json.loads(content, strict=False)
            except Exception:
                # If corrupt/truncated, perform a clean sync
                log.warning("[WorldNewsEngine] Cache syntax invalid, triggering fresh sync.")
                return False

            cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
            with self._lock:
                loaded_items = data.get("items", [])
                valid_items = []
                for it in loaded_items:
                    if it.get("timestamp", 0) >= cutoff_ts:
                        # Cached items are already enriched; load directly with zero CPU burn
                        valid_items.append(it)
                self.items = valid_items

                loaded_speeches = data.get("speeches", [])
                cleaned_speeches = []
                for sp in loaded_speeches:
                    if sp.get("timestamp", 0) < cutoff_ts:
                        continue
                    sp_title_lower = (sp.get("takeaways", [""])[0] if sp.get("takeaways") else "").lower()
                    if any(bad in sp_title_lower for bad in ["free speech", "hate speech", "speech fight", "creeps out", "savages trump"]):
                        continue
                    # Update speaker title & former official status
                    for k, info in KNOWN_SPEAKERS.items():
                        if info["name"].lower() == sp.get("speaker", "").lower():
                            sp["title"] = info["title"]
                            if info.get("is_former"):
                                sp["tone"] = "COMMENTARY (FORMER OFFICIAL)"
                                sp["occasion"] = f"Retrospective / Commentary ({sp.get('occasion', '').split('(')[-1]}"
                                sp["implications"] = ["Fed: Historical / Former Policy Commentary"]
                            break
                    cleaned_speeches.append(sp)
                self.speeches = cleaned_speeches
                last_str = data.get("last_fetch_time")
                if last_str:
                    self.last_fetch_time = datetime.fromisoformat(last_str)
            log.info("[WorldNewsEngine] Loaded %d news items, %d speeches from 30-day cache in <10ms.",
                     len(self.items), len(self.speeches))
            return True
        except Exception as e:
            log.warning("[WorldNewsEngine] Error loading cache: %s", e)
            return False

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with self._lock:
                data = {
                    "last_fetch_time": self.last_fetch_time.isoformat() if self.last_fetch_time else None,
                    "count": len(self.items),
                    "items": self.items[:3000],  # 30-day cumulative archive (up to 3000 items)
                    "speeches": self.speeches[:150],  # 30-day speech briefs (up to 150 speeches)
                }
            tmp_path = f"{self.cache_file}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Resilient atomic replace on Windows with exponential backoff & direct write fallback
            saved = False
            for attempt in range(4):
                try:
                    os.replace(tmp_path, self.cache_file)
                    saved = True
                    break
                except OSError:
                    if attempt < 3:
                        time.sleep(0.06 * (attempt + 1))
                    else:
                        # Fallback direct write to bypass file-lock collision on destination
                        try:
                            with open(self.cache_file, "w", encoding="utf-8") as f:
                                json.dump(data, f, indent=2, ensure_ascii=False)
                            saved = True
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)
                        except Exception as fallback_err:
                            log.error("[WorldNewsEngine] Direct fallback write failed: %s", fallback_err)
            if not saved:
                log.warning("[WorldNewsEngine] Cache save deferred; will persist on next sync cycle.")
        except Exception as e:
            log.error("[WorldNewsEngine] Error saving cache: %s", e)

    def _worker_loop(self):
        """Periodic background refresh loop."""
        # Initial run shortly after startup
        time.sleep(2)
        while not self._stop_event.is_set():
            try:
                self.sync_feeds()
            except Exception as e:
                log.error("[WorldNewsEngine] Sync cycle encountered error: %s", e)
            self._stop_event.wait(self.poll_interval)

    def stop(self):
        self._stop_event.set()

    def _fetch_truth_social_posts(self) -> List[Dict[str, Any]]:
        """Fetch real-time direct posts from Donald Trump's official Truth Social account."""
        url = "https://truthsocial.com/api/v1/accounts/107780257626128497/statuses?exclude_replies=true&limit=25"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        items = []
        now = datetime.now(timezone.utc)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                statuses = json.loads(resp.read().decode("utf-8", errors="replace"))

            for s in statuses:
                raw_html = s.get("content") or ""
                text = clean_html_text(raw_html)
                if not text or len(text) < 15:
                    continue
                # Skip pure re-tweets/re-truths without original text
                if text.startswith("RT:") or text.startswith("RT @"):
                    continue

                # Format title: first sentence or first 130 chars
                sentences = re.split(r"[.!?]\s+", text)
                first_sentence = sentences[0].strip() if sentences else text
                if len(first_sentence) > 130:
                    title = f"Donald Trump (Truth Social): \"{first_sentence[:125]}...\""
                else:
                    title = f"Donald Trump (Truth Social): \"{first_sentence}\""

                created_str = s.get("created_at")
                pub_dt = parse_rfc_date(created_str) or now
                link = s.get("url") or f"https://truthsocial.com/@realDonaldTrump/{s.get('id', '')}"

                items.append({
                    "title": title,
                    "link": link,
                    "summary": text[:400],
                    "published_at": pub_dt.isoformat(),
                    "timestamp": int(pub_dt.timestamp()),
                    "source": "Truth Social (@realDonaldTrump)",
                    "base_category": "Trade & Policy",
                })
        except Exception as e:
            log.warning("[WorldNewsEngine] Truth Social direct feed skipped: %s", e)
        return items

    def sync_feeds(self, force: bool = False):
        """Fetch all configured RSS feeds and Truth Social direct stream, process, classify, and update cache."""
        now = datetime.now(timezone.utc)
        all_raw_items = []

        # 1. Direct Truth Social Stream (@realDonaldTrump)
        truth_posts = self._fetch_truth_social_posts()
        if truth_posts:
            all_raw_items.extend(truth_posts)
            log.info("[WorldNewsEngine] Ingested %d posts directly from Truth Social.", len(truth_posts))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }

        for feed_cfg in RSS_FEEDS:
            url = feed_cfg["url"]
            source_name = feed_cfg["name"]
            default_cat = feed_cfg["category"]

            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=12) as resp:
                    xml_content = resp.read()
                    root = ET.fromstring(xml_content)

                    # Standard RSS items (<item>)
                    for item_elem in root.findall(".//item"):
                        title = clean_html_text(item_elem.findtext("title") or "")
                        link = (item_elem.findtext("link") or "").strip()
                        desc = clean_html_text(item_elem.findtext("description") or "")
                        pub_str = item_elem.findtext("pubDate") or ""
                        pub_dt = parse_rfc_date(pub_str) or now

                        if not title:
                            continue

                        all_raw_items.append({
                            "title": title,
                            "link": link,
                            "summary": desc[:300] if desc else title,
                            "published_at": pub_dt.isoformat(),
                            "timestamp": int(pub_dt.timestamp()),
                            "source": source_name,
                            "base_category": default_cat,
                        })
            except Exception as e:
                log.warning("[WorldNewsEngine] Failed to poll feed '%s': %s", source_name, e)

        if not all_raw_items:
            log.info("[WorldNewsEngine] No items retrieved from feeds.")
            return

        # Deduplicate and enrich
        processed_items = []
        seen_titles = set()

        # Build set of already-known title keys from memory to skip re-enriching existing items
        with self._lock:
            existing_title_keys = {
                re.sub(r"[^a-zA-Z0-9]", "", it.get("title", "").lower())[:60]
                for it in self.items
                if it.get("title")
            }

        # Sort newest first
        all_raw_items.sort(key=lambda x: x["timestamp"], reverse=True)

        # 30-day retention cutoff
        cutoff_30d = int((now - timedelta(days=30)).timestamp())

        new_speeches = []

        for raw in all_raw_items:
            if raw["timestamp"] < cutoff_30d:
                continue

            # Normalized title key for deduplication
            norm_title = re.sub(r"[^a-zA-Z0-9]", "", raw["title"].lower())[:60]
            if not norm_title or norm_title in seen_titles:
                continue
            seen_titles.add(norm_title)

            # Skip AI inference if story was already processed in previous cycles
            if norm_title in existing_title_keys:
                continue

            # Analyze severity, categories, pairs, and speech potential (fresh items only)
            enriched = self._enrich_item(raw)
            if enriched is None:
                continue
            processed_items.append(enriched)

            # Check for speech brief
            speech_brief = self._extract_speech_brief(enriched)
            if speech_brief:
                new_speeches.append(speech_brief)

        with self._lock:
            # 1. Merge incoming news items with existing items for rolling 30-day accumulation
            existing_items_map = {}
            # Index existing items within 30-day window
            for it in self.items:
                if it.get("timestamp", 0) >= cutoff_30d:
                    k = re.sub(r"[^a-zA-Z0-9]", "", it.get("title", "").lower())[:60]
                    if k:
                        existing_items_map[k] = it

            # Upsert / update with newly processed items
            for it in processed_items:
                if it.get("timestamp", 0) >= cutoff_30d:
                    k = re.sub(r"[^a-zA-Z0-9]", "", it.get("title", "").lower())[:60]
                    if k:
                        existing_items_map[k] = it

            # Sort descending by timestamp (newest first)
            merged_items = sorted(
                existing_items_map.values(),
                key=lambda x: x.get("timestamp", 0),
                reverse=True
            )
            # Retain up to 3000 items in rolling 30-day archive
            self.items = merged_items[:3000]

            # 2. Merge speeches with rolling 30-day retention
            existing_speech_ids = {s.get("id") for s in self.speeches if s.get("timestamp", 0) >= cutoff_30d}
            merged_speeches = [s for s in self.speeches if s.get("timestamp", 0) >= cutoff_30d]
            for sp in new_speeches:
                if sp.get("timestamp", 0) >= cutoff_30d and sp["id"] not in existing_speech_ids:
                    merged_speeches.append(sp)
                    existing_speech_ids.add(sp["id"])

            merged_speeches.sort(key=lambda s: s.get("timestamp", 0), reverse=True)
            self.speeches = merged_speeches[:150]  # retain up to 150 speech briefs across 30 days

            self.last_fetch_time = now

        self._save_cache()
        log.info("[WorldNewsEngine] Feed sync complete: %d items in 30-day archive, %d speech briefs.",
                 len(self.items), len(self.speeches))

    def _enrich_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Classify severity, macro category, directional sentiment, and correlated pairs."""
        text = f"{item['title']} {item['summary']}".lower()

        # Noise / sports / entertainment rejection (word boundaries avoid collisions like nfl in inflation)
        for w in EXCLUDED_KEYWORDS:
            if re.search(r"\b" + re.escape(w) + r"\b", text):
                return None

        # 1. AI Classification (FinBERT ONNX) with graceful fallback
        ai_res = None
        if analyze_news_item:
            try:
                ai_res = analyze_news_item(item.get("title", ""), item.get("summary", ""))
            except Exception as e:
                log.warning("[WorldNewsEngine] AI classification error: %s", e)

        # 2. Severity Classification (AI override or keyword fallback)
        if ai_res and ai_res.get("severity_override"):
            severity = ai_res["severity_override"]
        else:
            is_de_escalation = any(p in text for p in DE_ESCALATION_PHRASES)
            severity = "MONITOR"
            for kw in CRITICAL_KEYWORDS:
                if kw in text and not is_de_escalation:
                    severity = "CRITICAL"
                    break

            if severity != "CRITICAL":
                for kw in HIGH_KEYWORDS:
                    if kw in text:
                        severity = "HIGH"
                        break

            if severity == "MONITOR":
                for kw in MEDIUM_KEYWORDS:
                    if kw in text:
                        severity = "MEDIUM"
                        break

        # 3. Category Classification
        category = "Geopolitics"
        if any(w in text for w in ["speech", "remarks", "press conference", "testimony", "addresses", "fomc presser"]):
            category = "Speeches"
        elif any(w in text for w in ["tariff", "trade war", "sanction", "export ban", "import duty", "trade deal"]):
            category = "Trade & Policy"
        elif any(w in text for w in ["crude", "oil", "opec", "energy", "pipeline", "gas", "tanker"]):
            category = "Energy & Commodities"
        elif any(w in text for w in ["strike", "missile", "war", "military", "defense", "army", "drone", "conflict", "israel", "gaza", "iran", "ukraine", "russia", "red sea", "taiwan"]):
            category = "Geopolitics & Defense"
        elif any(w in text for w in ["earthquake", "tsunami", "flood", "disaster", "outbreak", "bank failure"]):
            category = "Crisis & Black Swan"

        # 4. Correlated Pairs & Implications (AI primary, regional FX supplement)
        impacted_pairs = list(ai_res.get("impacted_pairs", [])) if ai_res else []
        if impacted_pairs == ["GLOBAL"]:
            impacted_pairs = []
        implications = list(ai_res.get("implications", [])) if ai_res else []

        # JPY & Bank of Japan specific catalyst watch
        if any(w in text for w in ["yen", "boj", "bank of japan", "ueda", "tokyo", "japan", "intervention", "nikkei"]):
            for p in ["USDJPY", "GBPJPY", "EURJPY"]:
                if p not in impacted_pairs:
                    impacted_pairs.append(p)
            if len(implications) < 2:
                if any(w in text for w in ["intervention", "verbal warning", "currency check"]):
                    implications.append("USDJPY: FX Intervention Threat (JPY Surge Risk)")
                elif any(w in text for w in ["rate hike", "hike rates", "tightening"]):
                    implications.append("USDJPY: BOJ Rate Hike Catalyst (Bearish USDJPY)")
                elif any(w in text for w in ["hold", "dovish", "cautious", "easy policy"]):
                    implications.append("USDJPY: Dovish BOJ Stance (Yield Gap Intact)")

        # EUR & European Central Bank specific catalyst watch
        if any(w in text for w in ["ecb", "lagarde", "eurozone", "germany", "france", "brussels", "euro"]):
            for p in ["EURUSD", "EURJPY", "EURGBP"]:
                if p not in impacted_pairs:
                    impacted_pairs.append(p)
            if len(implications) < 2:
                if any(w in text for w in ["tariff", "trade war"]) and not any("EURUSD" in imp for imp in implications):
                    implications.append("EURUSD: Export Headwind (Bearish EUR)")
                elif any(w in text for w in ["rate cut", "easing", "rate reduction"]):
                    implications.append("EURUSD: ECB Easing Bias (Yield Drag)")

        # GBP & Bank of England specific catalyst watch
        if any(w in text for w in ["boe", "bank of england", "bailey", "sterling", "pound", "uk budget", "gilt", "london", "britain"]):
            for p in ["GBPUSD", "GBPJPY", "EURGBP"]:
                if p not in impacted_pairs:
                    impacted_pairs.append(p)
            if len(implications) < 2:
                if any(w in text for w in ["rate cut", "easing"]):
                    implications.append("GBPUSD: BOE Rate Cut Easing")
                elif any(w in text for w in ["sticky inflation", "hike", "wage growth"]):
                    implications.append("GBPUSD: Sticky UK Inflation (Rate Support)")

        # USD / Federal Reserve: Distinguish Active Policy vs Former Officials
        is_about_powell = ("powell" in text or "jerome powell" in text) and not any(w in text for w in ["warsh", "kevin warsh"])
        if is_about_powell:
            if not any("Former Official" in imp for imp in implications):
                implications.append("Fed: Former Official Commentary (Non-Actionable)")

        # Default fallback if no specific currency detected
        if not impacted_pairs:
            impacted_pairs = ["GLOBAL"]

        return {
            "id": f"news_{item['timestamp']}_{abs(hash(item['title'])) % 100000}",
            "title": item["title"],
            "summary": item["summary"],
            "link": item["link"],
            "source": item["source"],
            "published_at": item["published_at"],
            "timestamp": item["timestamp"],
            "severity": severity,
            "category": category,
            "impacted_pairs": impacted_pairs,
            "implications": implications[:2],  # top 2 key points
            "sentiment": ai_res.get("sentiment", "neutral") if ai_res else "neutral",
            "sentiment_conf": round(ai_res.get("confidence", 0.5), 3) if ai_res else 0.5,
        }

    def _extract_speech_brief(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Identify if item reports on a speech and generate an executive brief."""
        text = f"{item['title']} {item['summary']}".lower()

        # Reject non-speech colloquial/legal contexts (e.g. 'free speech', 'hate speech' lawsuits)
        if any(w in text for w in ["free speech", "hate speech", "speech fight", "speech battle", "freedom of speech", "speech lawsuit"]):
            return None

        is_speech = any(k in text for k in ["speech", "remarks", "press conference", "testimony", "addresses", "speaks", "delivers speech", "fomc conference", "presser", "briefing"])
        if not is_speech:
            return None

        # Detect speaker
        speaker_info = None
        for key, info in KNOWN_SPEAKERS.items():
            if key in text:
                speaker_info = info
                break

        if not speaker_info:
            return None

        # Determine tone & speaker authority
        if speaker_info.get("is_former"):
            tone = "COMMENTARY (FORMER OFFICIAL)"
            occasion = f"Retrospective / Commentary ({item['source'].split('(')[0].strip()})"
            speech_implications = ["Fed: Historical / Former Policy Commentary"]
        else:
            occasion = f"Official Address / Briefing ({item['source'].split('(')[0].strip()})"
            speech_implications = item.get("implications")
            if not speech_implications:
                en = self._enrich_item(item)
                speech_implications = en.get("implications", []) if en else []
            tone = "BALANCED"
            # Check hawkish / dovish / trade tones with word boundaries to avoid 'ban' in 'bank'
            if any(w in text for w in ["hike", "not in a hurry", "sticky", "inflation persistence", "higher for longer", "resilient", "cautious on cut", "hold rate"]):
                tone = "HAWKISH 🦅"
            elif any(w in text for w in ["rate cut", "cuts rate", "easing", "cool", "slowdown", "rate reduction", "labor softening"]):
                tone = "DOVISH 🕊️"
            elif any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in ["tariff", "tariffs", "levy", "export ban", "trade ban", "retaliation", "sanction", "sanctions", "trade war", "protectionism"]):
                tone = "PROTECTIONIST / TARIFF"

        # Generate bullet takeaways from title and summary sentences
        sentences = re.split(r"[.!?]\s+", item["summary"])
        clean_sentences = [s.strip() for s in sentences if len(s.strip()) > 15]

        # Normalized deduplication so near-identical summary/title sentences don't repeat
        norm_seen = set()
        deduped_takeaways = []
        for t in [item["title"]] + (clean_sentences[:2] if clean_sentences else []):
            clean_t = t.strip()
            comp_key = re.sub(r"[^a-zA-Z0-9]", "", clean_t.lower())
            if comp_key and comp_key not in norm_seen:
                norm_seen.add(comp_key)
                deduped_takeaways.append(clean_t)

        takeaways = deduped_takeaways[:3]

        return {
            "id": f"speech_{abs(hash(speaker_info['name'] + item['title'])) % 1000000}",
            "speaker": speaker_info["name"],
            "title": speaker_info["title"],
            "occasion": occasion,
            "tone": tone,
            "timestamp": item["timestamp"],
            "published_at": item["published_at"],
            "takeaways": takeaways,
            "impacted_pairs": speaker_info["pairs"],
            "implications": speech_implications,
            "link": item["link"],
        }

    # -------------------------------------------------------------------------
    # Public Query Methods
    # -------------------------------------------------------------------------

    def get_news(self, pair: Optional[str] = None, category: Optional[str] = None,
                 severity: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
        """Retrieve filtered news items."""
        with self._lock:
            result = list(self.items)

        if pair and pair.upper() != "ALL":
            p = pair.upper()
            result = [it for it in result if p in it.get("impacted_pairs", [])]

        if category and category.lower() != "all":
            c = category.lower()
            result = [it for it in result if c in it.get("category", "").lower()]

        if severity and severity.upper() != "ALL":
            s = severity.upper()
            result = [it for it in result if it.get("severity") == s]

        return result[:limit]

    def get_speeches(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Retrieve recent speech executive briefs."""
        with self._lock:
            return list(self.speeches)[:limit]

    def get_active_shocks(self, max_age_minutes: int = 60) -> List[Dict[str, Any]]:
        """Retrieve active CRITICAL or HIGH severity geopolitical shocks within max_age."""
        now_ts = int(datetime.now(timezone.utc).timestamp())
        cutoff_ts = now_ts - (max_age_minutes * 60)

        with self._lock:
            shocks = [
                it for it in self.items
                if it.get("severity") in ["CRITICAL", "HIGH"]
                and it.get("timestamp", 0) >= cutoff_ts
            ]
        return shocks


# Singleton instance helper
_world_news_engine: Optional[WorldNewsEngine] = None


def get_world_news_engine() -> WorldNewsEngine:
    global _world_news_engine
    if _world_news_engine is None:
        _world_news_engine = WorldNewsEngine()
    return _world_news_engine
