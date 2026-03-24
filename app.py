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

# Streamlit config her zaman en üstte olmalıdır!
st.set_page_config(page_title="CLOUD SENTINEL V8", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .status-bar { padding: 15px; border-radius: 12px; margin-bottom: 25px; font-weight: bold; text-align: center; }
    .online { background-color: #00FF4122; color: #00FF41; border: 1px solid #00FF41; }
    .offline { background-color: #FF003C22; color: #FF003C; border: 1px solid #FF003C; }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 12px; padding: 20px; }
    .trade-card { background-color: #1A1D23; border-left: 5px solid #00FF41; padding: 15px; border-radius: 8px; margin-bottom: 10px; }
    .trade-card-short { border-left: 5px solid #FF003C; }
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

def get_binance_data(symbol, interval='15m', limit=100):
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
# 3. ÇOKLU BULUT İŞLEMLERİ (MULTI-ASSET)
# ==========================================
def get_active_pos(symbol):
    """Belirli bir coine ait aktif pozisyonu getirir."""
    if not db: return None
    doc = db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(symbol).get()
    return doc.to_dict() if doc.exists else None

def get_all_active_positions():
    """Tüm açık pozisyonları listeler."""
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
# 4. ANA PANEL
# ==========================================
def main():
    st.title("🛡️ CLOUD SENTINEL V8.0 // MULTI-CORE")
    
    if status_msg == "BAĞLI":
        st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg} | ÇOKLU TARAMA AKTİF</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True)
        return 

    with st.sidebar:
        st.header("⚙️ ÇOKLU VARLIK AYARLARI")
        
        # Popüler Coin Listesi
        coin_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT"]
        selected_coins = st.multiselect("İzlenecek Varlıklar (Çoklu Seçim)", coin_list, default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        
        f_ema_val = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema_val = st.slider("Yavaş EMA", 10, 100, 21)
        tp_atr = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 4.5)
        
        st.divider()
        auto_pilot = st.toggle("🔄 Otopilot Döngüsü (Açık Bırak)", value=False, help="Açıkken sayfa her 30 saniyede bir otomatik yenilenir ve piyasayı sürekli tarar.")
        if st.button("Hemen Tara"):
            st.rerun()

    if not selected_coins:
        st.warning("Lütfen yan menüden en az bir varlık seçin.")
        return

    # --- OTOPİLOT MOTORU (DÖNGÜ) ---
    live_market_data = []
    
    # Seçilen her bir coin için tarama yap
    for coin in selected_coins:
        df = get_binance_data(coin)
        if df is None:
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
        
        # 1. Pozisyondaysa Kapanış Kontrolü
        if active_pos:
            exit_now, res = False, ""
            if active_pos['type'] == 'LONG':
                if price <= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price >= active_pos['tp']: exit_now, res = True, "WIN"
            else:
                if price >= active_pos['sl']: exit_now, res = True, "LOSS"
                elif price <= active_pos['tp']: exit_now, res = True, "WIN"
                
            if exit_now:
                pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                close_active_pos(coin, {
                    "symbol": coin, "type": active_pos['type'], "pnl": round(pnl, 2),
                    "result": res, "time": datetime.now().isoformat()
                })
                st.toast(f"🔔 {coin} işlemi kapandı: {res} (%{pnl:.2f})")
                active_pos = None # Tablo için durumu güncelle
        
        # 2. Pozisyonda Değilse Giriş Kontrolü
        else:
            if prev_f <= prev_s and last_f > last_s:
                new_pos = {"symbol": coin, "type": "LONG", "entry": price, "sl": price - (atr * 2), "tp": price + (atr * tp_atr)}
                save_active_pos(coin, new_pos)
                active_pos = new_pos
                st.toast(f"🚀 {coin} LONG işleme girildi!")
            elif prev_f >= prev_s and last_f < last_s:
                new_pos = {"symbol": coin, "type": "SHORT", "entry": price, "sl": price + (atr * 2), "tp": price - (atr * tp_atr)}
                save_active_pos(coin, new_pos)
                active_pos = new_pos
                st.toast(f"🔻 {coin} SHORT işleme girildi!")

        # Tablo için canlı veriyi hazırla
        pnl_now = 0.0
        if active_pos:
            pnl_now = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100

        live_market_data.append({
            "Varlık": coin,
            "Fiyat ($)": round(price, 4),
            "Trend": trend,
            "Durum": f"İŞLEMDE: {active_pos['type']}" if active_pos else "Pusu Modu",
            "Anlık PnL (%)": round(pnl_now, 2) if active_pos else 0.0
        })

    # --- CANLI PANEL GÖSTERİMİ ---
    st.subheader("🌐 Canlı Piyasa Radarı")
    
    # Aktif İşlemleri Kart Olarak Göster
    all_active = get_all_active_positions()
    if all_active:
        st.markdown("#### `AÇIK POZİSYONLAR`")
        cols = st.columns(min(len(all_active), 4)) # Yan yana max 4 kart
        for idx, pos in enumerate(all_active):
            with cols[idx % 4]:
                card_class = "trade-card" if pos['type'] == 'LONG' else "trade-card trade-card-short"
                st.markdown(f"""
                <div class="{card_class}">
                    <h4 style="margin:0;">{pos['symbol']} <span style="font-size:0.6em; color:gray;">{pos['type']}</span></h4>
                    <p style="margin:0; font-size:14px;">Giriş: {pos['entry']}</p>
                    <p style="margin:0; font-size:14px; color:#00FF41;">Hedef: {pos['tp']:.4f}</p>
                    <p style="margin:0; font-size:14px; color:#FF003C;">Stop: {pos['sl']:.4f}</p>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("Şu an açık pozisyon bulunmuyor. Radar pusu modunda.")

    st.divider()
    
    # Tüm İzlenen Coinlerin Özet Tablosu
    st.markdown("#### `TARAMA ÖZETİ`")
    df_live = pd.DataFrame(live_market_data)
    
    def highlight_pnl(val):
        color = '#00FF41' if val > 0 else '#FF003C' if val < 0 else 'gray'
        return f'color: {color}; font-weight: bold;'
        
    st.dataframe(df_live.style.applymap(highlight_pnl, subset=['Anlık PnL (%)']), use_container_width=True)
    
    # Geçmişi Çek
    hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
    history = [d.to_dict() for d in hist_docs]
    
    if history:
        st.divider()
        total_trades = len(history)
        wins = len([h for h in history if h.get('result') == 'WIN'])
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
        total_pnl = sum([h.get('pnl', 0) for h in history])
        
        st.markdown("#### `[OTURUM İSTATİSTİKLERİ]`")
        c_hist1, c_hist2, c_hist3 = st.columns(3)
        c_hist1.metric("Toplam Kapanan İşlem", total_trades)
        c_hist2.metric("Win Rate (Kazanma Oranı)", f"%{win_rate:.1f}")
        c_hist3.metric("Net PnL (Kâr/Zarar)", f"%{total_pnl:.2f}")
        
        st.markdown("#### `[SON İŞLEMLER TABLOSU]`")
        st.table(pd.DataFrame(history).sort_values('time', ascending=False).head(10))

    # --- OTOPİLOT DÖNGÜSÜ ---
    if auto_pilot:
        time.sleep(30) # 30 saniye bekle
        st.rerun()     # Sayfayı yenile ve en baştan tara

if __name__ == "__main__":
    main()
