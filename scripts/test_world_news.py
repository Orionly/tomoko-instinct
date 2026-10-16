import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.world_news_engine import WorldNewsEngine

def test_engine():
    print("Initializing WorldNewsEngine...")
    engine = WorldNewsEngine(poll_interval=300)
    engine.sync_feeds(force=True)

    print(f"\nIngested Items Count: {len(engine.items)}")
    print(f"Ingested Speeches Count: {len(engine.speeches)}")

    news = engine.get_news(limit=5)
    for i, it in enumerate(news):
        print(f"\n--- Item #{i+1} ---")
        print(f"Severity: {it['severity']} | Category: {it['category']}")
        print(f"Pairs: {it['impacted_pairs']}")
        print(f"Title: {it['title']}")
        if it.get('implications'):
            print(f"Implications: {it['implications']}")

    speeches = engine.get_speeches(limit=3)
    print(f"\n--- Speeches Sample ({len(speeches)}) ---")
    for sp in speeches:
        print(f"Speaker: {sp['speaker']} | Tone: {sp['tone']}")
        print(f"Occasion: {sp['occasion']}")
        print(f"Takeaways: {sp['takeaways']}")
        print(f"Pairs: {sp['impacted_pairs']}")

    shocks = engine.get_active_shocks(max_age_minutes=1440)
    print(f"\nActive Shocks (last 24h): {len(shocks)}")
    if shocks:
        print(f"Top Shock: [{shocks[0]['severity']}] {shocks[0]['title']}")

    engine.stop()
    print("\nTest finished successfully!")

if __name__ == "__main__":
    test_engine()
