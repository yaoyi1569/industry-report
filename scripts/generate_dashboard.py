#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复版本 - GENERATE_DASHBOARD.PY
包含所有修复：日期过滤、AI验证、动态降分
"""

import json
import os
import re
import time
import requests
import pytz
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MAX_RETRIES = 3
RETRY_SCORE_THRESHOLD = 80

_OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
_OPENROUTER_MODEL = 'deepseek/deepseek-chat'
_OPENROUTER_MAX_RETRIES = 5

_LENIENT_THRESHOLD_DEFAULT = 15

# ============================================================
# 修复 1：严格的专利日期过滤
# ============================================================
def filter_old_patents_from_items(items, max_age_days=30):
    """严格过滤超过max_age_days的专利/学术条目"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_date = cutoff.date()
    
    filtered = []
    removed_count = 0
    
    for item in items:
        is_patent_or_academic = (
            item.get('category_id') == '⑦' or 
            item.get('info_type') == '特許' or 
            item.get('is_academic') or
            item.get('permanent_record')
        )
        
        if not is_patent_or_academic:
            filtered.append(item)
            continue
        
        date_str = item.get('date')
        if not date_str:
            filtered.append(item)
            continue
        
        try:
            item_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            days_old = (cutoff_date - item_date).days
            
            if days_old <= 0:
                filtered.append(item)
            else:
                removed_count += 1
                print(f'  [PATENT-OLD] {item.get("title", "")[:60]} ({date_str}, {days_old} days old)')
        except Exception:
            filtered.append(item)
    
    if removed_count > 0:
        print(f'[DATE-FILTER] Removed {removed_count} old patents')
    
    return filtered

# ============================================================
# Helper Functions
# ============================================================
def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()

def _openrouter_generate(prompt):
    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY not set')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'HTTP-Referer': 'https://github.com/industry-analysis-yao/industry-report',
        'X-Title': 'Industry Analysis Report',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': _OPENROUTER_MODEL,
        'messages': [{'role': 'user', 'content': prompt}],
    }

    for attempt in range(_OPENROUTER_MAX_RETRIES):
        try:
            resp = requests.post(_OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f'  [OPENROUTER] Attempt {attempt + 1}/{_OPENROUTER_MAX_RETRIES} failed: {e}')
            if attempt < _OPENROUTER_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError('OpenRouter failed after all retries')

# ============================================================
# AGENT A — Summarizer
# ============================================================
def ai_summarize(title, snippet, company, api_key=None, retry_feedback=None, lenient_mode=False):
    clean_snippet = (snippet or '').strip()
    COMPETITOR_COMPANIES = [
        'ユニ・チャーム', 'unicharm', '花王', 'p&g', 'ライオン',
        'essity', 'kimberly', 'キンバリー', 'vinda', '维达', 'hengan', '恒安',
    ]
    is_competitor = any(kw in (company or '').lower() for kw in COMPETITOR_COMPANIES)
    min_snippet_len = 10 if (is_competitor or lenient_mode) else 30
    if len(clean_snippet) < min_snippet_len or clean_snippet == title.strip():
        print(f'  [SKIP paywall] {title[:60]}')
        return False, None
    try:
        retry_section = ''
        if retry_feedback:
            retry_section = f'\n\n【前回審査からのフィードバック】\n{retry_feedback}\n'
        
        prompt = (
            'あなたは家庭紙・衛生用品業界の専門記者です。\n\n'
            '【ステップ1: 関連性チェック】\n'
            'この記事が「家庭紙・ティッシュ・トイレットペーパー・おむつ・ナプキン・衛生用品・不織布・'
            '吸収体加工機・包装機・パレタイザー・学術論文・特許」に直接関連する業界ニュースかどうかを判断してください。\n'
            '関連しない場合は「IRRELEVANT」とだけ出力。\n'
            '競合他社（ユニ・チャーム・花王・P&G等）のニュースはスニペットが短くても保持してください。\n\n'
            '【ステップ2: 要約】\n'
            '業界関連ニュースの場合は、「誰が・いつ・何を・どのように・数値」が明確に伝わる、80〜150字で要約。\n'
            'タイトルの言い換え禁止。本文から新しい情報を付加すること。\n'
            f'{retry_section}\n'
            f'会社名: {company}\n'
            f'タイトル: {title}\n'
            f'本文スニペット: {clean_snippet}\n\n'
            '出力（「IRRELEVANT」またはサマリー日本語のみ）:'
        )
        text = _openrouter_generate(prompt)
        if text and text.strip().upper() == 'IRRELEVANT':
            return False, None
        return True, (text or '')[:300]
    except Exception as e:
        print(f'  OpenRouter error: {e}')
        return True, 'AI Summary Pending'

# ============================================================
# AGENT B — Auditor (with date verification)
# ============================================================
def audit_item(title, summary, company, date_str=None, api_key=None):
    """修复 2：添加日期验证"""
    
    date_warning = ""
    date_quality_penalty = 0
    
    if date_str:
        try:
            item_date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            days_old = (datetime.now().date() - item_date).days
            
            if days_old > 30:
                date_warning = (
                    f'\n\n【警告：このニュースは{days_old}日前のものです（{date_str}）】\n'
                    f'30日以上前の情報の場合、スコアを大幅に低下させてください。'
                )
                if days_old > 60:
                    date_quality_penalty = 40
                elif days_old > 30:
                    date_quality_penalty = 20
        except:
            pass
    
    try:
        prompt = (
            'あなたは大王製紙の最上席研究開発ディレクターです。業界歴30年以上です。\n\n'
            'このニュース要約を評価し、JSON形式のみで回答してください。\n\n'
            '【評価基準（合計100点）】\n'
            '1. 大王製紙R&D戦略への直接的インパクト（40点）\n'
            '2. 市場・業界構造への影響度（25点）\n'
            '3. 情報の具体性・信頼性（20点）\n'
            '4. 緊急性・時宜性（15点）\n\n'
            '【注意】\n'
            '・特許情報は30日以内のみを高く評価\n'
            '・30日以上前の古い特許情報はスコアを20点以下に低下\n'
            '・60日以上前は1-10点\n'
            f'{date_warning}\n\n'
            '【出力形式（JSON以外は一切禁止）】\n'
            '{\n'
            '  "score": <1-100の整数>,\n'
            '  "impact_analysis": "<戦略的含意>",\n'
            '  "formatting_feedback": <null或改善指示>\n'
            '}\n\n'
            f'会社名: {company}\n'
            f'日付: {date_str or "不明"}\n'
            f'タイトル: {title}\n'
            f'要約: {summary}\n'
        )
        
        text = _openrouter_generate(prompt)
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text)
        
        score = int(result.get('score', 0))
        score = max(1, min(100, score))
        
        if date_quality_penalty > 0 and date_str:
            original_score = score
            score = max(1, score - date_quality_penalty)
            print(f'  [DATE-PENALTY] {date_str}: {original_score} → {score}')
        
        impact_analysis = (result.get('impact_analysis') or '')[:300]
        formatting_feedback = result.get('formatting_feedback') or None
        
        return score, impact_analysis, formatting_feedback
    except Exception as e:
        print(f'  Audit error: {e}')
        return 0, '', None

# ============================================================
# DUAL-AGENT PIPELINE
# ============================================================
def process_item_with_retry(item, api_key=None, lenient_mode=False):
    """修复 3：传入日期信息"""
    
    title = item.get('title', '')
    snippet = strip_html(item.get('summary', ''))
    company = item.get('company', '不明')
    date_str = item.get('date', '')
    
    best_score = item.get('score') or 0
    best_summary = snippet
    best_impact = item.get('impact_analysis') or ''
    feedback = None
    
    for attempt in range(MAX_RETRIES):
        is_relevant, new_summary = ai_summarize(
            title, snippet, company, retry_feedback=feedback, lenient_mode=lenient_mode
        )
        
        if not is_relevant:
            return False
        
        current_summary = new_summary or best_summary
        if not current_summary:
            break
        
        score, impact_analysis, fmt_feedback = audit_item(
            title, current_summary, company, 
            date_str=date_str
        )
        
        is_better = score > best_score or (score == best_score and fmt_feedback is None and best_impact == '')
        if is_better:
            best_score = score
            best_summary = current_summary
            best_impact = impact_analysis
        
        summary_too_short = len(current_summary) < 80
        needs_retry = (summary_too_short or (score > RETRY_SCORE_THRESHOLD)) and fmt_feedback
        
        if needs_retry and attempt < MAX_RETRIES - 1:
            feedback = fmt_feedback
        else:
            break
    
    item['summary'] = best_summary or '分析待ち'
    item['score'] = best_score
    item['impact_analysis'] = best_impact
    return True

# ============================================================
# TOP-3 HIGHLIGHTS
# ============================================================
def generate_highlights(items, api_key=None, excluded_urls=None, today_str=None):
    excluded = excluded_urls or set()
    
    def _sorted_scored(pool):
        scored = [it for it in pool if it.get('score', 0) > 0]
        scored.sort(key=lambda x: (x.get('score', 0), x.get('date', '')), reverse=True)
        return scored
    
    ref_date_str = today_str
    if not ref_date_str:
        jst = pytz.timezone('Asia/Tokyo')
        ref_date_str = datetime.now(jst).strftime('%Y-%m-%d')
    try:
        ref_date = datetime.strptime(ref_date_str, '%Y-%m-%d').date()
    except ValueError:
        ref_date = None
    
    def _within_days(item, n):
        if ref_date is None:
            return True
        item_date_str = item.get('date', '')
        try:
            item_date = datetime.strptime(item_date_str[:10], '%Y-%m-%d').date()
            return (ref_date - item_date).days <= n
        except (ValueError, TypeError):
            return False
    
    for window_days in (1, 7, None):
        if window_days is None:
            daily_pool = items
        else:
            daily_pool = [it for it in items if _within_days(it, window_days)]
        scored = _sorted_scored(daily_pool)
        if len(scored) >= 3:
            break
    if not scored:
        scored = _sorted_scored(items)
    
    candidates = [it for it in scored if it.get('url', '') not in excluded]
    if len(candidates) < 3:
        candidates = scored
    top3 = (candidates if candidates else items)[:3]
    
    highlights = []
    for i, item in enumerate(top3):
        highlights.append({
            'rank': i + 1,
            'title': item.get('title', ''),
            'url': item.get('url', ''),
            'company': item.get('company', '不明'),
            'category': (item.get('category_name') or '') + ' / ' + (item.get('info_type') or ''),
            'date': item.get('date', ''),
            'impact': item.get('impact_analysis') or item.get('summary') or '',
            'score': item.get('score', 0),
            'is_patent': (item.get('permanent_record', False) or item.get('info_type') == '特許' or item.get('is_academic', False)),
        })
    return highlights

# ============================================================
# Data Persistence
# ============================================================
def load_data(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if isinstance(raw, list):
            return raw, None, []
        if 'dates' in raw:
            items = []
            for date_items in raw.get('dates', {}).values():
                items.extend(date_items)
            items.extend(raw.get('patents', []))
            return items, raw.get('last_updated'), raw.get('highlights', [])
        return raw.get('items', []), raw.get('last_updated'), raw.get('highlights', [])
    return [], None, []

def save_data(path, items, highlights=None, last_updated=None):
    patents = [i for i in items if i.get('permanent_record')]
    regular = [i for i in items if not i.get('permanent_record')]
    dates = {}
    for item in regular:
        d = item.get('date', 'unknown')
        dates.setdefault(d, []).append(item)
    payload = {
        'last_updated': last_updated or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'highlights': highlights or [],
        'dates': dates,
        'patents': patents,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

# ============================================================
# Main Entry Point
# ============================================================
def main():
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_data.json')
    data_path = os.path.normpath(data_path)

    jst = pytz.timezone('Asia/Tokyo')
    today = datetime.now(jst).strftime('%Y-%m-%d')
    
    data, last_updated, existing_highlights = load_data(data_path)
    if not data:
        print('No data found. Run fetch_news.py first.')
        return

    # 修复：加载后立即过滤旧专利
    print('[LOAD] Applying strict 30-day filter for patents...')
    data = filter_old_patents_from_items(data, max_age_days=30)
    print(f'[LOAD] After filtering: {len(data)} items')

    # Deduplicate
    url_map = {}
    no_url_items = []
    for item in data:
        url = item.get('url') or ''
        if not url:
            no_url_items.append(item)
        elif url not in url_map or (item.get('score') or 0) > (url_map[url].get('score') or 0):
            url_map[url] = item
    data = list(url_map.values()) + no_url_items

    # Score today's items
    today_items = [it for it in data if it.get('date') == today]
    unscored_today = [
        it for it in today_items
        if not ((it.get('score') or 0) > 0 and it.get('impact_analysis'))
    ]

    lenient_mode = len(unscored_today) < _LENIENT_THRESHOLD_DEFAULT
    if lenient_mode and unscored_today:
        print(f'[LENIENT-MODE] Only {len(unscored_today)} items — lowering threshold')

    updated = 0
    irrelevant_items = []

    for item in today_items:
        summary = strip_html(item.get('summary', ''))
        if item.get('summary', '') != summary:
            item['summary'] = summary

        has_quality_summary = len(summary) >= 80 and '<' not in summary
        has_score = (item.get('score') or 0) > 0
        has_impact = bool(item.get('impact_analysis'))

        if has_quality_summary and has_score and has_impact:
            continue
        if summary == 'AI Summary Pending':
            continue

        if not os.environ.get('OPENROUTER_API_KEY', ''):
            if not has_score:
                item['score'] = 0
            if not has_impact:
                item['impact_analysis'] = ''
            continue

        is_relevant = process_item_with_retry(item, lenient_mode=lenient_mode)
        if not is_relevant:
            irrelevant_items.append(item)
        else:
            updated += 1

    data = [it for it in data if it not in irrelevant_items]

    today_highlights = generate_highlights(
        today_items,
        excluded_urls={h['url'] for h in existing_highlights[-3:]},
        today_str=today
    )
    
    all_highlights = existing_highlights + today_highlights
    all_highlights = all_highlights[-30:]

    # Prune old
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    kept = []
    for item in data:
        if item.get('permanent_record'):
            kept.append(item)
        elif item.get('date', '9999-99-99') >= cutoff_str:
            kept.append(item)
    data = kept

    data.sort(key=lambda x: x.get('date', ''), reverse=True)
    save_data(data_path, data, highlights=all_highlights, last_updated=last_updated)

    print(f'[DONE] Updated {updated} items')

if __name__ == '__main__':
    main()
