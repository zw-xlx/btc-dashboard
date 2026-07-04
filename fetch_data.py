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
    """USDC 流通市值。多源 + 重试，返回整数美元。"""
    # 源1: CoinGecko coins/usd-coin（稳定，直接给 market_cap）
    for _a in range(3):
        try:
            d = fetch_json('https://api.coingecko.com/api/v3/coins/usd-coin?localization=false&tickers=false&community_data=false&developer_data=false&sparkline=false')
            mc = d['market_data']['market_cap']['usd']
            if mc and mc > 1e9:
                return round(mc)
        except Exception as e:
            log(f'USDC coingecko retry {_a}:', e)
            time.sleep(2)
    # 源2: CoinGecko simple/price（带市值）
    try:
        d = fetch_json('https://api.coingecko.com/api/v3/simple/price?ids=usd-coin&vs_currencies=usd&include_market_cap=true')
        mc = d['usd-coin']['usd_market_cap']
        if mc and mc > 1e9:
            return round(mc)
    except Exception as e:
        log('USDC simple/price failed:', e)
    # 源3: CoinGecko market_chart（旧源，偶发 SSL）
    try:
        d = fetch_json('https://api.coingecko.com/api/v3/coins/usd-coin/market_chart?vs_currency=usd&days=2&interval=daily')
        return round(d['market_caps'][-1][1])
    except Exception as e:
        log('USDC market_chart failed:', e)
    return None

def update_etf_history(html_content, today_str, current_etf_btc):
    """检测 ETF 持仓变化，每周一固定追加点（或变化 >5K BTC 时立即追加）"""
    if not current_etf_btc:
        return html_content, 0

    m = re.search(r'const etfHistory = \[(.*?)\];', html_content, re.DOTALL)
    if not m:
        log('etf_history: 找不到 etfHistory 数组')
        return html_content, 0

    arr_str = m.group(1)
    points = re.findall(r"x:'(\d{4}-\d{2}-\d{2})',\s*y:\s*(\d+)", arr_str)
    if not points:
        return html_content, 0
    last_date, last_etf = points[-1][0], int(points[-1][1])

    if today_str == last_date:
        log(f'etf_history: {today_str} 已存在')
        return html_content, 0

    # 触发条件：周一 OR 变化 >=5K BTC
    is_monday = dt.datetime.strptime(today_str, '%Y-%m-%d').weekday() == 0
    delta = current_etf_btc - last_etf
    if not is_monday and abs(delta) < 5000:
        log(f'etf_history: 非周一且变化小（{delta:,} BTC），跳过')
        return html_content, 0

    new_arr = arr_str.rstrip() + f"\n    {{ x:'{today_str}', y:{current_etf_btc} }},\n  "
    new_html = html_content.replace(m.group(0), f'const etfHistory = [{new_arr}];')
    sign = '+' if delta > 0 else ''
    log(f'etf_history: 追加 {today_str}:{current_etf_btc:,} ({sign}{delta:,} BTC)')
    return new_html, 1


def update_mstr_history(html_content, today_str, current_btc_holding):
    """检测 MSTR 持仓变化，触发增持时追加点到 mstrHistory 数组。
    策略：每周一比较一次（避免重复），或当持仓发生 >=10 BTC 变化时立即追加。
    """
    if not current_btc_holding:
        return html_content, 0

    # 提取现有 mstrHistory 数组
    m = re.search(r'const mstrHistory = \[(.*?)\];', html_content, re.DOTALL)
    if not m:
        log('mstr_history: 找不到 mstrHistory 数组')
        return html_content, 0

    arr_str = m.group(1)
    # 拿到最后一条记录的日期 + 持仓
    points = re.findall(r"x:'(\d{4}-\d{2}-\d{2})',\s*y:(\d+)", arr_str)
    if not points:
        return html_content, 0
    last_date, last_holding = points[-1][0], int(points[-1][1])

    # 已存在该日期则跳过
    if today_str == last_date:
        log(f'mstr_history: {today_str} 已存在')
        return html_content, 0

    # 持仓未变化（差 <10 BTC，可能是抖动）则跳过
    delta = current_btc_holding - last_holding
    if abs(delta) < 10:
        log(f'mstr_history: 持仓基本不变 ({last_holding}→{current_btc_holding})，跳过')
        return html_content, 0

    # 追加新点
    new_arr = arr_str.rstrip() + f"\n    {{ x:'{today_str}', y:{current_btc_holding} }},\n  "
    new_html = html_content.replace(m.group(0), f'const mstrHistory = [{new_arr}];')
    sign = '+' if delta > 0 else ''
    log(f'mstr_history: 追加 {today_str}:{current_btc_holding} ({sign}{delta:,} BTC)')
    return new_html, 1


def get_etf_btc(btc_price):
    """ETF 持仓 BTC 数（bitcointreasuries "US ETFs & Exchanges" 网页口径 ≈ 135 万）。
    含 18 个实体：14 只现货ETF + River交易所 + BITW/GDLC多币种基金 + MSBT信托。
    与用户在 bitcointreasuries.net 网页看到的 Total 一致，避免口径歧义。
    """
    for _attempt in range(3):
        try:
            req = urllib.request.Request('https://bitcointreasuries.net/', headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                html_raw = r.read().decode('utf-8', errors='ignore')
            break
        except Exception as e:
            log(f'ETF bitcointreasuries retry {_attempt}:', e)
            time.sleep(2)
            html_raw = ''
    try:
        # 通用抓法：每个 symbol 就近取其后第一个 btc_balance
        sym_pos = [(m.group(1), m.start()) for m in re.finditer(r'symbol:"([^"]+)"', html_raw)]
        bal_pos = [(float(m.group(1)), m.start()) for m in re.finditer(r'btc_balance:([\d.]+)', html_raw)]
        vals = {}
        for sym, sp in sym_pos:
            for b, bp in bal_pos:
                if bp > sp:
                    if sym not in vals:
                        vals[sym] = b
                    break

        # US ETFs & Exchanges 分组的 ticker 实体（有 symbol 的 17 个）
        group = ['IBIT', 'FBTC', 'GBTC', 'BTC', 'BITB', 'ARKB', 'HODL', 'BITW',
                 'BTCO', 'BRRR', 'EZBC', 'GDLC', 'BTCW', 'MSBT', 'OBTC', 'DEFI', 'BITA']
        total = 0
        n = 0
        for sym in group:
            if sym in vals and vals[sym] > 50:
                total += vals[sym]
                n += 1

        # River (Exchange) 无 symbol，用 name 匹配就近的 btc_balance
        mr = re.search(r'name:"River \(Exchange\)"[^}]*?\}.*?btc_balance:([\d.]+)', html_raw)
        if not mr:
            mr = re.search(r'slug:"river-exchange".*?btc_balance:([\d.]+)', html_raw)
        if mr:
            total += float(mr.group(1))
            n += 1

        if n >= 12:
            real = round(total)
            log(f'ETF bitcointreasuries (网页US ETF+Exchange口径): {n} 实体, 合计 = {real:,} BTC')
            return real
        else:
            log(f'ETF 命中实体不足 ({n}), 不可信')
    except Exception as e:
        log('ETF bitcointreasuries failed:', e)

    # 2. SoSoValue 官方 API（备用，目前 403）
    try:
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
        usdc = get_usdc()
        if usdc:
            data['usdc_mcap'] = usdc
            log(f"USDC ${usdc/1e9:.2f}B")
        else:
            m_prev = re.findall(r'usdc_mcap:(\d+)', html)
            if m_prev:
                data['usdc_mcap'] = int(m_prev[-1])
                log(f"USDC fallback (前一天): ${data['usdc_mcap']/1e9:.2f}B")
    except Exception as e:
        errors.append(f'USDC: {e}')
        m_prev = re.findall(r'usdc_mcap:(\d+)', html)
        if m_prev:
            data['usdc_mcap'] = int(m_prev[-1])
            log(f"USDC fallback (前一天): ${data['usdc_mcap']/1e9:.2f}B")

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
    log(f'✅ {today_str} 已追加到 RAW_DATA')

    # ===== 同步 MSTR 持仓走势图 =====
    new_html, mstr_added = update_mstr_history(new_html, today_str, data.get('mstr_btc'))

    # ===== 同步 ETF 持仓走势图 =====
    new_html, etf_added = update_etf_history(new_html, today_str, data.get('etf_btc'))

    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(new_html)

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
