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

    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    # New date-bucket format: {last_updated, highlights, dates, patents}
    if 'dates' in raw:
        dates = raw.get('dates', {})
        patents = raw.get('patents', [])
        highlights = raw.get('highlights', [])
        last_updated = raw.get('last_updated')

        removed = 0
        new_dates = {}
        for date_str, items in dates.items():
            if date_str < cutoff_str:
                for item in items:
                    print(f'  [PRUNED-OLD-NEWS] {item.get("title", "")[:60]} ({date_str})')
                    removed += 1
            else:
                new_dates[date_str] = items

        payload = {
            'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'highlights': highlights,
            'dates': new_dates,
            'patents': patents,  # NEVER pruned
        }
    else:
        # Legacy {last_updated, highlights, items} or bare-array format
        if isinstance(raw, list):
            items = raw
            highlights = []
            last_updated = None
        else:
            items = raw.get('items', [])
            highlights = raw.get('highlights', [])
            last_updated = raw.get('last_updated')

        kept = []
        removed = 0
        for item in items:
            if item.get('permanent_record'):
                kept.append(item)
            elif item.get('date', '9999-99-99') >= cutoff_str:
                kept.append(item)
            else:
                print(f'  [PRUNED-OLD-NEWS] {item.get("title", "")[:60]} ({item.get("date", "")})')
                removed += 1

        # Migrate to new format on save
        dates_dict = {}
        patents = []
        for item in kept:
            if item.get('permanent_record'):
                patents.append(item)
            else:
                d = item.get('date', 'unknown')
                dates_dict.setdefault(d, []).append(item)

        payload = {
            'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'highlights': highlights,
            'dates': dates_dict,
            'patents': patents,
        }

    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    total_regular = sum(len(v) for v in payload['dates'].values())
    print(f'Removed {removed} items older than {CUTOFF_DAYS} days (before {cutoff_str}).')
    print(f'Regular items remaining: {total_regular}. Patents preserved: {len(payload["patents"])} (permanent_record).')

    # ── Prune permanent_vault.json (90-day retention) ─────────────────────────
    vault_path = os.path.join(os.path.dirname(data_path), 'permanent_vault.json')
    if os.path.exists(vault_path):
        with open(vault_path, 'r', encoding='utf-8') as f:
            vault = json.load(f)
        vault_kept = [
            item for item in vault
            if item.get('date', '9999-99-99') >= cutoff_str
        ]
        vault_removed = len(vault) - len(vault_kept)
        if vault_removed > 0:
            with open(vault_path, 'w', encoding='utf-8') as f:
                json.dump(vault_kept, f, ensure_ascii=False, indent=2)
            print(
                f'Pruned {vault_removed} items from permanent_vault.json '
                f'older than {CUTOFF_DAYS} days ({len(vault_kept)} retained).'
            )
        else:
            print(f'permanent_vault.json: {len(vault_kept)} items retained (none pruned).')


if __name__ == '__main__':
    main()
