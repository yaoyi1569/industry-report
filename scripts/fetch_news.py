import feedparser
import requests
from datetime import datetime
import json
import os

# 配置新闻源
NEWS_SOURCES = {
    '日用品企业': [
        'https://news.yahoo.co.jp/rss/topics/business.xml',
    ],
    '制造业': [
        'https://www.nikkan.co.jp/feed',
    ],
    '环保规制': [
        'https://www.meti.go.jp/rss',
    ]
}

def fetch_news():
    """从RSS和网络API采集新闻"""
    all_news = []
    
    for category, urls in NEWS_SOURCES.items():
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:  # 每个源取前5条
                    all_news.append({
                        'title': entry.title if hasattr(entry, 'title') else 'No title',
                        'link': entry.link if hasattr(entry, 'link') else '',
                        'date': entry.published if hasattr(entry, 'published') else datetime.now().isoformat(),
                        'category': category,
                        'summary': entry.summary[:200] if hasattr(entry, 'summary') else '',
                        'source': url.split('/')[2]  # 提取域名作为源
                    })
            except Exception as e:
                print(f"Error fetching {url}: {e}")
    
    return all_news

if __name__ == '__main__':
    # 创建data目录
    os.makedirs('data', exist_ok=True)
    
    news = fetch_news()
    with open('data/news_raw.json', 'w', encoding='utf-8') as f:
        json.dump(news, f, ensure_ascii=False, indent=2)
    print(f"Fetched {len(news)} news items")