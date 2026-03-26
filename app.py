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
from datetime import datetime
import threading
import logging
import re

# ==========================================
# 🛡️ SİSTEM GÜNLÜĞÜ (LOGGING)
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 🎨 UI & TEMA AYARLARI
# ==========================================
st.set_page_config(page_title="QUANT OMNI V9.17 FINAL", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; border: 1px solid #333; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .info-box { background-color: #1A1D23; border-left: 5px solid #00A3FF; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .action-box { background-color: #1A1D23; border-left: 5px solid #FFD700; padding: 15px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 🔒 GÜVENLİK VE DOĞRULAMA
# ==========================================
VALID_SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]{2,20}$')
VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}

AVAILABLE_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "TAOUSDT", "AVAXUSDT"]
AVAILABLE_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
GRID_COINS = ["BTCUSDT", "ETHUSDT"]
HISTORY_QUERY_LIMIT = 500

def is_valid_symbol(symbol: str) -> bool:
    return isinstance(symbol, str) and bool(VALID_SYMBOL_PATTERN.match(symbol))

def is_valid_interval(interval: str) -> bool:
    return isinstance(interval, str) and interval in VALID_INTERVALS

def safe_float(val, default: float = 0.0) -> float:
    try: return float(val)
    except (TypeError, ValueError): return default

def safe_int(val, default: int = 0) -> int:
    try: return int(val)
    except (TypeError, ValueError): return default

# ==========================================
# 🌐 HTTP SESSION
# ==========================================
@st.cache_resource
def get_http_session():
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# ==========================================
# ☁️ FIREBASE
# ==========================================
@st.cache_resource
def get_db():
    if not firebase_admin._apps:
        try:
            fb_config_str = os.environ.get('FIREBASE_CONFIG', '').strip()
            if not fb_config_str: return None
            fb_config = json.loads(fb_config_str)
            if "project_id" in fb_config:
                cred = credentials.Certificate(fb_config)
                firebase_admin.initialize_app(cred)
            else:
                firebase_admin.initialize_app(options=fb_config)
        except Exception as e:
            logger.error(f"Firebase Init Error: {e}")
            return None
    return firestore.client()

db = get_db()
app_id = os.environ.get('APP_ID', 'quant-lab-v9-final')

def get_data_ref(collection_name):
    if db is None: raise RuntimeError("Firebase bağlantısı yok.")
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection(collection_name)

# ==========================================
# 🛠️ YARDIMCI FONKSİYONLAR
# ==========================================
def safe_str_time(val) -> str:
    if isinstance(val, str): return val
    if hasattr(val, 'isoformat'): return val.isoformat()
    return str(val)

def safe_short_time(val) -> str:
    s = safe_str_time(val)
    if 'T' in s: return s.split('T')[1][:8]
    if ' ' in s: return s.split(' ')[1][:8]
    return s[:8]

def safe_display_time(val) -> str:
    s = safe_str_time(val)
    return s.replace('T', ' ')[:16]

def filter_valid_defaults(defaults, options):
    if not isinstance(defaults, list): return [options[0]] if options else []
    filtered = [d for d in defaults if d in options]
    return filtered if filtered else [options[0]] if options else []

def safe_selectbox_index(value, options, fallback_index=0):
    try: return options.index(value)
    except ValueError: return fallback_index

# ==========================================
# 📊 İNDİKATÖR MOTORU
# ==========================================
def calculate_advanced_indicators(df, fast_ema, slow_ema):
    if df.empty or len(df) < slow_ema: return df
    df = df.copy()
    df['EMA_F'] = df['C'].ewm(span=fast_ema, adjust=False).mean()
    df['EMA_S'] = df['C'].ewm(span=slow_ema, adjust=False).mean()

    delta = df['C'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(100.0)

    tr = pd.concat([df['H'] - df['L'], np.abs(df['H'] - df['C'].shift()), np.abs(df['L'] - df['C'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    up_move = df['H'] - df['H'].shift(1)
    down_move = df['L'].shift(1) - df['L']
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr_safe = df['ATR'].replace(0, np.nan)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = (100 * np.abs(plus_di - minus_di) / di_sum).fillna(0)
    df['ADX'] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    return df

def fetch_klines(symbol, interval, limit=100):
    if not is_valid_symbol(symbol) or not is_valid_interval(interval): return pd.DataFrame()
    try:
        session = get_http_session()
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = session.get(url, timeout=10)
        if res.status_code != 200: return pd.DataFrame()
        try: raw = res.json()
        except ValueError: return pd.DataFrame()
        if not isinstance(raw, list) or len(raw) < 2: return pd.DataFrame()

        df = pd.DataFrame(raw, columns=['T', 'O', 'H', 'L', 'C', 'V', 'CT', 'QV', 'NT', 'TBV', 'TBQV', 'I'])
        for col in ['O', 'H', 'L', 'C', 'V']: df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['O', 'H', 'L', 'C', 'V'], inplace=True)
        return df
    except Exception as e:
        logger.error(f"API Error ({symbol}): {e}")
        return pd.DataFrame()

# ==========================================
# ⚙️ ARKA PLAN MOTORLARI
# ==========================================
@st.cache_resource
def whale_engine():
    def task():
        logger.info("🐳 Whale Motoru Başlatıldı")
        while True:
            try:
                if not db: break
                doc = get_data_ref('configs').document('trend').get()
                configs = doc.to_dict() if doc.exists else {}

                if configs and configs.get('autopilot', False):
                    coins = configs.get('coins', [])
                    if not isinstance(coins, list): coins = []
                    timeframe = configs.get('timeframe', '1h')
                    if not is_valid_interval(timeframe): timeframe = '1h'
                    ema_f_period = safe_int(configs.get('ema_f', 9), 9)
                    ema_s_period = safe_int(configs.get('ema_s', 21), 21)
                    adx_threshold = safe_float(configs.get('adx_t', 15), 15.0)
                    margin = safe_float(configs.get('margin', 300), 300.0)
                    leverage = safe_float(configs.get('leverage', 3), 3.0)
                    tp_atr = safe_float(configs.get('tp_atr', 3.5), 3.5)

                    if ema_f_period <= 0 or ema_s_period <= 0 or margin <= 0 or leverage <= 0: time.sleep(45); continue

                    current_status = {}

                    for symbol in coins:
                        if not is_valid_symbol(symbol): continue
                        raw_df = fetch_klines(symbol, timeframe, limit=150)
                        if raw_df.empty or len(raw_df) < ema_s_period * 2: continue

                        df = calculate_advanced_indicators(raw_df, ema_f_period, ema_s_period)
                        if len(df) < 2: continue

                        current, prev = df.iloc[-1], df.iloc[-2]
                        price = safe_float(current['C'])
                        if price <= 0: continue

                        if pd.isna(current['EMA_F']) or pd.isna(current['EMA_S']) or pd.isna(current['RSI']) or pd.isna(current['ADX']) or pd.isna(current['ATR']): continue
                            
                        current_status[symbol] = {
                            'price': round(price, 4), 'ema_f': round(float(current['EMA_F']), 4),
                            'ema_s': round(float(current['EMA_S']), 4), 'rsi': round(float(current['RSI']), 2),
                            'adx': round(float(current['ADX']), 2)
                        }

                        pos_ref = get_data_ref('active_trades').document(f"trend_{symbol}")
                        pos_doc = pos_ref.get()

                        if pos_doc.exists:
                            p = pos_doc.to_dict()
                            res = ""
                            p_sl = safe_float(p.get('sl', 0))
                            p_tp = safe_float(p.get('tp', 0))
                            p_entry = safe_float(p.get('entry', price))
                            p_type = p.get('type', 'LONG')

                            if p_entry <= 0: pos_ref.delete(); continue

                            if p_type == 'LONG':
                                if price <= p_sl: res = "LOSS"
                                elif price >= p_tp: res = "WIN"
                            else:
                                if price >= p_sl: res = "LOSS"
                                elif price <= p_tp: res = "WIN"

                            if res:
                                pnl = ((price - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - price) / p_entry * 100)
                                get_data_ref('history').add({
                                    'bot': 'trend', 'symbol': symbol, 'pnl_usd': round((margin * pnl * leverage) / 100, 2),
                                    'result': res, 'time': datetime.now().isoformat()
                                })
                                pos_ref.delete()
                        else:
                            atr_val = safe_float(current['ATR'])
                            if atr_val <= 0: continue
                            if (prev['EMA_F'] <= prev['EMA_S'] and current['EMA_F'] > current['EMA_S'] and current['RSI'] > 50 and current['ADX'] >= adx_threshold):
                                pos_ref.set({'type': 'LONG', 'entry': price, 'sl': price - (atr_val * 2), 'tp': price + (atr_val * tp_atr), 'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()})
                            elif (prev['EMA_F'] >= prev['EMA_S'] and current['EMA_F'] < current['EMA_S'] and current['RSI'] < 50 and current['ADX'] >= adx_threshold):
                                pos_ref.set({'type': 'SHORT', 'entry': price, 'sl': price + (atr_val * 2), 'tp': price - (atr_val * tp_atr), 'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()})
                    
                    try:
                        if current_status: get_data_ref('states').document('trend_status').set({'data': current_status, 'updated': datetime.now().isoformat()})
                    except Exception as e: pass

                time.sleep(45)
            except Exception as e:
                time.sleep(30)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

@st.cache_resource
def ant_engine():
    def task():
        logger.info("🐜 Ant Motoru Başlatıldı")
        while True:
            try:
                if not db: break
                doc = get_data_ref('configs').document('grid').get()
                configs = doc.to_dict() if doc.exists else {}

                if configs and configs.get('autopilot', False):
                    symbol = configs.get('coin', 'BTCUSDT')
                    if not is_valid_symbol(symbol): time.sleep(20); continue

                    spacing = safe_float(configs.get('grid_spacing_pct', 0.5), 0.5)
                    margin = safe_float(configs.get('margin_per_grid', 100), 100.0)
                    max_grids = safe_int(configs.get('max_grids', 50), 50)
                    if spacing <= 0 or margin <= 0 or max_grids <= 0: time.sleep(20); continue

                    session = get_http_session()
                    res = session.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
                    if res.status_code == 200:
                        price = safe_float(res.json().get('price', 0))
                        if price > 0:
                            state_ref = get_data_ref('states').document('grid')
                            state_doc = state_ref.get()
                            state = state_doc.to_dict() if state_doc.exists else {'grids': [], 'total_profit': 0.0}

                            grids = state.get('grids', [])
                            if not isinstance(grids, list): grids = []
                            new_grids = []
                            total_prof = safe_float(state.get('total_profit', 0.0))

                            for g in grids:
                                g_entry = safe_float(g.get('entry', 0))
                                if g_entry <= 0: continue
                                if price >= g_entry * (1 + spacing / 100):
                                    profit = (margin * spacing) / 100
                                    get_data_ref('history').add({'bot': 'grid', 'symbol': symbol, 'pnl_usd': round(profit, 2), 'result': 'WIN', 'time': datetime.now().isoformat()})
                                    total_prof += profit
                                else:
                                    new_grids.append(g)

                            if len(new_grids) < max_grids:
                                entries = [safe_float(x.get('entry', 0)) for x in new_grids]
                                entries = [e for e in entries if e > 0]
                                last_buy = min(entries) if entries else price
                                if price <= last_buy * (1 - spacing / 100) or not new_grids:
                                    new_grids.append({'entry': price, 'time': datetime.now().isoformat()})

                            state_ref.set({'grids': new_grids, 'total_profit': total_prof, 'last_price': price, 'updated': datetime.now().isoformat()})
                time.sleep(20)
            except Exception as e:
                time.sleep(20)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

@st.cache_resource
def falcon_engine():
    def task():
        logger.info("⚡ Falcon Motoru Başlatıldı")
        while True:
            try:
                if not db: break
                doc = get_data_ref('configs').document('flash').get()
                configs = doc.to_dict() if doc.exists else {}

                if configs and configs.get('autopilot', False):
                    vol_spike_threshold = safe_float(configs.get('vol_spike', 5.0), 5.0)
                    margin = safe_float(configs.get('margin', 200), 200.0)
                    leverage = safe_float(configs.get('leverage', 5), 5.0)
                    tp_pct = safe_float(configs.get('tp_pct', 5.0), 5.0)
                    sl_pct = safe_float(configs.get('sl_pct', 3.0), 3.0)
                    cooldown_hours = 6

                    if margin <= 0 or leverage <= 0 or tp_pct <= 0 or sl_pct <= 0: time.sleep(30); continue

                    pos_ref = get_data_ref('active_trades').document('flash_pos')
                    pos_doc = pos_ref.get()

                    # 1. ADIM: AKTİF İŞLEM
                    if pos_doc.exists:
                        p = pos_doc.to_dict()
                        symbol = p.get('symbol', '')
                        if not symbol or not is_valid_symbol(symbol): pos_ref.delete(); time.sleep(30); continue

                        session = get_http_session()
                        p_res = session.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
                        if p_res.status_code == 200:
                            price = safe_float(p_res.json().get('price', 0))
                            p_entry = safe_float(p.get('entry', 0))
                            p_type = p.get('type', 'LONG')
                            if p_entry <= 0: pos_ref.delete(); time.sleep(30); continue

                            res_str = ""
                            if price <= safe_float(p.get('sl', 0)): res_str = "LOSS"
                            elif price >= safe_float(p.get('tp', 0)): res_str = "WIN"

                            if res_str:
                                pnl = ((price - p_entry) / p_entry * 100) if p_type == 'LONG' else ((p_entry - price) / p_entry * 100)
                                get_data_ref('history').add({'bot': 'flash', 'symbol': symbol, 'pnl_usd': round((margin * pnl * leverage) / 100, 2), 'result': res_str, 'time': datetime.now().isoformat()})
                                
                                get_data_ref('states').document('flash_cooldown').set({symbol: datetime.now().isoformat()}, merge=True)
                                pos_ref.delete()
                    else:
                        # 2. ADIM: YENİ SİNYAL ARA (COOLDOWN FİLTRESİ İLE)
                        session = get_http_session()
                        res = session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
                        
                        cd_doc = get_data_ref('states').document('flash_cooldown').get()
                        cooldowns = cd_doc.to_dict() if cd_doc.exists else {}
                        now = datetime.now()

                        if res.status_code == 200:
                            try:
                                ticks = res.json()
                                if not isinstance(ticks, list): time.sleep(30); continue
                                
                                valid_spikes = []
                                for t in ticks:
                                    try:
                                        change = float(t.get('priceChangePercent', 0))
                                        sym = t.get('symbol', '')
                                        if change > vol_spike_threshold and sym.endswith('USDT') and is_valid_symbol(sym):
                                            last_trade_str = cooldowns.get(sym)
                                            is_cooling_down = False
                                            if last_trade_str:
                                                last_time = datetime.fromisoformat(last_trade_str)
                                                if (now - last_time).total_seconds() < (cooldown_hours * 3600):
                                                    is_cooling_down = True
                                            
                                            if not is_cooling_down:
                                                valid_spikes.append(t)
                                    except (ValueError, TypeError): continue
                                
                                if valid_spikes:
                                    top = max(valid_spikes, key=lambda x: safe_float(x.get('quoteVolume', 0)))
                                    symbol = top['symbol']
                                    price = safe_float(top.get('lastPrice', 0))
                                    if price > 0:
                                        pos_ref.set({'type': 'LONG', 'entry': price, 'sl': price * (1 - sl_pct / 100), 'tp': price * (1 + tp_pct / 100), 'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()})
                                        get_data_ref('signals').document('flash').set({'symbol': symbol, 'change': top.get('priceChangePercent', '0'), 'vol': top.get('quoteVolume', '0'), 'time': datetime.now().isoformat()})
                            except Exception as e: pass
                time.sleep(30)
            except Exception as e:
                time.sleep(30)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

# ==========================================
# 🖥️ MERKEZİ KONTROL ARAYÜZÜ (ŞEFFAF MOD)
# ==========================================
def calculate_bot_stats(history_data, bot_name):
    bot_data = [h for h in history_data if h.get('bot') == bot_name]
    if not bot_data: return 0, 0.0, 0.0
    df = pd.DataFrame(bot_data)
    if 'result' not in df.columns: df['result'] = 'UNKNOWN'
    if 'pnl_usd' in df.columns: df['pnl_usd'] = pd.to_numeric(df['pnl_usd'], errors='coerce').fillna(0.0)
    else: df['pnl_usd'] = 0.0
    wins = len(df[df['result'] == 'WIN'])
    total = len(df)
    win_rate = (wins / total) * 100 if total > 0 else 0.0
    total_pnl = float(df['pnl_usd'].sum())
    return total, win_rate, total_pnl

def safe_render_dataframe(data_list, rename_map, desired_order=None):
    if not data_list: return None
    df = pd.DataFrame(data_list)
    existing_renames = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=existing_renames)
    if desired_order: display_cols = [v for v in desired_order if v in df.columns]
    else: display_cols = [v for v in rename_map.values() if v in df.columns]
    return df[display_cols] if display_cols else df

def main():
    st.title("🛡️ QUANT OMNI SENTINEL V9.17 FINAL")

    if db:
        st.markdown('<div class="status-bar online">● SİSTEM ÇEVRİMİÇİ | 💠 KASA İZLENİYOR</div>', unsafe_allow_html=True)
        whale_engine()
        ant_engine()
        falcon_engine()

        hist_query = get_data_ref('history').order_by('time', direction=firestore.Query.DESCENDING).limit(HISTORY_QUERY_LIMIT)
        hist_docs = hist_query.get()
        all_history = [h.to_dict() for h in hist_docs] if hist_docs else []
    else:
        st.markdown('<div class="status-bar offline">❌ SİSTEM ÇEVRİMDIŞI (Firebase Config Hatası)</div>', unsafe_allow_html=True)
        return

    tabs = st.tabs(["🐳 TREND", "🐜 GRID", "⚡ FLASH", "📊 GENEL ANALİTİK"])

    # --- 1. TREND TAB ---
    with tabs[0]:
        st.markdown("""<div class='info-box'><b>ℹ️ Amaç:</b> Büyük piyasa dalgalarını (Trend) yakalamak. Seçilen coinlerde Hızlı EMA, Yavaş EMA'yı kestiğinde ve güç (ADX) yeterliyse işleme girer. Zarar-kes ve Kâr-al seviyelerini fiyatın hareketliliğine (ATR) göre otomatik ayarlar.</div>""", unsafe_allow_html=True)

        t_tot, t_wr, t_pnl = calculate_bot_stats(all_history, 'trend')
        c_st1, c_st2, c_st3 = st.columns(3)
        c_st1.metric("Trend İşlem Sayısı", t_tot)
        c_st2.metric("Kazanma Oranı", f"%{t_wr:.1f}")
        c_st3.metric("Trend Net Kâr", f"${t_pnl:.2f}")
        st.divider()

        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('trend').get()
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("t_cfg"):
                m = st.number_input("İşlem Marjini ($)", 10, 5000, safe_int(cfg.get('margin', 300), 300))
                l = st.slider("Kaldıraç (x)", 1, 20, safe_int(cfg.get('leverage', 3), 3))

                saved_coins = cfg.get('coins', ["BTCUSDT"])
                coins = st.multiselect("Varlıklar", AVAILABLE_COINS, default=filter_valid_defaults(saved_coins, AVAILABLE_COINS))

                current_tf = cfg.get('timeframe', '1h')
                tf = st.selectbox("Zaman Dilimi", AVAILABLE_TIMEFRAMES, index=safe_selectbox_index(current_tf, AVAILABLE_TIMEFRAMES, 1))
                ema_f = st.slider("Hızlı EMA", 5, 50, safe_int(cfg.get('ema_f', 9), 9))
                ema_s = st.slider("Yavaş EMA", 10, 200, safe_int(cfg.get('ema_s', 21), 21))
                adx_t = st.slider("ADX Filtresi (Gürültü Önleyici)", 0, 40, safe_int(cfg.get('adx_t', 15), 15))
                tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, float(min(max(safe_float(cfg.get('tp_atr', 3.5), 3.5), 1.0), 6.0)))

                auto = st.checkbox("Otopilot Aktif", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('trend').set({'margin': m, 'leverage': l, 'coins': coins, 'timeframe': tf, 'ema_f': ema_f, 'ema_s': ema_s, 'adx_t': adx_t, 'tp_atr': tp_atr, 'autopilot': auto})
                    st.rerun()
        with c2:
            st.markdown("#### `🟢 AKTİF İŞLEMLER VE BEKLENTİLER`")
            active_docs = get_data_ref('active_trades').get()
            trend_trades = [a.to_dict() for a in active_docs if a.id.startswith('trend_')]
            if trend_trades:
                rename_map = {'symbol': 'Varlık', 'type': 'Yön', 'entry': 'Giriş Fiyatı', 'tp': 'Kâr Al', 'sl': 'Zarar Kes', 'margin': 'Marjin($)'}
                df_t = safe_render_dataframe(trend_trades, rename_map)
                if df_t is not None: st.dataframe(df_t, width="stretch")
                st.markdown("""<div class='action-box'><b>⚡ Ne Olacak?</b> Fiyat 'Kâr Al' seviyesine gelirse işlem kazançla kapatılacak. 'Zarar Kes' seviyesine düşerse zararla kapatılıp fon korunacak.</div>""", unsafe_allow_html=True)
            else:
                st.info("Şu an açık Trend işlemi yok. Sistem yeni EMA kesişimi ve yeterli ADX gücü bekliyor.")

            st.markdown("#### `📡 CANLI SİNYAL RADARI`")
            status_doc = get_data_ref('states').document('trend_status').get()
            if status_doc.exists:
                status_data = status_doc.to_dict().get('data', {})
                if status_data:
                    radar_list = []
                    adx_thresh = float(cfg.get('adx_t', 15))
                    for sym, vals in status_data.items():
                        ema_cross = "🟢 Yükseliş" if vals['ema_f'] > vals['ema_s'] else "🔴 Düşüş"
                        adx_ok = "✅ Yeterli" if vals['adx'] >= adx_thresh else "⏳ Zayıf (Bekliyor)"
                        radar_list.append({'Varlık': sym, 'Trend Yönü': ema_cross, 'Güç (ADX)': f"{vals['adx']} ({adx_ok})", 'RSI': vals['rsi']})
                    st.dataframe(pd.DataFrame(radar_list), width="stretch")
                else: st.info("Piyasa verileri analiz ediliyor...")
            else: st.info("Radar sistemi veri topluyor, lütfen bekleyin...")

    # --- 2. GRID TAB ---
    with tabs[1]:
        st.markdown("""<div class='info-box'><b>ℹ️ Amaç:</b> Piyasa yatay giderken veya düşerken belirlenen % aralıklarla sürekli küçük alımlar yapmak. Fiyat hafif yükseldiğinde her parçayı kârla satarak düzenli nakit akışı (kumbara) sağlamak.</div>""", unsafe_allow_html=True)

        g_tot, _, g_pnl = calculate_bot_stats(all_history, 'grid')
        c_sg1, c_sg2, c_sg3 = st.columns(3)
        c_sg1.metric("Kapatılan Grid (Parça)", g_tot)
        c_sg2.metric("Sistem Durumu", "Kumbara Modu")
        c_sg3.metric("Toplanan Harçlık", f"${g_pnl:.2f}")
        st.divider()

        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('grid').get()
            cfg_grid = doc.to_dict() if doc.exists else {}
            with st.form("g_cfg"):
                current_coin = cfg_grid.get('coin', 'BTCUSDT')
                coin = st.selectbox("Güvenli Liman", GRID_COINS, index=safe_selectbox_index(current_coin, GRID_COINS, 0))
                space = st.number_input("Ağ Aralığı (%)", 0.1, 5.0, float(min(max(safe_float(cfg_grid.get('grid_spacing_pct', 0.5), 0.5), 0.1), 5.0)))
                m_grid = st.number_input("Parça Başı Bütçe ($)", 10, 500, safe_int(cfg_grid.get('margin_per_grid', 100), 100))
                max_g = st.number_input("Maksimum Ağ Sayısı", 10, 100, safe_int(cfg_grid.get('max_grids', 50), 50))
                auto_g = st.checkbox("Otopilot Aktif", value=cfg_grid.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('grid').set({'coin': coin, 'grid_spacing_pct': space, 'margin_per_grid': m_grid, 'max_grids': max_g, 'autopilot': auto_g})
                    st.rerun()
        with c2:
            st.markdown("#### `🐜 ELDEKİ PARÇALAR (PUSUDAKİLER)`")
            state = get_data_ref('states').document('grid').get()
            if state.exists:
                state_dict = state.to_dict()
                grids_list = state_dict.get('grids', [])
                if isinstance(grids_list, list) and grids_list:
                    df_g = pd.DataFrame(grids_list)
                    space_pct = safe_float(cfg_grid.get('grid_spacing_pct', 0.5), 0.5)

                    if 'entry' in df_g.columns:
                        df_g['entry'] = pd.to_numeric(df_g['entry'], errors='coerce').fillna(0)
                        df_g['Hedef Fiyat ($)'] = df_g['entry'] * (1 + (space_pct / 100))
                        df_g['Zaman'] = df_g['time'].apply(safe_short_time) if 'time' in df_g.columns else '—'
                        df_g = df_g.rename(columns={'entry': 'Alış Fiyatı ($)'})
                        
                        st.dataframe(df_g[['Zaman', 'Alış Fiyatı ($)', 'Hedef Fiyat ($)']].sort_values('Alış Fiyatı ($)'), width="stretch")

                        valid_prices = df_g[df_g['Alış Fiyatı ($)'] > 0]['Alış Fiyatı ($)']
                        if not valid_prices.empty:
                            lowest_buy = valid_prices.min()
                            next_buy = lowest_buy * (1 - (space_pct / 100))
                            st.markdown(f"""<div class='action-box'><b>⚡ Sıradaki Hamleler:</b><br>
                            1. Fiyat tablodaki <b>Hedef Fiyat</b>'lara ulaşırsa o parça satılıp kâr kumbaraya atılacak.<br>
                            2. Fiyat düşmeye devam ederse, bir sonraki yeni parça alımı <b>~${next_buy:.2f}</b> seviyesinden yapılacak.</div>""", unsafe_allow_html=True)
                    else:
                        st.dataframe(pd.DataFrame(grids_list), width="stretch")
                else: st.info("Şu an elde parça yok. Otopilot açıksa anlık fiyattan ilk alım yapılacak.")
            else: st.info("Grid motoru beklemede.")

    # --- 3. FLASH TAB ---
    with tabs[2]:
        st.markdown("""<div class='info-box'><b>ℹ️ Amaç:</b> Binance'deki tüm coinleri tarayarak 24 saat içinde aniden fırlayan (hacim patlaması yaşayan) coinleri yakalamak. "Soğuma (Cooldown)" koruması sayesinde kâr/zarar aldığı bir coine 6 saat boyunca tekrar girmez.</div>""", unsafe_allow_html=True)

        f_tot, f_wr, f_pnl = calculate_bot_stats(all_history, 'flash')
        c_sf1, c_sf2, c_sf3 = st.columns(3)
        c_sf1.metric("Yakalanan Patlama", f_tot)
        c_sf2.metric("Hedefi Vurma Oranı", f"%{f_wr:.1f}")
        c_sf3.metric("Flash Net Kâr", f"${f_pnl:.2f}")
        st.divider()

        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('flash').get()
            cfg_flash = doc.to_dict() if doc.exists else {}
            with st.form("f_cfg"):
                m_flash = st.number_input("İşlem Marjini ($)", 10, 5000, safe_int(cfg_flash.get('margin', 200), 200))
                l_flash = st.slider("Kaldıraç (x)", 1, 20, safe_int(cfg_flash.get('leverage', 5), 5))
                tp_pct_input = st.number_input("Kâr Hedefi (%)", 1.0, 20.0, float(min(max(safe_float(cfg_flash.get('tp_pct', 5.0), 5.0), 1.0), 20.0)))
                sl_pct_input = st.number_input("Zarar Kes (%)", 1.0, 20.0, float(min(max(safe_float(cfg_flash.get('sl_pct', 3.0), 3.0), 1.0), 20.0)))
                f_s = st.slider("Hacim Patlama Eşiği (%)", 2.0, 50.0, float(min(max(safe_float(cfg_flash.get('vol_spike', 10.0), 10.0), 2.0), 50.0)))
                f_a = st.checkbox("Otopilot Aktif", value=cfg_flash.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('flash').set({'margin': m_flash, 'leverage': l_flash, 'tp_pct': tp_pct_input, 'sl_pct': sl_pct_input, 'vol_spike': f_s, 'autopilot': f_a})
                    st.rerun()
            
            if st.button("♻️ Kara Listeyi (Cooldown) Temizle"):
                get_data_ref('states').document('flash_cooldown').delete()
                st.success("Hafıza temizlendi, tüm coinler tekrar taranabilir!")
                time.sleep(1)
                st.rerun()

        with c2:
            st.markdown("#### `⚡ AKTİF FLASH İŞLEMİ (AV)`")
            active_f = get_data_ref('active_trades').document('flash_pos').get()
            if active_f.exists:
                p = active_f.to_dict()
                flash_rename = {'symbol': 'Varlık', 'entry': 'Giriş Fiyatı', 'tp': 'Kâr Hedefi', 'sl': 'Stop Loss', 'margin': 'Bütçe($)'}
                df_flash = safe_render_dataframe([p], flash_rename)
                if df_flash is not None: st.dataframe(df_flash, width="stretch")
                st.markdown("""<div class='action-box'><b>⚡ Ne Olacak?</b> Vurgun yapıldı! Fiyat Kâr Hedefine ulaşırsa kazançla çıkılacak. Tersine dönerse Stop Loss devrede.</div>""", unsafe_allow_html=True)
            else:
                sig = get_data_ref('signals').document('flash').get()
                if sig.exists:
                    s = sig.to_dict()
                    st.warning(f"📡 SON RADAR TESPİTİ: {s.get('symbol', '?')} | Patlama: %{s.get('change', '?')}")
                st.info("Radarda uygun hedef yok veya coinler 6 saatlik bekleme (soğuma) süresinde...")
                
            st.markdown("#### `🕒 BEKLEMEDEKİLER (KARA LİSTE)`")
            cd_doc = get_data_ref('states').document('flash_cooldown').get()
            if cd_doc.exists:
                cooldowns = cd_doc.to_dict()
                if cooldowns:
                    cd_list = []
                    now = datetime.now()
                    for sym, time_str in cooldowns.items():
                        try:
                            last_time = datetime.fromisoformat(time_str)
                            hours_passed = (now - last_time).total_seconds() / 3600
                            if hours_passed < 6:
                                cd_list.append({"Varlık": sym, "Kalan Süre": f"{round(6 - hours_passed, 1)} Saat"})
                        except: pass
                    if cd_list:
                        st.dataframe(pd.DataFrame(cd_list), width="stretch")
                    else: st.caption("Beklemede coin yok.")
                else: st.caption("Beklemede coin yok.")

    # --- 4. ANALYTICS TAB ---
    with tabs[3]:
        st.markdown("### 📊 Tüm Fonun Merkezi Analitiği")
        
        # ULTIMATE HARD RESET (Mutlak Sıfırlama)
        if st.button("🚨 Mutlak Sıfırlama (Tüm Verileri Sil)", type="primary", help="Ayarlarınız kalır, ancak işlem geçmişi, eldeki grid parçaları, aktif işlemler ve radar temizlenir."):
            try:
                # 1. Geçmişi Sil
                for doc in get_data_ref('history').stream(): doc.reference.delete()
                # 2. Aktif İşlemleri Sil
                for doc in get_data_ref('active_trades').stream(): doc.reference.delete()
                # 3. Durumları Sil (Grid, Cooldown, Radar)
                for doc in get_data_ref('states').stream(): doc.reference.delete()
                # 4. Sinyalleri Sil
                for doc in get_data_ref('signals').stream(): doc.reference.delete()
                
                st.success("Tüm sistem hafızası başarıyla silindi! Yeni döneme hazır.")
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Sıfırlama sırasında hata: {e}")

        if all_history:
            df = pd.DataFrame(all_history)
            df['pnl_usd'] = pd.to_numeric(df['pnl_usd'], errors='coerce').fillna(0.0) if 'pnl_usd' in df.columns else 0.0
            if 'result' not in df.columns: df['result'] = 'UNKNOWN'

            m1, m2, m3 = st.columns(3)
            m1.metric("Kümülatif Net Kâr", f"${df['pnl_usd'].sum():.2f}")
            m2.metric("Toplam Kapanan İşlem", len(df))
            wins = len(df[df['result'] == 'WIN'])
            m3.metric("Genel Başarı Oranı", f"%{(wins / max(len(df), 1)) * 100:.1f}")

            st.markdown("#### `📜 KASA DEFTERİ (Tüm İşlemler)`")
            df['Zaman'] = df['time'].apply(safe_display_time) if 'time' in df.columns else '—'
            analytics_rename = {'bot': 'Motor', 'symbol': 'Varlık', 'pnl_usd': 'Kâr/Zarar ($)', 'result': 'Sonuç'}
            existing_analytics = {k: v for k, v in analytics_rename.items() if k in df.columns}
            df = df.rename(columns=existing_analytics)
            display_analytics = ['Zaman'] + [v for v in analytics_rename.values() if v in df.columns]
            st.dataframe(df[display_analytics].sort_values('Zaman', ascending=False), width="stretch")
        else: 
            st.info("İstatistik verisi bekleniyor. İlk işlem kapandığında burada görünecektir.")

    time.sleep(10)
    st.rerun()

if __name__ == "__main__":
    main()


