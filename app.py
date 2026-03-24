import streamlit as st
import pandas as pd
import numpy as np
import requests
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
from datetime import datetime

# Streamlit config her zaman en üstte olmalıdır!
st.set_page_config(page_title="CLOUD SENTINEL V7.3", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. BULUT BAĞLANTISI (GÜVENLİ BAŞLATMA)
# ==========================================
def init_firebase():
    if not firebase_admin._apps:
        try:
            fb_config_str = os.environ.get('FIREBASE_CONFIG', '').strip()
            
            if not fb_config_str:
                return None, "⚠️ BAĞLANTI YOK: Railway panelinde 'FIREBASE_CONFIG' değişkeni bulunamadı."
            
            fb_config = json.loads(fb_config_str)
            
            # KULLANICI YANLIŞLIKLA WEB ANAHTARI GİRDİYSE UYAR
            if 'apiKey' in fb_config and 'private_key' not in fb_config:
                return None, "❌ YANLIŞ ANAHTAR: Web anahtarını (apiKey) girdiniz. Python için 'Service Account' (Hizmet Hesabı) JSON dosyası gereklidir."
                
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
            return firestore.client(), "BAĞLI"
            
        except json.JSONDecodeError:
            return None, "❌ HATA: Girdiğiniz FIREBASE_CONFIG geçerli bir JSON formatında değil."
        except Exception as e:
            return None, f"❌ SİSTEM HATASI: {str(e)}"
            
    return firestore.client(), "BAĞLI"

db, status_msg = init_firebase()
app_id = os.environ.get('APP_ID', 'quant-lab-v7')

# ==========================================
# 2. TEKNİK ANALİZ MOTORU
# ==========================================
def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def get_binance_data(symbol, interval='15m', limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        df = pd.DataFrame(res, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vol', 'CloseTime', 'QuoteVol', 'Trades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'])
        df['Close'] = df['Close'].astype(float)
        df['High'] = df['High'].astype(float)
        df['Low'] = df['Low'].astype(float)
        return df
    except Exception as e:
        return None

# ==========================================
# 3. BULUT İŞLEMLERİ
# ==========================================
def get_active_pos():
    if not db: return None
    doc = db.collection('artifacts').document(app_id).collection('public').document('active_trade').get()
    return doc.to_dict() if doc.exists else None

def save_active_pos(pos):
    if not db: return
    db.collection('artifacts').document(app_id).collection('public').document('active_trade').set(pos)

def close_active_pos(trade_record):
    if not db: return
    db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add(trade_record)
    db.collection('artifacts').document(app_id).collection('public').document('active_trade').delete()

# ==========================================
# 4. ANA PANEL
# ==========================================
def main():
    st.title("🛡️ CLOUD SENTINEL V7.3")
    
    # Durum Çubuğu
    if status_msg == "BAĞLI":
        st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True)
        st.warning("Lütfen aşağıdaki adımları takip ederek doğru Service Account anahtarını Railway'e ekleyin.")
        return # Bağlantı yoksa kodun geri kalanını çalıştırma (çökmeyi önler)

    with st.sidebar:
        st.header("⚙️ AYARLAR")
        target_coin = st.selectbox("Varlık Seçimi", ["BTCUSDT", "SOLUSDT", "ETHUSDT"])
        f_ema_val = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema_val = st.slider("Yavaş EMA", 10, 100, 21)
        tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 4.5)
        
        if st.button("🔄 VERİLERİ YENİLE"):
            st.rerun()

    # --- OTOPİLOT MANTIĞI ---
    df = get_binance_data(target_coin)
    if df is None:
        st.error("Binance verisi alınamadı. Lütfen sayfayı yenileyin.")
        return

    price = df['Close'].iloc[-1]
    df['EMA_F'] = calculate_ema(df['Close'], f_ema_val)
    df['EMA_S'] = calculate_ema(df['Close'], s_ema_val)
    df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
    
    active_pos = get_active_pos()
    
    if active_pos:
        st.warning(f"🔔 İŞLEMDE: {active_pos['type']} | Giriş: {active_pos['entry']}")
        
        exit_now, res = False, ""
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
            st.rerun()
    else:
        last_f, last_s = df['EMA_F'].iloc[-1], df['EMA_S'].iloc[-1]
        prev_f, prev_s = df['EMA_F'].iloc[-2], df['EMA_S'].iloc[-2]
        atr = df['ATR'].iloc[-1]
        
        if prev_f <= prev_s and last_f > last_s:
            save_active_pos({"type": "LONG", "entry": price, "sl": price - (atr * 2), "tp": price + (atr * tp_atr)})
            st.rerun()
        elif prev_f >= prev_s and last_f < last_s:
            save_active_pos({"type": "SHORT", "entry": price, "sl": price + (atr * 2), "tp": price - (atr * tp_atr)})
            st.rerun()

    # --- CANLI PANEL ---
    st.subheader("📊 Canlı Terminal")
    c1, c2, c3 = st.columns(3)
    c1.metric("Anlık Fiyat", f"{price:,.2f} $")
    c2.metric("Piyasa Yönü", "BOĞA (YUKARI)" if df['EMA_F'].iloc[-1] > df['EMA_S'].iloc[-1] else "AYI (AŞAĞI)")
    
    # Geçmişi Çek
    hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
    history = [d.to_dict() for d in hist_docs]
    
    if history:
        total_pnl = sum([h['pnl'] for h in history])
        c3.metric("Oturum Getirisi", f"%{total_pnl:.2f}")
        st.divider()
        st.markdown("#### `[SON İŞLEMLER]`")
        st.table(pd.DataFrame(history).sort_values('time', ascending=False).head(10))
    else:
        st.info("Pusu Modu: İlk EMA kesişimi bekleniyor...")

if __name__ == "__main__":
    main()
