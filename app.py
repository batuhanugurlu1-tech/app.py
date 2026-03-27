import streamlit as st
import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import time
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
st.set_page_config(page_title="QUANT OMNI V11 FINAL", layout="wide")

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
VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}

AVAILABLE_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "TAOUSDT", "AVAXUSDT"]
AVAILABLE_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
GRID_COINS = ["BTCUSDT", "ETHUSDT"]
HISTORY_QUERY_LIMIT = 100
HISTORY_MAX_DOCS = 1000

# ══════════════════════════════════════════
# 💰 İŞLEM MALİYETİ SABİTLERİ (BOT-BAZLI)
# Paper trade sonuçlarının gerçeği yansıtması
# için HER trade'e uygulanır.
#
# Neden bot bazlı?
# - Trend: Sakin piyasada giriş, slippage düşük
# - Grid:  Spot benzeri, sakin alım, slippage minimal
# - Flash: Hacim patlaması anında giriş, order book
#          dağınık, slippage 5-10x normal seviye
# ══════════════════════════════════════════
FRICTION_RATES = {
    'trend': 0.0005,   # %0.05/taraf = %0.04 fee + %0.01 slippage (sakin giriş)
    'grid':  0.0004,   # %0.04/taraf = %0.04 fee + ~0 slippage (limit emir benzeri)
    'flash': 0.0010,   # %0.10/taraf = %0.04 fee + %0.06 slippage (volatil an!)
}
DEFAULT_FRICTION = 0.0005

def is_valid_symbol(s): return isinstance(s, str) and bool(VALID_SYMBOL_PATTERN.match(s))
def is_valid_interval(i): return isinstance(i, str) and i in VALID_INTERVALS

def safe_float(val, default=0.0):
    try: return float(val)
    except (TypeError, ValueError): return default

def safe_int(val, default=0):
    try: return int(val)
    except (TypeError, ValueError): return default

# ==========================================
# 💸 FEE & NET PNL HESAPLAYICI (BOT-BAZLI)
# ==========================================
def get_friction(bot_name='trend'):
    """Bot tipine göre friction rate döndür."""
    return FRICTION_RATES.get(bot_name, DEFAULT_FRICTION)

def calculate_net_pnl(gross_pnl_usd, margin, leverage, bot='trend'):
    """
    Gerçek net PnL = brüt kâr - toplam sürtünme maliyeti.
    Bot tipine göre farklı slippage uygulanır.
    """
    friction = get_friction(bot)
    notional = margin * leverage
    total_friction = notional * friction * 2  # giriş + çıkış
    net = gross_pnl_usd - total_friction
    return round(net, 2), round(total_friction, 2)

def calculate_grid_net_profit(margin, spacing_pct):
    """Grid parça kârından fee'yi düş. Net negatifse grid spacing çok dar."""
    gross = margin * spacing_pct / 100
    friction = get_friction('grid')
    total_fee = margin * friction * 2
    net = gross - total_fee
    return round(net, 2), round(total_fee, 2)

# ==========================================
# 🌐 HTTP SESSION
# ==========================================
@st.cache_resource
def get_http_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def safe_api_get(session, url, timeout=10):
    try:
        res = session.get(url, timeout=timeout)
        if res.status_code != 200: return None
        ct = res.headers.get('Content-Type', '')
        if 'application/json' not in ct and 'text/plain' not in ct: return None
        return res.json()
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None

# ==========================================
# ☁️ FIREBASE
# ==========================================
@st.cache_resource
def get_db():
    if not firebase_admin._apps:
        try:
            fb = os.environ.get('FIREBASE_CONFIG', '').strip()
            if not fb: return None
            cfg = json.loads(fb)
            if "project_id" in cfg:
                cred = credentials.Certificate(cfg)
                firebase_admin.initialize_app(cred)
            else:
                firebase_admin.initialize_app(options=cfg)
        except Exception as e:
            logger.error(f"Firebase Init: {e}")
            return None
    return firestore.client()

db = get_db()
app_id = os.environ.get('APP_ID', 'quant-lab-v11')

def get_data_ref(col):
    if db is None: raise RuntimeError("Firebase bağlantısı yok.")
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection(col)

def close_position_atomically(pos_ref, history_data):
    txn = db.transaction()
    @firestore.transactional
    def _close(t, pr, hd):
        t.delete(pr)
        t.set(get_data_ref('history').document(), hd)
    try:
        _close(txn, pos_ref, history_data)
        return True
    except Exception as e:
        logger.error(f"Txn Error: {e}")
        return False

def cleanup_old_history(max_docs=HISTORY_MAX_DOCS):
    try:
        docs = list(get_data_ref('history').order_by('time', direction=firestore.Query.DESCENDING).limit(max_docs + 200).get())
        if len(docs) > max_docs:
            excess = docs[max_docs:]
            batch = db.batch()
            c = 0
            for d in excess:
                batch.delete(d.reference)
                c += 1
                if c >= 400: batch.commit(); batch = db.batch(); c = 0
            if c > 0: batch.commit()
    except Exception: pass

# ==========================================
# 🚨 GÜNLÜK RİSK YÖNETİCİSİ
# Tüm botları kapsayan merkezi fren sistemi
# ==========================================
def check_daily_loss_limit():
    """Bugünkü toplam kayıp limitin altında mı? True = trade edilebilir."""
    try:
        state = get_data_ref('states').document('daily_risk').get()
        if not state.exists: return True, 0.0, 100.0

        data = state.to_dict()
        today = datetime.now().strftime('%Y-%m-%d')

        if data.get('date') != today:
            # Yeni gün — sıfırla
            get_data_ref('states').document('daily_risk').set({
                'date': today, 'daily_pnl': 0.0, 'daily_fees': 0.0, 'trade_count': 0
            })
            return True, 0.0, safe_float(data.get('daily_limit', 100.0), 100.0)

        daily_pnl = safe_float(data.get('daily_pnl', 0))
        daily_limit = safe_float(data.get('daily_limit', 100.0), 100.0)
        return daily_pnl > -daily_limit, daily_pnl, daily_limit
    except Exception:
        return True, 0.0, 100.0

def record_daily_pnl(pnl_usd, fee_usd):
    """Trade kapandığında günlük PnL'e ekle."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        ref = get_data_ref('states').document('daily_risk')
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict()
            if data.get('date') == today:
                ref.update({
                    'daily_pnl': firestore.Increment(pnl_usd),
                    'daily_fees': firestore.Increment(fee_usd),
                    'trade_count': firestore.Increment(1)
                })
                return
        ref.set({'date': today, 'daily_pnl': pnl_usd, 'daily_fees': fee_usd, 'trade_count': 1,
                 'daily_limit': safe_float((doc.to_dict() or {}).get('daily_limit', 100.0), 100.0)})
    except Exception as e:
        logger.error(f"Daily PnL kayıt hatası: {e}")

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

def safe_display_time(v):
    return safe_str_time(v).replace('T', ' ')[:16]

def filter_valid_defaults(defaults, options):
    if not isinstance(defaults, list): return [options[0]] if options else []
    f = [d for d in defaults if d in options]
    return f if f else ([options[0]] if options else [])

def safe_selectbox_index(value, options, fallback=0):
    try: return options.index(value)
    except ValueError: return fallback

# ==========================================
# 📊 İNDİKATÖR MOTORU
# ==========================================
def calculate_advanced_indicators(df, fast_ema, slow_ema):
    if df.empty or len(df) < slow_ema: return df
    df = df.copy()
    df['EMA_F'] = df['C'].ewm(span=fast_ema, adjust=False).mean()
    df['EMA_S'] = df['C'].ewm(span=slow_ema, adjust=False).mean()

    delta = df['C'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(100.0)

    tr = pd.concat([df['H']-df['L'], np.abs(df['H']-df['C'].shift()), np.abs(df['L']-df['C'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, adjust=False).mean()

    up = df['H'] - df['H'].shift(1)
    dn = df['L'].shift(1) - df['L']
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)

    atr_s = df['ATR'].replace(0, np.nan)
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_s)
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_s)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = (100 * np.abs(plus_di - minus_di) / di_sum).fillna(0)
    df['ADX'] = dx.ewm(alpha=1/14, adjust=False).mean()

    df['Vol_SMA'] = df['V'].rolling(window=20, min_periods=1).mean()
    df['Vol_Ratio'] = df['V'] / df['Vol_SMA'].replace(0, np.nan)

    bb_sma = df['C'].rolling(window=20, min_periods=1).mean()
    bb_std = df['C'].rolling(window=20, min_periods=1).std()
    df['BB_Upper'] = bb_sma + (bb_std * 2)
    df['BB_Lower'] = bb_sma - (bb_std * 2)
    bb_range = (df['BB_Upper'] - df['BB_Lower']).replace(0, np.nan)
    df['BB_Pct'] = ((df['C'] - df['BB_Lower']) / bb_range).fillna(0.5)

    return df

def fetch_klines(symbol, interval, limit=100):
    if not is_valid_symbol(symbol) or not is_valid_interval(interval): return pd.DataFrame()
    try:
        raw = safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
        if raw is None or not isinstance(raw, list) or len(raw) < 2: return pd.DataFrame()
        df = pd.DataFrame(raw, columns=['T','O','H','L','C','V','CT','QV','NT','TBV','TBQV','I'])
        for c in ['O','H','L','C','V']: df[c] = pd.to_numeric(df[c], errors='coerce')
        df.dropna(subset=['O','H','L','C','V'], inplace=True)
        return df
    except Exception as e:
        logger.error(f"Kline Error ({symbol}): {e}")
        return pd.DataFrame()

# ==========================================
# ⚙️ MOTORLAR (V11 - FEE AWARE)
# ==========================================
_engine_threads = {}
_engine_lock = threading.Lock()
_last_cleanup = {'time': datetime.min}

# ══════════════════════════════════════════
# 📦 CONFIG CACHE SİSTEMİ
# Firebase'e her döngüde okuma yapmak yerine
# RAM'de tut, 5 dakikada bir yenile.
# Fayda: Firebase read sayısı ~90% düşer.
# ══════════════════════════════════════════
_config_cache = {}
_config_cache_lock = threading.Lock()
CONFIG_CACHE_TTL = 300  # 5 dakika (saniye)

def get_cached_config(config_name):
    """
    Config'i cache'den oku. TTL dolmuşsa Firebase'den yenile.
    config_name: 'trend', 'grid', 'flash'
    """
    now = time.time()
    with _config_cache_lock:
        cached = _config_cache.get(config_name)
        if cached and (now - cached['ts']) < CONFIG_CACHE_TTL:
            return cached['data']

    # Cache miss veya expired — Firebase'den oku
    try:
        doc = get_data_ref('configs').document(config_name).get()
        data = doc.to_dict() if doc.exists else {}
    except Exception as e:
        logger.error(f"Config okuma hatası ({config_name}): {e}")
        # Eski cache varsa onu döndür
        with _config_cache_lock:
            cached = _config_cache.get(config_name)
            return cached['data'] if cached else {}

    with _config_cache_lock:
        _config_cache[config_name] = {'data': data, 'ts': now}

    return data

def invalidate_config_cache(config_name=None):
    """UI'dan ayar kaydedildiğinde cache'i temizle."""
    with _config_cache_lock:
        if config_name:
            _config_cache.pop(config_name, None)
        else:
            _config_cache.clear()

def ensure_engine_running(name, func):
    with _engine_lock:
        t = _engine_threads.get(name)
        if t is None or not t.is_alive():
            t = threading.Thread(target=func, daemon=True, name=name)
            t.start()
            _engine_threads[name] = t
    return t


# ──────────────────────────────────────────
# 🐳 WHALE V11
# Değişiklikler vs V10:
# + Fee-aware PnL
# + Candle wick SL kontrolü (Low/High)
# + Stale position timeout (96h)
# + Daily loss limit kontrolü
# + Akıllı polling (5dk minimum)
# ──────────────────────────────────────────
def whale_task():
    logger.info("🐳 Whale V11 Başlatıldı")
    while True:
        try:
            if not db: break
            configs = get_cached_config('trend')

            if configs and configs.get('autopilot', False):
                # Daily limit kontrolü
                can_trade, daily_pnl, daily_limit = check_daily_loss_limit()
                if not can_trade:
                    logger.warning(f"🐳 GÜNLÜK LİMİT AŞILDI (${daily_pnl:.2f} / -${daily_limit:.2f}) — Bekleniyor")
                    time.sleep(120)
                    continue

                coins = configs.get('coins', [])
                if not isinstance(coins, list): coins = []
                timeframe = configs.get('timeframe', '1h')
                if not is_valid_interval(timeframe): timeframe = '1h'
                ema_f = safe_int(configs.get('ema_f', 9), 9)
                ema_s = safe_int(configs.get('ema_s', 21), 21)
                adx_t = safe_float(configs.get('adx_t', 15), 15.0)
                margin = safe_float(configs.get('margin', 300), 300.0)
                leverage = safe_float(configs.get('leverage', 3), 3.0)
                tp_atr = safe_float(configs.get('tp_atr', 3.5), 3.5)
                trailing_on = configs.get('trailing_stop', True)
                partial_on = configs.get('partial_tp', True)
                vol_filter_on = configs.get('vol_filter', True)
                vol_min = safe_float(configs.get('vol_min_ratio', 1.2), 1.2)
                stale_hours = safe_float(configs.get('stale_timeout_h', 96), 96.0)

                if ema_f <= 0 or ema_s <= 0 or margin <= 0 or leverage <= 0:
                    time.sleep(120); continue

                current_status = {}

                for symbol in coins:
                    if not is_valid_symbol(symbol): continue
                    raw_df = fetch_klines(symbol, timeframe, limit=150)
                    if raw_df.empty or len(raw_df) < ema_s * 2: continue

                    df = calculate_advanced_indicators(raw_df, ema_f, ema_s)
                    if len(df) < 2: continue

                    cur, prev = df.iloc[-1], df.iloc[-2]
                    price = safe_float(cur['C'])
                    candle_low = safe_float(cur['L'])
                    candle_high = safe_float(cur['H'])
                    if price <= 0: continue

                    if any(pd.isna(cur[k]) for k in ['EMA_F','EMA_S','RSI','ADX','ATR']): continue

                    atr_val = safe_float(cur['ATR'])
                    vol_ratio = safe_float(cur.get('Vol_Ratio', 1.0), 1.0)
                    bb_pct = safe_float(cur.get('BB_Pct', 0.5), 0.5)

                    current_status[symbol] = {
                        'price': round(price, 4), 'ema_f': round(float(cur['EMA_F']), 4),
                        'ema_s': round(float(cur['EMA_S']), 4), 'rsi': round(float(cur['RSI']), 2),
                        'adx': round(float(cur['ADX']), 2), 'vol_ratio': round(vol_ratio, 2),
                        'bb_pct': round(bb_pct, 2),
                    }

                    pos_ref = get_data_ref('active_trades').document(f"trend_{symbol}")
                    pos_doc = pos_ref.get()

                    if pos_doc.exists:
                        p = pos_doc.to_dict()
                        p_sl = safe_float(p.get('sl', 0))
                        p_tp = safe_float(p.get('tp', 0))
                        p_entry = safe_float(p.get('entry', price))
                        p_type = p.get('type', 'LONG')
                        p_highest = safe_float(p.get('highest', p_entry))
                        p_lowest = safe_float(p.get('lowest', p_entry))
                        p_partial = p.get('partial_done', False)
                        p_margin = safe_float(p.get('margin', margin))
                        p_lev = safe_float(p.get('leverage', leverage))
                        p_time = p.get('time', '')

                        if p_entry <= 0: pos_ref.delete(); continue

                        # ── STALE TIMEOUT ──
                        # Pozisyon çok uzun süredir açıksa kapat
                        try:
                            open_time = datetime.fromisoformat(p_time) if p_time else datetime.now()
                            hours_open = (datetime.now() - open_time).total_seconds() / 3600
                        except (ValueError, TypeError):
                            hours_open = 0

                        if hours_open >= stale_hours:
                            pnl_pct = ((price - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - price) / p_entry * 100)
                            gross = (p_margin * pnl_pct * p_lev) / 100
                            net, fee = calculate_net_pnl(gross, p_margin, p_lev, bot="trend")
                            result = "TIMEOUT_WIN" if net > 0 else "TIMEOUT_LOSS"
                            close_position_atomically(pos_ref, {
                                'bot': 'trend', 'symbol': symbol, 'pnl_usd': net,
                                'gross_pnl': round(gross, 2), 'fee_usd': fee,
                                'result': result, 'hours_open': round(hours_open, 1),
                                'time': datetime.now().isoformat()
                            })
                            record_daily_pnl(net, fee)
                            logger.info(f"🐳 TIMEOUT {symbol}: {hours_open:.0f}h → Net ${net:.2f} (fee ${fee:.2f})")
                            continue

                        # ── TRAILING STOP ──
                        upd = {}
                        if trailing_on and atr_val > 0:
                            if p_type == 'LONG' and price > p_highest:
                                upd['highest'] = price
                                ns = price - (atr_val * 2)
                                if ns > p_sl: upd['sl'] = ns; p_sl = ns
                            elif p_type == 'SHORT' and price < p_lowest:
                                upd['lowest'] = price
                                ns = price + (atr_val * 2)
                                if ns < p_sl: upd['sl'] = ns; p_sl = ns

                        # ── BREAKEVEN LOCK ──
                        if p_type == 'LONG' and price >= p_entry + atr_val and p_sl < p_entry:
                            upd['sl'] = p_entry; p_sl = p_entry
                        elif p_type == 'SHORT' and price <= p_entry - atr_val and p_sl > p_entry:
                            upd['sl'] = p_entry; p_sl = p_entry

                        # ── WICK-AWARE SL/TP CHECK ──
                        # Sadece close değil, candle low/high ile kontrol
                        res = ""
                        if p_type == 'LONG':
                            if candle_low <= p_sl: res = "LOSS"
                            elif candle_high >= p_tp: res = "WIN"
                        else:
                            if candle_high >= p_sl: res = "LOSS"
                            elif candle_low <= p_tp: res = "WIN"

                        # ── PARTIAL TP ──
                        if partial_on and not p_partial and not res:
                            tp1 = p_entry + (p_tp - p_entry) * 0.5 if p_type == 'LONG' else p_entry - (p_entry - p_tp) * 0.5
                            hit = (p_type == 'LONG' and candle_high >= tp1) or (p_type == 'SHORT' and candle_low <= tp1)
                            if hit:
                                ppnl = ((tp1 - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - tp1) / p_entry * 100)
                                half = p_margin * 0.5
                                gross = (half * ppnl * p_lev) / 100
                                net, fee = calculate_net_pnl(gross, half, p_lev, bot="trend")
                                get_data_ref('history').add({
                                    'bot': 'trend', 'symbol': symbol, 'pnl_usd': net,
                                    'gross_pnl': round(gross, 2), 'fee_usd': fee,
                                    'result': 'PARTIAL_WIN', 'time': datetime.now().isoformat()
                                })
                                record_daily_pnl(net, fee)
                                upd['partial_done'] = True
                                upd['margin'] = half
                                p_margin = half

                        if res:
                            # SL/TP hit — çıkış fiyatı wick'teki SL/TP seviyesi
                            exit_price = p_sl if res == "LOSS" else p_tp
                            pnl_pct = ((exit_price - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - exit_price) / p_entry * 100)
                            gross = (p_margin * pnl_pct * p_lev) / 100
                            net, fee = calculate_net_pnl(gross, p_margin, p_lev, bot="trend")
                            close_position_atomically(pos_ref, {
                                'bot': 'trend', 'symbol': symbol, 'pnl_usd': net,
                                'gross_pnl': round(gross, 2), 'fee_usd': fee,
                                'result': res, 'time': datetime.now().isoformat()
                            })
                            record_daily_pnl(net, fee)
                        elif upd:
                            pos_ref.update(upd)
                    else:
                        if atr_val <= 0: continue
                        if vol_filter_on and vol_ratio < vol_min: continue

                        long_sig = (prev['EMA_F'] <= prev['EMA_S'] and cur['EMA_F'] > cur['EMA_S']
                                    and cur['RSI'] > 50 and cur['ADX'] >= adx_t and bb_pct < 0.85)
                        short_sig = (prev['EMA_F'] >= prev['EMA_S'] and cur['EMA_F'] < cur['EMA_S']
                                     and cur['RSI'] < 50 and cur['ADX'] >= adx_t and bb_pct > 0.15)

                        if long_sig:
                            pos_ref.set({
                                'type': 'LONG', 'entry': price,
                                'sl': price - (atr_val * 2), 'tp': price + (atr_val * tp_atr),
                                'margin': margin, 'leverage': leverage, 'symbol': symbol,
                                'highest': price, 'lowest': price, 'partial_done': False,
                                'time': datetime.now().isoformat()
                            })
                        elif short_sig:
                            pos_ref.set({
                                'type': 'SHORT', 'entry': price,
                                'sl': price + (atr_val * 2), 'tp': price - (atr_val * tp_atr),
                                'margin': margin, 'leverage': leverage, 'symbol': symbol,
                                'highest': price, 'lowest': price, 'partial_done': False,
                                'time': datetime.now().isoformat()
                            })

                try:
                    if current_status:
                        get_data_ref('states').document('trend_status').set({
                            'data': current_status, 'updated': datetime.now().isoformat()
                        })
                except Exception: pass

            now = datetime.now()
            if (now - _last_cleanup['time']).total_seconds() > 21600:
                cleanup_old_history()
                _last_cleanup['time'] = now

            # V11: Akıllı polling — 1h TF için 5dk yeter, 15m için 2dk
            tf = (configs or {}).get('timeframe', '1h')
            poll = {'15m': 120, '1h': 300, '4h': 600, '1d': 900}.get(tf, 300)
            time.sleep(poll)
        except Exception as e:
            logger.error(f"Whale Error: {e}")
            time.sleep(60)


# ──────────────────────────────────────────
# 🐜 ANT V11
# + Fee-aware grid profit
# + Grid net profitability check
# + Circuit breaker
# + Unrealized PnL
# ──────────────────────────────────────────
def ant_task():
    logger.info("🐜 Ant V11 Başlatıldı")
    while True:
        try:
            if not db: break
            configs = get_cached_config('grid')

            if configs and configs.get('autopilot', False):
                symbol = configs.get('coin', 'BTCUSDT')
                if not is_valid_symbol(symbol): time.sleep(20); continue

                spacing = safe_float(configs.get('grid_spacing_pct', 0.5), 0.5)
                margin = safe_float(configs.get('margin_per_grid', 100), 100.0)
                max_grids = safe_int(configs.get('max_grids', 50), 50)
                cb_pct = safe_float(configs.get('circuit_breaker_pct', 15.0), 15.0)
                dynamic = configs.get('dynamic_spacing', False)

                if spacing <= 0 or margin <= 0 or max_grids <= 0: time.sleep(20); continue

                # V11: Grid spacing fee-check
                net_per_grid, fee_per_grid = calculate_grid_net_profit(margin, spacing)
                if net_per_grid <= 0:
                    logger.warning(f"🐜 UYARI: Grid spacing %{spacing} ile net kâr NEGATİF! (kâr=${net_per_grid:.2f}, fee=${fee_per_grid:.2f})")
                    # Devam et ama state'e uyarı yaz
                    get_data_ref('states').document('grid').set({
                        'fee_warning': True,
                        'net_per_grid': net_per_grid,
                        'fee_per_grid': fee_per_grid,
                        'updated': datetime.now().isoformat()
                    }, merge=True)

                data = safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
                if data is None: time.sleep(20); continue
                price = safe_float(data.get('price', 0))
                if price <= 0: time.sleep(20); continue

                eff_spacing = spacing
                if dynamic:
                    kl = fetch_klines(symbol, '1h', limit=30)
                    if not kl.empty and len(kl) >= 14:
                        ki = calculate_advanced_indicators(kl, 9, 21)
                        atr = safe_float(ki.iloc[-1].get('ATR', 0))
                        if atr > 0 and price > 0:
                            atr_pct = (atr / price) * 100
                            eff_spacing = max(spacing, atr_pct * 0.5)
                            eff_spacing = min(eff_spacing, spacing * 3)

                st_ref = get_data_ref('states').document('grid')
                st_doc = st_ref.get()
                state = st_doc.to_dict() if st_doc.exists else {'grids': [], 'total_profit': 0.0, 'total_fees': 0.0}

                grids = state.get('grids', [])
                if not isinstance(grids, list): grids = []

                # Circuit breaker
                if grids:
                    entries = [safe_float(g.get('entry', 0)) for g in grids if safe_float(g.get('entry', 0)) > 0]
                    if entries:
                        highest = max(entries)
                        dd = ((highest - price) / highest) * 100
                        if dd >= cb_pct:
                            unreal = sum(((price - safe_float(g.get('entry', 0))) / safe_float(g.get('entry', price)) * margin)
                                         for g in grids if safe_float(g.get('entry', 0)) > 0)
                            st_ref.update({
                                'circuit_breaker_active': True, 'drawdown_pct': round(dd, 2),
                                'unrealized_pnl': round(unreal, 2), 'last_price': price,
                                'updated': datetime.now().isoformat()
                            })
                            time.sleep(20); continue

                new_grids = []
                total_prof = safe_float(state.get('total_profit', 0.0))
                total_fees = safe_float(state.get('total_fees', 0.0))

                for g in grids:
                    ge = safe_float(g.get('entry', 0))
                    if ge <= 0: continue
                    if price >= ge * (1 + eff_spacing / 100):
                        net, fee = calculate_grid_net_profit(margin, eff_spacing)
                        get_data_ref('history').add({
                            'bot': 'grid', 'symbol': symbol, 'pnl_usd': net,
                            'gross_pnl': round(margin * eff_spacing / 100, 2), 'fee_usd': fee,
                            'result': 'WIN', 'time': datetime.now().isoformat()
                        })
                        total_prof += net
                        total_fees += fee
                        record_daily_pnl(net, fee)
                    else:
                        new_grids.append(g)

                if len(new_grids) < max_grids:
                    ents = [safe_float(x.get('entry', 0)) for x in new_grids if safe_float(x.get('entry', 0)) > 0]
                    last_buy = min(ents) if ents else price
                    if price <= last_buy * (1 - eff_spacing / 100) or not new_grids:
                        new_grids.append({'entry': price, 'time': datetime.now().isoformat()})

                unreal = sum(((price - safe_float(g.get('entry', 0))) / safe_float(g.get('entry', price)) * margin)
                             for g in new_grids if safe_float(g.get('entry', 0)) > 0)

                st_ref.set({
                    'grids': new_grids, 'total_profit': round(total_prof, 2),
                    'total_fees': round(total_fees, 2),
                    'last_price': price, 'effective_spacing': round(eff_spacing, 3),
                    'unrealized_pnl': round(unreal, 2), 'circuit_breaker_active': False,
                    'net_per_grid': net_per_grid, 'fee_per_grid': fee_per_grid,
                    'fee_warning': net_per_grid <= 0,
                    'updated': datetime.now().isoformat()
                })
            time.sleep(20)
        except Exception as e:
            logger.error(f"Ant Error: {e}")
            time.sleep(20)


# ──────────────────────────────────────────
# ⚡ FALCON V11
# + Fee-aware PnL
# + Momentum freshness check (son 4 saatteki trend)
# + Stale timeout (48h)
# + Daily loss limit
# + RSI filter + trailing
# ──────────────────────────────────────────
def falcon_task():
    logger.info("⚡ Falcon V11 Başlatıldı")
    while True:
        try:
            if not db: break
            configs = get_cached_config('flash')

            if configs and configs.get('autopilot', False):
                can_trade, _, _ = check_daily_loss_limit()
                if not can_trade:
                    time.sleep(120); continue

                spike_t = safe_float(configs.get('vol_spike', 5.0), 5.0)
                margin = safe_float(configs.get('margin', 200), 200.0)
                leverage = safe_float(configs.get('leverage', 5), 5.0)
                tp_pct = safe_float(configs.get('tp_pct', 5.0), 5.0)
                sl_pct = safe_float(configs.get('sl_pct', 3.0), 3.0)
                cooldown_h = 6
                rsi_on = configs.get('rsi_filter', True)
                rsi_max = safe_float(configs.get('rsi_max', 78), 78.0)
                trail_on = configs.get('trailing_flash', True)
                min_qvol = safe_float(configs.get('min_quote_vol', 50_000_000), 50_000_000)
                stale_h = safe_float(configs.get('stale_timeout_h', 48), 48.0)

                if margin <= 0 or leverage <= 0 or tp_pct <= 0 or sl_pct <= 0:
                    time.sleep(30); continue

                pos_ref = get_data_ref('active_trades').document('flash_pos')
                pos_doc = pos_ref.get()

                if pos_doc.exists:
                    p = pos_doc.to_dict()
                    sym = p.get('symbol', '')
                    if not sym or not is_valid_symbol(sym): pos_ref.delete(); time.sleep(30); continue

                    pd_data = safe_api_get(get_http_session(), f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")
                    if pd_data is None: time.sleep(30); continue

                    price = safe_float(pd_data.get('price', 0))
                    p_entry = safe_float(p.get('entry', 0))
                    p_type = p.get('type', 'LONG')
                    p_sl = safe_float(p.get('sl', 0))
                    p_tp = safe_float(p.get('tp', 0))
                    p_hi = safe_float(p.get('highest', p_entry))
                    p_margin = safe_float(p.get('margin', margin))
                    p_lev = safe_float(p.get('leverage', leverage))
                    if p_entry <= 0: pos_ref.delete(); time.sleep(30); continue

                    # ── STALE TIMEOUT ──
                    p_time = p.get('time', '')
                    try:
                        h_open = (datetime.now() - datetime.fromisoformat(p_time)).total_seconds() / 3600 if p_time else 0
                    except: h_open = 0

                    if h_open >= stale_h:
                        pnl_pct = ((price - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - price) / p_entry * 100)
                        gross = (p_margin * pnl_pct * p_lev) / 100
                        net, fee = calculate_net_pnl(gross, p_margin, p_lev, bot="flash")
                        close_position_atomically(pos_ref, {
                            'bot': 'flash', 'symbol': sym, 'pnl_usd': net,
                            'gross_pnl': round(gross, 2), 'fee_usd': fee,
                            'result': "TIMEOUT_WIN" if net > 0 else "TIMEOUT_LOSS",
                            'hours_open': round(h_open, 1),
                            'time': datetime.now().isoformat()
                        })
                        record_daily_pnl(net, fee)
                        get_data_ref('states').document('flash_cooldown').set({sym: datetime.now().isoformat()}, merge=True)
                        time.sleep(30); continue

                    upd = {}
                    # Trailing
                    if trail_on and p_type == 'LONG' and price > p_hi:
                        upd['highest'] = price
                        prof_dist = price - p_entry
                        if prof_dist > 0:
                            ns = p_entry + (prof_dist * 0.5)
                            if ns > p_sl: upd['sl'] = ns; p_sl = ns
                    elif trail_on and p_type == 'SHORT' and price < p_hi:
                        upd['highest'] = price
                        prof_dist = p_entry - price
                        if prof_dist > 0:
                            ns = p_entry - (prof_dist * 0.5)
                            if ns < p_sl: upd['sl'] = ns; p_sl = ns

                    res = ""
                    if p_type == 'LONG':
                        if price <= p_sl: res = "LOSS"
                        elif price >= p_tp: res = "WIN"
                    else:
                        if price >= p_sl: res = "LOSS"
                        elif price <= p_tp: res = "WIN"

                    if res:
                        exit_p = p_sl if res == "LOSS" else p_tp
                        pnl_pct = ((exit_p - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - exit_p) / p_entry * 100)
                        gross = (p_margin * pnl_pct * p_lev) / 100
                        net, fee = calculate_net_pnl(gross, p_margin, p_lev, bot="flash")
                        close_position_atomically(pos_ref, {
                            'bot': 'flash', 'symbol': sym, 'pnl_usd': net,
                            'gross_pnl': round(gross, 2), 'fee_usd': fee,
                            'result': res, 'time': datetime.now().isoformat()
                        })
                        record_daily_pnl(net, fee)
                        get_data_ref('states').document('flash_cooldown').set({sym: datetime.now().isoformat()}, merge=True)
                    elif upd:
                        pos_ref.update(upd)

                else:
                    # ── YENİ SİNYAL (V11: MOMENTUM FRESHNESS) ──
                    ticks = safe_api_get(get_http_session(), "https://api.binance.com/api/v3/ticker/24hr")
                    cd_doc = get_data_ref('states').document('flash_cooldown').get()
                    cooldowns = cd_doc.to_dict() if cd_doc.exists else {}
                    now = datetime.now()

                    if ticks and isinstance(ticks, list):
                        candidates = []
                        for t in ticks:
                            try:
                                chg = float(t.get('priceChangePercent', 0))
                                sym = t.get('symbol', '')
                                qvol = safe_float(t.get('quoteVolume', 0))
                                if chg <= spike_t or not sym.endswith('USDT') or not is_valid_symbol(sym) or qvol < min_qvol:
                                    continue
                                # Cooldown
                                lt = cooldowns.get(sym)
                                if lt:
                                    try:
                                        if (now - datetime.fromisoformat(lt)).total_seconds() < cooldown_h * 3600: continue
                                    except: pass

                                candidates.append({'symbol': sym, 'change': chg, 'qvol': qvol,
                                                   'price': safe_float(t.get('lastPrice', 0)),
                                                   'high': safe_float(t.get('highPrice', 0)),
                                                   'quality': qvol * (chg / 100)})
                            except: continue

                        if candidates:
                            top = max(candidates, key=lambda x: x['quality'])
                            sym = top['symbol']
                            price = top['price']
                            high_24h = top['high']
                            if price <= 0: time.sleep(30); continue

                            # ═══ V11: MOMENTUM FRESHNESS CHECK ═══
                            # Fiyat 24 saatlik zirvesinden %3'ten fazla düştüyse,
                            # momentum bitmiş demektir — girme
                            if high_24h > 0:
                                pullback = ((high_24h - price) / high_24h) * 100
                                if pullback > 3.0:
                                    logger.info(f"⚡ MOMENTUM BİTMİŞ: {sym} peak'ten %{pullback:.1f} geri çekilmiş")
                                    time.sleep(30); continue

                            # Son 4 saatlik mumları kontrol et — en az 2/4 yeşil olmalı
                            momentum_ok = True
                            kl = fetch_klines(sym, '1h', limit=6)
                            if not kl.empty and len(kl) >= 4:
                                last4 = kl.tail(4)
                                green_count = (last4['C'] > last4['O']).sum()
                                if green_count < 2:
                                    momentum_ok = False
                                    logger.info(f"⚡ MOMENTUM ZAYIF: {sym} son 4 saatte sadece {green_count}/4 yeşil mum")

                            if not momentum_ok: time.sleep(30); continue

                            # RSI filtresi
                            should_enter = True
                            entry_rsi = None
                            if rsi_on:
                                kl2 = fetch_klines(sym, '15m', limit=30)
                                if not kl2.empty and len(kl2) >= 14:
                                    ki = calculate_advanced_indicators(kl2, 9, 21)
                                    entry_rsi = safe_float(ki.iloc[-1].get('RSI', 50))
                                    if entry_rsi > rsi_max: should_enter = False

                            if should_enter:
                                pos_ref.set({
                                    'type': 'LONG', 'entry': price,
                                    'sl': price * (1 - sl_pct / 100),
                                    'tp': price * (1 + tp_pct / 100),
                                    'margin': margin, 'leverage': leverage,
                                    'symbol': sym, 'highest': price,
                                    'quality_score': round(top['quality'], 2),
                                    'entry_rsi': entry_rsi,
                                    'pullback_pct': round(pullback if high_24h > 0 else 0, 2),
                                    'time': datetime.now().isoformat()
                                })
                                get_data_ref('signals').document('flash').set({
                                    'symbol': sym, 'change': str(round(top['change'], 2)),
                                    'vol': str(round(top['qvol'])),
                                    'quality': round(top['quality'], 2), 'rsi': entry_rsi,
                                    'time': datetime.now().isoformat()
                                })

            time.sleep(30)
        except Exception as e:
            logger.error(f"Falcon Error: {e}")
            time.sleep(30)


# ==========================================
# 🖥️ UI
# ==========================================
def calculate_bot_stats(hist, bot):
    bd = [h for h in hist if h.get('bot') == bot]
    if not bd: return 0, 0.0, 0.0, 0, 0.0
    df = pd.DataFrame(bd)
    if 'result' not in df.columns: df['result'] = 'UNKNOWN'
    df['pnl_usd'] = pd.to_numeric(df.get('pnl_usd', 0), errors='coerce').fillna(0.0)
    df['fee_usd'] = pd.to_numeric(df.get('fee_usd', 0), errors='coerce').fillna(0.0)
    wins = len(df[df['result'].isin(['WIN', 'PARTIAL_WIN', 'TIMEOUT_WIN'])])
    total = len(df)
    wr = (wins / total * 100) if total > 0 else 0.0
    pnl = float(df['pnl_usd'].sum())
    fees = float(df['fee_usd'].sum())
    partials = len(df[df['result'] == 'PARTIAL_WIN'])
    return total, wr, pnl, partials, fees

def safe_render_dataframe(data_list, rename_map, desired_order=None):
    if not data_list: return None
    df = pd.DataFrame(data_list)
    existing = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=existing)
    if desired_order: cols = [v for v in desired_order if v in df.columns]
    else: cols = [v for v in rename_map.values() if v in df.columns]
    return df[cols] if cols else df

def get_engine_status():
    return {n: (_engine_threads.get(n) is not None and _engine_threads[n].is_alive()) for n in ['whale', 'ant', 'falcon']}

# ══════════════════════════════════════════
# 📦 UI VERİ CACHE — Firebase okumalarını azalt
# History ve active trades UI'da cache'lenir,
# sadece "Yenile" butonuna basınca güncellenir.
# ══════════════════════════════════════════
_ui_cache = {}
_ui_cache_lock = threading.Lock()
UI_CACHE_TTL = 300  # 5 dakika

def get_cached_history():
    """History'yi cache'den oku, TTL dolmuşsa Firebase'den yenile."""
    now = time.time()
    with _ui_cache_lock:
        c = _ui_cache.get('history')
        if c and (now - c['ts']) < UI_CACHE_TTL:
            return c['data']
    try:
        docs = get_data_ref('history').order_by('time', direction=firestore.Query.DESCENDING).limit(HISTORY_QUERY_LIMIT).get()
        data = [h.to_dict() for h in docs] if docs else []
    except Exception as e:
        logger.error(f"History okuma hatası: {e}")
        with _ui_cache_lock:
            c = _ui_cache.get('history')
            return c['data'] if c else []

    with _ui_cache_lock:
        _ui_cache['history'] = {'data': data, 'ts': now}
    return data

def safe_firebase_read(read_func, fallback=None):
    """Firebase okumasını try/except ile sar. Quota aşılırsa fallback döndür."""
    try:
        return read_func()
    except Exception as e:
        logger.error(f"Firebase read hatası: {e}")
        return fallback

def main():
    st.title("🛡️ QUANT OMNI V11 FINAL")
    st.caption("Fee-Aware • Daily Loss Limit • Momentum Check • Wick SL • Stale Timeout")

    if not db:
        st.markdown('<div class="status-bar offline">❌ SİSTEM ÇEVRİMDIŞI (Firebase Config Hatası)</div>', unsafe_allow_html=True)
        return

    ensure_engine_running('whale', whale_task)
    ensure_engine_running('ant', ant_task)
    ensure_engine_running('falcon', falcon_task)

    es = get_engine_status()

    # Daily risk check
    can_trade, daily_pnl, daily_limit = check_daily_loss_limit()
    if not can_trade:
        st.markdown(f'<div class="status-bar paused">⏸️ GÜNLÜK KAYIP LİMİTİ AŞILDI! PnL: ${daily_pnl:.2f} / Limit: -${daily_limit:.2f} — TÜM BOTLAR BEKLEMEDE</div>', unsafe_allow_html=True)
    elif all(es.values()):
        st.markdown('<div class="status-bar online">● SİSTEM ÇEVRİMİÇİ | V11 FINAL | 🐳🐜⚡ AKTİF</div>', unsafe_allow_html=True)
    else:
        dead = [k for k, v in es.items() if not v]
        st.markdown(f'<div class="status-bar offline">⚠️ {", ".join(dead)} yeniden başlatılıyor</div>', unsafe_allow_html=True)

    # Fee info bar
    fr_t = get_friction('trend'); fr_f = get_friction('flash'); fr_g = get_friction('grid')
    st.markdown(f"""<div class='fee-box'>💰 <b>Fee Modeli (taraf başı):</b> Trend %{fr_t*100:.2f} | Grid %{fr_g*100:.2f} | Flash %{fr_f*100:.2f} | 
    Günlük PnL: <b>${daily_pnl:.2f}</b> / Limit: <b>-${daily_limit:.2f}</b></div>""", unsafe_allow_html=True)

    all_history = get_cached_history()

    col_ref1, col_ref2 = st.columns([4, 1])
    with col_ref2:
        if st.button("🔄 Yenile", use_container_width=True): st.rerun()

    tabs = st.tabs(["🐳 TREND", "🐜 GRID", "⚡ FLASH", "📊 ANALİTİK", "⚙️ RİSK"])

    # ═══ TAB 1: TREND ═══
    with tabs[0]:
        st.markdown("""<div class='info-box'><b>V11:</b> EMA kesişimi + ADX + Volume + Bollinger girişi. Trailing Stop, Breakeven Lock, Partial TP, Stale Timeout. <b>Tüm PnL fee dahil.</b></div>""", unsafe_allow_html=True)

        t_tot, t_wr, t_pnl, t_part, t_fees = calculate_bot_stats(all_history, 'trend')
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("İşlem", t_tot); c2.metric("Win Rate", f"%{t_wr:.1f}")
        c3.metric("Net Kâr (fee dahil)", f"${t_pnl:.2f}"); c4.metric("Ödenen Fee", f"${t_fees:.2f}")
        st.divider()

        col1, col2 = st.columns([1, 2])
        with col1:
            cfg = get_cached_config('trend')
            with st.form("t_cfg"):
                m = st.number_input("Marjin ($)", 10, 5000, safe_int(cfg.get('margin', 300), 300))
                lev = st.slider("Kaldıraç", 1, 20, safe_int(cfg.get('leverage', 3), 3))

                # Fee preview
                notional = m * lev
                rt_fee = notional * get_friction('trend') * 2
                st.caption(f"💰 Her trade maliyeti: ${rt_fee:.2f} (notional ${notional:,.0f})")

                saved = cfg.get('coins', ["BTCUSDT"])
                coins = st.multiselect("Varlıklar", AVAILABLE_COINS, default=filter_valid_defaults(saved, AVAILABLE_COINS))
                tf = st.selectbox("Zaman Dilimi", AVAILABLE_TIMEFRAMES, index=safe_selectbox_index(cfg.get('timeframe','1h'), AVAILABLE_TIMEFRAMES, 1))
                ef = st.slider("Hızlı EMA", 5, 50, safe_int(cfg.get('ema_f', 9), 9))
                es_val = st.slider("Yavaş EMA", 10, 200, safe_int(cfg.get('ema_s', 21), 21))
                adx = st.slider("ADX Filtresi", 0, 40, safe_int(cfg.get('adx_t', 15), 15))
                tp = st.slider("TP (ATR x)", 1.0, 6.0, float(min(max(safe_float(cfg.get('tp_atr', 3.5)), 1.0), 6.0)))

                st.markdown("**🧠 Akıllı Filtreler**")
                trail = st.checkbox("Trailing Stop", value=cfg.get('trailing_stop', True))
                part = st.checkbox("Partial TP", value=cfg.get('partial_tp', True))
                vf = st.checkbox("Volume Filtre", value=cfg.get('vol_filter', True))
                vr = st.slider("Min Hacim Oranı", 0.5, 3.0, float(min(max(safe_float(cfg.get('vol_min_ratio', 1.2)), 0.5), 3.0)), 0.1)
                stale = st.number_input("Stale Timeout (saat)", 12, 240, safe_int(cfg.get('stale_timeout_h', 96), 96))

                auto = st.checkbox("Otopilot", value=cfg.get('autopilot', False))
                if st.form_submit_button("Kaydet"):
                    get_data_ref('configs').document('trend').set({
                        'margin': m, 'leverage': lev, 'coins': coins, 'timeframe': tf,
                        'ema_f': ef, 'ema_s': es_val, 'adx_t': adx, 'tp_atr': tp,
                        'trailing_stop': trail, 'partial_tp': part, 'vol_filter': vf,
                        'vol_min_ratio': vr, 'stale_timeout_h': stale, 'autopilot': auto
                    })
                    invalidate_config_cache('trend')
                    st.rerun()

            if st.button("🗑️ Trend Sıfırla"):
                for d in get_data_ref('history').stream():
                    if d.to_dict().get('bot') == 'trend': d.reference.delete()
                for d in get_data_ref('active_trades').stream():
                    if d.id.startswith('trend_'): d.reference.delete()
                get_data_ref('states').document('trend_status').delete()
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            st.markdown("#### `🟢 AKTİF İŞLEMLER`")
            ads = safe_firebase_read(lambda: get_data_ref('active_trades').get(), [])
            tt = [a.to_dict() for a in ads if a.id.startswith('trend_')]
            if tt:
                rn = {'symbol':'Varlık','type':'Yön','entry':'Giriş','tp':'TP','sl':'SL','margin':'Marjin($)','partial_done':'Partial'}
                dft = safe_render_dataframe(tt, rn)
                if dft is not None: st.dataframe(dft, use_container_width=True)
                for trade in tt:
                    ent = safe_float(trade.get('entry',0))
                    sl = safe_float(trade.get('sl',0))
                    tp = safe_float(trade.get('tp',0))
                    if ent>0 and tp>0 and sl>0:
                        risk = abs(ent-sl); reward = abs(tp-ent)
                        rr = reward/risk if risk>0 else 0
                        fee_cost = safe_float(trade.get('margin',300)) * safe_float(trade.get('leverage',3)) * get_friction('trend') * 2
                        st.caption(f"{trade.get('symbol','?')}: R:R=1:{rr:.1f} | Fee=${fee_cost:.2f} | {'🔒 BE' if sl>=ent and trade.get('type')=='LONG' else '⏳'}")
            else:
                st.info("Açık işlem yok.")

            st.markdown("#### `📡 RADAR`")
            sd = safe_firebase_read(lambda: get_data_ref('states').document('trend_status').get())
            if sd and sd.exists:
                sdata = sd.to_dict().get('data', {})
                if sdata:
                    rl = []
                    at = float(cfg.get('adx_t', 15))
                    for s, v in sdata.items():
                        rl.append({
                            'Varlık': s,
                            'Trend': "🟢" if v['ema_f']>v['ema_s'] else "🔴",
                            'ADX': f"{v['adx']} {'✅' if v['adx']>=at else '⏳'}",
                            'RSI': v['rsi'],
                            'Vol': f"{v.get('vol_ratio','—')}x",
                            'BB': f"{v.get('bb_pct',0.5):.0%}"
                        })
                    st.dataframe(pd.DataFrame(rl), use_container_width=True)

    # ═══ TAB 2: GRID ═══
    with tabs[1]:
        st.markdown("""<div class='info-box'><b>V11:</b> Grid kumbara + Circuit Breaker + Fee-aware kâr hesabı. <b>Spacing çok darsa fee kârı yer — sistem uyarır.</b></div>""", unsafe_allow_html=True)

        g_tot, _, g_pnl, _, g_fees = calculate_bot_stats(all_history, 'grid')
        c1,c2,c3 = st.columns(3)
        c1.metric("Kapatılan Grid", g_tot); c2.metric("Net Kâr (fee dahil)", f"${g_pnl:.2f}"); c3.metric("Ödenen Fee", f"${g_fees:.2f}")
        st.divider()

        col1, col2 = st.columns([1, 2])
        with col1:
            cg = get_cached_config('grid')
            with st.form("g_cfg"):
                gc = st.selectbox("Coin", GRID_COINS, index=safe_selectbox_index(cg.get('coin','BTCUSDT'), GRID_COINS, 0))
                sp = st.number_input("Spacing (%)", 0.1, 5.0, float(min(max(safe_float(cg.get('grid_spacing_pct', 0.5)), 0.1), 5.0)))
                mg = st.number_input("Parça Bütçe ($)", 10, 500, safe_int(cg.get('margin_per_grid', 100), 100))
                mx = st.number_input("Maks Ağ", 10, 100, safe_int(cg.get('max_grids', 50), 50))

                # Fee preview
                net_g, fee_g = calculate_grid_net_profit(mg, sp)
                if net_g <= 0:
                    st.error(f"⛔ Bu spacing'de net kâr NEGATİF! Kâr=${mg*sp/100:.2f}, Fee=${fee_g:.2f} → Net=${net_g:.2f}")
                else:
                    st.success(f"✅ Parça başı net kâr: ${net_g:.2f} (kâr=${mg*sp/100:.2f} - fee=${fee_g:.2f})")

                cb = st.slider("Circuit Breaker (%)", 5.0, 30.0, float(min(max(safe_float(cg.get('circuit_breaker_pct', 15)), 5), 30)), 1.0)
                dy = st.checkbox("Dinamik Spacing", value=cg.get('dynamic_spacing', False))
                ag = st.checkbox("Otopilot", value=cg.get('autopilot', False))
                if st.form_submit_button("Kaydet"):
                    get_data_ref('configs').document('grid').set({
                        'coin': gc, 'grid_spacing_pct': sp, 'margin_per_grid': mg,
                        'max_grids': mx, 'circuit_breaker_pct': cb,
                        'dynamic_spacing': dy, 'autopilot': ag
                    })
                    invalidate_config_cache('grid')
                    st.rerun()

            if st.button("🗑️ Grid Sıfırla"):
                for d in get_data_ref('history').stream():
                    if d.to_dict().get('bot') == 'grid': d.reference.delete()
                get_data_ref('states').document('grid').delete()
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            max_risk = safe_int(cg.get('margin_per_grid',100)) * safe_int(cg.get('max_grids',50))
            st.markdown(f"""<div class='warn-box'><b>⚠️ Maks Risk:</b> ${max_risk:,.0f}</div>""", unsafe_allow_html=True)

            state = safe_firebase_read(lambda: get_data_ref('states').document('grid').get())
            if state and state.exists:
                sd = state.to_dict()
                if sd.get('circuit_breaker_active'):
                    st.markdown(f"""<div class='warn-box'><b>🚨 CIRCUIT BREAKER!</b> Düşüş: %{sd.get('drawdown_pct',0):.1f}</div>""", unsafe_allow_html=True)
                if sd.get('fee_warning'):
                    st.markdown(f"""<div class='warn-box'><b>⛔ FEE UYARISI:</b> Net kâr negatif! Spacing artırın.</div>""", unsafe_allow_html=True)

                u1,u2 = st.columns(2)
                u1.metric("Unrealized", f"${safe_float(sd.get('unrealized_pnl',0)):.2f}")
                u2.metric("Spacing", f"%{safe_float(sd.get('effective_spacing',sp)):.2f}")

                gl = sd.get('grids', [])
                if isinstance(gl, list) and gl:
                    dg = pd.DataFrame(gl)
                    if 'entry' in dg.columns:
                        esp = safe_float(sd.get('effective_spacing', sp))
                        dg['entry'] = pd.to_numeric(dg['entry'], errors='coerce').fillna(0)
                        dg['Hedef'] = dg['entry'] * (1 + esp/100)
                        dg['Zaman'] = dg['time'].apply(safe_short_time) if 'time' in dg.columns else '—'
                        lp = safe_float(sd.get('last_price', 0))
                        if lp > 0: dg['PnL($)'] = ((lp - dg['entry'])/dg['entry'] * safe_int(cg.get('margin_per_grid',100))).round(2)
                        dg = dg.rename(columns={'entry': 'Alış'})
                        cols = ['Zaman','Alış','Hedef']
                        if 'PnL($)' in dg.columns: cols.append('PnL($)')
                        st.dataframe(dg[cols].sort_values('Alış'), use_container_width=True)
                        st.caption(f"Açık: {len(dg)}/{mx}")

    # ═══ TAB 3: FLASH ═══
    with tabs[2]:
        st.markdown("""<div class='info-box'><b>V11:</b> Hacim spike + <b>Momentum Freshness</b> (zirveden %3+ düşmüşse girme) + <b>4 saatlik yeşil mum kontrolü</b> + RSI + Trailing. <b>Fee dahil PnL.</b></div>""", unsafe_allow_html=True)

        f_tot, f_wr, f_pnl, _, f_fees = calculate_bot_stats(all_history, 'flash')
        c1,c2,c3 = st.columns(3)
        c1.metric("Patlama", f_tot); c2.metric("Win Rate", f"%{f_wr:.1f}"); c3.metric("Net Kâr", f"${f_pnl:.2f}")
        st.caption(f"Ödenen fee: ${f_fees:.2f}")
        st.divider()

        col1, col2 = st.columns([1, 2])
        with col1:
            cf = get_cached_config('flash')
            with st.form("f_cfg"):
                mf = st.number_input("Marjin ($)", 10, 5000, safe_int(cf.get('margin', 200), 200))
                lf = st.slider("Kaldıraç", 1, 20, safe_int(cf.get('leverage', 5), 5))
                rt_f = mf * lf * get_friction('flash') * 2
                st.caption(f"💰 Trade maliyeti: ${rt_f:.2f} (⚠️ Flash slippage dahil)")
                tpf = st.number_input("TP (%)", 1.0, 20.0, float(min(max(safe_float(cf.get('tp_pct', 5)), 1), 20)))
                slf = st.number_input("SL (%)", 1.0, 20.0, float(min(max(safe_float(cf.get('sl_pct', 3)), 1), 20)))
                fs = st.slider("Spike Eşiği (%)", 2.0, 50.0, float(min(max(safe_float(cf.get('vol_spike', 10)), 2), 50)))

                st.markdown("**🧠 V11 Filtreler**")
                rf = st.checkbox("RSI Filtresi", value=cf.get('rsi_filter', True))
                rm = st.slider("RSI Maks", 60, 90, safe_int(cf.get('rsi_max', 78), 78))
                trf = st.checkbox("Trailing Stop", value=cf.get('trailing_flash', True))
                mv = st.number_input("Min 24h Hacim ($)", 1_000_000, 500_000_000,
                                     safe_int(cf.get('min_quote_vol', 50_000_000), 50_000_000), step=10_000_000)
                stf = st.number_input("Stale Timeout (saat)", 6, 96, safe_int(cf.get('stale_timeout_h', 48), 48))

                fa = st.checkbox("Otopilot", value=cf.get('autopilot', False))
                if st.form_submit_button("Kaydet"):
                    get_data_ref('configs').document('flash').set({
                        'margin': mf, 'leverage': lf, 'tp_pct': tpf, 'sl_pct': slf,
                        'vol_spike': fs, 'rsi_filter': rf, 'rsi_max': rm,
                        'trailing_flash': trf, 'min_quote_vol': mv,
                        'stale_timeout_h': stf, 'autopilot': fa
                    })
                    invalidate_config_cache('flash')
                    st.rerun()

            if st.button("🗑️ Flash Sıfırla"):
                for d in get_data_ref('history').stream():
                    if d.to_dict().get('bot') == 'flash': d.reference.delete()
                get_data_ref('active_trades').document('flash_pos').delete()
                get_data_ref('signals').document('flash').delete()
                get_data_ref('states').document('flash_cooldown').delete()
                st.success("Sıfırlandı!"); time.sleep(1); st.rerun()

        with col2:
            af = safe_firebase_read(lambda: get_data_ref('active_trades').document('flash_pos').get())
            if af and af.exists:
                p = af.to_dict()
                rn = {'symbol':'Varlık','type':'Yön','entry':'Giriş','tp':'TP','sl':'SL','margin':'Bütçe($)','quality_score':'Kalite','entry_rsi':'RSI'}
                df = safe_render_dataframe([p], rn)
                if df is not None: st.dataframe(df, use_container_width=True)
                ep = safe_float(p.get('entry',0))
                cs = safe_float(p.get('sl',0))
                fee_cost = safe_float(p.get('margin',200))*safe_float(p.get('leverage',5))*get_friction('flash')*2
                st.caption(f"Bu trade'in fee maliyeti: ${fee_cost:.2f}")
                if ep > 0 and cs > ep:
                    st.markdown("""<div class='edge-box'><b>🔒 Trailing aktif — kayıpsız bölgede</b></div>""", unsafe_allow_html=True)
            else:
                sig = safe_firebase_read(lambda: get_data_ref('signals').document('flash').get())
                if sig and sig.exists:
                    s = sig.to_dict()
                    st.warning(f"📡 SON: {s.get('symbol','?')} Δ%{s.get('change','?')} Q:{s.get('quality','?')}")
                st.info("Momentum onaylı hedef bekleniyor...")

            st.markdown("#### `🕒 SOĞUMA`")
            cd = safe_firebase_read(lambda: get_data_ref('states').document('flash_cooldown').get())
            if cd and cd.exists:
                cds = cd.to_dict()
                cl = []
                now = datetime.now()
                for s, ts in (cds or {}).items():
                    try:
                        hp = (now - datetime.fromisoformat(ts)).total_seconds()/3600
                        if hp < 6: cl.append({"Varlık": s, "Kalan": f"{6-hp:.1f}h"})
                    except: pass
                if cl: st.dataframe(pd.DataFrame(cl), use_container_width=True)
                else: st.caption("Boş")

    # ═══ TAB 4: ANALİTİK ═══
    with tabs[3]:
        st.markdown("### 📊 Merkezi Analitik")

        es = get_engine_status()
        ec = st.columns(3)
        for i, (n, a) in enumerate(es.items()):
            lb = {'whale':'🐳 Trend','ant':'🐜 Grid','falcon':'⚡ Flash'}[n]
            ec[i].metric(lb, "✅" if a else "❌")

        if st.button("🚨 Tüm Sistemi Sıfırla", type="primary"):
            try:
                for c in ['history','active_trades','states','signals']:
                    for d in get_data_ref(c).stream(): d.reference.delete()
                st.success("Sıfırlandı!"); time.sleep(2); st.rerun()
            except Exception as e: st.error(str(e))

        if all_history:
            df = pd.DataFrame(all_history)
            df['pnl_usd'] = pd.to_numeric(df.get('pnl_usd', 0), errors='coerce').fillna(0)
            df['fee_usd'] = pd.to_numeric(df.get('fee_usd', 0), errors='coerce').fillna(0)
            df['gross_pnl'] = pd.to_numeric(df.get('gross_pnl', 0), errors='coerce').fillna(0)
            if 'result' not in df.columns: df['result'] = 'UNKNOWN'

            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Net Kâr (fee dahil)", f"${df['pnl_usd'].sum():.2f}")
            m2.metric("Brüt Kâr", f"${df['gross_pnl'].sum():.2f}")
            m3.metric("Toplam Fee", f"${df['fee_usd'].sum():.2f}")
            wins = len(df[df['result'].isin(['WIN','PARTIAL_WIN','TIMEOUT_WIN'])])
            m4.metric("Win Rate", f"%{(wins/max(len(df),1))*100:.1f}")

            # Fee drag yüzdesi
            gross_total = df['gross_pnl'].sum()
            fee_total = df['fee_usd'].sum()
            if gross_total > 0:
                fee_drag = (fee_total / gross_total) * 100
                st.markdown(f"""<div class='fee-box'><b>📉 Fee Drag:</b> Brüt kârın <b>%{fee_drag:.1f}</b>'i fee'ye gidiyor. {'⚠️ Çok yüksek!' if fee_drag > 30 else '✅ Kabul edilebilir.' if fee_drag < 20 else '⚡ İzlenmeli.'}</div>""", unsafe_allow_html=True)

            if 'time' in df.columns:
                st.markdown("#### `📈 KÜMÜLATİF KÂR (FEE DAHİL)`")
                dfs = df.sort_values('time')
                dfs['net_cum'] = dfs['pnl_usd'].cumsum()
                dfs['gross_cum'] = dfs['gross_pnl'].cumsum()
                chart_df = dfs.set_index('time')[['net_cum', 'gross_cum']]
                chart_df.columns = ['Net (fee dahil)', 'Brüt (fee hariç)']
                st.line_chart(chart_df)

            st.markdown("#### `📜 KASA DEFTERİ`")
            df['Zaman'] = df['time'].apply(safe_display_time) if 'time' in df.columns else '—'
            rn = {'bot':'Motor','symbol':'Varlık','pnl_usd':'Net($)','gross_pnl':'Brüt($)','fee_usd':'Fee($)','result':'Sonuç'}
            ex = {k:v for k,v in rn.items() if k in df.columns}
            df = df.rename(columns=ex)
            dc = ['Zaman'] + [v for v in rn.values() if v in df.columns]
            st.dataframe(df[dc].sort_values('Zaman', ascending=False), use_container_width=True)
        else:
            st.info("İlk işlem bekleniyor.")

    # ═══ TAB 5: RİSK ═══
    with tabs[4]:
        st.markdown("### ⚙️ Risk Yönetimi")
        st.markdown("""<div class='info-box'><b>Günlük Kayıp Limiti:</b> Tüm botların bugünkü toplam net kaybı bu limiti aşarsa, sistem otomatik olarak tüm otopilotları durdurur. Yeni güne geçildiğinde sıfırlanır.</div>""", unsafe_allow_html=True)

        risk_doc = safe_firebase_read(lambda: get_data_ref('states').document('daily_risk').get())
        risk_data = risk_doc.to_dict() if (risk_doc and risk_doc.exists) else {}

        with st.form("risk_cfg"):
            dl = st.number_input("Günlük Maks Kayıp ($)", 10, 1000, safe_int(risk_data.get('daily_limit', 100), 100))
            if st.form_submit_button("Limiti Güncelle"):
                get_data_ref('states').document('daily_risk').set({
                    'daily_limit': dl,
                    'date': risk_data.get('date', datetime.now().strftime('%Y-%m-%d')),
                    'daily_pnl': safe_float(risk_data.get('daily_pnl', 0)),
                    'daily_fees': safe_float(risk_data.get('daily_fees', 0)),
                    'trade_count': safe_int(risk_data.get('trade_count', 0))
                })
                st.rerun()

        r1,r2,r3,r4 = st.columns(4)
        r1.metric("Bugünkü PnL", f"${safe_float(risk_data.get('daily_pnl',0)):.2f}")
        r2.metric("Bugünkü Fee", f"${safe_float(risk_data.get('daily_fees',0)):.2f}")
        r3.metric("İşlem Sayısı", safe_int(risk_data.get('trade_count',0)))
        r4.metric("Limit", f"-${safe_float(risk_data.get('daily_limit',100)):.0f}")

        st.markdown("#### `💰 FEE HESAPLAYICI`")
        st.markdown("Farklı ayarların fee etkisini önceden gör:")
        hc1, hc2 = st.columns(2)
        with hc1:
            st.markdown("**Trend / Flash Fee**")
            test_bot = st.selectbox("Bot Tipi", ['trend', 'grid', 'flash'])
            test_margin = st.number_input("Test Marjin ($)", 50, 5000, 300)
            test_lev = st.slider("Test Kaldıraç", 1, 20, 3)
            test_notional = test_margin * test_lev
            test_fr = get_friction(test_bot)
            test_fee = test_notional * test_fr * 2
            st.metric("Round-Trip Fee", f"${test_fee:.2f}")
            st.caption(f"Notional: ${test_notional:,.0f} × %{test_fr*200:.2f} ({test_bot})")
        with hc2:
            st.markdown("**Grid Fee**")
            test_sp = st.number_input("Test Grid Spacing (%)", 0.1, 5.0, 0.5)
            test_gm = st.number_input("Test Grid Margin ($)", 10, 500, 100)
            gnet, gfee = calculate_grid_net_profit(test_gm, test_sp)
            st.metric("Grid Net Kâr", f"${gnet:.2f}")
            st.caption(f"Brüt: ${test_gm*test_sp/100:.2f} - Fee: ${gfee:.2f}")
            if gnet <= 0: st.error("⛔ Net negatif — bu spacing'de trade etme!")

        # Friction rate özeti
        st.markdown("---")
        fr_t = get_friction('trend'); fr_g = get_friction('grid'); fr_f = get_friction('flash')
        st.caption(f"Friction rates (taraf başı): Trend %{fr_t*100:.3f} | Grid %{fr_g*100:.3f} | Flash %{fr_f*100:.3f}. Flash yüksek çünkü volatil anlarda slippage artar. Kodda FRICTION_RATES dict'ini Binance fee tier'ınıza göre güncelleyin.")


if __name__ == "__main__":
    main()
