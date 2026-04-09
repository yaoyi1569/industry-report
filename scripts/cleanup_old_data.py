import json
import os
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CUTOFF_DAYS = 90


def main():
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    if not os.path.exists(data_path):
        print(f'File not found: {data_path}')
        return

    with open(data_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    # Support both legacy array format and new {"last_updated":..., "items":[...]} format
    if isinstance(raw, list):
        items = raw
        highlights = []
        last_updated = None
    else:
        items = raw.get('items', [])
        highlights = raw.get('highlights', [])
        last_updated = raw.get('last_updated')

    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    before = len(items)
    items = [item for item in items if item.get('date', '9999-99-99') >= cutoff_str]
    removed = before - len(items)

    payload = {
        'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'highlights': highlights,
        'items': items,
    }
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f'Removed {removed} items older than {CUTOFF_DAYS} days (before {cutoff_str}).')
    print(f'Remaining: {len(items)} items.')


if __name__ == '__main__':
    main()
