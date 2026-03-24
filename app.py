import streamlit as st
import pandas as pd
import numpy as np
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import time
from datetime import datetime
import threading
import logging

# ==========================================
# 🛡️ SİSTEM GÜNLÜĞÜ (LOGGING)
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 🎨 UI & TEMA AYARLARI
# ==========================================
st.set_page_config(page_title="QUANT OMNI V9.10 PRIME", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; border: 1px solid #333; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# ☁️ FIREBASE SINGLETON
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
app_id = os.environ.get('APP_ID', 'quant-lab-v9-prime')

def get_data_ref(collection_name):
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection(collection_name)

# ==========================================
# 📊 BİLİMSEL İNDİKATÖR MOTORU
# ==========================================
def calculate_advanced_indicators(df, fast_ema, slow_ema):
    if df.empty or len(df) < slow_ema: return df
    
    df['EMA_F'] = df['C'].ewm(span=fast_ema, adjust=False).mean()
    df['EMA_S'] = df['C'].ewm(span=slow_ema, adjust=False).mean()
    
    delta = df['C'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + (gain / loss)))
    
    tr = pd.concat([df['H']-df['L'], np.abs(df['H']-df['C'].shift()), np.abs(df['L']-df['C'].shift())], axis=1).max(axis=1)
    df['ATR'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    up_move = df['H'] - df['H'].shift(1)
    down_move = df['L'].shift(1) - df['L']
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR'])
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR'])
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    df['ADX'] = dx.fillna(0).ewm(alpha=1/14, adjust=False).mean() 
    
    return df

def fetch_klines(symbol, interval, limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=10)
        if res.status_code != 200: return pd.DataFrame()
        try: raw = res.json()
        except ValueError: return pd.DataFrame()
        if not isinstance(raw, list) or len(raw) < 2: return pd.DataFrame()
        
        df = pd.DataFrame(raw, columns=['T', 'O', 'H', 'L', 'C', 'V', 'CT', 'QV', 'NT', 'TBV', 'TBQV', 'I'])
        df[['O', 'H', 'L', 'C', 'V']] = df[['O', 'H', 'L', 'C', 'V']].apply(pd.to_numeric)
        return df
    except Exception as e:
        logger.error(f"API Error ({symbol}): {e}")
        return pd.DataFrame()

# ==========================================
# ⚙️ ARKA PLAN MOTORLARI
# ==========================================

# 1. TREND MOTORU (WHALE)
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
                    timeframe = configs.get('timeframe', '1h')
                    ema_f_period = configs.get('ema_f', 9)
                    ema_s_period = configs.get('ema_s', 21)
                    adx_threshold = configs.get('adx_t', 15)
                    margin = configs.get('margin', 300)
                    leverage = configs.get('leverage', 3)
                    tp_atr = configs.get('tp_atr', 3.5)

                    for symbol in coins:
                        raw_df = fetch_klines(symbol, timeframe, limit=150)
                        if raw_df.empty or len(raw_df) < ema_s_period * 2: continue
                        
                        df = calculate_advanced_indicators(raw_df, ema_f_period, ema_s_period)
                        
                        current = df.iloc[-1]
                        prev = df.iloc[-2]
                        price = current['C']
                        
                        pos_ref = get_data_ref('active_trades').document(f"trend_{symbol}")
                        pos_doc = pos_ref.get()
                        
                        if pos_doc.exists:
                            p = pos_doc.to_dict()
                            res = ""
                            p_sl = p.get('sl', 0)
                            p_tp = p.get('tp', 0)
                            p_entry = p.get('entry', price)
                            p_type = p.get('type', 'LONG')
                            
                            if p_type == 'LONG':
                                if price <= p_sl: res = "LOSS"
                                elif price >= p_tp: res = "WIN"
                            else:
                                if price >= p_sl: res = "LOSS"
                                elif price <= p_tp: res = "WIN"
                            
                            if res:
                                pnl = ((price - p_entry)/p_entry*100) if p_type == 'LONG' else ((p_entry - price)/p_entry*100)
                                get_data_ref('history').add({
                                    'bot': 'trend', 'symbol': symbol, 'pnl_usd': round((margin * pnl * leverage)/100, 2),
                                    'result': res, 'time': datetime.now().isoformat()
                                })
                                pos_ref.delete()
                        else:
                            if prev['EMA_F'] <= prev['EMA_S'] and current['EMA_F'] > current['EMA_S'] and current['RSI'] > 50 and current['ADX'] >= adx_threshold:
                                pos_ref.set({'type': 'LONG', 'entry': price, 'sl': price-(current['ATR']*2), 'tp': price+(current['ATR']*tp_atr), 'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()})
                            elif prev['EMA_F'] >= prev['EMA_S'] and current['EMA_F'] < current['EMA_S'] and current['RSI'] < 50 and current['ADX'] >= adx_threshold:
                                pos_ref.set({'type': 'SHORT', 'entry': price, 'sl': price+(current['ATR']*2), 'tp': price-(current['ATR']*tp_atr), 'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()})
                time.sleep(45)
            except Exception as e:
                logger.error(f"Whale Error: {e}")
                time.sleep(30)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

# 2. GRID MOTORU (ANT)
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
                    spacing = configs.get('grid_spacing_pct', 0.5)
                    margin = configs.get('margin_per_grid', 100)
                    max_grids = configs.get('max_grids', 50)

                    res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
                    if res.status_code == 200:
                        price = float(res.json().get('price', 0))
                        if price > 0:
                            state_ref = get_data_ref('states').document('grid')
                            state_doc = state_ref.get()
                            state = state_doc.to_dict() if state_doc.exists else {'grids': [], 'total_profit': 0.0}
                            
                            grids = state.get('grids', [])
                            new_grids = []
                            total_prof = state.get('total_profit', 0.0)
                            
                            for g in grids:
                                g_entry = g.get('entry', price)
                                if price >= g_entry * (1 + spacing/100):
                                    profit = (margin * spacing) / 100
                                    get_data_ref('history').add({
                                        'bot': 'grid', 'symbol': symbol, 'pnl_usd': round(profit, 2), 'result': 'WIN', 'time': datetime.now().isoformat()
                                    })
                                    total_prof += profit
                                else:
                                    new_grids.append(g)
                            
                            if len(new_grids) < max_grids:
                                entries = [x.get('entry', price) for x in new_grids]
                                last_buy = min(entries) if entries else price * 1.05
                                if price <= last_buy * (1 - spacing/100) or not new_grids:
                                    new_grids.append({'entry': price, 'time': datetime.now().isoformat()})
                            
                            state_ref.set({'grids': new_grids, 'total_profit': total_prof, 'last_price': price, 'updated': datetime.now().isoformat()})
                time.sleep(20)
            except Exception as e:
                logger.error(f"Ant Error: {e}")
                time.sleep(20)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

# 3. FLASH MOTORU (FALCON)
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
                    vol_spike_threshold = configs.get('vol_spike', 5.0)
                    margin = configs.get('margin', 200)
                    leverage = configs.get('leverage', 5)
                    tp_pct = configs.get('tp_pct', 5.0)
                    sl_pct = configs.get('sl_pct', 3.0)

                    pos_ref = get_data_ref('active_trades').document('flash_pos')
                    pos_doc = pos_ref.get()

                    if pos_doc.exists:
                        p = pos_doc.to_dict()
                        symbol = p.get('symbol')
                        p_res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
                        if p_res.status_code == 200:
                            price = float(p_res.json().get('price', 0))
                            res_str = ""
                            if price <= p.get('sl', 0): res_str = "LOSS"
                            elif price >= p.get('tp', 0): res_str = "WIN"

                            if res_str:
                                pnl = ((price - p['entry'])/p['entry']*100) if p['type'] == 'LONG' else ((p['entry'] - price)/p['entry']*100)
                                get_data_ref('history').add({
                                    'bot': 'flash', 'symbol': symbol, 'pnl_usd': round((margin * pnl * leverage)/100, 2),
                                    'result': res_str, 'time': datetime.now().isoformat()
                                })
                                pos_ref.delete()
                    else:
                        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
                        if res.status_code == 200:
                            try:
                                ticks = res.json()
                                spikes = [t for t in ticks if float(t.get('priceChangePercent', 0)) > vol_spike_threshold and "USDT" in t.get('symbol', '')]
                                if spikes:
                                    top = sorted(spikes, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[0]
                                    symbol = top['symbol']
                                    price = float(top['lastPrice'])
                                    
                                    tp_price = price * (1 + tp_pct/100)
                                    sl_price = price * (1 - sl_pct/100)

                                    pos_ref.set({
                                        'type': 'LONG', 'entry': price, 'sl': sl_price, 'tp': tp_price,
                                        'margin': margin, 'leverage': leverage, 'symbol': symbol, 'time': datetime.now().isoformat()
                                    })

                                    get_data_ref('signals').document('flash').set({
                                        'symbol': symbol, 'change': top['priceChangePercent'], 'vol': top['quoteVolume'], 'time': datetime.now().isoformat()
                                    })
                            except ValueError:
                                pass
                time.sleep(30)
            except Exception as e:
                logger.error(f"Falcon Error: {e}")
                time.sleep(30)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

# ==========================================
# 🖥️ MERKEZİ KONTROL ARAYÜZÜ
# ==========================================
def main():
    st.title("🛡️ QUANT OMNI SENTINEL V9.10 PRIME")
    
    if db:
        st.markdown(f'<div class="status-bar online">● SİSTEM ÇEVRİMİÇİ | 💠 APP_ID: {app_id}</div>', unsafe_allow_html=True)
        whale_engine()
        ant_engine()
        falcon_engine()
    else:
        st.markdown(f'<div class="status-bar offline">❌ SİSTEM ÇEVRİMDIŞI (Firebase Ayarları Bekleniyor)</div>', unsafe_allow_html=True)
        return

    tabs = st.tabs(["🐳 TREND (Whale)", "🐜 GRID (Ant)", "⚡ FLASH (Falcon)", "📊 ANALYTICS"])

    # --- 1. TREND TAB ---
    with tabs[0]:
        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('trend').get()
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("t_cfg"):
                m = st.number_input("İşlem Marjini ($)", 10, 5000, cfg.get('margin', 300))
                l = st.slider("Kaldıraç (x)", 1, 20, cfg.get('leverage', 3))
                coins = st.multiselect("Varlıklar", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], default=cfg.get('coins', ["BTCUSDT"]))
                
                tf_opts = ["15m", "1h", "4h", "1d"]
                tf_index = tf_opts.index(cfg.get('timeframe', '1h')) if cfg.get('timeframe', '1h') in tf_opts else 1
                tf = st.selectbox("Zaman Dilimi", tf_opts, index=tf_index)
                
                ema_f = st.slider("Hızlı EMA", 5, 50, cfg.get('ema_f', 9))
                ema_s = st.slider("Yavaş EMA", 10, 200, cfg.get('ema_s', 21))
                adx_t = st.slider("ADX Filtresi (Trend Gücü)", 0, 40, int(cfg.get('adx_t', 15)))
                tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, float(cfg.get('tp_atr', 3.5)))
                
                auto = st.checkbox("Otopilot (Otomatik İşlem)", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('trend').set({
                        **cfg, 'margin': m, 'leverage': l, 'coins': coins, 'timeframe': tf,
                        'ema_f': ema_f, 'ema_s': ema_s, 'adx_t': adx_t, 'tp_atr': tp_atr, 'autopilot': auto
                    })
                    st.rerun()
        with c2:
            st.markdown("#### `🟢 AKTİF TREND İŞLEMLERİ`")
            active_docs = get_data_ref('active_trades').get()
            
            # FİLTRE BURAYA EKLENDİ: Sadece 'trend_' ile başlayan dokümanları al
            trend_trades = [a.to_dict() for a in active_docs if a.id.startswith('trend_')]
            
            if trend_trades: 
                st.dataframe(pd.DataFrame(trend_trades), use_container_width=True)
            else: 
                st.info("Otopilot sinyal arıyor (ADX & RSI kontrol ediliyor)...")

    # --- 2. GRID TAB ---
    with tabs[1]:
        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('grid').get()
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("g_cfg"):
                current_coin = cfg.get('coin', 'BTCUSDT')
                coin = st.selectbox("Güvenli Liman", ["BTCUSDT", "ETHUSDT"], index=0 if current_coin=="BTCUSDT" else 1)
                space = st.number_input("Izgara Aralığı (%)", 0.1, 5.0, float(cfg.get('grid_spacing_pct', 0.5)))
                m_grid = st.number_input("Ağ Başına Bütçe ($)", 10, 500, int(cfg.get('margin_per_grid', 100)))
                max_g = st.number_input("Maksimum Ağ Sayısı", 10, 100, int(cfg.get('max_grids', 50)))
                
                auto_g = st.checkbox("Otopilot (Otomatik Al-Sat)", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('grid').set({
                        **cfg, 'coin': coin, 'grid_spacing_pct': space, 'margin_per_grid': m_grid, 
                        'max_grids': max_g, 'autopilot': auto_g
                    })
                    st.rerun()
        with c2:
            st.markdown("#### `🐜 ELDEKİ PARÇALAR`")
            state = get_data_ref('states').document('grid').get()
            if state.exists:
                gs = state.to_dict()
                st.metric("Kumbaradaki Net Kâr", f"${gs.get('total_profit', 0.0):.2f}")
                if gs.get('grids'): 
                    st.dataframe(pd.DataFrame(gs['grids']), use_container_width=True)
            else: 
                st.info("Grid motoru beklemede.")

    # --- 3. FLASH TAB ---
    with tabs[2]:
        st.markdown("<h2 style='color:#FFD700;'>⚡ Falcon: Hacim Avcısı</h2>", unsafe_allow_html=True)
        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('flash').get()
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("f_cfg"):
                m_flash = st.number_input("İşlem Marjini ($)", 10, 5000, int(cfg.get('margin', 200)))
                l_flash = st.slider("Kaldıraç (x)", 1, 20, int(cfg.get('leverage', 5)))
                tp_pct = st.number_input("Kâr Hedefi (%)", 1.0, 20.0, float(cfg.get('tp_pct', 5.0)))
                sl_pct = st.number_input("Stop Loss (%)", 1.0, 20.0, float(cfg.get('sl_pct', 3.0)))
                f_s = st.slider("Hacim Patlama Eşiği (%)", 2.0, 30.0, float(cfg.get('vol_spike', 10.0)))
                f_a = st.checkbox("Otopilot (Otomatik İşlem)", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('flash').set({
                        **cfg, 'margin': m_flash, 'leverage': l_flash, 'tp_pct': tp_pct, 'sl_pct': sl_pct,
                        'vol_spike': f_s, 'autopilot': f_a
                    })
                    st.rerun()
        with c2:
            st.markdown("#### `⚡ AKTİF FLASH İŞLEMİ`")
            active_f = get_data_ref('active_trades').document('flash_pos').get()
            if active_f.exists:
                st.dataframe(pd.DataFrame([active_f.to_dict()]), use_container_width=True)
            else:
                sig = get_data_ref('signals').document('flash').get()
                if sig.exists:
                    s = sig.to_dict()
                    st.warning(f"📡 SON RADAR TESPİTİ: {s.get('symbol')} | %{s.get('change')}")
                st.info("Otopilot hacim patlaması arıyor...")

    # --- 4. ANALYTICS TAB ---
    with tabs[3]:
        st.markdown("### 📊 Fon Performans Analitiği")
        hist = get_data_ref('history').get()
        if hist:
            df = pd.DataFrame([h.to_dict() for h in hist])
            if 'pnl_usd' not in df.columns: df['pnl_usd'] = 0.0
            if 'result' not in df.columns: df['result'] = 'UNKNOWN'
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Toplam Net Kâr", f"${df['pnl_usd'].sum():.2f}")
            m2.metric("Kapanan İşlem", len(df))
            wins = len(df[df['result'] == 'WIN'])
            m3.metric("Başarı Oranı", f"%{(wins/max(len(df), 1))*100:.1f}")
            
            if 'time' in df.columns:
                st.dataframe(df.sort_values('time', ascending=False), use_container_width=True)
            else:
                st.dataframe(df, use_container_width=True)
        else:
            st.info("İstatistik verisi bekleniyor. İlk işlem kapandığında burada görünecektir.")

    time.sleep(10)
    st.rerun()

if __name__ == "__main__":
    main()
