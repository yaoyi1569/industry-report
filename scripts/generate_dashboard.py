import json
import re
from datetime import datetime
import os

# 如果有 Gemini API，取消下行注释
# import google.generativeai as genai

def extract_company(title):
    """从标题提取公司名称"""
    companies = [
        'ユニ・チャーム', '花王', 'P&G', 'ライオン', 'キンバリー・クラーク', 
        '大王製紙', '王子ホールディングス', '日本製紙', 'Essity', '瑞光', 
        'ファナック', 'GDM', 'Fameccanica', 'OPTIMA', 'Winner Medical', 'Kimberly-Clark'
    ]
    for company in companies:
        if company in title:
            return company
    return '不明'

def map_category(title, summary):
    """自动分配分类ID"""
    category_keywords = {
        '①': ['ユニ・チャーム', '花王', 'P&G', 'ライオン', 'キンバリー', '衛生用品', 'おむつ'],
        '②': ['製紙', 'パルプ', '王子', '日本製紙', 'Essity'],
        '③': ['瑞光', 'GDM', 'Fameccanica', '加工機', '不織布'],
        '④': ['OPTIMA', 'ファナック', '包装', 'パレタイザー', '充填'],
        '⑤': ['ウェット', 'Winner Medical', '稳健医療'],
        '⑥': ['ティシュー', 'ティッシュ', 'トイレット', '家庭紙'],
    }
    
    full_text = (title + ' ' + summary).lower()
    for cat_id, keywords in category_keywords.items():
        for keyword in keywords:
            if keyword.lower() in full_text:
                return cat_id
    return '⑥'

def determine_info_type(title, summary):
    """确定信息类型"""
    full_text = (title + ' ' + summary).lower()
    
    if any(k in full_text for k in ['投資', '買収', '出資', 'M&A', '資金', '買い', 'acquisition']):
        return '投資'
    elif any(k in full_text for k in ['特許', 'patent', '知的']):
        return '特許'
    elif any(k in full_text for k in ['研究', '論文', '学会', '技術開発', 'research', 'development']):
        return '研究開発'
    elif any(k in full_text for k in ['機械', 'マシン', '加工', 'machine', '設備']):
        return '加工機技術'
    elif any(k in full_text for k in ['包装', 'パッケージ', '充填', 'packaging']):
        return '包装機技術'
    elif any(k in full_text for k in ['新製品', '新商品', '新発売', 'new product', 'launch']):
        return '新製品'
    elif any(k in full_text for k in ['環境', 'エコ', '規制', 'sustainability', 'eco', 'carbon']):
        return '環境'
    elif any(k in full_text for k in ['規制', 'law', '法律', 'regulation', '義務']):
        return '規制'
    else:
        return '其他'

def ai_summarize_placeholder(title, summary, company):
    """
    AI摘要占位符 - 可以集成真实的Gemini API
    使用方法：
    1. 安装: pip install google-generativeai
    2. 设置环境变量: export GEMINI_API_KEY=your_key
    3. 取消下方注释并实现真实的API调用
    """
    # 如果有API密钥，使用真实AI摘要
    # if os.environ.get('GEMINI_API_KEY'):
    #     genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
    #     prompt = f"""
    #     作为行业分析师，为以下新闻生成300字以内的日文摘要。
    #     公司：{company}
    #     标题：{title}
    #     内容：{summary}
    #     """
    #     response = genai.generate_text(prompt=prompt)
    #     return response.result[:200]
    
    # 否则返回原始摘要的前200字
    return summary[:200] if summary else title[:200]

def generate_data_json(news_items):
    """转换新闻为仪表板数据格式"""
    processed = []
    
    for idx, item in enumerate(news_items):
        company = extract_company(item.get('title', ''))
        category_id = map_category(item.get('title', ''), item.get('summary', ''))
        info_type = determine_info_type(item.get('title', ''), item.get('summary', ''))
        
        processed.append({
            'title': item.get('title', 'No title'),
            'summary': ai_summarize_placeholder(
                item.get('title', ''),
                item.get('summary', ''),
                company
            ),
            'company': company,
            'date': item.get('date', datetime.now().strftime('%Y-%m-%d')),
            'category_id': category_id,
            'category_name': item.get('category', '未分类'),
            'info_type': info_type,
            'url': item.get('link', '#'),
            'source_name': item.get('source', 'Unknown'),
            'confidence': '高' if company != '不明' else '中'
        })
    
    return processed

def update_html(processed_data):
    """更新HTML文件中的DATA变量和时间"""
    html_file = 'index.html.txt'
    
    if not os.path.exists(html_file):
        print(f"Warning: {html_file} not found")
        return
    
    with open(html_file, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # 生成新的DATA JSON
    data_json = json.dumps(processed_data, ensure_ascii=False, indent=2)
    
    # 替换DATA数组
    html = re.sub(
        r'const DATA = \[.*?\];',
        f'const DATA = {data_json};',
        html,
        flags=re.DOTALL
    )
    
    # 更新日期和时间
    now = datetime.now()
    date_str = now.strftime('%Y年%m月%d日')
    time_str = now.strftime('%H:%M JST')
    
    # 更新header中的日期
    html = re.sub(
        r'<div class="header-date">\d{4}年\d{1,2}月\d{1,2}日',
        f'<div class="header-date">{date_str}',
        html
    )
    
    # 更新section header中的日期
    html = re.sub(
        r'全カテゴリーの最新業界情報を集約表示 ― \d{4}年\d{1,2}月\d{1,2}日',
        f'全カテゴリーの最新業界情報を集約表示 ― {date_str}',
        html
    )
    
    # 更新时间
    html = re.sub(
        r'最終更新: \d{2}:\d{2} JST',
        f'最終更新: {time_str}',
        html
    )
    
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✓ Dashboard updated at {date_str} {time_str}")
    print(f"✓ Total {len(processed_data)} news items processed")

def main():
    """主函数"""
    data_file = 'data/news_raw.json'
    
    # 检查是否有原始新闻数据
    if not os.path.exists(data_file):
        print(f"Error: {data_file} not found. Run fetch_news.py first.")
        return
    
    # 读取原始新闻数据
    with open(data_file, 'r', encoding='utf-8') as f:
        raw_news = json.load(f)
    
    print(f"Processing {len(raw_news)} news items...")
    
    # 处理新闻数据
    processed_data = generate_data_json(raw_news)
    
    # 更新HTML
    update_html(processed_data)
    
    # 也保存处理后的数据以备后用
    os.makedirs('data', exist_ok=True)
    with open('data/news_processed.json', 'w', encoding='utf-8') as f:
        json.dump(processed_data, f, ensure_ascii=False, indent=2)
    
    print("✓ Complete! Dashboard is ready.")

if __name__ == '__main__':
    main()