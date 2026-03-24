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
st.set_page_config(page_title="QUANT OMNI V9.11 TRANSPARENT", layout="wide")

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
# ☁️ FIREBASE BAĞLANTISI
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
app_id = os.environ.get('APP_ID', 'quant-lab-v9-transparent')

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
                    for symbol in coins:
                        raw_df = fetch_klines(symbol, timeframe, limit=150)
                        if raw_df.empty: continue
                        df = calculate_advanced_indicators(raw_df, configs.get('ema_f', 9), configs.get('ema_s', 21))
                        current, prev = df.iloc[-1], df.iloc[-2]
                        price = current['C']
                        
                        pos_ref = get_data_ref('active_trades').document(f"trend_{symbol}")
                        pos_doc = pos_ref.get()
                        
                        if pos_doc.exists:
                            p = pos_doc.to_dict()
                            res = ""
                            if p['type'] == 'LONG':
                                if price <= p['sl']: res = "LOSS"
                                elif price >= p['tp']: res = "WIN"
                            else:
                                if price >= p['sl']: res = "LOSS"
                                elif price <= p['tp']: res = "WIN"
                            if res:
                                pnl = ((price - p['entry'])/p['entry']*100) if p['type']=='LONG' else ((p['entry'] - price)/p['entry']*100)
                                get_data_ref('history').add({'bot': 'trend', 'symbol': symbol, 'pnl_usd': round((configs['margin']*pnl*configs['leverage'])/100, 2), 'result': res, 'time': datetime.now().isoformat()})
                                pos_ref.delete()
                        else:
                            if prev['EMA_F'] <= prev['EMA_S'] and current['EMA_F'] > current['EMA_S'] and current['RSI'] > 50 and current['ADX'] >= configs.get('adx_t', 15):
                                pos_ref.set({'type': 'LONG', 'entry': price, 'sl': price-(current['ATR']*2), 'tp': price+(current['ATR']*configs['tp_atr']), 'margin': configs['margin'], 'leverage': configs['leverage'], 'symbol': symbol, 'time': datetime.now().isoformat()})
                            elif prev['EMA_F'] >= prev['EMA_S'] and current['EMA_F'] < current['EMA_S'] and current['RSI'] < 50 and current['ADX'] >= configs.get('adx_t', 15):
                                pos_ref.set({'type': 'SHORT', 'entry': price, 'sl': price+(current['ATR']*2), 'tp': price-(current['ATR']*configs['tp_atr']), 'margin': configs['margin'], 'leverage': configs['leverage'], 'symbol': symbol, 'time': datetime.now().isoformat()})
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
                    spacing = configs.get('grid_spacing_pct', 0.5)
                    
                    res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
                    if res.status_code == 200:
                        price = float(res.json().get('price', 0))
                        state_ref = get_data_ref('states').document('grid')
                        state_doc = state_ref.get()
                        state = state_doc.to_dict() if state_doc.exists else {'grids': [], 'total_profit': 0.0}
                        
                        grids = state.get('grids', [])
                        new_grids = []
                        total_prof = state.get('total_profit', 0.0)
                        
                        for g in grids:
                            if price >= g['entry'] * (1 + spacing/100):
                                profit = (configs['margin_per_grid'] * spacing) / 100
                                get_data_ref('history').add({'bot': 'grid', 'symbol': symbol, 'pnl_usd': round(profit, 2), 'result': 'WIN', 'time': datetime.now().isoformat()})
                                total_prof += profit
                            else:
                                new_grids.append(g)
                        
                        if len(new_grids) < configs['max_grids']:
                            entries = [x['entry'] for x in new_grids]
                            last_buy = min(entries) if entries else price * 1.05
                            if price <= last_buy * (1 - spacing/100) or not new_grids:
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
                    pos_ref = get_data_ref('active_trades').document('flash_pos')
                    pos_doc = pos_ref.get()
                    if pos_doc.exists:
                        p = pos_doc.to_dict()
                        p_res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={p['symbol']}", timeout=10)
                        if p_res.status_code == 200:
                            price = float(p_res.json().get('price', 0))
                            res_str = ""
                            if price <= p.get('sl', 0): res_str = "LOSS"
                            elif price >= p.get('tp', 0): res_str = "WIN"
                            if res_str:
                                pnl = ((price - p['entry'])/p['entry']*100)
                                get_data_ref('history').add({'bot': 'flash', 'symbol': p['symbol'], 'pnl_usd': round((configs['margin'] * pnl * configs['leverage'])/100, 2), 'result': res_str, 'time': datetime.now().isoformat()})
                                pos_ref.delete()
                    else:
                        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
                        if res.status_code == 200:
                            try:
                                ticks = res.json()
                                spikes = [t for t in ticks if float(t.get('priceChangePercent', 0)) > configs.get('vol_spike', 5.0) and "USDT" in t.get('symbol', '')]
                                if spikes:
                                    top = sorted(spikes, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[0]
                                    price = float(top['lastPrice'])
                                    pos_ref.set({'type': 'LONG', 'entry': price, 'sl': price * (1 - configs.get('sl_pct',3.0)/100), 'tp': price * (1 + configs.get('tp_pct',5.0)/100), 'margin': configs.get('margin', 200), 'leverage': configs.get('leverage', 5), 'symbol': top['symbol'], 'time': datetime.now().isoformat()})
                                    get_data_ref('signals').document('flash').set({'symbol': top['symbol'], 'change': top['priceChangePercent'], 'vol': top['quoteVolume'], 'time': datetime.now().isoformat()})
                            except ValueError: pass
                time.sleep(30)
            except Exception as e:
                time.sleep(30)
    t = threading.Thread(target=task, daemon=True); t.start(); return t

# ==========================================
# 🖥️ MERKEZİ KONTROL ARAYÜZÜ (ŞEFFAF MOD)
# ==========================================
def calculate_bot_stats(history_data, bot_name):
    df = pd.DataFrame([h for h in history_data if h.get('bot') == bot_name])
    if df.empty: return 0, 0, 0.0
    wins = len(df[df['result'] == 'WIN'])
    total = len(df)
    win_rate = (wins / total) * 100 if total > 0 else 0
    total_pnl = df.get('pnl_usd', pd.Series([0])).sum()
    return total, win_rate, total_pnl

def main():
    st.title("🛡️ QUANT OMNI SENTINEL V9.11")
    
    if db:
        st.markdown(f'<div class="status-bar online">● SİSTEM ÇEVRİMİÇİ | 💠 KASA İZLENİYOR</div>', unsafe_allow_html=True)
        whale_engine(); ant_engine(); falcon_engine()
        
        # Tüm geçmişi bir kere çek (Performans Optimizasyonu)
        hist_docs = get_data_ref('history').get()
        all_history = [h.to_dict() for h in hist_docs] if hist_docs else []
    else:
        st.markdown(f'<div class="status-bar offline">❌ SİSTEM ÇEVRİMDIŞI (Firebase Config Hatası)</div>', unsafe_allow_html=True)
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
                m = st.number_input("İşlem Marjini ($)", 10, 5000, cfg.get('margin', 300))
                l = st.slider("Kaldıraç (x)", 1, 20, cfg.get('leverage', 3))
                coins = st.multiselect("Varlıklar", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], default=cfg.get('coins', ["BTCUSDT"]))
                tf = st.selectbox("Zaman Dilimi", ["15m", "1h", "4h", "1d"], index=["15m", "1h", "4h", "1d"].index(cfg.get('timeframe', '1h')))
                ema_f = st.slider("Hızlı EMA", 5, 50, cfg.get('ema_f', 9))
                ema_s = st.slider("Yavaş EMA", 10, 200, cfg.get('ema_s', 21))
                adx_t = st.slider("ADX Filtresi (Gürültü Önleyici)", 0, 40, int(cfg.get('adx_t', 15)))
                tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, float(cfg.get('tp_atr', 3.5)))
                auto = st.checkbox("Otopilot Aktif", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('trend').set({**cfg, 'margin': m, 'leverage': l, 'coins': coins, 'timeframe': tf, 'ema_f': ema_f, 'ema_s': ema_s, 'adx_t': adx_t, 'tp_atr': tp_atr, 'autopilot': auto})
                    st.rerun()
        with c2:
            st.markdown("#### `🟢 AKTİF İŞLEMLER VE BEKLENTİLER`")
            active_docs = get_data_ref('active_trades').get()
            trend_trades = [a.to_dict() for a in active_docs if a.id.startswith('trend_')]
            if trend_trades: 
                df_t = pd.DataFrame(trend_trades)
                df_t = df_t.rename(columns={'symbol':'Varlık', 'type':'Yön', 'entry':'Giriş Fiyatı', 'tp':'Kâr Al', 'sl':'Zarar Kes', 'margin':'Marjin($)'})
                st.dataframe(df_t[['Varlık', 'Yön', 'Giriş Fiyatı', 'Kâr Al', 'Zarar Kes', 'Marjin($)']], use_container_width=True)
                st.markdown("""<div class='action-box'><b>⚡ Ne Olacak?</b> Fiyat 'Kâr Al' seviyesine gelirse işlem kazançla kapatılacak. 'Zarar Kes' seviyesine düşerse zararla kapatılıp fon korunacak.</div>""", unsafe_allow_html=True)
            else: 
                st.info("Şu an açık Trend işlemi yok. Sistem yeni EMA kesişimi ve yeterli ADX gücü bekliyor.")

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
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("g_cfg"):
                coin = st.selectbox("Güvenli Liman", ["BTCUSDT", "ETHUSDT"], index=0 if cfg.get('coin', 'BTCUSDT')=="BTCUSDT" else 1)
                space = st.number_input("Ağ Aralığı (%)", 0.1, 5.0, float(cfg.get('grid_spacing_pct', 0.5)))
                m_grid = st.number_input("Parça Başı Bütçe ($)", 10, 500, int(cfg.get('margin_per_grid', 100)))
                max_g = st.number_input("Maksimum Ağ Sayısı", 10, 100, int(cfg.get('max_grids', 50)))
                auto_g = st.checkbox("Otopilot Aktif", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('grid').set({**cfg, 'coin': coin, 'grid_spacing_pct': space, 'margin_per_grid': m_grid, 'max_grids': max_g, 'autopilot': auto_g})
                    st.rerun()
        with c2:
            st.markdown("#### `🐜 ELDEKİ PARÇALAR (PUSUDAKİLER)`")
            state = get_data_ref('states').document('grid').get()
            if state.exists and state.to_dict().get('grids'):
                grids_list = state.to_dict()['grids']
                df_g = pd.DataFrame(grids_list)
                space_pct = float(cfg.get('grid_spacing_pct', 0.5))
                # Şeffaflık için hedefleri hesapla
                df_g['Hedef Fiyat ($)'] = df_g['entry'] * (1 + (space_pct/100))
                df_g['Zaman'] = df_g['time'].apply(lambda x: x.split('T')[1][:8] if 'T' in x else x)
                df_g = df_g.rename(columns={'entry': 'Alış Fiyatı ($)'})
                st.dataframe(df_g[['Zaman', 'Alış Fiyatı ($)', 'Hedef Fiyat ($)']].sort_values('Alış Fiyatı ($)'), use_container_width=True)
                
                lowest_buy = df_g['Alış Fiyatı ($)'].min()
                next_buy = lowest_buy * (1 - (space_pct/100))
                st.markdown(f"""<div class='action-box'><b>⚡ Sıradaki Hamleler:</b><br>
                1. Fiyat tablodaki <b>Hedef Fiyat</b>'lara ulaşırsa o parça satılıp kâr kumbaraya atılacak.<br>
                2. Fiyat düşmeye devam ederse, bir sonraki yeni parça alımı <b>~${next_buy:.2f}</b> seviyesinden yapılacak.</div>""", unsafe_allow_html=True)
            else: 
                st.info("Şu an elde parça yok. Otopilot açıkysa anlık fiyattan ilk alım yapılacak.")

    # --- 3. FLASH TAB ---
    with tabs[2]:
        st.markdown("""<div class='info-box'><b>ℹ️ Amaç:</b> Binance'deki tüm coinleri tarayarak 24 saat içinde aniden %X oranında fırlayan (hacim patlaması yaşayan) coinleri yakalamak. Radara düşen coini anında belirlediğin kâr/zarar oranlarıyla alıp hızlıca (scalp) kâr elde etmek.</div>""", unsafe_allow_html=True)
        
        f_tot, f_wr, f_pnl = calculate_bot_stats(all_history, 'flash')
        c_sf1, c_sf2, c_sf3 = st.columns(3)
        c_sf1.metric("Yakalanan Patlama", f_tot)
        c_sf2.metric("Hedefi Vurma Oranı", f"%{f_wr:.1f}")
        c_sf3.metric("Flash Net Kâr", f"${f_pnl:.2f}")
        st.divider()

        c1, c2 = st.columns([1, 2])
        with c1:
            doc = get_data_ref('configs').document('flash').get()
            cfg = doc.to_dict() if doc.exists else {}
            with st.form("f_cfg"):
                m_flash = st.number_input("İşlem Marjini ($)", 10, 5000, int(cfg.get('margin', 200)))
                l_flash = st.slider("Kaldıraç (x)", 1, 20, int(cfg.get('leverage', 5)))
                tp_pct = st.number_input("Kâr Hedefi (%)", 1.0, 20.0, float(cfg.get('tp_pct', 5.0)))
                sl_pct = st.number_input("Zarar Kes (%)", 1.0, 20.0, float(cfg.get('sl_pct', 3.0)))
                f_s = st.slider("Hacim Patlama Eşiği (%)", 2.0, 50.0, float(cfg.get('vol_spike', 10.0)))
                f_a = st.checkbox("Otopilot Aktif", value=cfg.get('autopilot', False))
                if st.form_submit_button("Ayarları Kaydet"):
                    get_data_ref('configs').document('flash').set({**cfg, 'margin': m_flash, 'leverage': l_flash, 'tp_pct': tp_pct, 'sl_pct': sl_pct, 'vol_spike': f_s, 'autopilot': f_a})
                    st.rerun()
        with c2:
            st.markdown("#### `⚡ AKTİF FLASH İŞLEMİ (AV)`")
            active_f = get_data_ref('active_trades').document('flash_pos').get()
            if active_f.exists:
                p = active_f.to_dict()
                df_flash = pd.DataFrame([p]).rename(columns={'symbol':'Varlık', 'entry':'Giriş Fiyatı', 'tp':'Kâr Hedefi', 'sl':'Stop Loss', 'margin':'Bütçe($)'})
                st.dataframe(df_flash[['Varlık', 'Giriş Fiyatı', 'Kâr Hedefi', 'Stop Loss', 'Bütçe($)']], use_container_width=True)
                st.markdown("""<div class='action-box'><b>⚡ Ne Olacak?</b> Vurgun yapıldı! Fiyat Kâr Hedefine ulaşırsa %5 kazançla çıkılacak. Tersine dönerse Stop Loss devrede.</div>""", unsafe_allow_html=True)
            else:
                sig = get_data_ref('signals').document('flash').get()
                if sig.exists:
                    st.warning(f"📡 SON RADAR TESPİTİ: {sig.to_dict().get('symbol')} | Patlama: %{sig.to_dict().get('change')}")
                st.info("Radarda hedef yok. Tüm piyasa taranıyor...")

    # --- 4. ANALYTICS TAB ---
    with tabs[3]:
        st.markdown("### 📊 Tüm Fonun Merkezi Analitiği")
        if all_history:
            df = pd.DataFrame(all_history)
            df['pnl_usd'] = df.get('pnl_usd', 0.0)
            df['result'] = df.get('result', 'UNKNOWN')
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Kümülatif Net Kâr", f"${df['pnl_usd'].sum():.2f}")
            m2.metric("Toplam Kapanan İşlem", len(df))
            wins = len(df[df['result'] == 'WIN'])
            m3.metric("Genel Başarı Oranı", f"%{(wins/max(len(df), 1))*100:.1f}")
            
            st.markdown("#### `📜 KASA DEFTERİ (Tüm İşlemler)`")
            if 'time' in df.columns:
                df['Zaman'] = df['time'].apply(lambda x: x.replace('T', ' ')[:16] if isinstance(x, str) else x)
                df = df.rename(columns={'bot':'Motor', 'symbol':'Varlık', 'pnl_usd':'Kâr/Zarar ($)', 'result':'Sonuç'})
                st.dataframe(df[['Zaman', 'Motor', 'Varlık', 'Kâr/Zarar ($)', 'Sonuç']].sort_values('Zaman', ascending=False), use_container_width=True)
        else:
            st.info("İstatistik verisi bekleniyor. İlk işlem kapandığında burada görünecektir.")

    time.sleep(10)
    st.rerun()

if __name__ == "__main__":
    main()
