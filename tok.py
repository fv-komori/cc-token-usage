#!/usr/bin/env python3
import json, glob, os, collections, datetime, sys, unicodedata

# ============================================================================
# Claude Code トークン集計 & Bedrockコスト試算 (統合版)
#
# 使い方:
#   python3 ~/tok.py                          本日の時間帯別 (トークンのみ)
#   python3 ~/tok.py "2026-06-26 09:00"       指定時刻以降 (トークン + コスト)
#   python3 ~/tok.py "2026-06-26 09:00" "2026-06-26 12:00"   範囲指定
#   python3 ~/tok.py daily                    日別サマリ (トークン + コスト)
#
# コスト = Bedrock 標準/グローバル・オンデマンド単価 (Anthropic API 標準と同額)。
#   cache書込 = 5分キャッシュ input*1.25 / 1時間キャッシュ input*2 (実データの内訳で按分)。
#   cache読込 = input*0.1。
# ============================================================================

RATES = {  # (input, output) USD / 1M tokens
    'fable5': (10.0, 50.0),
    'opus':   (5.0, 25.0),
    'sonnet': (3.0, 15.0),
    'haiku':  (1.0, 5.0),
}
def model_key(m):
    if not m or m.startswith('<'): return None       # <synthetic> 等は課金対象外
    if m.startswith('claude-fable') or m.startswith('claude-mythos'): return 'fable5'
    if m.startswith('claude-opus'):   return 'opus'
    if m.startswith('claude-sonnet'): return 'sonnet'
    if m.startswith('claude-haiku'):  return 'haiku'
    return 'opus'  # 不明は安全側でOpus単価

# ---- 表示ユーティリティ (全角=2セル幅を考慮した桁揃え) ----
def dw(s):  # 表示幅
    return sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in str(s))
def pad(s, width, right=True):
    s = str(s); gap = max(0, width - dw(s))
    return (' '*gap + s) if right else (s + ' '*gap)
def row(cells):  # cells = [(text, width, right?), ...]
    return ''.join(pad(t, w, r) for t, w, r in cells)
def usd(x): return f"${x:,.2f}"
def fmt(n): return f"{n:,}"

# ---- 引数解析 ----
args = sys.argv[1:]
mode = 'hourly'; start = end = None
if args and args[0] == 'daily':
    mode = 'daily'
elif args:
    try:
        start = datetime.datetime.fromisoformat(args[0]).astimezone()
        mode = 'range'
        if len(args) >= 2 and args[1]:
            end = datetime.datetime.fromisoformat(args[1]).astimezone()
    except ValueError:
        print(f"日時の形式が不正です: {args[0]!r}  (例: \"2026-06-26 09:00\")"); sys.exit(1)

# ---- 全ログを1パスで読み込み ----
# rows の各要素: (localdt, modelkey, in, out, cc5, cc1, cr)
#   cc5 = 5分キャッシュ書込, cc1 = 1時間キャッシュ書込 (usage.cache_creation の内訳)
seen = set()
rows = []
files = glob.glob(os.path.expanduser('~/.claude/projects/**/*.jsonl'), recursive=True)
for f in files:
    try:
        with open(f, errors='ignore') as fh:
            for line in fh:
                line = line.strip()
                if not line: continue
                try: o = json.loads(line)
                except: continue
                msg = o.get('message') or {}
                usage = msg.get('usage'); ts = o.get('timestamp')
                if not usage or not ts: continue
                mid = msg.get('id'); rid = o.get('requestId') or o.get('uuid')
                dedup = (mid, rid, usage.get('output_tokens'))
                if mid and dedup in seen: continue
                if mid: seen.add(dedup)
                try: dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone()
                except: continue
                k = model_key(msg.get('model'))
                if k is None: continue
                cc_total = usage.get('cache_creation_input_tokens', 0)
                cd = usage.get('cache_creation') or {}
                cc5 = cd.get('ephemeral_5m_input_tokens', 0)
                cc1 = cd.get('ephemeral_1h_input_tokens', 0)
                if cc5 == 0 and cc1 == 0:      # 内訳が無い古い記録は5分扱いにフォールバック
                    cc5 = cc_total
                rows.append((dt, k, usage.get('input_tokens', 0), usage.get('output_tokens', 0),
                             cc5, cc1, usage.get('cache_read_input_tokens', 0)))
    except: continue

def row_cost(k, i, ot, cc5, cc1, cr):
    ir, orr = RATES[k]
    return (i*ir + ot*orr + cc5*ir*1.25 + cc1*ir*2 + cr*ir*0.1) / 1e6

# ---- モデル別コスト表 ----
def print_cost_table(sel):
    W = [('model',7,False),('応答',5,True),('input',12,True),('output',11,True),
         ('cache_w',13,True),('cache_r',14,True),('コスト',12,True)]
    per = collections.defaultdict(lambda: collections.Counter())
    for dt, k, i, ot, cc5, cc1, cr in sel:
        c = per[k]; c['in'] += i; c['out'] += ot; c['cc5'] += cc5; c['cc1'] += cc1; c['cr'] += cr; c['n'] += 1
    line = '-'*sum(w for _, w, _ in W)
    print(line); print(row([(h, w, r) for h, w, r in W])); print(line)
    grand = 0.0; gc = collections.Counter()
    for k in sorted(per):
        c = per[k]; cost = row_cost(k, c['in'], c['out'], c['cc5'], c['cc1'], c['cr']); grand += cost
        for kk in ('in','out','cc5','cc1','cr','n'): gc[kk] += c[kk]
        print(row([(k,7,False),(c['n'],5,True),(fmt(c['in']),12,True),(fmt(c['out']),11,True),
                   (fmt(c['cc5']+c['cc1']),13,True),(fmt(c['cr']),14,True),(usd(cost),12,True)]))
    print(line)
    print(row([('合計',7,False),(gc['n'],5,True),(fmt(gc['in']),12,True),(fmt(gc['out']),11,True),
               (fmt(gc['cc5']+gc['cc1']),13,True),(fmt(gc['cr']),14,True),(usd(grand),12,True)]))
    return grand

# ============================ 各モード ============================
if mode == 'hourly':
    today = datetime.datetime.now().astimezone().date()
    hrs = collections.defaultdict(lambda: collections.Counter())
    for dt, k, i, ot, cc5, cc1, cr in rows:
        if dt.date() != today: continue
        x = hrs[dt.strftime('%H:00')]
        x['in'] += i; x['out'] += ot; x['cc'] += cc5 + cc1; x['cr'] += cr; x['n'] += 1
    W = [('時刻',6,False),('応答',5,True),('入力',11,True),('出力',11,True),
         ('cache_w',13,True),('cache_r',14,True),('入+出',11,True)]
    print(f"== {today} 時間帯別 ==")
    print(row([(h, w, r) for h, w, r in W]))
    print('-'*sum(w for _, w, _ in W))
    for h in sorted(hrs):
        c = hrs[h]
        print(row([(h,6,False),(c['n'],5,True),(fmt(c['in']),11,True),(fmt(c['out']),11,True),
                   (fmt(c['cc']),13,True),(fmt(c['cr']),14,True),(fmt(c['in']+c['out']),11,True)]))
    print("\n(範囲を指定するとコスト試算も出ます:  python3 ~/tok.py \"YYYY-MM-DD HH:MM\" \"YYYY-MM-DD HH:MM\")")

elif mode == 'range':
    sel = [r for r in rows if (not start or r[0] >= start) and (not end or r[0] < end)]
    c = collections.Counter()
    for _, k, i, ot, cc5, cc1, cr in sel:
        c['in'] += i; c['out'] += ot; c['cc'] += cc5 + cc1; c['cr'] += cr
    print(f"範囲: {start or '(最初)'} 〜 {end or '(最新)'}")
    print(f"やり取り(assistant応答数): {len(sel)}")
    print(f"入力         : {fmt(c['in'])}")
    print(f"出力         : {fmt(c['out'])}")
    print(f"キャッシュ書込 : {fmt(c['cc'])}")
    print(f"キャッシュ読込 : {fmt(c['cr'])}")
    print(f"入力+出力     : {fmt(c['in']+c['out'])}  <- コスト感覚に近い")
    print(f"総合計         : {fmt(c['in']+c['out']+c['cc']+c['cr'])}")
    print(f"\n== Bedrock コスト試算 (標準/グローバル・オンデマンド) ==")
    grand = print_cost_table(sel)
    print('='*76)
    print(f"■ 概算コスト: {usd(grand)}")
    print(f"   (地域エンドポイント +10%: {usd(grand*1.1)} / バッチAPI半額: 約 {usd(grand*0.5)})")

elif mode == 'daily':
    days = collections.defaultdict(list)
    for r in rows:
        days[r[0].date().isoformat()].append(r)
    W = [('date',11,False),('応答',6,True),('入+出',14,True),('総トークン',16,True),('コスト',12,True)]
    print(row([(h, w, r) for h, w, r in W]))
    print('-'*sum(w for _, w, _ in W))
    gtot = 0.0
    for day in sorted(days):
        sel = days[day]
        io = sum(i+ot for _, k, i, ot, cc5, cc1, cr in sel)
        tot = sum(i+ot+cc5+cc1+cr for _, k, i, ot, cc5, cc1, cr in sel)
        cost = sum(row_cost(k, i, ot, cc5, cc1, cr) for _, k, i, ot, cc5, cc1, cr in sel)
        gtot += cost
        print(row([(day,11,False),(len(sel),6,True),(fmt(io),14,True),(fmt(tot),16,True),(usd(cost),12,True)]))
    print('-'*sum(w for _, w, _ in W))
    print(row([('合計',11,False),('',6,True),('',14,True),('',16,True),(usd(gtot),12,True)]))
