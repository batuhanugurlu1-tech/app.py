import streamlit as st
import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import sqlite3
import json
import os
import time
import uuid
from datetime import datetime, timedelta
import threading
import logging
import re

# ==========================================
# 🛡️ LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 🎨 UI THEME
# ==========================================
st.set_page_config(page_title="QUANT OMNI V12", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; border: 1px solid #333; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .paused { background-color: #FFD70022; color: #FFD700; border: 1px solid #FFD700; }
    .info-box { background-color: #1A1D23; border-left: 5px solid #00A3FF; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .action-box { background-color: #1A1D23; border-left: 5px solid #FFD700; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .warn-box { background-color: #1A1D23; border-left: 5px solid #FF4500; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .edge-box { background-color: #1A1D23; border-left: 5px solid #00FF41; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .fee-box { background-color: #1A1D23; border-left: 5px solid #FF69B4; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 🔒 CONSTANTS & VALIDATION
# ==========================================
VALID_SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]{2,20}$')
VALID_INTERVALS = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w","1M"}
AVAILABLE_COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","TAOUSDT","AVAXUSDT"]
AVAILABLE_TIMEFRAMES = ["15m","1h","4h","1d"]
GRID_COINS = ["BTCUSDT","ETHUSDT"]
HISTORY_LIMIT = 100

FRICTION_RATES = {
    'trend': 0.0005,  # %0.05/taraf
    'grid':  0.0004,  # %0.04/taraf
    'flash': 0.0010,  # %0.10/taraf (volatil slippage)
}
DEFAULT_FRICTION = 0.0005

def is_valid_symbol(s): return isinstance(s, str) and bool(VALID_SYMBOL_PATTERN.match(s))
def is_valid_interval(i): return isinstance(i, str) and i in VALID_INTERVALS
def safe_float(v, d=0.0):
    try: return float(v)
    except: return d
def safe_int(v, d=0):
    try: return int(v)
    except: return d
def get_friction(bot='trend'): return FRICTION_RATES.get(bot, DEFAULT_FRICTION)

def calculate_net_pnl(gross, margin, leverage, bot='trend'):
    f = get_friction(bot)
    cost = margin * leverage * f * 2
    return round(gross - cost, 2), round(cost, 2)

def calculate_grid_net_profit(margin, spacing_pct):
    gross = margin * spacing_pct / 100
    cost = margin * get_friction('grid') * 2
    return round(gross - cost, 2), round(cost, 2)

# ==========================================
# 🌐 HTTP SESSION
# ==========================================
@st.cache_resource
def get_http_session():
    s = requests.Session()
    r = Retry(total=3, backoff_factor=0.5, status_forcelist=[429,500,502,503,504], allowed_methods=["GET"])
    a = HTTPAdapter(max_retries=r, pool_connections=10, pool_maxsize=20)
    s.mount("https://", a); s.mount("http://", a)
    return s

def safe_api_get(session, url, timeout=10):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200: return None
        ct = r.headers.get('Content-Type','')
        if 'application/json' not in ct and 'text/plain' not in ct: return None
        return r.json()
    except: return None

# ==========================================
# 💾 SQLite DEPOLAMA (Firebase Yerine)
# Sıfır kota, sıfır maliyet, sıfır gecikme.
# Railway'de persistent volume'a bağla.
# ==========================================
DB_PATH = os.environ.get('DB_PATH', '/data/quant_omni.db')
_db_lock = threading.Lock()

def _ensure_dir():
    d = os.path.dirname(DB_PATH)
    if d and not os.path.exists(d):
        try: os.makedirs(d, exist_ok=True)
        except: pass

def get_conn():
    """Thread-safe SQLite bağlantısı."""
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")  # Concurrent read/write
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn

@st.cache_resource
def init_db():
    """Tabloları oluştur (ilk çalışmada)."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS configs (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS active_trades (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY,
            time TEXT NOT NULL,
            data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_history_time ON history(time DESC);
        CREATE TABLE IF NOT EXISTS states (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS signals (
            name TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '{}'
        );
    """)
    conn.commit()
    conn.close()
    logger.info("💾 SQLite DB hazır: " + DB_PATH)
    return True

# ── CRUD İŞLEMLERİ ──

def db_get_config(name):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM configs WHERE name=?", (name,)).fetchone()
        conn.close()
        return json.loads(row['data']) if row else {}

def db_set_config(name, data):
    with _db_lock:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO configs(name, data) VALUES(?,?)", (name, json.dumps(data)))
        conn.commit(); conn.close()

def db_get_trade(trade_id):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM active_trades WHERE id=?", (trade_id,)).fetchone()
        conn.close()
        return json.loads(row['data']) if row else None

def db_set_trade(trade_id, data):
    with _db_lock:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO active_trades(id, data) VALUES(?,?)", (trade_id, json.dumps(data)))
        conn.commit(); conn.close()

def db_update_trade(trade_id, updates):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM active_trades WHERE id=?", (trade_id,)).fetchone()
        if row:
            d = json.loads(row['data'])
            d.update(updates)
            conn.execute("UPDATE active_trades SET data=? WHERE id=?", (json.dumps(d), trade_id))
            conn.commit()
        conn.close()

def db_delete_trade(trade_id):
    with _db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM active_trades WHERE id=?", (trade_id,))
        conn.commit(); conn.close()

def db_get_all_trades(prefix=None):
    with _db_lock:
        conn = get_conn()
        if prefix:
            rows = conn.execute("SELECT id, data FROM active_trades WHERE id LIKE ?", (prefix+'%',)).fetchall()
        else:
            rows = conn.execute("SELECT id, data FROM active_trades").fetchall()
        conn.close()
        return [(r['id'], json.loads(r['data'])) for r in rows]

def db_add_history(data):
    hid = str(uuid.uuid4())[:12]
    t = data.get('time', datetime.now().isoformat())
    with _db_lock:
        conn = get_conn()
        conn.execute("INSERT INTO history(id, time, data) VALUES(?,?,?)", (hid, t, json.dumps(data)))
        conn.commit(); conn.close()

def db_get_history(limit=100):
    with _db_lock:
        conn = get_conn()
        rows = conn.execute("SELECT data FROM history ORDER BY time DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [json.loads(r['data']) for r in rows]

def db_get_history_by_bot(bot_name):
    with _db_lock:
        conn = get_conn()
        rows = conn.execute("SELECT data FROM history ORDER BY time DESC").fetchall()
        conn.close()
        return [json.loads(r['data']) for r in rows if json.loads(r['data']).get('bot') == bot_name]

def db_clear_history(bot_name=None):
    with _db_lock:
        conn = get_conn()
        if bot_name:
            rows = conn.execute("SELECT id, data FROM history").fetchall()
            for r in rows:
                if json.loads(r['data']).get('bot') == bot_name:
                    conn.execute("DELETE FROM history WHERE id=?", (r['id'],))
        else:
            conn.execute("DELETE FROM history")
        conn.commit(); conn.close()

def db_cleanup_history(max_rows=1000):
    with _db_lock:
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        if count > max_rows:
            conn.execute("""DELETE FROM history WHERE id NOT IN 
                (SELECT id FROM history ORDER BY time DESC LIMIT ?)""", (max_rows,))
            conn.commit()
        conn.close()

def db_get_state(name):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM states WHERE name=?", (name,)).fetchone()
        conn.close()
        return json.loads(row['data']) if row else {}

def db_set_state(name, data):
    with _db_lock:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO states(name, data) VALUES(?,?)", (name, json.dumps(data)))
        conn.commit(); conn.close()

def db_merge_state(name, updates):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM states WHERE name=?", (name,)).fetchone()
        d = json.loads(row['data']) if row else {}
        d.update(updates)
        conn.execute("INSERT OR REPLACE INTO states(name, data) VALUES(?,?)", (name, json.dumps(d)))
        conn.commit(); conn.close()

def db_delete_state(name):
    with _db_lock:
        conn = get_conn()
        conn.execute("DELETE FROM states WHERE name=?", (name,))
        conn.commit(); conn.close()

def db_set_signal(name, data):
    with _db_lock:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO signals(name, data) VALUES(?,?)", (name, json.dumps(data)))
        conn.commit(); conn.close()

def db_get_signal(name):
    with _db_lock:
        conn = get_conn()
        row = conn.execute("SELECT data FROM signals WHERE name=?", (name,)).fetchone()
        conn.close()
        return json.loads(row['data']) if row else None

def db_clear_all():
    with _db_lock:
        conn = get_conn()
        for t in ['history','active_trades','states','signals']:
            conn.execute(f"DELETE FROM {t}")
        conn.commit(); conn.close()

def close_position_atomic(trade_id, history_data):
    """Pozisyonu sil + history'ye yaz — tek transaction."""
    with _db_lock:
        conn = get_conn()
        try:
            t = history_data.get('time', datetime.now().isoformat())
            conn.execute("DELETE FROM active_trades WHERE id=?", (trade_id,))
            conn.execute("INSERT INTO history(id, time, data) VALUES(?,?,?)",
                        (str(uuid.uuid4())[:12], t, json.dumps(history_data)))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Atomic close hata: {e}")
        finally:
            conn.close()

# ==========================================
# 🚨 GÜNLÜK RİSK YÖNETİCİSİ
# ==========================================
def check_daily_loss_limit():
    state = db_get_state('daily_risk')
    today = datetime.now().strftime('%Y-%m-%d')
    if state.get('date') != today:
        db_set_state('daily_risk', {'date': today, 'daily_pnl': 0.0, 'daily_fees': 0.0, 'trade_count': 0,
                                     'daily_limit': safe_float(state.get('daily_limit', 100), 100)})
        return True, 0.0, safe_float(state.get('daily_limit', 100), 100)
    pnl = safe_float(state.get('daily_pnl', 0))
    limit = safe_float(state.get('daily_limit', 100), 100)
    return pnl > -limit, pnl, limit

def record_daily_pnl(pnl_usd, fee_usd):
    state = db_get_state('daily_risk')
    today = datetime.now().strftime('%Y-%m-%d')
    if state.get('date') != today:
        state = {'date': today, 'daily_pnl': 0.0, 'daily_fees': 0.0, 'trade_count': 0, 'daily_limit': safe_float(state.get('daily_limit',100),100)}
    state['daily_pnl'] = safe_float(state.get('daily_pnl',0)) + pnl_usd
    state['daily_fees'] = safe_float(state.get('daily_fees',0)) + fee_usd
    state['trade_count'] = safe_int(state.get('trade_count',0)) + 1
    db_set_state('daily_risk', state)

# ==========================================
# 🛠️ HELPERS
# ==========================================
def safe_str_time(v):
    if isinstance(v, str): return v
    if hasattr(v, 'isoformat'): return v.isoformat()
    return str(v)
def safe_short_time(v):
    s = safe_str_time(v)
    if 'T' in s: return s.split('T')[1][:8]
    if ' ' in s: return s.split(' ')[1][:8]
    return s[:8]
def safe_display_time(v): return safe_str_time(v).replace('T',' ')[:16]
def filter_valid_defaults(defaults, options):
    if not isinstance(defaults, list): return [options[0]] if options else []
    f = [d for d in defaults if d in options]
    return f if f else ([options[0]] if options else [])
def safe_selectbox_index(v, opts, fb=0):
    try: return opts.index(v)
    except: return fb

# ==========================================
# 📊 İNDİKATÖR MOTORU
# ==========================================
def calculate_advanced_indicators(df, fast_ema, slow_ema):
    if df.empty or len(df) < slow_ema: return df
    df = df.copy()
    df['EMA_F'] = df['C'].ewm(span=fast_ema, adjust=False).mean()
    df['EMA_S'] = df['C'].ewm(span=slow_ema, adjust=False).mean()
    delta = df['C'].diff()
    gain = (delta.where(delta>0,0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta<0,0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100-(100/(1+rs))).fillna(100.0)
    tr = pd.concat([df['H']-df['L'], np.abs(df['H']-df['C'].shift()), np.abs(df['L']-df['C'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, adjust=False).mean()
    up = df['H']-df['H'].shift(1); dn = df['L'].shift(1)-df['L']
    plus_dm = pd.Series(np.where((up>dn)&(up>0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn>up)&(dn>0), dn, 0.0), index=df.index)
    atr_s = df['ATR'].replace(0, np.nan)
    plus_di = 100*(plus_dm.ewm(alpha=1/14, adjust=False).mean()/atr_s)
    minus_di = 100*(minus_dm.ewm(alpha=1/14, adjust=False).mean()/atr_s)
    di_sum = (plus_di+minus_di).replace(0, np.nan)
    dx = (100*np.abs(plus_di-minus_di)/di_sum).fillna(0)
    df['ADX'] = dx.ewm(alpha=1/14, adjust=False).mean()
    df['Vol_SMA'] = df['V'].rolling(window=20, min_periods=1).mean()
    df['Vol_Ratio'] = df['V']/df['Vol_SMA'].replace(0, np.nan)
    bb_sma = df['C'].rolling(window=20, min_periods=1).mean()
    bb_std = df['C'].rolling(window=20, min_periods=1).std()
    df['BB_Upper'] = bb_sma+(bb_std*2); df['BB_Lower'] = bb_sma-(bb_std*2)
    bb_range = (df['BB_Upper']-df['BB_Lower']).replace(0, np.nan)
    df['BB_Pct'] = ((df['C']-df['BB_Lower'])/bb_range).fillna(0.5)
    return df

def fetch_klines(symbol, interval, limit=100):
    if not is_valid_symbol(symbol) or not is_valid_interval(interval): return pd.DataFrame()
    try:
        raw = safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
        if raw is None or not isinstance(raw, list) or len(raw)<2: return pd.DataFrame()
        df = pd.DataFrame(raw, columns=['T','O','H','L','C','V','CT','QV','NT','TBV','TBQV','I'])
        for c in ['O','H','L','C','V']: df[c] = pd.to_numeric(df[c], errors='coerce')
        df.dropna(subset=['O','H','L','C','V'], inplace=True)
        return df
    except: return pd.DataFrame()

# ==========================================
# ⚙️ MOTORLAR
# ==========================================
_engine_threads = {}
_engine_lock = threading.Lock()
_last_cleanup = {'time': datetime.min}

def ensure_engine_running(name, func):
    with _engine_lock:
        t = _engine_threads.get(name)
        if t is None or not t.is_alive():
            t = threading.Thread(target=func, daemon=True, name=name)
            t.start(); _engine_threads[name] = t

# ── 🐳 WHALE V12 ──
def whale_task():
    logger.info("🐳 Whale V12 Başlatıldı")
    while True:
        try:
            configs = db_get_config('trend')
            if configs and configs.get('autopilot', False):
                can_trade, dp, dl = check_daily_loss_limit()
                if not can_trade: time.sleep(120); continue

                coins = configs.get('coins', [])
                if not isinstance(coins, list): coins = []
                timeframe = configs.get('timeframe','1h')
                if not is_valid_interval(timeframe): timeframe = '1h'
                ema_f = safe_int(configs.get('ema_f',9),9)
                ema_s = safe_int(configs.get('ema_s',21),21)
                adx_t = safe_float(configs.get('adx_t',15),15)
                margin = safe_float(configs.get('margin',300),300)
                leverage = safe_float(configs.get('leverage',3),3)
                tp_atr = safe_float(configs.get('tp_atr',3.5),3.5)
                trailing_on = configs.get('trailing_stop', True)
                partial_on = configs.get('partial_tp', True)
                vol_on = configs.get('vol_filter', True)
                vol_min = safe_float(configs.get('vol_min_ratio',1.2),1.2)
                stale_h = safe_float(configs.get('stale_timeout_h',96),96)

                if ema_f<=0 or ema_s<=0 or margin<=0 or leverage<=0: time.sleep(120); continue

                current_status = {}
                for symbol in coins:
                    if not is_valid_symbol(symbol): continue
                    raw = fetch_klines(symbol, timeframe, 150)
                    if raw.empty or len(raw)<ema_s*2: continue
                    df = calculate_advanced_indicators(raw, ema_f, ema_s)
                    if len(df)<2: continue

                    cur, prev = df.iloc[-1], df.iloc[-2]
                    price = safe_float(cur['C']); clow = safe_float(cur['L']); chigh = safe_float(cur['H'])
                    if price<=0: continue
                    if any(pd.isna(cur[k]) for k in ['EMA_F','EMA_S','RSI','ADX','ATR']): continue

                    atr = safe_float(cur['ATR']); vr = safe_float(cur.get('Vol_Ratio',1),1); bb = safe_float(cur.get('BB_Pct',0.5),0.5)
                    current_status[symbol] = {'price':round(price,4),'ema_f':round(float(cur['EMA_F']),4),'ema_s':round(float(cur['EMA_S']),4),
                        'rsi':round(float(cur['RSI']),2),'adx':round(float(cur['ADX']),2),'vol_ratio':round(vr,2),'bb_pct':round(bb,2)}

                    tid = f"trend_{symbol}"
                    p = db_get_trade(tid)

                    if p:
                        p_sl=safe_float(p.get('sl')); p_tp=safe_float(p.get('tp')); p_entry=safe_float(p.get('entry',price))
                        p_type=p.get('type','LONG'); p_hi=safe_float(p.get('highest',p_entry)); p_lo=safe_float(p.get('lowest',p_entry))
                        p_partial=p.get('partial_done',False); p_margin=safe_float(p.get('margin',margin)); p_lev=safe_float(p.get('leverage',leverage))
                        if p_entry<=0: db_delete_trade(tid); continue

                        # Stale timeout
                        try: h_open=(datetime.now()-datetime.fromisoformat(p.get('time',''))).total_seconds()/3600
                        except: h_open=0
                        if h_open>=stale_h:
                            pct=((price-p_entry)/p_entry*100) if p_type=='LONG' else ((p_entry-price)/p_entry*100)
                            gross=(p_margin*pct*p_lev)/100; net,fee=calculate_net_pnl(gross,p_margin,p_lev,bot='trend')
                            close_position_atomic(tid, {'bot':'trend','symbol':symbol,'pnl_usd':net,'gross_pnl':round(gross,2),'fee_usd':fee,
                                'result':"TIMEOUT_WIN" if net>0 else "TIMEOUT_LOSS",'hours_open':round(h_open,1),'time':datetime.now().isoformat()})
                            record_daily_pnl(net,fee); continue

                        upd={}
                        if trailing_on and atr>0:
                            if p_type=='LONG' and price>p_hi:
                                upd['highest']=price; ns=price-(atr*2)
                                if ns>p_sl: upd['sl']=ns; p_sl=ns
                            elif p_type=='SHORT' and price<p_lo:
                                upd['lowest']=price; ns=price+(atr*2)
                                if ns<p_sl: upd['sl']=ns; p_sl=ns

                        if p_type=='LONG' and price>=p_entry+atr and p_sl<p_entry: upd['sl']=p_entry; p_sl=p_entry
                        elif p_type=='SHORT' and price<=p_entry-atr and p_sl>p_entry: upd['sl']=p_entry; p_sl=p_entry

                        res=""
                        if p_type=='LONG':
                            if clow<=p_sl: res="LOSS"
                            elif chigh>=p_tp: res="WIN"
                        else:
                            if chigh>=p_sl: res="LOSS"
                            elif clow<=p_tp: res="WIN"

                        if partial_on and not p_partial and not res:
                            tp1=p_entry+(p_tp-p_entry)*0.5 if p_type=='LONG' else p_entry-(p_entry-p_tp)*0.5
                            hit=(p_type=='LONG' and chigh>=tp1) or (p_type=='SHORT' and clow<=tp1)
                            if hit:
                                ppct=((tp1-p_entry)/p_entry*100) if p_type=='LONG' else ((p_entry-tp1)/p_entry*100)
                                half=p_margin*0.5; gross=(half*ppct*p_lev)/100; net,fee=calculate_net_pnl(gross,half,p_lev,bot='trend')
                                db_add_history({'bot':'trend','symbol':symbol,'pnl_usd':net,'gross_pnl':round(gross,2),'fee_usd':fee,'result':'PARTIAL_WIN','time':datetime.now().isoformat()})
                                record_daily_pnl(net,fee); upd['partial_done']=True; upd['margin']=half; p_margin=half

                        if res:
                            exit_p=p_sl if res=="LOSS" else p_tp
                            pct=((exit_p-p_entry)/p_entry*100) if p_type=='LONG' else ((p_entry-exit_p)/p_entry*100)
                            gross=(p_margin*pct*p_lev)/100; net,fee=calculate_net_pnl(gross,p_margin,p_lev,bot='trend')
                            close_position_atomic(tid, {'bot':'trend','symbol':symbol,'pnl_usd':net,'gross_pnl':round(gross,2),'fee_usd':fee,'result':res,'time':datetime.now().isoformat()})
                            record_daily_pnl(net,fee)
                        elif upd:
                            db_update_trade(tid, upd)
                    else:
                        if atr<=0: continue
                        if vol_on and vr<vol_min: continue
                        long_sig=(prev['EMA_F']<=prev['EMA_S'] and cur['EMA_F']>cur['EMA_S'] and cur['RSI']>50 and cur['ADX']>=adx_t and bb<0.85)
                        short_sig=(prev['EMA_F']>=prev['EMA_S'] and cur['EMA_F']<cur['EMA_S'] and cur['RSI']<50 and cur['ADX']>=adx_t and bb>0.15)
                        if long_sig:
                            db_set_trade(tid, {'type':'LONG','entry':price,'sl':price-(atr*2),'tp':price+(atr*tp_atr),'margin':margin,'leverage':leverage,
                                'symbol':symbol,'highest':price,'lowest':price,'partial_done':False,'time':datetime.now().isoformat()})
                        elif short_sig:
                            db_set_trade(tid, {'type':'SHORT','entry':price,'sl':price+(atr*2),'tp':price-(atr*tp_atr),'margin':margin,'leverage':leverage,
                                'symbol':symbol,'highest':price,'lowest':price,'partial_done':False,'time':datetime.now().isoformat()})

                db_set_state('trend_status', {'data':current_status,'updated':datetime.now().isoformat()})

            now=datetime.now()
            if (now-_last_cleanup['time']).total_seconds()>21600:
                db_cleanup_history(); _last_cleanup['time']=now

            tf=(configs or {}).get('timeframe','1h') if configs else '1h'
            poll={'15m':120,'1h':300,'4h':600,'1d':900}.get(tf,300)
            time.sleep(poll)
        except Exception as e:
            logger.error(f"Whale Error: {e}"); time.sleep(60)

# ── 🐜 ANT V12 ──
def ant_task():
    logger.info("🐜 Ant V12 Başlatıldı")
    while True:
        try:
            configs = db_get_config('grid')
            if configs and configs.get('autopilot', False):
                symbol=configs.get('coin','BTCUSDT')
                if not is_valid_symbol(symbol): time.sleep(20); continue
                spacing=safe_float(configs.get('grid_spacing_pct',0.5),0.5)
                margin=safe_float(configs.get('margin_per_grid',100),100)
                max_grids=safe_int(configs.get('max_grids',50),50)
                cb_pct=safe_float(configs.get('circuit_breaker_pct',15),15)
                dynamic=configs.get('dynamic_spacing',False)
                if spacing<=0 or margin<=0 or max_grids<=0: time.sleep(20); continue

                net_g, fee_g = calculate_grid_net_profit(margin, spacing)

                data=safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
                if not data: time.sleep(20); continue
                price=safe_float(data.get('price',0))
                if price<=0: time.sleep(20); continue

                eff_sp=spacing
                if dynamic:
                    kl=fetch_klines(symbol,'1h',30)
                    if not kl.empty and len(kl)>=14:
                        ki=calculate_advanced_indicators(kl,9,21)
                        a=safe_float(ki.iloc[-1].get('ATR',0))
                        if a>0 and price>0:
                            ap=(a/price)*100; eff_sp=max(spacing,ap*0.5); eff_sp=min(eff_sp,spacing*3)

                state=db_get_state('grid')
                grids=state.get('grids',[])
                if not isinstance(grids,list): grids=[]

                if grids:
                    entries=[safe_float(g.get('entry',0)) for g in grids if safe_float(g.get('entry',0))>0]
                    if entries:
                        highest=max(entries); dd=((highest-price)/highest)*100
                        if dd>=cb_pct:
                            unreal=sum(((price-safe_float(g.get('entry',0)))/safe_float(g.get('entry',price))*margin) for g in grids if safe_float(g.get('entry',0))>0)
                            db_set_state('grid', {**state,'circuit_breaker_active':True,'drawdown_pct':round(dd,2),'unrealized_pnl':round(unreal,2),'last_price':price,'updated':datetime.now().isoformat()})
                            time.sleep(20); continue

                new_grids=[]; total_prof=safe_float(state.get('total_profit',0)); total_fees=safe_float(state.get('total_fees',0))
                for g in grids:
                    ge=safe_float(g.get('entry',0))
                    if ge<=0: continue
                    if price>=ge*(1+eff_sp/100):
                        net,fee=calculate_grid_net_profit(margin,eff_sp)
                        db_add_history({'bot':'grid','symbol':symbol,'pnl_usd':net,'gross_pnl':round(margin*eff_sp/100,2),'fee_usd':fee,'result':'WIN','time':datetime.now().isoformat()})
                        total_prof+=net; total_fees+=fee; record_daily_pnl(net,fee)
                    else: new_grids.append(g)

                if len(new_grids)<max_grids:
                    ents=[safe_float(x.get('entry',0)) for x in new_grids if safe_float(x.get('entry',0))>0]
                    lb=min(ents) if ents else price
                    if price<=lb*(1-eff_sp/100) or not new_grids:
                        new_grids.append({'entry':price,'time':datetime.now().isoformat()})

                unreal=sum(((price-safe_float(g.get('entry',0)))/safe_float(g.get('entry',price))*margin) for g in new_grids if safe_float(g.get('entry',0))>0)
                db_set_state('grid', {'grids':new_grids,'total_profit':round(total_prof,2),'total_fees':round(total_fees,2),'last_price':price,
                    'effective_spacing':round(eff_sp,3),'unrealized_pnl':round(unreal,2),'circuit_breaker_active':False,
                    'net_per_grid':net_g,'fee_per_grid':fee_g,'fee_warning':net_g<=0,'updated':datetime.now().isoformat()})
            time.sleep(20)
        except Exception as e:
            logger.error(f"Ant Error: {e}"); time.sleep(20)

# ── ⚡ FALCON V12 ──
def falcon_task():
    logger.info("⚡ Falcon V12 Başlatıldı")
    while True:
        try:
            configs = db_get_config('flash')
            if configs and configs.get('autopilot', False):
                can_trade,_,_=check_daily_loss_limit()
                if not can_trade: time.sleep(120); continue

                spike_t=safe_float(configs.get('vol_spike',5),5); margin=safe_float(configs.get('margin',200),200)
                leverage=safe_float(configs.get('leverage',5),5); tp_pct=safe_float(configs.get('tp_pct',5),5)
                sl_pct=safe_float(configs.get('sl_pct',3),3); cooldown_h=6
                rsi_on=configs.get('rsi_filter',True); rsi_max=safe_float(configs.get('rsi_max',78),78)
                trail_on=configs.get('trailing_flash',True); min_qv=safe_float(configs.get('min_quote_vol',50_000_000),50_000_000)
                stale_h=safe_float(configs.get('stale_timeout_h',48),48)
                if margin<=0 or leverage<=0 or tp_pct<=0 or sl_pct<=0: time.sleep(30); continue

                p=db_get_trade('flash_pos')
                if p:
                    sym=p.get('symbol','')
                    if not sym or not is_valid_symbol(sym): db_delete_trade('flash_pos'); time.sleep(30); continue
                    pd_data=safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")
                    if not pd_data: time.sleep(30); continue
                    price=safe_float(pd_data.get('price',0)); p_entry=safe_float(p.get('entry',0))
                    p_type=p.get('type','LONG'); p_sl=safe_float(p.get('sl',0)); p_tp=safe_float(p.get('tp',0))
                    p_hi=safe_float(p.get('highest',p_entry)); p_margin=safe_float(p.get('margin',margin)); p_lev=safe_float(p.get('leverage',leverage))
                    if p_entry<=0: db_delete_trade('flash_pos'); time.sleep(30); continue

                    try: h_open=(datetime.now()-datetime.fromisoformat(p.get('time',''))).total_seconds()/3600
                    except: h_open=0
                    if h_open>=stale_h:
                        pct=((price-p_entry)/p_entry*100) if p_type=='LONG' else ((p_entry-price)/p_entry*100)
                        gross=(p_margin*pct*p_lev)/100; net,fee=calculate_net_pnl(gross,p_margin,p_lev,bot='flash')
                        close_position_atomic('flash_pos', {'bot':'flash','symbol':sym,'pnl_usd':net,'gross_pnl':round(gross,2),'fee_usd':fee,
                            'result':"TIMEOUT_WIN" if net>0 else "TIMEOUT_LOSS",'hours_open':round(h_open,1),'time':datetime.now().isoformat()})
                        record_daily_pnl(net,fee); db_merge_state('flash_cooldown',{sym:datetime.now().isoformat()})
                        time.sleep(30); continue

                    upd={}
                    if trail_on and p_type=='LONG' and price>p_hi:
                        upd['highest']=price; pd_dist=price-p_entry
                        if pd_dist>0: ns=p_entry+(pd_dist*0.5);
                        else: ns = p_sl
                        if ns>p_sl: upd['sl']=ns; p_sl=ns
                    elif trail_on and p_type=='SHORT' and price<p_hi:
                        upd['highest']=price; pd_dist=p_entry-price
                        if pd_dist>0: ns=p_entry-(pd_dist*0.5)
                        else: ns = p_sl
                        if ns<p_sl: upd['sl']=ns; p_sl=ns

                    res=""
                    if p_type=='LONG':
                        if price<=p_sl: res="LOSS"
                        elif price>=p_tp: res="WIN"
                    else:
                        if price>=p_sl: res="LOSS"
                        elif price<=p_tp: res="WIN"

                    if res:
                        exit_p=p_sl if res=="LOSS" else p_tp
                        pct=((exit_p-p_entry)/p_entry*100) if p_type=='LONG' else ((p_entry-exit_p)/p_entry*100)
                        gross=(p_margin*pct*p_lev)/100; net,fee=calculate_net_pnl(gross,p_margin,p_lev,bot='flash')
                        close_position_atomic('flash_pos', {'bot':'flash','symbol':sym,'pnl_usd':net,'gross_pnl':round(gross,2),'fee_usd':fee,'result':res,'time':datetime.now().isoformat()})
                        record_daily_pnl(net,fee); db_merge_state('flash_cooldown',{sym:datetime.now().isoformat()})
                    elif upd:
                        db_update_trade('flash_pos', upd)
                else:
                    ticks=safe_api_get(get_http_session(),"https://api.binance.com/api/v3/ticker/24hr")
                    cooldowns=db_get_state('flash_cooldown'); now=datetime.now()
                    if ticks and isinstance(ticks,list):
                        candidates=[]
                        for t in ticks:
                            try:
                                chg=float(t.get('priceChangePercent',0)); sym=t.get('symbol',''); qvol=safe_float(t.get('quoteVolume',0))
                                if chg<=spike_t or not sym.endswith('USDT') or not is_valid_symbol(sym) or qvol<min_qv: continue
                                lt=cooldowns.get(sym)
                                if lt:
                                    try:
                                        if (now-datetime.fromisoformat(lt)).total_seconds()<cooldown_h*3600: continue
                                    except: pass
                                candidates.append({'symbol':sym,'change':chg,'qvol':qvol,'price':safe_float(t.get('lastPrice',0)),
                                    'high':safe_float(t.get('highPrice',0)),'quality':qvol*(chg/100)})
                            except: continue

                        if candidates:
                            top=max(candidates, key=lambda x: x['quality']); sym=top['symbol']; price=top['price']; high24=top['high']
                            if price<=0: time.sleep(30); continue
                            if high24>0:
                                pullback=((high24-price)/high24)*100
                                if pullback>3.0: time.sleep(30); continue
                            kl=fetch_klines(sym,'1h',6)
                            if not kl.empty and len(kl)>=4:
                                if (kl.tail(4)['C']>kl.tail(4)['O']).sum()<2: time.sleep(30); continue

                            should_enter=True; entry_rsi=None
                            if rsi_on:
                                kl2=fetch_klines(sym,'15m',30)
                                if not kl2.empty and len(kl2)>=14:
                                    ki=calculate_advanced_indicators(kl2,9,21)
                                    entry_rsi=safe_float(ki.iloc[-1].get('RSI',50))
                                    if entry_rsi>rsi_max: should_enter=False

                            if should_enter:
                                db_set_trade('flash_pos', {'type':'LONG','entry':price,'sl':price*(1-sl_pct/100),'tp':price*(1+tp_pct/100),
                                    'margin':margin,'leverage':leverage,'symbol':sym,'highest':price,'quality_score':round(top['quality'],2),
                                    'entry_rsi':entry_rsi,'time':datetime.now().isoformat()})
                                db_set_signal('flash', {'symbol':sym,'change':str(round(top['change'],2)),'vol':str(round(top['qvol'])),
                                    'quality':round(top['quality'],2),'rsi':entry_rsi,'time':datetime.now().isoformat()})
            time.sleep(30)
        except Exception as e:
            logger.error(f"Falcon Error: {e}"); time.sleep(30)

# ==========================================
# 🖥️ UI
# ==========================================
def calc_stats(hist, bot):
    bd=[h for h in hist if h.get('bot')==bot]
    if not bd: return 0,0.0,0.0,0,0.0
    df=pd.DataFrame(bd)
    if 'result' not in df.columns: df['result']='UNKNOWN'
    df['pnl_usd']=pd.to_numeric(df.get('pnl_usd',0),errors='coerce').fillna(0)
    df['fee_usd']=pd.to_numeric(df.get('fee_usd',0),errors='coerce').fillna(0)
    wins=len(df[df['result'].isin(['WIN','PARTIAL_WIN','TIMEOUT_WIN'])])
    total=len(df); wr=(wins/total*100) if total>0 else 0
    return total,wr,float(df['pnl_usd'].sum()),len(df[df['result']=='PARTIAL_WIN']),float(df['fee_usd'].sum())

def render_df(data_list, rename_map):
    if not data_list: return None
    df=pd.DataFrame(data_list)
    ex={k:v for k,v in rename_map.items() if k in df.columns}
    df=df.rename(columns=ex)
    cols=[v for v in rename_map.values() if v in df.columns]
    return df[cols] if cols else df

def get_engine_status():
    return {n:(_engine_threads.get(n) is not None and _engine_threads[n].is_alive()) for n in ['whale','ant','falcon']}

def main():
    st.title("🛡️ QUANT OMNI V12")
    st.caption("SQLite • Zero Quota • Fee-Aware • Trailing • Daily Limit")

    db_ready = init_db()
    if not db_ready:
        st.markdown('<div class="status-bar offline">❌ DB HATASI</div>', unsafe_allow_html=True)
        return

    ensure_engine_running('whale', whale_task)
    ensure_engine_running('ant', ant_task)
    ensure_engine_running('falcon', falcon_task)

    es=get_engine_status()
    can_trade,daily_pnl,daily_limit=check_daily_loss_limit()

    if not can_trade:
        st.markdown(f'<div class="status-bar paused">⏸️ GÜNLÜK LİMİT AŞILDI! PnL: ${daily_pnl:.2f} / -${daily_limit:.2f}</div>', unsafe_allow_html=True)
    elif all(es.values()):
        st.markdown('<div class="status-bar online">● V12 ÇEVRİMİÇİ | SQLite | 🐳🐜⚡ AKTİF</div>', unsafe_allow_html=True)
    else:
        dead=[k for k,v in es.items() if not v]
        st.markdown(f'<div class="status-bar offline">⚠️ {", ".join(dead)} başlatılıyor</div>', unsafe_allow_html=True)

    fr_t=get_friction('trend'); fr_f=get_friction('flash'); fr_g=get_friction('grid')
    st.markdown(f"""<div class='fee-box'>💰 <b>Fee:</b> Trend %{fr_t*100:.2f} | Grid %{fr_g*100:.2f} | Flash %{fr_f*100:.2f} | PnL: <b>${daily_pnl:.2f}</b> / -${daily_limit:.2f}</div>""", unsafe_allow_html=True)

    all_history = db_get_history(HISTORY_LIMIT)

    col1,col2=st.columns([4,1])
    with col2:
        if st.button("🔄 Yenile", use_container_width=True): st.rerun()

    tabs = st.tabs(["🐳 TREND","🐜 GRID","⚡ FLASH","📊 ANALİTİK","⚙️ RİSK"])

    # ═══ TREND ═══
    with tabs[0]:
        st.markdown("""<div class='info-box'><b>V12:</b> EMA+ADX+Volume+BB giriş. Trailing, Breakeven, Partial TP, Stale Timeout. Fee dahil.</div>""", unsafe_allow_html=True)
        t_tot,t_wr,t_pnl,t_part,t_fees=calc_stats(all_history,'trend')
        c1,c2,c3,c4=st.columns(4)
        c1.metric("İşlem",t_tot); c2.metric("Win Rate",f"%{t_wr:.1f}"); c3.metric("Net Kâr",f"${t_pnl:.2f}"); c4.metric("Fee",f"${t_fees:.2f}")
        st.divider()

        col1,col2=st.columns([1,2])
        with col1:
            cfg=db_get_config('trend')
            with st.form("t_cfg"):
                m=st.number_input("Marjin($)",10,5000,safe_int(cfg.get('margin',300),300))
                lv=st.slider("Kaldıraç",1,20,safe_int(cfg.get('leverage',3),3))
                st.caption(f"💰 Fee: ${m*lv*get_friction('trend')*2:.2f}/trade")
                saved=cfg.get('coins',["BTCUSDT"])
                coins=st.multiselect("Varlıklar",AVAILABLE_COINS,default=filter_valid_defaults(saved,AVAILABLE_COINS))
                tf=st.selectbox("TF",AVAILABLE_TIMEFRAMES,index=safe_selectbox_index(cfg.get('timeframe','1h'),AVAILABLE_TIMEFRAMES,1))
                ef=st.slider("Hızlı EMA",5,50,safe_int(cfg.get('ema_f',9),9))
                es_v=st.slider("Yavaş EMA",10,200,safe_int(cfg.get('ema_s',21),21))
                adx=st.slider("ADX",0,40,safe_int(cfg.get('adx_t',15),15))
                tp=st.slider("TP(ATRx)",1.0,6.0,float(min(max(safe_float(cfg.get('tp_atr',3.5)),1),6)))
                st.markdown("**🧠 Filtreler**")
                tr=st.checkbox("Trailing",value=cfg.get('trailing_stop',True))
                pt=st.checkbox("Partial TP",value=cfg.get('partial_tp',True))
                vf=st.checkbox("Volume",value=cfg.get('vol_filter',True))
                vr=st.slider("Min Vol",0.5,3.0,float(min(max(safe_float(cfg.get('vol_min_ratio',1.2)),0.5),3.0)),0.1)
                stale=st.number_input("Stale(saat)",12,240,safe_int(cfg.get('stale_timeout_h',96),96))
                auto=st.checkbox("Otopilot",value=cfg.get('autopilot',False))
                if st.form_submit_button("Kaydet"):
                    db_set_config('trend',{'margin':m,'leverage':lv,'coins':coins,'timeframe':tf,'ema_f':ef,'ema_s':es_v,'adx_t':adx,'tp_atr':tp,
                        'trailing_stop':tr,'partial_tp':pt,'vol_filter':vf,'vol_min_ratio':vr,'stale_timeout_h':stale,'autopilot':auto})
                    st.rerun()
            if st.button("🗑️ Trend Sıfırla"):
                db_clear_history('trend')
                for tid,_ in db_get_all_trades('trend_'): db_delete_trade(tid)
                db_delete_state('trend_status')
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            st.markdown("#### `🟢 AKTİF İŞLEMLER`")
            tt=[d for _,d in db_get_all_trades('trend_')]
            if tt:
                dft=render_df(tt,{'symbol':'Varlık','type':'Yön','entry':'Giriş','tp':'TP','sl':'SL','margin':'Marjin($)','partial_done':'Partial'})
                if dft is not None: st.dataframe(dft,use_container_width=True)
                for trade in tt:
                    ent=safe_float(trade.get('entry',0)); sl=safe_float(trade.get('sl',0)); tp=safe_float(trade.get('tp',0))
                    if ent>0 and tp>0 and sl>0:
                        risk=abs(ent-sl); reward=abs(tp-ent); rr=reward/risk if risk>0 else 0
                        st.caption(f"{trade.get('symbol','?')}: R:R=1:{rr:.1f} | {'🔒 BE' if sl>=ent and trade.get('type')=='LONG' else '⏳'}")
            else: st.info("Açık işlem yok.")

            st.markdown("#### `📡 RADAR`")
            sd=db_get_state('trend_status')
            sdata=sd.get('data',{})
            if sdata:
                rl=[]
                at=float(cfg.get('adx_t',15))
                for s,v in sdata.items():
                    rl.append({'Varlık':s,'Trend':"🟢" if v['ema_f']>v['ema_s'] else "🔴",'ADX':f"{v['adx']} {'✅' if v['adx']>=at else '⏳'}",
                        'RSI':v['rsi'],'Vol':f"{v.get('vol_ratio','—')}x",'BB':f"{v.get('bb_pct',0.5):.0%}"})
                st.dataframe(pd.DataFrame(rl),use_container_width=True)
            else: st.info("Radar veri topluyor...")

    # ═══ GRID ═══
    with tabs[1]:
        st.markdown("""<div class='info-box'><b>V12:</b> Grid + Circuit Breaker + Dinamik Spacing + Fee-aware.</div>""", unsafe_allow_html=True)
        g_tot,_,g_pnl,_,g_fees=calc_stats(all_history,'grid')
        c1,c2,c3=st.columns(3)
        c1.metric("Grid",g_tot); c2.metric("Net Kâr",f"${g_pnl:.2f}"); c3.metric("Fee",f"${g_fees:.2f}")
        st.divider()

        col1,col2=st.columns([1,2])
        with col1:
            cg=db_get_config('grid')
            with st.form("g_cfg"):
                gc=st.selectbox("Coin",GRID_COINS,index=safe_selectbox_index(cg.get('coin','BTCUSDT'),GRID_COINS,0))
                sp=st.number_input("Spacing(%)",0.1,5.0,float(min(max(safe_float(cg.get('grid_spacing_pct',0.5)),0.1),5.0)))
                mg=st.number_input("Parça($)",10,500,safe_int(cg.get('margin_per_grid',100),100))
                mx=st.number_input("Maks",10,100,safe_int(cg.get('max_grids',50),50))
                ng,fg=calculate_grid_net_profit(mg,sp)
                if ng<=0: st.error(f"⛔ Net negatif! ${ng:.2f}")
                else: st.success(f"✅ Net: ${ng:.2f}/parça")
                cb=st.slider("Circuit(%)",5.0,30.0,float(min(max(safe_float(cg.get('circuit_breaker_pct',15)),5),30)),1.0)
                dy=st.checkbox("Dinamik",value=cg.get('dynamic_spacing',False))
                ag=st.checkbox("Otopilot",value=cg.get('autopilot',False))
                if st.form_submit_button("Kaydet"):
                    db_set_config('grid',{'coin':gc,'grid_spacing_pct':sp,'margin_per_grid':mg,'max_grids':mx,'circuit_breaker_pct':cb,'dynamic_spacing':dy,'autopilot':ag})
                    st.rerun()
            if st.button("🗑️ Grid Sıfırla"):
                db_clear_history('grid'); db_delete_state('grid')
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            mr=safe_int(cg.get('margin_per_grid',100))*safe_int(cg.get('max_grids',50))
            st.markdown(f"""<div class='warn-box'>⚠️ Maks Risk: ${mr:,.0f}</div>""", unsafe_allow_html=True)
            gs=db_get_state('grid')
            if gs.get('circuit_breaker_active'): st.markdown(f"""<div class='warn-box'>🚨 CIRCUIT BREAKER! %{gs.get('drawdown_pct',0):.1f}</div>""", unsafe_allow_html=True)
            if gs.get('fee_warning'): st.markdown("""<div class='warn-box'>⛔ Fee > kâr! Spacing artır.</div>""", unsafe_allow_html=True)

            u1,u2=st.columns(2)
            u1.metric("Unrealized",f"${safe_float(gs.get('unrealized_pnl',0)):.2f}")
            u2.metric("Spacing",f"%{safe_float(gs.get('effective_spacing',sp)):.2f}")

            gl=gs.get('grids',[])
            if isinstance(gl,list) and gl:
                dg=pd.DataFrame(gl)
                if 'entry' in dg.columns:
                    esp=safe_float(gs.get('effective_spacing',sp))
                    dg['entry']=pd.to_numeric(dg['entry'],errors='coerce').fillna(0)
                    dg['Hedef']=dg['entry']*(1+esp/100)
                    dg['Zaman']=dg['time'].apply(safe_short_time) if 'time' in dg.columns else '—'
                    lp=safe_float(gs.get('last_price',0))
                    if lp>0: dg['PnL($)']=((lp-dg['entry'])/dg['entry']*safe_int(cg.get('margin_per_grid',100))).round(2)
                    dg=dg.rename(columns={'entry':'Alış'})
                    cols=['Zaman','Alış','Hedef']
                    if 'PnL($)' in dg.columns: cols.append('PnL($)')
                    st.dataframe(dg[cols].sort_values('Alış'),use_container_width=True)
                    st.caption(f"Açık: {len(dg)}/{mx}")
            else: st.info("Parça yok.")

    # ═══ FLASH ═══
    with tabs[2]:
        st.markdown("""<div class='info-box'><b>V12:</b> Momentum + RSI + Kalite + Trailing. Flash slippage dahil.</div>""", unsafe_allow_html=True)
        f_tot,f_wr,f_pnl,_,f_fees=calc_stats(all_history,'flash')
        c1,c2,c3=st.columns(3)
        c1.metric("Patlama",f_tot); c2.metric("Win%",f"%{f_wr:.1f}"); c3.metric("Net",f"${f_pnl:.2f}")
        st.caption(f"Fee: ${f_fees:.2f}")
        st.divider()

        col1,col2=st.columns([1,2])
        with col1:
            cf=db_get_config('flash')
            with st.form("f_cfg"):
                mf=st.number_input("Marjin($)",10,5000,safe_int(cf.get('margin',200),200))
                lf=st.slider("Kaldıraç",1,20,safe_int(cf.get('leverage',5),5))
                st.caption(f"💰 Fee: ${mf*lf*get_friction('flash')*2:.2f} (slippage dahil)")
                tpf=st.number_input("TP(%)",1.0,20.0,float(min(max(safe_float(cf.get('tp_pct',5)),1),20)))
                slf=st.number_input("SL(%)",1.0,20.0,float(min(max(safe_float(cf.get('sl_pct',3)),1),20)))
                fs=st.slider("Spike(%)",2.0,50.0,float(min(max(safe_float(cf.get('vol_spike',10)),2),50)))
                st.markdown("**🧠 Filtreler**")
                rf=st.checkbox("RSI",value=cf.get('rsi_filter',True))
                rm=st.slider("RSI Maks",60,90,safe_int(cf.get('rsi_max',78),78))
                trf=st.checkbox("Trailing",value=cf.get('trailing_flash',True))
                mv=st.number_input("Min Vol($)",1_000_000,500_000_000,safe_int(cf.get('min_quote_vol',50_000_000),50_000_000),step=10_000_000)
                stf=st.number_input("Stale(saat)",6,96,safe_int(cf.get('stale_timeout_h',48),48))
                fa=st.checkbox("Otopilot",value=cf.get('autopilot',False))
                if st.form_submit_button("Kaydet"):
                    db_set_config('flash',{'margin':mf,'leverage':lf,'tp_pct':tpf,'sl_pct':slf,'vol_spike':fs,'rsi_filter':rf,'rsi_max':rm,
                        'trailing_flash':trf,'min_quote_vol':mv,'stale_timeout_h':stf,'autopilot':fa})
                    st.rerun()
            if st.button("🗑️ Flash Sıfırla"):
                db_clear_history('flash'); db_delete_trade('flash_pos'); db_delete_state('flash_cooldown')
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            fp=db_get_trade('flash_pos')
            if fp:
                dff=render_df([fp],{'symbol':'Varlık','type':'Yön','entry':'Giriş','tp':'TP','sl':'SL','margin':'Bütçe($)','quality_score':'Kalite','entry_rsi':'RSI'})
                if dff is not None: st.dataframe(dff,use_container_width=True)
                ep=safe_float(fp.get('entry',0)); cs=safe_float(fp.get('sl',0))
                if ep>0 and cs>ep: st.markdown("""<div class='edge-box'>🔒 Trailing — kayıpsız</div>""", unsafe_allow_html=True)
            else:
                sig=db_get_signal('flash')
                if sig: st.warning(f"📡 SON: {sig.get('symbol','?')} Δ%{sig.get('change','?')} Q:{sig.get('quality','?')}")
                st.info("Hedef bekleniyor...")

            st.markdown("#### `🕒 SOĞUMA`")
            cd=db_get_state('flash_cooldown')
            if cd:
                cl=[]; now=datetime.now()
                for s,ts in cd.items():
                    try:
                        hp=(now-datetime.fromisoformat(ts)).total_seconds()/3600
                        if hp<6: cl.append({"Varlık":s,"Kalan":f"{6-hp:.1f}h"})
                    except: pass
                if cl: st.dataframe(pd.DataFrame(cl),use_container_width=True)
                else: st.caption("Boş")

    # ═══ ANALİTİK ═══
    with tabs[3]:
        st.markdown("### 📊 Analitik")
        ec=st.columns(3)
        for i,(n,a) in enumerate(es.items()):
            lb={'whale':'🐳 Trend','ant':'🐜 Grid','falcon':'⚡ Flash'}[n]
            ec[i].metric(lb,"✅" if a else "❌")

        if st.button("🚨 Tüm Sistemi Sıfırla",type="primary"):
            db_clear_all(); st.success("Sıfırlandı!"); time.sleep(2); st.rerun()

        if all_history:
            df=pd.DataFrame(all_history)
            df['pnl_usd']=pd.to_numeric(df.get('pnl_usd',0),errors='coerce').fillna(0)
            df['fee_usd']=pd.to_numeric(df.get('fee_usd',0),errors='coerce').fillna(0)
            df['gross_pnl']=pd.to_numeric(df.get('gross_pnl',0),errors='coerce').fillna(0)
            if 'result' not in df.columns: df['result']='UNKNOWN'

            m1,m2,m3,m4=st.columns(4)
            m1.metric("Net Kâr",f"${df['pnl_usd'].sum():.2f}"); m2.metric("Brüt",f"${df['gross_pnl'].sum():.2f}")
            m3.metric("Fee",f"${df['fee_usd'].sum():.2f}")
            wins=len(df[df['result'].isin(['WIN','PARTIAL_WIN','TIMEOUT_WIN'])])
            m4.metric("Win%",f"%{(wins/max(len(df),1))*100:.1f}")

            gt=df['gross_pnl'].sum(); ft=df['fee_usd'].sum()
            if gt>0:
                fd=(ft/gt)*100
                st.markdown(f"""<div class='fee-box'>📉 Fee Drag: %{fd:.1f} {'⚠️' if fd>30 else '✅' if fd<20 else '⚡'}</div>""", unsafe_allow_html=True)

            if 'time' in df.columns:
                st.markdown("#### `📈 KÜMÜLATİF`")
                dfs=df.sort_values('time'); dfs['net_cum']=dfs['pnl_usd'].cumsum(); dfs['gross_cum']=dfs['gross_pnl'].cumsum()
                chart=dfs.set_index('time')[['net_cum','gross_cum']]; chart.columns=['Net','Brüt']
                st.line_chart(chart)

            st.markdown("#### `📜 KASA DEFTERİ`")
            df['Zaman']=df['time'].apply(safe_display_time) if 'time' in df.columns else '—'
            rn={'bot':'Motor','symbol':'Varlık','pnl_usd':'Net($)','gross_pnl':'Brüt($)','fee_usd':'Fee($)','result':'Sonuç'}
            ex={k:v for k,v in rn.items() if k in df.columns}; df=df.rename(columns=ex)
            dc=['Zaman']+[v for v in rn.values() if v in df.columns]
            st.dataframe(df[dc].sort_values('Zaman',ascending=False),use_container_width=True)
        else: st.info("İlk işlem bekleniyor.")

    # ═══ RİSK ═══
    with tabs[4]:
        st.markdown("### ⚙️ Risk Yönetimi")
        rd=db_get_state('daily_risk')
        with st.form("risk_cfg"):
            dl=st.number_input("Günlük Maks Kayıp($)",10,1000,safe_int(rd.get('daily_limit',100),100))
            if st.form_submit_button("Güncelle"):
                rd['daily_limit']=dl; db_set_state('daily_risk',rd); st.rerun()

        r1,r2,r3,r4=st.columns(4)
        r1.metric("PnL",f"${safe_float(rd.get('daily_pnl',0)):.2f}"); r2.metric("Fee",f"${safe_float(rd.get('daily_fees',0)):.2f}")
        r3.metric("Trade",safe_int(rd.get('trade_count',0))); r4.metric("Limit",f"-${safe_float(rd.get('daily_limit',100)):.0f}")

        st.markdown("#### `💰 FEE HESAPLAYICI`")
        hc1,hc2=st.columns(2)
        with hc1:
            tb=st.selectbox("Bot",['trend','grid','flash']); tm=st.number_input("Marjin($)",50,5000,300); tl=st.slider("Kaldıraç",1,20,3)
            st.metric("Fee",f"${tm*tl*get_friction(tb)*2:.2f}")
        with hc2:
            ts=st.number_input("Spacing(%)",0.1,5.0,0.5); tgm=st.number_input("Grid$",10,500,100)
            gn,gf=calculate_grid_net_profit(tgm,ts); st.metric("Net",f"${gn:.2f}")
            if gn<=0: st.error("⛔ Negatif!")

        st.markdown("---")
        st.caption(f"Friction: Trend %{get_friction('trend')*100:.3f} | Grid %{get_friction('grid')*100:.3f} | Flash %{get_friction('flash')*100:.3f}")
        st.caption(f"DB: {DB_PATH} | Boyut: {os.path.getsize(DB_PATH)/1024:.1f} KB" if os.path.exists(DB_PATH) else f"DB: {DB_PATH} (henüz oluşmadı)")

if __name__ == "__main__":
    main()
