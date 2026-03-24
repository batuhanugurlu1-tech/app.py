import streamlit as st
import pandas as pd
import numpy as np
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
from datetime import datetime

# ==========================================
# ADIM 1: BULUT BAĞLANTISI (FIREBASE)
# ==========================================
# Bu kısım botun "hafızasını" Railway üzerinde canlı tutar.
if not firebase_admin._apps:
    try:
        # Railway'de "Variables" kısmına eklediğin config'i okur
        fb_config_str = os.environ.get('FIREBASE_CONFIG')
        if fb_config_str:
            fb_config = json.loads(fb_config_str)
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
        else:
            st.error("⚠️ FIREBASE_CONFIG bulunamadı! Railway panelinden Variables kısmına eklemelisin.")
    except Exception as e:
        st.error(f"Bağlantı Hatası: {e}")

# Veritabanı ve Uygulama Kimliği
db = firestore.client()
app_id = os.environ.get('APP_ID', 'quant-lab-v7')

# ==========================================
# ADIM 2: MATEMATİK MOTORU (TEKNİK ANALİZ)
# ==========================================
def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def get_binance_data(symbol, interval='15m', limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    res = requests.get(url).json()
    df = pd.DataFrame(res, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vol', 'CloseTime', 'QuoteVol', 'Trades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'])
    df['Close'] = df['Close'].astype(float)
    df['High'] = df['High'].astype(float)
    df['Low'] = df['Low'].astype(float)
    return df

# ==========================================
# ADIM 3: BULUT İŞLEMLERİ (KAYIT VE OKUMA)
# ==========================================
def get_active_pos():
    # Aktif işlemi buluttan oku
    doc = db.collection('artifacts').document(app_id).collection('public').document('active_trade').get()
    return doc.to_dict() if doc.exists else None

def save_active_pos(pos):
    # Yeni işlemi buluta yaz
    db.collection('artifacts').document(app_id).collection('public').document('active_trade').set(pos)

def close_active_pos(trade_record):
    # İşlemi kapat ve geçmişe kaydet
    db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add(trade_record)
    db.collection('artifacts').document(app_id).collection('public').document('active_trade').delete()

# ==========================================
# ADIM 4: ARAYÜZ (TASARIM)
# ==========================================
st.set_page_config(page_title="CLOUD SENTINEL V7", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 15px; padding: 20px; }
    h1 { color: #00FF41 !important; font-weight: 900 !important; }
    </style>
""", unsafe_allow_html=True)

def main():
    st.title("🛡️ CLOUD SENTINEL V7.0")
    
    with st.sidebar:
        st.header("⚙️ AYARLAR")
        target_coin = st.selectbox("Varlık", ["BTCUSDT", "SOLUSDT", "ETHUSDT"])
        f_ema_val = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema_val = st.slider("Yavaş EMA", 10, 100, 21)
        tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 4.5)
        
        if st.button("🔄 SİSTEMİ YENİLE"):
            st.rerun()

    # --- BOT MANTIĞI ÇALIŞIYOR ---
    try:
        df = get_binance_data(target_coin)
        price = df['Close'].iloc[-1]
        
        df['EMA_F'] = calculate_ema(df['Close'], f_ema_val)
        df['EMA_S'] = calculate_ema(df['Close'], s_ema_val)
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        active_pos = get_active_pos()
        
        # 1. POZİSYON KAPATMA KONTROLÜ
        if active_pos:
            st.warning(f"🔔 ŞU AN İŞLEMDE: {active_pos['type']} | Giriş: {active_pos['entry']}")
            
            exit_now = False
            res = ""
            if active_pos['type'] == 'LONG':
                if price <= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price >= active_pos['tp']: exit_now, res = True, "WIN"
            else:
                if price >= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price <= active_pos['tp']: exit_now, res = True, "WIN"
                
            if exit_now:
                pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                close_active_pos({
                    "symbol": target_coin, "type": active_pos['type'], "pnl": round(pnl, 2),
                    "result": res, "time": datetime.now().isoformat()
                })
                st.balloons() if res == "WIN" else st.snow()
                st.rerun()
        
        # 2. YENİ İŞLEM ARAMA
        else:
            last_f, last_s = df['EMA_F'].iloc[-1], df['EMA_S'].iloc[-1]
            prev_f, prev_s = df['EMA_F'].iloc[-2], df['EMA_S'].iloc[-2]
            atr = df['ATR'].iloc[-1]
            
            if prev_f <= prev_s and last_f > last_s: # LONG
                save_active_pos({
                    "type": "LONG", "entry": price, "sl": price - (atr * 2), "tp": price + (atr * tp_atr)
                })
                st.rerun()
            elif prev_f >= prev_s and last_f < last_s: # SHORT
                save_active_pos({
                    "type": "SHORT", "entry": price, "sl": price + (atr * 2), "tp": price - (atr * tp_atr)
                })
                st.rerun()

        # --- GÖSTERGE PANELİ ---
        st.subheader("📊 Canlı Takip")
        c1, c2, c3 = st.columns(3)
        c1.metric("Güncel Fiyat", f"{price} $")
        c2.metric("EMA Durumu", f"{f_ema_val}/{s_ema_val}", delta="Kesişim Bekleniyor" if not active_pos else "İşlem Aktif")
        
        # Geçmişi Buluttan Oku
        hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
        history = [d.to_dict() for d in hist_docs]
        if history:
            total_pnl = sum([h['pnl'] for h in history])
            c3.metric("Oturum PnL", f"%{total_pnl:.2f}")
            st.divider()
            st.table(pd.DataFrame(history).sort_values('time', ascending=False).head(5))
        else:
            st.info("Henüz geçmiş işlem kaydı yok. Bot pusuya yattı.")

    except Exception as e:
        st.error(f"Hata: {e}")

if __name__ == "__main__":
    main()
