#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC 看板数据采集器（独立脚本版）
- 用法: python3 fetch_data.py
- 输出: 直接修改 index.html 追加今日数据 + git commit & push
- 无需 LLM，30 秒内完成
"""
import json
import urllib.request
import urllib.error
import math
import time
import re
import subprocess
import sys
from datetime import date, timezone, timedelta
import datetime as dt

# ============ 配置 ============
DASHBOARD_DIR = '/Users/ysyc01/.openclaw/workspace-web3/btc-dashboard'
INDEX_FILE = f'{DASHBOARD_DIR}/index.html'
BTC_ATH = 126080  # BTC ATH 基准，回撤计算用
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

# ============ 工具 ============
def fetch_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def log(*a):
    print('[fetch]', *a, flush=True)

# ============ 数据源 ============
def get_btc():
    """BTC 价格 + 24h 涨跌幅，CoinGecko 失败降级 Binance"""
    try:
        d = fetch_json('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true')
        return round(d['bitcoin']['usd']), round(d['bitcoin']['usd_24h_change'], 2)
    except Exception as e:
        log('BTC CG failed:', e, '→ Binance')
        d = fetch_json('https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT')
        return round(float(d['lastPrice'])), round(float(d['priceChangePercent']), 2)

def get_fng():
    d = fetch_json('https://api.alternative.me/fng/?limit=1')
    value = int(d['data'][0]['value'])
    label_map = {'Extreme Fear': '极度恐惧', 'Fear': '恐惧', 'Neutral': '中性', 'Greed': '贪婪', 'Extreme Greed': '极度贪婪'}
    return value, label_map.get(d['data'][0]['value_classification'], d['data'][0]['value_classification'])

def get_ahr999(btc_price):
    btc_birthday = date(2009, 1, 3)
    age = (date.today() - btc_birthday).days
    fitted = 10 ** (5.84 * math.log10(age) - 17.01)
    d = fetch_json('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=200')
    closes = [float(k[4]) for k in d]
    cost_200d = sum(closes) / len(closes)
    return round((btc_price / cost_200d) * (btc_price / fitted), 4)

def get_mvrv():
    d = fetch_json('https://crypto3d.pro/indicators/data/mvrv.json', headers={'Referer': 'https://crypto3d.pro/'})
    if isinstance(d, dict) and 'current' in d:
        return round(d['current'].get('value', 0), 2)
    return None

def get_wma200():
    try:
        d = fetch_json('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1w&limit=200')
        closes = [float(k[4]) for k in d]
        return round(sum(closes) / len(closes))
    except Exception as e:
        log('WMA Binance failed:', e, '→ OKX')
        d = fetch_json('https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1W&limit=200')
        closes = [float(k[4]) for k in d['data']]
        return round(sum(closes) / len(closes))

def get_mstr(btc_price):
    d = fetch_json('https://looknode-proxy.corms-cushier-0l.workers.dev/mnav', headers={'Referer': 'https://fuckbtc.com/'})
    m = d['mstr']
    mcap = m['shares'] * m['stock_price']
    ev = mcap + m['debt'] + m['pref'] - m['cash']
    nav = m['btc_holdings'] * btc_price
    return {
        'mstr_btc': m['btc_holdings'],
        'mstr_price': round(m['stock_price'], 2),
        'mstr_shares': m['shares'],
        'mstr_mnav': round(ev / nav, 2) if nav else 0,
        'mstr_debt_ratio': round(m['debt'] / nav, 2) if nav else 0,
    }

def get_usdc():
    d = fetch_json('https://api.coingecko.com/api/v3/coins/usd-coin/market_chart?vs_currency=usd&days=365&interval=daily')
    return round(d['market_caps'][-1][1])

def get_etf_btc(btc_price):
    """ETF 持仓 BTC 数。CoinGlass 403 时降级估算（跳过不写）"""
    try:
        # SoSoValue 官方 API
        d = fetch_json(
            'https://gw.sosovalue.com/openapi/v2/etf/historicalInflowChart?type=us-btc-spot',
            headers={'Referer': 'https://sosovalue.com/'}
        )
        latest = d['data'][-1]
        return round(latest['totalNetAssets'] / btc_price)
    except Exception as e:
        log('ETF SoSoValue failed:', e)
        return None

# ============ 主流程 ============
def main():
    cst = timezone(timedelta(hours=8))
    today_str = dt.datetime.now(tz=cst).strftime('%Y-%m-%d')
    log(f'采集日期: {today_str}')

    # 检查是否已存在
    with open(INDEX_FILE, 'r', encoding='utf-8') as f:
        html = f.read()
    if f'date:"{today_str}"' in html:
        log(f'{today_str} 已存在于 RAW_DATA，跳过')
        return 0

    # 拉数据（每个源 try/except，失败不影响其他）
    data = {'date': today_str}
    errors = []

    try:
        data['btc_price'], data['btc_change'] = get_btc()
        data['drawdown'] = round((data['btc_price'] - BTC_ATH) / BTC_ATH * 100, 1)
        log(f"BTC ${data['btc_price']:,} ({data['btc_change']}%)")
    except Exception as e:
        errors.append(f'BTC: {e}')

    try:
        data['fear'], data['fear_label'] = get_fng()
        log(f"F&G {data['fear']} {data['fear_label']}")
    except Exception as e:
        errors.append(f'FNG: {e}')

    try:
        if 'btc_price' in data:
            data['ahr999'] = get_ahr999(data['btc_price'])
            log(f"AHR999 {data['ahr999']}")
    except Exception as e:
        errors.append(f'AHR: {e}')

    try:
        mvrv = get_mvrv()
        if mvrv is not None:
            data['mvrv'] = mvrv
            log(f"MVRV {mvrv}")
    except Exception as e:
        errors.append(f'MVRV: {e}')

    try:
        data['wma200'] = get_wma200()
        log(f"WMA200 ${data['wma200']:,}")
    except Exception as e:
        errors.append(f'WMA: {e}')

    try:
        mstr = get_mstr(data.get('btc_price', 80000))
        data.update(mstr)
        log(f"MSTR {mstr['mstr_btc']:,} BTC @ ${mstr['mstr_price']}, mNAV {mstr['mstr_mnav']}x")
    except Exception as e:
        errors.append(f'MSTR: {e}')

    try:
        time.sleep(1)
        data['usdc_mcap'] = get_usdc()
        log(f"USDC ${data['usdc_mcap']/1e9:.2f}B")
    except Exception as e:
        errors.append(f'USDC: {e}')

    try:
        if 'btc_price' in data:
            etf = get_etf_btc(data['btc_price'])
            if etf:
                data['etf_btc'] = etf
                log(f"ETF {etf:,} BTC")
            else:
                # ETF 源失败，用最近一条作为 fallback（避免前端 NaN）
                m_prev = re.findall(r'etf_btc:(\d+)', html)
                if m_prev:
                    data['etf_btc'] = int(m_prev[-1])
                    log(f"ETF fallback (前一天): {data['etf_btc']:,}")
    except Exception as e:
        errors.append(f'ETF: {e}')

    # 必须字段检查
    required = ['btc_price', 'fear', 'mstr_btc']
    missing = [k for k in required if k not in data]
    if missing:
        log(f'❌ 缺少关键字段: {missing}')
        log(f'errors: {errors}')
        return 1

    # 组装 RAW_DATA 行（按顺序）
    fields = [
        ('date', lambda v: f'"{v}"'),
        ('btc_price', None),
        ('btc_change', None),
        ('drawdown', None),
        ('fear', None),
        ('fear_label', lambda v: f'"{v}"'),
        ('ahr999', None),
        ('mvrv', None),
        ('wma200', None),
        ('mstr_btc', None),
        ('mstr_price', None),
        ('mstr_shares', None),
        ('mstr_mnav', None),
        ('mstr_debt_ratio', None),
        ('etf_btc', None),
        ('usdc_mcap', None),
    ]
    # 加 cost_basis（固定值，如果有 MSTR 增持需手动更新）
    data['mstr_cost_basis'] = 75527

    parts = []
    for key, fmt in fields:
        if key in data:
            v = data[key]
            parts.append(f'{key}:{fmt(v) if fmt else v}')
    # cost_basis 插在 mstr 系列后面
    if 'mstr_cost_basis' in data:
        # 找到 mstr_debt_ratio 的位置插入
        for i, p in enumerate(parts):
            if p.startswith('mstr_debt_ratio:'):
                parts.insert(i + 1, f'mstr_cost_basis:{data["mstr_cost_basis"]}')
                break

    new_row = '  { ' + ', '.join(parts) + ' },'
    log(f'NEW ROW: {new_row}')

    # 插入到 RAW_DATA 数组末尾前
    pattern = re.compile(r'(const RAW_DATA = \[.*?)(\n\];)', re.DOTALL)
    m = pattern.search(html)
    if not m:
        log('❌ 找不到 RAW_DATA 数组')
        return 2

    new_html = html[:m.end(1)] + '\n' + new_row + html[m.end(1):]
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(new_html)
    log(f'✅ {today_str} 已追加到 RAW_DATA')

    # 语法检查
    check = subprocess.run(
        ['node', '-e', '''
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const re = /<script[^>]*>([\\s\\S]*?)<\\/script>/gi;
let m, i = 0;
while ((m = re.exec(html)) !== null) { i++; try { new Function(m[1]); } catch(e) { console.error('SCRIPT ' + i + ' ERR: ' + e.message); process.exit(1); } }
console.log('Scripts OK:', i);
''', INDEX_FILE],
        capture_output=True, text=True
    )
    if check.returncode != 0:
        log('❌ 语法检查失败:', check.stderr)
        return 3
    log(check.stdout.strip())

    # git commit & push
    try:
        subprocess.run(['git', 'add', 'index.html'], cwd=DASHBOARD_DIR, check=True, capture_output=True)
        subprocess.run(
            ['git', 'commit', '-m', f'data: {today_str} update'],
            cwd=DASHBOARD_DIR, check=True, capture_output=True
        )
        push = subprocess.run(
            ['git', 'push', 'origin', 'main'],
            cwd=DASHBOARD_DIR, check=True, capture_output=True, text=True
        )
        log('✅ git push 完成')
    except subprocess.CalledProcessError as e:
        log('❌ git 操作失败:', e.stderr if e.stderr else e)
        return 4

    if errors:
        log(f'⚠️ 部分数据源失败: {errors}')

    log('🎉 全部完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
