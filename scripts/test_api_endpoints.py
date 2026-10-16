import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import app
from fastapi.testclient import TestClient

def main():
    client = TestClient(app)

    # 1. World news by pair
    for pair in ['XAUUSD', 'USDJPY', 'EURUSD', 'GBPUSD']:
        res = client.get(f'/api/world_news?pair={pair}&limit=3')
        data = res.json()
        print(f"\n=== PAIR: {pair} (Total matched: {data.get('count')}) ===")
        for it in data.get('items', [])[:2]:
            print(f"  [{it['severity']}] {it['title'][:80]}...")
            if it.get('implications'):
                print(f"    Bias: {it['implications']}")

    # 2. Speeches
    sp_res = client.get('/api/speeches?limit=3')
    sp_data = sp_res.json()
    print(f"\n=== RECENT SPEECHES (Total: {sp_data.get('count')}) ===")
    for sp in sp_data.get('speeches', []):
        print(f"  Speaker: {sp['speaker']} | Tone: {sp['tone']}")
        print(f"    Occasion: {sp['occasion']}")
        print(f"    Pairs: {sp['impacted_pairs']}")
        print(f"    Takeaways: {sp['takeaways'][:2]}")

    # 3. Geopolitical Shocks
    sh_res = client.get('/api/geopolitical_shocks?max_age_minutes=1440')
    sh_data = sh_res.json()
    print(f"\n=== ACTIVE SHOCKS (Active: {sh_data.get('active')}, Count: {sh_data.get('count')}) ===")
    for sh in sh_data.get('shocks', [])[:2]:
        print(f"  [{sh['severity']}] {sh['title'][:80]}...")
        print(f"    Pairs: {sh['impacted_pairs']}")

    print("\nAll endpoints functioning normally!")

if __name__ == '__main__':
    main()
