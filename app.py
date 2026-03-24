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

# ==========================================
# SAYFA AYARLARI
# ==========================================
st.set_page_config(page_title="CLOUD SENTINEL V8.2", layout="wide")

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
    'margin': 200, 'leverage': 3, 'timeframe': '1h',
    'coins': ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    'ema_f': 21, 'ema_s': 55, 'tp_atr': 3.5, 'autopilot': False
}

def get_cloud_config():
    if not db: return default_config
    doc = db.collection('artifacts').document(app_id).collection('public').document('config').get()
    if doc.exists:
        return doc.to_dict()
    else:
        db.collection('artifacts').document(app_id).collection('public').document('config').set(default_config)
        return default_config

# ==========================================
# 3. GERÇEK 7/24 ARKA PLAN MOTORU (DAEMON)
# ==========================================
# Sayfa kapansa bile Railway içinde ömür boyu dönen döngü.
@st.cache_resource
def start_background_daemon():
    def run_bot():
        time.sleep(5) # Veritabanının oturmasını bekle
        try:
            db_t = firestore.client()
        except:
            return

        while True:
            try:
                cfg_doc = db_t.collection('artifacts').document(app_id).collection('public').document('config').get()
                if not cfg_doc.exists:
                    time.sleep(30)
                    continue

                cfg = cfg_doc.to_dict()
                coins = cfg.get('coins', [])
                timeframe = cfg.get('timeframe', '1h')
                f_ema = cfg.get('ema_f', 21)
                s_ema = cfg.get('ema_s', 55)
                tp_atr = cfg.get('tp_atr', 3.5)
                margin = cfg.get('margin', 200)
                leverage = cfg.get('leverage', 3)
                autopilot = cfg.get('autopilot', False)

                live_data = []

                for coin in coins:
                    try:
                        url = f"https://api.binance.com/api/v3/klines?symbol={coin}&interval={timeframe}&limit=100"
                        res = requests.get(url, timeout=10).json()
                        if not isinstance(res, list) or len(res) < 50:
                            continue

                        closes = pd.Series([float(k[4]) for k in res])
                        highs = pd.Series([float(k[2]) for k in res])
                        lows = pd.Series([float(k[3]) for k in res])

                        price = closes.iloc[-1]
                        ema_f_s = closes.ewm(span=f_ema, adjust=False).mean()
                        ema_s_s = closes.ewm(span=s_ema, adjust=False).mean()

                        last_f, last_s = ema_f_s.iloc[-1], ema_s_s.iloc[-1]
                        prev_f, prev_s = ema_f_s.iloc[-2], ema_s_s.iloc[-2]

                        tr = pd.concat([highs - lows, (highs - closes.shift()).abs(), (lows - closes.shift()).abs()], axis=1).max(axis=1)
                        atr = tr.rolling(14).mean().iloc[-1]
                        trend = "YUKARI" if last_f > last_s else "AŞAĞI"

                        # Aktif işlemi buluttan kontrol et
                        pos_ref = db_t.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').document(coin)
                        pos_doc = pos_ref.get()
                        active_pos = pos_doc.to_dict() if pos_doc.exists else None

                        # --- KAPANIS KONTROLU ---
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
                                lev = active_pos.get('leverage', 1)
                                mar = active_pos.get('margin', 0)
                                lev_pnl = raw_pnl * lev
                                usd_pnl = (mar * lev_pnl) / 100

                                db_t.collection('artifacts').document(app_id).collection('public').document('data').collection('history').add({
                                    "symbol": coin, "type": active_pos['type'], "pnl_pct": round(lev_pnl, 2), "pnl_usd": round(usd_pnl, 2),
                                    "margin": mar, "leverage": lev, "result": result, "time": datetime.now().isoformat()
                                })
                                pos_ref.delete()
                                active_pos = None

                        # --- GIRIS KONTROLU ---
                        elif autopilot:
                            if prev_f <= prev_s and last_f > last_s:
                                active_pos = {
                                    "symbol": coin, "type": "LONG", "entry": price,
                                    "sl": price - (atr * 2), "tp": price + (atr * tp_atr),
                                    "margin": margin, "leverage": leverage, "timeframe": timeframe
                                }
                                pos_ref.set(active_pos)
                            elif prev_f >= prev_s and last_f < last_s:
                                active_pos = {
                                    "symbol": coin, "type": "SHORT", "entry": price,
                                    "sl": price + (atr * 2), "tp": price - (atr * tp_atr),
                                    "margin": margin, "leverage": leverage, "timeframe": timeframe
                                }
                                pos_ref.set(active_pos)

                        # Canlı PnL Hesapla (Sadece Radar İçin)
                        p_pct, p_usd = 0.0, 0.0
                        if active_pos:
                            raw_pnl = ((price - active_pos['entry'])/active_pos['entry'])*100 if active_pos['type'] == 'LONG' else ((active_pos['entry'] - price)/active_pos['entry'])*100
                            p_pct = raw_pnl * active_pos.get('leverage', 1)
                            p_usd = (active_pos.get('margin', 0) * p_pct) / 100

                        live_data.append({
                            "Varlık": coin, "Fiyat ($)": round(price, 4), "Trend": trend,
                            "Durum": f"İŞLEMDE: {active_pos['type']} ({active_pos.get('leverage',1)}x)" if active_pos else "Pusu Modu",
                            "Anlık PnL (%)": round(p_pct, 2) if active_pos else 0.0,
                            "Kâr/Zarar ($)": round(p_usd, 2) if active_pos else 0.0
                        })
                    except Exception as e:
                        print(f"Error scanning {coin}: {e}")

                # Buluttaki "Canlı Radar" belgesini güncelle
                db_t.collection('artifacts').document(app_id).collection('public').document('live_market').set({
                    'data': live_data, 'updated_at': datetime.now().strftime("%H:%M:%S")
                })

            except Exception as e:
                print(f"Daemon Main Error: {e}")

            time.sleep(30) # Motor her 30 saniyede bir tur atar

    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    return t

if db:
    start_background_daemon()

# ==========================================
# 4. ARAYÜZ (FRONTEND DASHBOARD)
# ==========================================
def main():
    st.title("🛡️ CLOUD SENTINEL V8.2 // TRUE DAEMON")
    st.caption("Arayüzden Bağımsız 7/24 Arka Plan Bulut Motoru")
    
    cfg = get_cloud_config()
    is_autopilot_on = cfg.get('autopilot', False)
    
    if status_msg == "BAĞLI":
        if is_autopilot_on:
            st.markdown(f'<div class="status-bar online">● BULUT DURUMU: {status_msg} | 🚀 MOTOR 7/24 ÇALIŞIYOR (SAYFAYI KAPATABİLİRSİNİZ)</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="status-bar" style="background-color:#333; color:white;">● BULUT DURUMU: {status_msg} | ⏸️ MOTOR DURAKLATILDI</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="status-bar offline">{status_msg}</div>', unsafe_allow_html=True)
        return 

    with st.sidebar:
        with st.form("config_form"):
            st.header("☁️ BULUT AYARLARI")
            st.info("Değişikliklerin bot motoruna geçmesi için 'Buluta Kaydet' tuşuna basın.")
            
            new_margin = st.number_input("İşlem Başına Marjin ($)", min_value=10, max_value=100000, value=int(cfg.get('margin', 200)))
            new_leverage = st.slider("Kaldıraç (x)", 1, 20, int(cfg.get('leverage', 3)))
            
            tf_options = ["15m", "1h", "4h", "1d"]
            curr_tf = cfg.get('timeframe', '1h')
            new_tf = st.selectbox("Mum Zaman Dilimi", tf_options, index=tf_options.index(curr_tf) if curr_tf in tf_options else 1)
            
            coin_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT"]
            curr_coins = cfg.get('coins', ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            new_coins = st.multiselect("Varlıklar", coin_list, default=[c for c in curr_coins if c in coin_list])
            
            new_f_ema = st.slider("Hızlı EMA", 5, 50, int(cfg.get('ema_f', 21)))
            new_s_ema = st.slider("Yavaş EMA", 10, 200, int(cfg.get('ema_s', 55)))
            new_tp = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, float(cfg.get('tp_atr', 3.5)))
            
            st.divider()
            new_autopilot = st.toggle("🤖 7/24 Arka Plan Motoru", value=is_autopilot_on)
            
            submitted = st.form_submit_button("☁️ Ayarları Buluta Kaydet")
            if submitted:
                new_cfg = {
                    'margin': new_margin, 'leverage': new_leverage, 'timeframe': new_tf,
                    'coins': new_coins, 'ema_f': new_f_ema, 'ema_s': new_s_ema,
                    'tp_atr': new_tp, 'autopilot': new_autopilot
                }
                db.collection('artifacts').document(app_id).collection('public').document('config').set(new_cfg)
                st.success("Ayarlar Buluta İletildi!")
                time.sleep(1)
                st.rerun()

        st.divider()
        ui_refresh = st.toggle("👁️ Ekranda Canlı İzle (10sn Yenileme)", value=False, help="Açıkken bu web sayfası buluttan yeni verileri çekmek için sürekli yenilenir.")

    # --- EKRAN ÇIKTILARI ---
    # Aktif Pozisyonları Buluttan Çek
    active_docs = db.collection('artifacts').document(app_id).collection('public').document('active_trades').collection('positions').get()
    all_active = [doc.to_dict() for doc in active_docs]
    
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
                    <p style="margin:0; font-size:14px; color:#00FF41;">Hedef: {pos['tp']:.4f}</p>
                    <p style="margin:0; font-size:14px; color:#FF003C;">Stop: {pos['sl']:.4f}</p>
                </div>
                """, unsafe_allow_html=True)
    
    st.divider()
    
    # Canlı Radar Verilerini Buluttan Çek (Motor Oraya Yazıyor)
    live_doc = db.collection('artifacts').document(app_id).collection('public').document('live_market').get()
    if live_doc.exists:
        live_data = live_doc.to_dict()
        st.markdown(f"#### `📡 CANLI RADAR ÖZETİ` <span style='font-size:12px; color:gray;'>Son Güncelleme: {live_data.get('updated_at', '...')}</span>", unsafe_allow_html=True)
        df_live = pd.DataFrame(live_data.get('data', []))
        if not df_live.empty:
            def highlight_pnl(val):
                color = '#00FF41' if val > 0 else '#FF003C' if val < 0 else 'gray'
                return f'color: {color}; font-weight: bold;'
            try:
                st.dataframe(df_live.style.map(highlight_pnl, subset=['Anlık PnL (%)', 'Kâr/Zarar ($)']), width='stretch')
            except Exception:
                try:
                    st.dataframe(df_live.style.applymap(highlight_pnl, subset=['Anlık PnL (%)', 'Kâr/Zarar ($)']), width='stretch')
                except:
                    st.dataframe(df_live, width='stretch')
    else:
        st.info("Motor henüz piyasa taramasını tamamlamadı. Lütfen biraz bekleyin.")

    # Geçmiş Çekimi ve İstatistikler
    hist_docs = db.collection('artifacts').document(app_id).collection('public').document('data').collection('history').get()
    history = [d.to_dict() for d in hist_docs]
    
    if history:
        st.divider()
        total_trades = len(history)
        wins = len([h for h in history if h.get('result') == 'WIN'] + [h for h in history if 'WIN' in h.get('result', '')])
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

    # YALNIZCA GÖRSELLİĞİ CANLI TUTMAK İÇİN
    if ui_refresh:
        time.sleep(10)
        st.rerun()

if __name__ == "__main__":
    main()
