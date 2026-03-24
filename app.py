import streamlit as st
import pandas as pd
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import time
from datetime import datetime
import threading
import numpy as np

# ==========================================
# SAYFA AYARLARI
# ==========================================
st.set_page_config(page_title="CLOUD SENTINEL V8.7 FINAL", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; border: 1px solid #333; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    .trade-card { background-color: #1A1D23; border-left: 5px solid #00FF41; padding: 15px; border-radius: 8px; margin-bottom: 10px; }
    .trade-card-short { border-left: 5px solid #FF003C; }
    .grid-card { background-color: #1A1D23; border-left: 5px solid #00FFFF; padding: 15px; border-radius: 8px; margin-bottom: 10px; }
    h1, h2, h3 { color: #00FF41 !important; }
    .grid-title { color: #00FFFF !important; }
    .stat-box { background: #1A1D23; padding: 20px; border-radius: 15px; border: 1px solid #333; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. BULUT BAĞLANTISI
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        try:
            fb_config_str = os.environ.get('FIREBASE_CONFIG', '').strip()
            if not fb_config_str: return None, "⚠️ BAĞLANTI YOK"
            fb_config = json.loads(fb_config_str)
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
            return firestore.client(), "BAĞLI"
        except Exception as e:
            return None, f"❌ SİSTEM HATASI: {str(e)}"
    return firestore.client(), "BAĞLI"

db, status_msg = init_firebase()
app_id = os.environ.get('APP_ID', 'quant-lab-v8')

# ==========================================
# 2. BULUT KONFİGÜRASYONU YÖNETİMİ
# ==========================================
default_config = {
    'margin': 400, 'leverage': 3, 'timeframe': '1h',
    'coins': ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    'ema_f': 21, 'ema_s': 55, 'tp_atr': 3.5, 'autopilot': False
}

def get_cloud_config():
    if not db: return default_config
    doc = db.collection('artifacts').document(app_id).collection('public').document('config').get()
    if doc.exists: return doc.to_dict()
    db.collection('artifacts').document(app_id).collection('public').document('config').set(default_config)
    return default_config

default_grid_config = {
    'coin': "BTCUSDT", 'grid_spacing_pct': 0.5,
    'margin_per_grid': 120, 'max_grids': 50, 'autopilot': False
}

def get_grid_config():
    if not db: return default_grid_config
    doc = db.collection('artifacts').document(app_id).collection('public').document('config_grid').get()
    if doc.exists: return doc.to_dict()
    db.collection('artifacts').document(app_id).collection('public').document('config_grid').set(default_grid_config)
    return default_grid_config

# ==========================================
# 3. GERÇEK 7/24 ARKA PLAN MOTORLARI (DAEMON)
# ==========================================
@st.cache_resource
def start_background_daemon():
    def run_bot():
        time.sleep(5)
        try: db_t = firestore.client()
        except: return
        while True:
            try:
                cfg_doc = db_t.collection('artifacts').document(app_id).collection('public').document('config').get()
                if not cfg_doc.exists: time.sleep(30); continue
                cfg = cfg_doc.to_dict()
                coins, timeframe, f_ema, s_ema, tp_atr, margin, leverage, autopilot = cfg.get('coins', []), cfg.get('timeframe', '1h'), cfg.get('ema_f', 21), cfg.get('ema_s', 55), cfg.get('tp_atr', 3.5), cfg.get('margin', 400), cfg.get('leverage', 3), cfg.get('autopilot', False)
                live_data = []
                for coin in coins:
                    try:
                        url = f"https://api.binance.com/api/v3/klines?symbol={coin}&interval={timeframe}&limit=100"
                        res = requests.get(url, timeout=10).json()
                        if not isinstance(res, list) or len(res) < 50: continue
                        closes, highs, lows = pd.Series([float(k[4]) for k in res]), pd.Series([float(k[2]) for k in res]), pd.Series([float(k[3]) for k in res])
                        price = closes.iloc[-1]
                        ema_f_s, ema_s_s = closes.ewm(span=f_ema, adjust=False).mean(), closes.ewm(span=s_ema, adjust=False).mean()
                        last_f, last_s, prev_f, prev_s = ema_f_s.iloc[-1], ema_s_s.iloc[-1], ema_f_s.iloc[-2], ema_s_s.iloc[-2]
                        tr = pd.concat([highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()], axis=1).max(axis=1)
                        atr, trend = tr.rolling(14).mean().iloc[-1], "YUKARI" if last_f > last_s else "AŞAĞI"
                        pos_ref = db_t.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(coin)
                        pos_doc = pos_ref.get()
                        active_pos = pos_doc.to_dict() if pos_doc.exists else None
                        if active_pos:
                            exit_now, result = False, ""
                            if active_pos['type'] == 'LONG':
                                if price <= active_pos['sl']: exit_now, result = True, "LOSS"
                                elif price >= active_pos['tp']: exit_now, result = True, "WIN"
                            else:
                                if price >= active_pos['sl']: exit_now, result = True, "LOSS"
                                elif price <= active_pos['tp']: exit_now, result = True, "WIN"
                            if exit_now and autopilot:
                                raw_pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                                lev, mar = active_pos.get('leverage', 1), active_pos.get('margin', 0)
                                lev_pnl, usd_pnl = raw_pnl * lev, (mar * raw_pnl * lev) / 100
                                db_t.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add({"symbol": coin, "type": active_pos['type'], "pnl_pct": round(lev_pnl, 2), "pnl_usd": round(usd_pnl, 2), "margin": mar, "leverage": lev, "result": result, "time": datetime.now().isoformat()})
                                pos_ref.delete()
                                active_pos = None
                        elif autopilot:
                            if prev_f <= prev_s and last_f > last_s:
                                active_pos = {"symbol": coin, "type": "LONG", "entry": price, "sl": price - (atr * 2), "tp": price + (atr * tp_atr), "margin": margin, "leverage": leverage, "timeframe": timeframe}
                                pos_ref.set(active_pos)
                            elif prev_f >= prev_s and last_f < last_s:
                                active_pos = {"symbol": coin, "type": "SHORT", "entry": price, "sl": price + (atr * 2), "tp": price - (atr * tp_atr), "margin": margin, "leverage": leverage, "timeframe": timeframe}
                                pos_ref.set(active_pos)
                        p_pct, p_usd = 0.0, 0.0
                        if active_pos:
                            raw_pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                            p_pct, p_usd = raw_pnl * active_pos.get('leverage', 1), (active_pos.get('margin', 0) * raw_pnl * active_pos.get('leverage', 1)) / 100
                        live_data.append({"Varlık": coin, "Fiyat ($)": round(price, 4), "Trend": trend, "Durum": f"İŞLEMDE: {active_pos['type']} ({active_pos.get('leverage',1)}x)" if active_pos else "Pusu Modu", "Anlık PnL (%)": round(p_pct, 2) if active_pos else 0.0, "Kâr/Zarar ($)": round(p_usd, 2) if active_pos else 0.0})
                    except: pass
                db_t.collection('artifacts').document(app_id).collection('public').document('live_market').set({'data': live_data, 'updated_at': datetime.now().strftime("%H:%M:%S")})
            except: pass
            time.sleep(30)
    t = threading.Thread(target=run_bot, daemon=True); t.start(); return t

@st.cache_resource
def start_grid_daemon():
    def run_grid():
        time.sleep(15)
        try: db_g = firestore.client()
        except: return
        while True:
            try:
                cfg_doc = db_g.collection('artifacts').document(app_id).collection('public').document('config_grid').get()
                if not cfg_doc.exists: time.sleep(30); continue
                cfg = cfg_doc.to_dict()
                if not cfg.get('autopilot', False): time.sleep(30); continue
                coin, spacing_pct, margin, max_grids = cfg.get('coin', 'BTCUSDT'), cfg.get('grid_spacing_pct', 0.5), cfg.get('margin_per_grid', 120), cfg.get('max_grids', 50)
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={coin}"
                res = requests.get(url, timeout=10).json()
                price = float(res['price'])
                state_ref = db_g.collection('artifacts').document(app_id).collection('public').document('grid_state')
                state_doc = state_ref.get()
                state = state_doc.to_dict() if state_doc.exists else {'active_grids': [], 'total_profit': 0.0}
                active_grids, grids_changed, surviving_grids = state.get('active_grids', []), False, []
                for grid in active_grids:
                    entry = grid['entry']
                    profit_target = entry * (1 + (spacing_pct / 100))
                    if price >= profit_target:
                        profit_usd = margin * (spacing_pct / 100)
                        state['total_profit'] = state.get('total_profit', 0.0) + profit_usd
                        db_g.collection('artifacts').document(app_id).collection('public').document('data').collection('grid_history').add({"symbol": coin, "entry": entry, "exit": price, "profit_usd": round(profit_usd, 2), "time": datetime.now().isoformat()})
                        grids_changed = True
                    else: surviving_grids.append(grid)
                active_grids = surviving_grids
                if len(active_grids) < max_grids:
                    if not active_grids: active_grids.append({'entry': price, 'time': datetime.now().isoformat()}); grids_changed = True
                    else:
                        lowest_entry = min([g['entry'] for g in active_grids])
                        buy_target = lowest_entry * (1 - (spacing_pct / 100))
                        if price <= buy_target: active_grids.append({'entry': price, 'time': datetime.now().isoformat()}); grids_changed = True
                if grids_changed or abs(state.get('last_price', 0) - price) > (price * 0.001):
                    state['active_grids'], state['last_price'], state['updated_at'] = active_grids, price, datetime.now().strftime("%H:%M:%S")
                    state_ref.set(state)
            except: pass
            time.sleep(10)
    t_grid = threading.Thread(target=run_grid, daemon=True); t_grid.start(); return t_grid

if db: start_background_daemon(); start_grid_daemon()

# ==========================================
# 4. ARAYÜZ (FRONTEND DASHBOARD)
# ==========================================
def main():
    st.title("🛡️ CLOUD SENTINEL V8.7 // QUANT ANALYTICS")
    cfg, grid_cfg = get_cloud_config(), get_grid_config()
    is_autopilot_on, is_grid_on = cfg.get('autopilot', False), grid_cfg.get('autopilot', False)
    
    if status_msg == "BAĞLI":
        motor_status = []
        if is_autopilot_on: motor_status.append("🐳 TREND")
        if is_grid_on: motor_status.append("🐜 GRID")
        st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg} | 🚀 AKTİF SİSTEMLER: {" | ".join(motor_status) if motor_status else "İZLEMEDE"}</div>', unsafe_allow_html=True)
    else: st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True); return 

    with st.sidebar:
        st.markdown("### ⚙️ EKRAN YÖNETİMİ")
        ui_refresh = st.toggle("👁️ Canlı Ekranı Yenile (10sn)", value=False)
        st.divider()
        st.markdown("### 💼 KASA DAĞILIMI")
        st.progress(0.40, text="🐳 Trend: 4.000$")
        st.progress(0.60, text="🐜 Grid: 6.000$")

    tab_trend, tab_grid, tab_analytics = st.tabs(["🐳 TREND LABORATUVARI", "🐜 GRID FABRİKASI", "📊 SİSTEM ANALİTİĞİ"])

    # SEKME 1: TREND BOTU
    with tab_trend:
        col_t_form, col_t_dash = st.columns([1, 3])
        with col_t_form:
            with st.form("config_form"):
                st.header("☁️ Trend Ayarları")
                new_margin, new_leverage = st.number_input("İşlem Marjini ($)", 10, 10000, int(cfg.get('margin', 400))), st.slider("Kaldıraç (x)", 1, 20, int(cfg.get('leverage', 3)))
                curr_tf = cfg.get('timeframe', '1h')
                new_tf = st.selectbox("Zaman Dilimi", ["15m", "1h", "4h", "1d"], index=["15m", "1h", "4h", "1d"].index(curr_tf))
                new_coins = st.multiselect("Varlıklar", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"], default=cfg.get('coins', ["BTCUSDT", "ETHUSDT", "SOLUSDT"]))
                new_f_ema, new_s_ema, new_tp = st.slider("Hızlı EMA", 5, 50, int(cfg.get('ema_f', 21))), st.slider("Yavaş EMA", 10, 200, int(cfg.get('ema_s', 55))), st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, float(cfg.get('tp_atr', 3.5)))
                new_auto_str = st.radio("Trend Motoru:", ["AÇIK", "KAPALI"], index=0 if is_autopilot_on else 1, horizontal=True)
                if st.form_submit_button("☁️ Trendi Kaydet"):
                    db.collection('artifacts').document(app_id).collection('public').document('config').set({'margin': new_margin, 'leverage': new_leverage, 'timeframe': new_tf, 'coins': new_coins, 'ema_f': new_f_ema, 'ema_s': new_s_ema, 'tp_atr': new_tp, 'autopilot': (new_auto_str == "AÇIK")})
                    st.rerun()
        with col_t_dash:
            active_docs = db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').get()
            all_active = [doc.to_dict() for doc in active_docs]
            if all_active:
                st.markdown("#### `🟢 AKTİF POZİSYONLAR`")
                cols = st.columns(min(len(all_active), 4)) 
                for idx, pos in enumerate(all_active):
                    with cols[idx % 4]:
                        card_class = "trade-card" if pos['type'] == 'LONG' else "trade-card trade-card-short"
                        st.markdown(f'<div class="{card_class}"><h4 style="margin:0;">{pos["symbol"]} <span style="font-size:0.6em; color:gray;">{pos["type"]}</span></h4><p style="margin:0; font-size:12px; color:gray;">Marjin: ${pos.get("margin",0)} | {pos.get("leverage",1)}x</p><p style="margin:0; font-size:14px; margin-top:5px;">Giriş: {pos["entry"]}</p><p style="margin:0; font-size:14px; color:#00FF41;">Hedef: {pos["tp"]:.4f}</p></div>', unsafe_allow_html=True)
            st.divider()
            live_doc = db.collection('artifacts').document(app_id).collection('public').document('live_market').get()
            if live_doc.exists:
                live_data = live_doc.to_dict()
                st.markdown(f"#### `📡 CANLI RADAR ÖZETİ` <span style='font-size:12px; color:gray;'>Güncelleme: {live_data.get('updated_at', '...')}</span>", unsafe_allow_html=True)
                df_live = pd.DataFrame(live_data.get('data', []))
                if not df_live.empty:
                    def highlight_pnl(val): return 'color: #00FF41; font-weight: bold;' if val > 0 else '#FF003C; font-weight: bold;' if val < 0 else 'gray'
                    st.dataframe(df_live.style.applymap(highlight_pnl, subset=['Anlık PnL (%)', 'Kâr/Zarar ($)']), width='stretch')

    # SEKME 2: GRID BOTU
    with tab_grid:
        col_g_form, col_g_dash = st.columns([1, 3])
        with col_g_form:
            with st.form("grid_config_form"):
                st.markdown("<h2 class='grid-title'>🐜 Karınca Ayarları</h2>", unsafe_allow_html=True)
                new_g_coin = st.selectbox("Varlık", ["BTCUSDT", "ETHUSDT"], index=0 if grid_cfg.get('coin') == 'BTCUSDT' else 1)
                new_g_spacing, new_g_margin, new_g_max = st.number_input("Aralık (%)", 0.1, 5.0, float(grid_cfg.get('grid_spacing_pct', 0.5)), 0.1), st.number_input("Bütçe ($)", 10, 1000, int(grid_cfg.get('margin_per_grid', 120))), st.number_input("Maks Ağ", 1, 100, int(grid_cfg.get('max_grids', 50)))
                new_g_auto_str = st.radio("Grid Motoru:", ["AÇIK", "KAPALI"], index=0 if is_grid_on else 1, horizontal=True)
                if st.form_submit_button("☁️ Grid'i Kaydet"):
                    db.collection('artifacts').document(app_id).collection('public').document('config_grid').set({'coin': new_g_coin, 'grid_spacing_pct': new_g_spacing, 'margin_per_grid': new_g_margin, 'max_grids': new_g_max, 'autopilot': (new_g_auto_str == "AÇIK")})
                    st.rerun()
        with col_g_dash:
            grid_state_doc = db.collection('artifacts').document(app_id).collection('public').document('grid_state').get()
            grid_state = grid_state_doc.to_dict() if grid_state_doc.exists else {}
            active_grids, total_grid_profit = grid_state.get('active_grids', []), grid_state.get('total_profit', 0.0)
            c_g1, c_g2, c_g3 = st.columns(3)
            c_g1.metric("Net Kâr", f"${total_grid_profit:.2f}"); c_g2.metric("Açık Ağ", f"{len(active_grids)} / {grid_cfg.get('max_grids', 50)}"); c_g3.metric("Fiyat", f"${grid_state.get('last_price', 0):.2f}")
            st.divider()
            if active_grids:
                cols = st.columns(min(len(active_grids), 4))
                for idx, grid in enumerate(sorted(active_grids, key=lambda x: x['entry'])):
                    with cols[idx % 4]: st.markdown(f'<div class="grid-card"><h5 style="margin:0; color:gray;">Alış: ${grid["entry"]:.2f}</h5><p style="margin:0; font-size:12px; margin-top:5px; color:#00FFFF;">Hedef: ${grid["entry"] * (1 + (grid_cfg.get("grid_spacing_pct", 0.5) / 100)):.2f}</p></div>', unsafe_allow_html=True)
            else: st.info("Grid şu an pasif.")

    # ==========================================
    # SEKME 3: SİSTEM ANALİTİĞİ (BİLİMSEL TEST)
    # ==========================================
    with tab_analytics:
        st.markdown("### 📊 Bilimsel Sistem Doğrulama (Alpha Test)")
        
        # Geçmiş Verileri Topla
        hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
        grid_hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('grid_history').get()
        
        trend_hist = [d.to_dict() for d in hist_docs]
        grid_hist = [d.to_dict() for d in grid_hist_docs]
        all_trades = trend_hist + grid_hist
        
        if not all_trades:
            st.warning("Henüz yeterli işlem verisi yok. Analiz için botun birkaç işlem kapatmasını bekleyin.")
        else:
            # METRİKLERİ HESAPLA
            total_n = len(all_trades)
            trend_n = len(trend_hist)
            grid_n = len(grid_hist)
            
            # Trend Profit Factor
            trend_wins = [h.get('pnl_usd', 0) for h in trend_hist if h.get('pnl_usd', 0) > 0]
            trend_losses = [abs(h.get('pnl_usd', 0)) for h in trend_hist if h.get('pnl_usd', 0) <= 0]
            pf_trend = sum(trend_wins) / sum(trend_losses) if sum(trend_losses) > 0 else (99.0 if sum(trend_wins) > 0 else 0.0)
            
            # Toplam Kar
            total_profit = sum([h.get('pnl_usd', 0) for h in trend_hist]) + sum([h.get('profit_usd', 0) for h in grid_hist])
            
            # GÜVEN SKORU ALGORİTMASI
            confidence_score = 0
            if total_n >= 30: confidence_score += 40
            elif total_n >= 10: confidence_score += 15
            
            if pf_trend >= 1.4: confidence_score += 30
            elif pf_trend >= 1.1: confidence_score += 10
            
            if total_profit > 0: confidence_score += 30
            
            # ARAYÜZ
            c_an1, c_an2, c_an3 = st.columns(3)
            with c_an1:
                st.markdown(f'<div class="stat-box"><h5 style="color:gray;">İstatistiksel Anlamlılık</h5><h2 style="color:{"#00FF41" if total_n >= 30 else "#FF003C"};">{total_n} / 30</h2><p style="font-size:12px;">Minimum 30 işlem önerilir.</p></div>', unsafe_allow_html=True)
            with c_an2:
                st.markdown(f'<div class="stat-box"><h5 style="color:gray;">Profit Factor (Trend)</h5><h2 style="color:{"#00FF41" if pf_trend >= 1.25 else "#FF003C"};">{pf_trend:.2f}</h2><p style="font-size:12px;">Hedef: > 1.25</p></div>', unsafe_allow_html=True)
            with c_an3:
                st.markdown(f'<div class="stat-box"><h5 style="color:gray;">Sistem Güven Skoru</h5><h2 style="color:{"#00FF41" if confidence_score >= 70 else "#FFA500"};">%{confidence_score}</h2><p style="font-size:12px;">%70 altı riskli kabul edilir.</p></div>', unsafe_allow_html=True)
            
            st.divider()
            
            # BİLİMSEL TEŞHİS
            st.markdown("#### 🩺 Stratejist Teşhisi")
            if confidence_score >= 80:
                st.success("🚀 SİSTEM HAZIR: Veriler tutarlı, kâr faktörü yüksek ve örneklem yeterli. Gerçek piyasada ölçek artırılabilir.")
            elif confidence_score >= 50:
                st.warning("⚖️ TEMKİNLİ OL: Sistem kârda ancak işlem sayısı az veya kâr/zarar dengesi sınırda. Bir süre daha izle.")
            else:
                st.error("🛑 TESTE DEVAM: Veriler henüz istatistiksel bir başarıyı doğrulamıyor. Gerçek parayı riske atmak için erken.")

            # Geçmiş Tablosu
            st.markdown("#### `📜 TÜM İŞLEM GEÇMİŞİ`")
            df_all = pd.DataFrame(all_trades).sort_values('time', ascending=False)
            st.dataframe(df_all, width='stretch')

    if ui_refresh: time.sleep(10); st.rerun()

if __name__ == "__main__": main()
