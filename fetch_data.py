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
import html as _html
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
    # 优先 Binance，451 时降级 OKX（同为 200 日日线收盘）
    try:
        d = fetch_json('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=200')
        closes = [float(k[4]) for k in d]
    except Exception as e:
        log('AHR Binance failed:', e, '→ OKX')
        d = fetch_json('https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1D&limit=200')
        closes = [float(k[4]) for k in d['data']]
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

def get_mstr_btc_from_treasuries():
    """降级源：从 bitcointreasuries.net 页面抓 MSTR (Strategy) 的 BTC 持仓。
    页面结构含 name:"Strategy" 或 symbol:"MSTR" ... btc_balance:847363 之类。
    """
    for _a in range(3):
        try:
            req = urllib.request.Request('https://bitcointreasuries.net/', headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read().decode('utf-8', errors='ignore')
            # MSTR/Strategy 是最大持仓公司（2026-09 新格式：holdings:[{asset:"BTC",balance:N}]）
            mm = re.search(r'symbol:"MSTR".{0,400}?holdings:\[\{asset:"BTC",balance:([\d.]+)', raw, re.DOTALL)
            if not mm:
                mm = re.search(r'name:"(?:Strategy|MicroStrategy)".{0,400}?holdings:\[\{asset:"BTC",balance:([\d.]+)', raw, re.DOTALL)
            if mm:
                return round(float(mm.group(1)))
        except Exception as e:
            log(f'MSTR treasuries retry {_a}:', e)
            time.sleep(2)
    return None


def get_mstr(btc_price):
    """MSTR 持仓 + mNAV。fuckbtc 代理退化后做字段级容错：
    代理只给 stock_price 时，btc_holdings 从 bitcointreasuries 降级抓取；
    缺 debt/pref/cash 则 mNAV/负债率返回 None（主流程用前一天真实值回填）。
    """
    result = {}
    m = {}
    try:
        d = fetch_json('https://looknode-proxy.corms-cushier-0l.workers.dev/mnav', headers={'Referer': 'https://fuckbtc.com/'})
        m = d.get('mstr', {}) or {}
    except Exception as e:
        log('MSTR fuckbtc代理失败:', e)

    # 股价（代理有则用）
    if m.get('stock_price'):
        result['mstr_price'] = round(m['stock_price'], 2)

    # BTC 持仓：优先代理，缺失则从 bitcointreasuries 降级
    btc_holdings = m.get('btc_holdings')
    if not btc_holdings:
        btc_holdings = get_mstr_btc_from_treasuries()
        if btc_holdings:
            log(f'MSTR 持仓走降级源(bitcointreasuries): {btc_holdings:,}')
    if btc_holdings:
        result['mstr_btc'] = btc_holdings

    if m.get('shares'):
        result['mstr_shares'] = m['shares']

    # mNAV/负债率：需要 shares+debt+pref+cash+btc_holdings 全齐才算，否则留空由主流程回填
    need = ('shares', 'stock_price', 'debt', 'pref', 'cash', 'btc_holdings')
    if all(m.get(k) is not None for k in need) and btc_price:
        mcap = m['shares'] * m['stock_price']
        ev = mcap + m['debt'] + m['pref'] - m['cash']
        nav = m['btc_holdings'] * btc_price
        if nav:
            result['mstr_mnav'] = round(ev / nav, 2)
            result['mstr_debt_ratio'] = round(m['debt'] / nav, 2)
    return result

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


# ============ MSTR 周度 8-K（SEC EDGAR 官方源） ============
# 数据源：SEC EDGAR，MSTR 现名 Strategy Inc，CIK 1050446。免费官方，无需 key。
# 每周 8-K 的 "ATM Update" 表给出各证券当周 Net Proceeds（百万美元），
# "BTC Update" 表/段给出当周增持 BTC。2026 下半年主力融资为 MSTR 普通股 ATM，
# 优先股（STRF/STRC/STRK/STRD）当周多为 $ -，合并计入 funding.STRC（"优先股"段）。
SEC_UA = 'zw-web3 research zw@example.com'
SEC_CIK = '1050446'


def _sec_fetch(url, tries=5):
    """带官方 UA + 重试的 SEC 请求（SEC 强制要求 UA 含邮箱，间隔≥0.3s）。"""
    last = None
    for _a in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': SEC_UA})
            return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise last


def _sec_clean(raw):
    """8-K HTML → 纯文本：先解码实体，再去标签，再压空白。"""
    t = _html.unescape(raw)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    return t


def _sec_num(s):
    s = s.replace(',', '').strip()
    if s in ('-', '\u2013', '\u2014', ''):
        return 0.0
    return float(s)


def _parse_8k_atm(txt):
    """解析 "ATM Update" 表，返回各证券当周 Net Proceeds（百万美元）。
    行格式：`{SYM} Stock {SharesSold} $ {Notional} $ {NetProceeds}[ (n)] $ {Available}`。
    未发行的字段是 `$ -`（记 0）。"""
    res = {}
    i = txt.find('ATM Update')
    if i < 0:
        return res
    seg = txt[i:i + 2600]
    for sym in ['STRF', 'STRC', 'STRK', 'STRD', 'MSTR']:
        m = re.search(
            sym + r' Stock\s+([\d,\.\-\u2013\u2014]+)\s*\$\s*([\d,\.\-\u2013\u2014]+)'
            r'\s*\$\s*([\d,\.\-\u2013\u2014]+)(?:\s*\(\d\))?\s*\$', seg)
        res[sym] = _sec_num(m.group(3)) if m else 0.0
    return res


def _parse_8k_btc(txt):
    """解析当周增持 BTC + 总持仓。仅计"增持"（净卖出/未买入的周记 0）。
    返回 (weekly_btc_purchased or None, holdings or None)。
    容错：拿不到明确数值返回 None（调用方跳过或标注）。"""
    i = txt.find('BTC Update')
    if i < 0:
        # 无 BTC Update 段：可能是纯散文披露"did not purchase any bitcoin"
        mh = re.search(r'holds approximately ([\d,]+) bitcoin', txt)
        if re.search(r'did not (?:sell|purchase|acquire)', txt) and mh:
            return (0.0, _sec_num(mh.group(1)))
        return (None, None)
    seg = txt[i:i + 1600]
    header = seg[:340]
    # 段内散文式"did not purchase any bitcoin"（如 5/26 这类全零周）
    if re.search(r'did not (?:sell any shares.*?did not purchase|purchase any bitcoin)', seg):
        mh = re.search(r'holds approximately ([\d,]+) bitcoin', seg) or \
             re.search(r'Aggregate BTC Holdings[^\d]*?([\d,]{5,})', seg)
        return (0.0, _sec_num(mh.group(1)) if mh else None)
    week_sold_only = ('BTC Sold' in header) and ('BTC Purchased' not in header)
    # 合并布局：周表 + 持仓表拼接，锚定持仓表头再取数据行
    # ...Average Purchase Price (2) {v1}[ (1)] $ {v2} $ {v3} {holdings}[ $ ...]
    m = re.search(
        r'Aggregate BTC Holdings.*?Average Purchase Price\s*\(2\)\s+'
        r'([\d,\.\-\u2013\u2014]+)\s*(?:\(\d\))?\s*\$\s*([\d,\.\-\u2013\u2014]+)'
        r'\s*\$\s*([\d,\.\-\u2013\u2014]+)\s+([\d,]{5,})', seg)
    if m:
        weekly = _sec_num(m.group(1))
        holdings = _sec_num(m.group(4))
        return (0.0 if week_sold_only else max(0.0, weekly), holdings)
    # 分表布局（月/季末特殊格式）：持仓单独一块
    mh = re.search(r'Aggregate BTC Holdings[^\d]*?([\d,]{5,})', seg)
    holdings = _sec_num(mh.group(1)) if mh else None
    if week_sold_only:
        return (0.0, holdings)
    m2 = re.search(r'Average (?:Purchase|Sale) Price\s*\(2\)\s+([\d,\.\-\u2013\u2014]+)', seg)
    if m2:
        return (max(0.0, _sec_num(m2.group(1))), holdings)
    return (None, holdings)


def get_mstr_weekly_8k(limit=20):
    """从 SEC EDGAR 拉最近 N 份 MSTR 周度 8-K，解析每周
    {x:'M/DD'(披露日), btc:增持BTC, funding:{STRC:优先股$M, MSTR:普通股$M}}。
    STRF/STRC/STRK/STRD 合并进 funding.STRC（"优先股"段），MSTR 普通股单列。
    仅返回能明确解析出 btc 的周（btc 为 None 的非周度/结构化 8-K 跳过）。
    结果按披露日升序。"""
    sub = json.loads(_sec_fetch(f'https://data.sec.gov/submissions/CIK{int(SEC_CIK):010d}.json'))
    r = sub['filings']['recent']
    rows = list(zip(r['form'], r['filingDate'], r['accessionNumber'], r['primaryDocument']))
    out = []
    seen = 0
    for form, fdate, acc, doc in rows:
        if form != '8-K' or not doc.startswith('mstr-'):
            continue
        seen += 1
        if seen > limit:
            break
        accn = acc.replace('-', '')
        url = f'https://www.sec.gov/Archives/edgar/data/{int(SEC_CIK)}/{accn}/{doc}'
        try:
            txt = _sec_clean(_sec_fetch(url))
        except Exception as e:
            log(f'8k fetch fail {fdate}:', e)
            continue
        time.sleep(0.4)
        btc, _hold = _parse_8k_btc(txt)
        atm = _parse_8k_atm(txt)
        # 跳过非周度/结构化 8-K（既无 BTC 也无 ATM 数据）
        if btc is None and not atm:
            continue
        if btc is None:
            # 有 ATM 但拿不到 BTC 数（罕见）：容错跳过该周柱子，避免塞假数
            log(f'8k {fdate}: 拿不到 BTC 数，跳过该周')
            continue
        mm, dd = fdate.split('-')[1], fdate.split('-')[2]
        x = f'{int(mm)}/{dd}'
        strc = round(atm.get('STRF', 0) + atm.get('STRC', 0) +
                     atm.get('STRK', 0) + atm.get('STRD', 0), 1)
        mstr = round(atm.get('MSTR', 0), 1)
        out.append({'x': x, 'btc': int(round(btc)), 'STRC': strc, 'MSTR': mstr})
    out.reverse()  # 披露日升序
    return out


def update_mstr_weekly(html_content):
    """检测新的一周 8-K，把新周追加到 index.html 里的 mstrBuys 数组。
    以 x（披露日 M/DD）去重，只追加数组末尾之后的新周。真实数据，无假 fallback。"""
    m = re.search(r'(const mstrBuys = \[)(.*?)(\n  \];)', html_content, re.DOTALL)
    if not m:
        log('mstr_weekly: 找不到 mstrBuys 数组')
        return html_content, 0
    arr_str = m.group(2)
    xs = re.findall(r"x:'([^']+)'", arr_str)
    existing = set(xs)

    def _mmdd_key(x):
        mm, dd = x.split('/')
        return int(mm) * 100 + int(dd)

    # 数组末尾（当前最新）披露日 → 只追加严格更晚的新周，保持时间顺序，
    # 不回填数组中间缺失的旧周（避免乱序）。
    last_key = _mmdd_key(xs[-1]) if xs else 0
    try:
        weeks = get_mstr_weekly_8k(limit=25)
    except Exception as e:
        log('mstr_weekly: EDGAR 拉取失败:', e)
        return html_content, 0
    new_lines = []
    for w in weeks:
        if w['x'] in existing:
            continue
        if _mmdd_key(w['x']) <= last_key:
            continue  # 早于/等于当前末尾的旧周不回填，避免乱序
        new_lines.append(
            f"    {{ x:'{w['x']}', btc:{w['btc']}, "
            f"funding:{{STRC:{w['STRC']}, MSTR:{w['MSTR']}}} }},")
    if not new_lines:
        log('mstr_weekly: 无新周')
        return html_content, 0
    insert = '\n' + '\n'.join(new_lines)
    new_arr = arr_str.rstrip() + insert + '\n  '
    new_html = html_content.replace(m.group(0), m.group(1) + new_arr + m.group(3))
    log(f'mstr_weekly: 追加 {len(new_lines)} 周: ' +
        ', '.join(l.split("x:'")[1].split("'")[0] for l in new_lines))
    return new_html, len(new_lines)


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
        # 2026-09 页面改版：数值从 btc_balance 移到 holdings:[{asset:"BTC",balance:N}]
        # 按 symbol 就近（400字内）取其 holdings 里的 BTC balance
        def grab_sym(sym):
            m = re.search(r'symbol:"' + re.escape(sym) + r'".{0,400}?holdings:\[\{asset:"BTC",balance:([\d.]+)', html_raw, re.DOTALL)
            return round(float(m.group(1))) if m else None

        # US ETFs & Exchanges 分组的 ticker 实体
        group = ['IBIT', 'FBTC', 'GBTC', 'BTC', 'BITB', 'ARKB', 'HODL', 'BITW',
                 'BTCO', 'BRRR', 'EZBC', 'GDLC', 'BTCW', 'MSBT', 'OBTC', 'DEFI', 'BITA']
        total = 0
        n = 0
        for sym in group:
            v = grab_sym(sym)
            if v and v > 50:
                total += v
                n += 1

        # River (Exchange) 无 symbol，用 name 匹配就近的 holdings BTC balance
        mr = re.search(r'name:"River \(Exchange\)".{0,400}?holdings:\[\{asset:"BTC",balance:([\d.]+)', html_raw, re.DOTALL)
        if mr:
            total += round(float(mr.group(1)))
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
        log(f"MSTR {mstr.get('mstr_btc','?')} BTC @ ${mstr.get('mstr_price','?')}, mNAV {mstr.get('mstr_mnav','?')}x")
    except Exception as e:
        errors.append(f'MSTR: {e}')
    # MSTR 各字段缺失时用前一天真实值回填（防止单源退化导致整行缺数据）
    for _f in ('mstr_btc', 'mstr_price', 'mstr_shares', 'mstr_mnav', 'mstr_debt_ratio'):
        if data.get(_f) is None:
            _mp = re.findall(rf'{_f}:([\d.]+)', html)
            if _mp:
                _val = _mp[-1]
                data[_f] = float(_val) if '.' in _val else int(_val)
                log(f"{_f} fallback (前一天): {data[_f]}")

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

    # 必须字段检查：只有 BTC 价格是硬性的（连价格都没有才中止）。
    # 其余字段（fear/mstr/etf/usdc 等）缺失一律用前一天真实值回填，绝不因单源失败中止整行落库。
    required = ['btc_price']
    missing = [k for k in required if k not in data]
    if missing:
        log(f'❌ 缺少核心字段（BTC价格），中止: {missing}')
        log(f'errors: {errors}')
        return 1

    # 其余字段统一前一天回填兜底（fear/fear_label/ahr999/mvrv/wma200/etf_btc/usdc_mcap）
    _num_fallback = {
        'fear': r'fear:(\d+)', 'ahr999': r'ahr999:([\d.]+)', 'mvrv': r'mvrv:([\d.]+)',
        'wma200': r'wma200:(\d+)', 'etf_btc': r'etf_btc:(\d+)', 'usdc_mcap': r'usdc_mcap:(\d+)',
    }
    for _f, _pat in _num_fallback.items():
        if data.get(_f) is None:
            _mp = re.findall(_pat, html)
            if _mp:
                _v = _mp[-1]
                data[_f] = float(_v) if '.' in _v else int(_v)
                log(f"{_f} fallback (前一天): {data[_f]}")
    if data.get('fear_label') is None:
        _fl = re.findall(r'fear_label:"([^"]+)"', html)
        if _fl:
            data['fear_label'] = _fl[-1]
    if errors:
        log(f'⚠️ 本次降级/告警: {errors}')

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

    # ===== 同步 MSTR 周度增持+资金来源柱状图（SEC 8-K）=====
    try:
        new_html, mstr_wk_added = update_mstr_weekly(new_html)
    except Exception as e:
        log('mstr_weekly 更新异常（不阻断主流程）:', e)
        mstr_wk_added = 0

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
