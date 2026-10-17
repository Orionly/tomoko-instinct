import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.news_classifier import analyze_news_item

headlines = [
    "Trump Gives Putin an 'Economic Lifeline,' Waives Sanctions on Russia - Common Dreams",
    "Zelensky blasts 'unfortunate' decision to pause Russia sanctions as Moscow hits power plants - New York Post",
    "Trump Stalls Russia Sanctions, Says It's in National Interest - Newser",
    "Ukraine Hits Russian Drone Factory in Rostov, Bryansk Depot - Kyiv Post"
]

for h in headlines:
    print("=" * 60)
    print("Headline:", h)
    res = analyze_news_item(h)
    print("Sentiment:", res["sentiment"], f"{res['confidence']*100:.1f}%", res["probabilities"])
    print("Impacted Pairs:", res["impacted_pairs"])
    print("Implications:", res["implications"])
    print("Severity:", res["severity_override"])
