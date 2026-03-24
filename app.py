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
# ADIM 1: GELİŞMİŞ BULUT BAĞLANTISI (FIREBASE)
# ==========================================
def init_firebase():
    """Firebase bağlantısını güvenli bir şekilde başlatır."""
    if not firebase_admin._apps:
        try:
            # Railway Variables (Ortam Değişkenleri) kısmından veriyi çek
            fb_config_str = os.environ.get('FIREBASE_CONFIG', '').strip()
            
            if not fb_config_str:
                return None, "⚠️ Railway panelinde 'FIREBASE_CONFIG' bulunamadı veya boş."
            
            # Eğer config içinde yanlışlıkla 'const config = {...}' gibi JS kalıntıları varsa temizle
            if 'firebaseConfig = {' in fb_config_str:
                fb_config_str = '{' + fb_config_str.split('{', 1)[1].rsplit('}', 1)[0] + '}'
            
            # JSON formatını doğrula ve bağlamayı dene
            fb_config = json.loads(fb_config_str)
            cred = credentials.Certificate(fb_config)
            firebase_admin.initialize_app(cred)
            return firestore.client(), "BAĞLI"
        except json.JSONDecodeError:
            return None, "❌ HATA: FIREBASE_CONFIG içeriği geçerli bir JSON formatında değil."
        except Exception as e:
            return None, f"❌ BAĞLANTI HATASI: {str(e)}"
    return firestore.client(), "BAĞLI"

# Bulut veritabanı bağlantısı
db, status_msg = init_firebase()
app_id = os.environ.get('APP_ID', 'quant-lab-v7')

# ==========================================
# ADIM 2: MATEMATİK MOTORU (TEKNİK ANALİZ)
# ==========================================
def calculate_ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def get_binance_data(symbol, interval='15m', limit=100):
    """Binance API üzerinden canlı mum verilerini çeker."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    res = requests.get(url).json()
    df = pd.DataFrame(res, columns=['Time', 'Open', 'High', 'Low', 'Close', 'Vol', 'CloseTime', 'QuoteVol', 'Trades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'])
    df['Close'] = df['Close'].astype(float)
    df['High'] = df['High'].astype(float)
    df['Low'] = df['Low'].astype(float)
    return df

# ==========================================
# ADIM 3: BULUT VERİ YÖNETİMİ
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
    # İşlemi geçmiş koleksiyonuna ekle
    db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add(trade_record)
    # Aktif pozisyonu sil
    db.collection('artifacts').document(app_id).collection('public').document('active_trade').delete()

# ==========================================
# ADIM 4: DASHBOARD ARAYÜZÜ (UI)
# ==========================================
st.set_page_config(page_title="CLOUD SENTINEL V7.1", layout="wide")

st.markdown(f"""
    <style>
    .stApp {{ background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }}
    .status-bar {{ padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; font-size: 1.1em; }}
    .online {{ background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }}
    .offline {{ background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }}
    .stMetric {{ background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }}
    h1 {{ color: #00FF41 !important; font-weight: 900 !important; }}
    </style>
""", unsafe_allow_html=True)

def main():
    st.title("🛡️ CLOUD SENTINEL V7.1")
    
    # Bulut Bağlantı Durumu Göstergesi
    if status_msg == "BAĞLI":
        st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg}</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True)
        st.info("💡 İpucu: Railway panelinden 'Variables' kısmına gidin ve FIREBASE_CONFIG değerini kontrol edin.")
        return

    with st.sidebar:
        st.header("⚙️ STRATEJİ AYARLARI")
        target_coin = st.selectbox("Varlık Seçimi", ["BTCUSDT", "SOLUSDT", "ETHUSDT"])
        f_ema_val = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema_val = st.slider("Yavaş EMA", 10, 100, 21)
        tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 4.5)
        
        st.divider()
        if st.button("🔄 VERİLERİ YENİLE"):
            st.rerun()

    # --- OTOPİLOT MOTORU ---
    try:
        df = get_binance_data(target_coin)
        price = df['Close'].iloc[-1]
        
        df['EMA_F'] = calculate_ema(df['Close'], f_ema_val)
        df['EMA_S'] = calculate_ema(df['Close'], s_ema_val)
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        active_pos = get_active_pos()
        
        # 1. Pozisyon Kapatma Kontrolü (Exit Logic)
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
        
        # 2. Yeni İşlem Arama (Entry Logic)
        else:
            last_f, last_s = df['EMA_F'].iloc[-1], df['EMA_S'].iloc[-1]
            prev_f, prev_s = df['EMA_F'].iloc[-2], df['EMA_S'].iloc[-2]
            atr = df['ATR'].iloc[-1]
            
            # EMA Kesişimi Kontrolü
            if prev_f <= prev_s and last_f > last_s: # LONG Kesişim
                save_active_pos({
                    "type": "LONG", "entry": price, 
                    "sl": price - (atr * 2), "tp": price + (atr * tp_atr)
                })
                st.rerun()
            elif prev_f >= prev_s and last_f < last_s: # SHORT Kesişim
                save_active_pos({
                    "type": "SHORT", "entry": price, 
                    "sl": price + (atr * 2), "tp": price - (atr * tp_atr)
                })
                st.rerun()

        # --- CANLI MONITOR ---
        st.subheader("📊 Canlı Terminal")
        c1, c2, c3 = st.columns(3)
        c1.metric("Anlık Fiyat", f"{price:,.2f} $")
        c2.metric("Piyasa Yönü", "YUKARI (BOĞA)" if df['EMA_F'].iloc[-1] > df['EMA_S'].iloc[-1] else "AŞAĞI (AYI)")
        
        # Bulut Geçmişini Getir
        hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
        history = [d.to_dict() for d in hist_docs]
        
        if history:
            total_pnl = sum([h['pnl'] for h in history])
            c3.metric("Oturum Getirisi", f"%{total_pnl:.2f}")
            st.divider()
            st.markdown("#### `[SON İŞLEMLER]`")
            st.table(pd.DataFrame(history).sort_values('time', ascending=False).head(10))
        else:
            st.info("Pusu Modu: Stratejiye uygun ilk kesişim bekleniyor.")

    except Exception as e:
        st.error(f"⚠️ Kritik Hata: {e}")

if __name__ == "__main__":
    main()
