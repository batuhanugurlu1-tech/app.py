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

# ==========================================
# SAYFA AYARLARI
# ==========================================
st.set_page_config(page_title="CLOUD SENTINEL V8.1", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    .trade-card { background-color: #1A1D23; border-left: 5px solid #00FF41; padding: 15px; border-radius: 8px; margin-bottom: 10px; }
    .trade-card-short { border-left: 5px solid #FF003C; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. BULUT BAĞLANTISI (FIREBASE)
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        try:
            fb_config_str = os.environ.get('FIREBASE_CONFIG', '').strip()
            if not fb_config_str:
                return None, "⚠️ BAĞLANTI YOK: Railway panelinde 'FIREBASE_CONFIG' bulunamadı."
            
            fb_config = json.loads(fb_config_str)
            if 'apiKey' in fb_config and 'private_key' not in fb_config:
                return None, "❌ YANLIŞ ANAHTAR: Python için 'Service Account' JSON dosyası gereklidir."
                
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
            return firestore.client(), "BAĞLI"
        except Exception as e:
            return None, f"❌ SİSTEM HATASI: {str(e)}"
    return firestore.client(), "BAĞLI"

db, status_msg = init_firebase()
app_id = os.environ.get('APP_ID', 'quant-lab-v8')

# ==========================================
# 2. TEKNİK ANALİZ MOTORU
# ==========================================
def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def get_binance_data(symbol, interval='1h', limit=150):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        df = pd.DataFrame(res, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vol', 'CloseTime', 'QuoteVol', 'Trades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'])
        df['Close'] = df['Close'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
        return df
    except Exception:
        return None

# ==========================================
# 3. BULUT İŞLEMLERİ (CRUD)
# ==========================================
def get_active_pos(symbol):
    if not db: return None
    doc = db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(symbol).get()
    return doc.to_dict() if doc.exists else None

def get_all_active_positions():
    if not db: return []
    docs = db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').get()
    return [doc.to_dict() for doc in docs]

def save_active_pos(symbol, pos):
    if not db: return
    db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(symbol).set(pos)

def close_active_pos(symbol, trade_record):
    if not db: return
    db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add(trade_record)
    db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(symbol).delete()

# ==========================================
# 4. ANA PANEL VE OTOPİLOT
# ==========================================
def main():
    st.title("🛡️ CLOUD SENTINEL V8.1 // WHALE MODE")
    st.caption("Muhafazakar Kasa Yönetimi & Saatlik Trend Taraması")
    
    if status_msg == "BAĞLI":
        st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg} | ÇOKLU TARAMA AKTİF</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True)
        return 

    with st.sidebar:
        st.header("💰 RİSK YÖNETİMİ")
        # Muhafazakar Ayarlar Varsayılan
        margin_per_trade = st.number_input("İşlem Başına Marjin ($)", min_value=10, max_value=100000, value=200, step=10, help="Kasanızın %2'sini geçmemelidir.")
        leverage = st.slider("Kaldıraç (x)", min_value=1, max_value=20, value=3, help="Düşük risk için 2x - 5x arası tavsiye edilir.")
        
        st.divider()
        st.header("⚙️ STRATEJİ AYARLARI")
        
        timeframe = st.selectbox("Mum Zaman Dilimi", ["15m", "1h", "4h", "1d"], index=1, help="1h ve 4h sahte sinyalleri filtreler.")
        
        coin_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"]
        selected_coins = st.multiselect("Demirbaş Varlıklar", coin_list, default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        
        # Trend Onayı için Yavaş EMA'lar
        f_ema_val = st.slider("Hızlı EMA", 5, 50, 21)
        s_ema_val = st.slider("Yavaş EMA", 10, 200, 55)
        tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 3.5)
        
        st.divider()
        auto_pilot = st.toggle("🔄 Otopilot Döngüsü (Açık Bırak)", value=False)
        if st.button("Şimdi Tara"):
            st.rerun()

    if not selected_coins:
        st.warning("Lütfen yan menüden en az bir varlık seçin.")
        return

    # --- OTOPİLOT DÖNGÜSÜ (PİYASA TARAMASI) ---
    live_market_data = []
    
    for coin in selected_coins:
        df = get_binance_data(coin, interval=timeframe)
        if df is None or df.empty:
            continue

        price = df['Close'].iloc[-1]
        df['EMA_F'] = calculate_ema(df['Close'], f_ema_val)
        df['EMA_S'] = calculate_ema(df['Close'], s_ema_val)
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        last_f, last_s = df['EMA_F'].iloc[-1], df['EMA_S'].iloc[-1]
        prev_f, prev_s = df['EMA_F'].iloc[-2], df['EMA_S'].iloc[-2]
        atr = df['ATR'].iloc[-1]
        trend = "YUKARI" if last_f > last_s else "AŞAĞI"

        active_pos = get_active_pos(coin)
        
        # 1. KAPANIS KONTROLU
        if active_pos:
            exit_now, res = False, ""
            if active_pos['type'] == 'LONG':
                if price <= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price >= active_pos['tp']: exit_now, res = True, "WIN"
            else:
                if price >= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price <= active_pos['tp']: exit_now, res = True, "WIN"
                
            if exit_now:
                raw_pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                pos_lev = active_pos.get('leverage', 1)
                pos_margin = active_pos.get('margin', 0)
                
                lev_pnl_pct = raw_pnl * pos_lev
                pnl_usd = (pos_margin * lev_pnl_pct) / 100
                
                close_active_pos(coin, {
                    "symbol": coin, "type": active_pos['type'], "pnl_pct": round(lev_pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
                    "margin": pos_margin, "leverage": pos_lev,
                    "result": res, "time": datetime.now().isoformat()
                })
                active_pos = None 
        
        # 2. GIRIS KONTROLU
        else:
            if prev_f <= prev_s and last_f > last_s:
                new_pos = {
                    "symbol": coin, "type": "LONG", "entry": price, 
                    "sl": price - (atr * 2), "tp": price + (atr * tp_atr),
                    "margin": margin_per_trade, "leverage": leverage, "timeframe": timeframe
                }
                save_active_pos(coin, new_pos)
                active_pos = new_pos
            elif prev_f >= prev_s and last_f < last_s:
                new_pos = {
                    "symbol": coin, "type": "SHORT", "entry": price, 
                    "sl": price + (atr * 2), "tp": price - (atr * tp_atr),
                    "margin": margin_per_trade, "leverage": leverage, "timeframe": timeframe
                }
                save_active_pos(coin, new_pos)
                active_pos = new_pos

        # Canlı Veri Hazırlığı
        pnl_now_pct, pnl_now_usd = 0.0, 0.0
        if active_pos:
            raw_pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
            pnl_now_pct = raw_pnl * active_pos.get('leverage', 1)
            pnl_now_usd = (active_pos.get('margin', 0) * pnl_now_pct) / 100

        live_market_data.append({
            "Varlık": coin,
            "Fiyat ($)": round(price, 4),
            "Trend": trend,
            "Durum": f"İŞLEMDE: {active_pos['type']} ({active_pos.get('leverage',1)}x)" if active_pos else "Pusu Modu",
            "Anlık PnL (%)": round(pnl_now_pct, 2) if active_pos else 0.0,
            "Kâr/Zarar ($)": round(pnl_now_usd, 2) if active_pos else 0.0
        })

    # --- EKRAN ÇIKTILARI ---
    all_active = get_all_active_positions()
    if all_active:
        st.markdown("#### `🟢 AKTİF POZİSYONLAR`")
        cols = st.columns(min(len(all_active), 4)) 
        for idx, pos in enumerate(all_active):
            with cols[idx % 4]:
                card_class = "trade-card" if pos['type'] == 'LONG' else "trade-card trade-card-short"
                st.markdown(f"""
                <div class="{card_class}">
                    <h4 style="margin:0;">{pos['symbol']} <span style="font-size:0.6em; color:gray;">{pos['type']}</span></h4>
                    <p style="margin:0; font-size:12px; color:gray;">Marjin: ${pos.get('margin',0)} | {pos.get('leverage',1)}x | TF: {pos.get('timeframe', '1h')}</p>
                    <p style="margin:0; font-size:14px; margin-top:5px;">Giriş: {pos['entry']}</p>
                    <p style="margin:0; font-size:14px; color:#00FF41;">Hedef (TP): {pos['tp']:.4f}</p>
                    <p style="margin:0; font-size:14px; color:#FF003C;">Stop (SL): {pos['sl']:.4f}</p>
                </div>
                """, unsafe_allow_html=True)
    
    st.divider()
    st.markdown("#### `📡 CANLI RADAR ÖZETİ`")
    df_live = pd.DataFrame(live_market_data)
    
    def highlight_pnl(val):
        color = '#00FF41' if val > 0 else '#FF003C' if val < 0 else 'gray'
        return f'color: {color}; font-weight: bold;'
    
    # Pandas Sürüm Uyumluluğu (Güvenli Boyama ve Streamlit deprecated parametre düzeltmesi)
    try:
        st.dataframe(df_live.style.map(highlight_pnl, subset=['Anlık PnL (%)', 'Kâr/Zarar ($)']), width='stretch')
    except Exception:
        try:
            st.dataframe(df_live.style.applymap(highlight_pnl, subset=['Anlık PnL (%)', 'Kâr/Zarar ($)']), width='stretch')
        except:
            st.dataframe(df_live, width='stretch')
    
    # Geçmiş Çekimi ve İstatistikler
    hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
    history = [d.to_dict() for d in hist_docs]
    
    if history:
        st.divider()
        total_trades = len(history)
        wins = len([h for h in history if h.get('result') == 'WIN'])
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
        total_pnl_usd = sum([h.get('pnl_usd', 0) for h in history])
        
        st.markdown("#### `🏆 OTURUM İSTATİSTİKLERİ`")
        c_hist1, c_hist2, c_hist3 = st.columns(3)
        c_hist1.metric("Kapanan İşlem", total_trades)
        c_hist2.metric("Kazanma Oranı", f"%{win_rate:.1f}")
        c_hist3.metric("Net Kâr/Zarar", f"${total_pnl_usd:.2f}")
        
        st.markdown("#### `[SON İŞLEMLER TABLOSU]`")
        df_hist = pd.DataFrame(history)
        if 'pnl_usd' in df_hist.columns:
            display_cols = ['time', 'symbol', 'type', 'margin', 'leverage', 'result', 'pnl_pct', 'pnl_usd']
            display_cols = [c for c in display_cols if c in df_hist.columns]
            st.table(df_hist[display_cols].sort_values('time', ascending=False).head(10))
        else:
            st.table(df_hist.sort_values('time', ascending=False).head(10))

    if auto_pilot:
        time.sleep(30)
        st.rerun()

if __name__ == "__main__":
    main()
