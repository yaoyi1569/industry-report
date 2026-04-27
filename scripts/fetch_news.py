# ============================================================
# INDUSTRY MONITORING SYSTEM V3 (CONSULTING-GRADE UPGRADE)
# Added: multi-source data (RSS + official sites + patents)
# Core goal: fix data source weakness
# ============================================================

import re
import requests
import json
import os
from datetime import datetime, timedelta

# ============================================================
# CONFIG
# ============================================================
MAX_NEWS_AGE_DAYS = 50
MAX_PATENT_AGE_DAYS = 30
DATA_PATH = "./data/news_data.json"

# ============================================================
# KEYWORDS
# ============================================================
MACHINE_TERMS = ['加工機','包装機','パレタイザー','machine','packaging','automation']
HYGIENE_TERMS = ['おむつ','ナプキン','生理','失禁']
TISSUE_TERMS = ['ティシュー','トイレット','家庭紙']
PATENT_TERMS = ['特許','patent']

# ============================================================
# UTIL
# ============================================================
def today():
    return datetime.utcnow().strftime('%Y-%m-%d')

# ============================================================
# DATE EXTRACTION
# ============================================================
def extract_real_date(text):
    match = re.search(r'(20\d{2})[/-](\d{1,2})[/-](\d{1,2})', text)
    if match:
        y,m,d = match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return None

# ============================================================
# RELEVANCE
# ============================================================
def is_relevant(text):
    t = text.lower()

    if any(k.lower() in t for k in MACHINE_TERMS):
        return True

    if any(k.lower() in t for k in HYGIENE_TERMS + TISSUE_TERMS):
        return True

    return False

# ============================================================
# CATEGORY
# ============================================================
def classify(text):
    t = text.lower()

    if any(k in t for k in HYGIENE_TERMS):
        return 'HYGIENE'

    if any(k in t for k in MACHINE_TERMS):
        return 'MACHINE'

    if any(k in t for k in TISSUE_TERMS):
        return 'TISSUE'

    if any(k in t for k in PATENT_TERMS):
        return 'PATENT'

    return 'OTHER'

# ============================================================
# 1️⃣ RSS SOURCE
# ============================================================
def fetch_rss(query):
    url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
    xml = requests.get(url).text
    items = re.findall(r'<item>(.*?)</item>', xml, re.S)

    results = []
    for item in items:
        title = re.search(r'<title>(.*?)</title>', item)
        link = re.search(r'<link>(.*?)</link>', item)
        desc = re.search(r'<description>(.*?)</description>', item)

        results.append({
            'title': title.group(1) if title else '',
            'url': link.group(1) if link else '',
            'snippet': desc.group(1) if desc else ''
        })
    return results

# ============================================================
# 2️⃣ OFFICIAL SITE SCRAPER (KEY UPGRADE)
# ============================================================
OFFICIAL_SITES = [
    "https://www.optima-packaging.com/en/news",
    "https://www.fameccanica.com/en/news/",
    "https://www.zuiko.co.jp/news/",
]

def fetch_official_sites():
    results = []

    for url in OFFICIAL_SITES:
        try:
            html = requests.get(url, timeout=10).text

            titles = re.findall(r'>([^<>]{20,120})<', html)

            for t in titles[:20]:
                if is_relevant(t):
                    results.append({
                        'title': t.strip(),
                        'url': url,
                        'snippet': t.strip()
                    })
        except:
            continue

    return results

# ============================================================
# 3️⃣ PATENT SOURCE (SIMPLIFIED)
# ============================================================
def fetch_patents():
    query = "おむつ 特許"
    items = fetch_rss(query)

    results = []
    for it in items:
        text = it['title'] + it['snippet']

        if '特許' not in text:
            continue

        real_date = extract_real_date(text)
        if real_date:
            d = datetime.strptime(real_date, '%Y-%m-%d')
            if d < datetime.utcnow() - timedelta(days=MAX_PATENT_AGE_DAYS):
                continue

        results.append(it)

    return results

# ============================================================
# MAIN FETCH
# ============================================================
def fetch_all():
    queries = [
        'おむつ 技術',
        'ナプキン 新製品',
        '包装機 自動化',
        'パレタイザー ロボット'
    ]

    results = []

    # RSS
    for q in queries:
        for it in fetch_rss(q):
            text = it['title'] + it['snippet']
            if not is_relevant(text):
                continue

            real_date = extract_real_date(text)
            date = real_date if real_date else today()

            results.append({
                'title': it['title'],
                'summary': it['snippet'],
                'date': date,
                'category': classify(text),
                'url': it['url']
            })

    # OFFICIAL
    for it in fetch_official_sites():
        text = it['title']
        results.append({
            'title': it['title'],
            'summary': it['snippet'],
            'date': today(),
            'category': classify(text),
            'url': it['url']
        })

    # PATENT
    for it in fetch_patents():
        text = it['title']
        results.append({
            'title': it['title'],
            'summary': it['snippet'],
            'date': today(),
            'category': 'PATENT',
            'url': it['url']
        })

    return results

# ============================================================
# SIMPLE SCORING
# ============================================================
def score(item):
    t = item['title']

    s = 50
    if '投資' in t: s += 20
    if '特許' in t: s += 25
    if '新製品' in t: s += 15

    return min(s,100)

# ============================================================
# PIPELINE
# ============================================================
def run():
    items = fetch_all()

    for it in items:
        it['score'] = score(it)

    items.sort(key=lambda x: x['score'], reverse=True)

    os.makedirs("./data", exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"DONE: {len(items)} items")

# ============================================================
# ENTRY
# ============================================================
if __name__ == "__main__":
    run()
