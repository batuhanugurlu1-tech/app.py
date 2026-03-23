import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# UI YAPILANDIRMASI (Elite SaaS Teması)
# ==========================================
st.set_page_config(page_title="QUANT LAB V6.1 // LIVE SIGNALS", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .stButton>button { 
        background-color: #00FF41 !important; 
        color: #0A0A0A !important; 
        font-weight: bold; 
        width: 100%; 
        border-radius: 8px; 
        height: 3.5em; 
        border: none;
    }
    .stMetric { 
        background-color: #1A1D23; 
        border: 1px solid #333; 
        border-radius: 10px; 
        padding: 15px; 
    }
    .signal-card {
        padding: 20px;
        border-radius: 15px;
        text-align: center;
        margin-bottom: 25px;
        font-weight: bold;
        font-size: 1.2em;
    }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# TEKNİK ANALİZ MOTORU
# ==========================================
def calculate_indicators(df, fast_ema, slow_ema, is_trend_check=False):
    if df.empty: return df
    
    # EMA Hesaplamaları
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    if is_trend_check:
        # Büyük Abi: 1 Saatlik EMA 200
        df['EMA_200_Trend'] = df['Close'].ewm(span=200, adjust=False).mean()
        return df

    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    # ATR (14)
    tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    df['ATR_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # ADX (14)
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df['ADX_14'] = dx.fillna(0).ewm(alpha=1/14, adjust=False).mean()
    
    return df

# ==========================================
# ELITE BACKTEST MOTORU
# ==========================================
def run_elite_backtest(df_15m, df_1h, target_trades, fast_ema, slow_ema, adx_threshold, use_mtf):
    trades = []
    in_pos, entry_p, sl, tp, p_type = False, 0, 0, 0, ""
    e_f, e_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    df_1h_trend = df_1h[['EMA_200_Trend']].rename(columns={'EMA_200_Trend': 'BIG_BROTHER_TREND'})
    df_combined = df_15m.join(df_1h_trend, how='left').ffill()

    for i in range(max(200, slow_ema), len(df_combined)):
        if len(trades) >= target_trades: break
        c, p = df_combined.iloc[i], df_combined.iloc[i-1]

        is_bullish = c['Close'] > c['BIG_BROTHER_TREND'] if use_mtf else True
        is_bearish = c['Close'] < c['BIG_BROTHER_TREND'] if use_mtf else True

        if not in_pos:
            if p[e_f] <= p[e_s] and c[e_f] > c[e_s] and c['RSI_14'] > 50 and c['ADX_14'] >= adx_threshold and is_bullish:
                in_pos, p_type, entry_p = True, "LONG", c['Close']
                sl, tp = entry_p - (c['ATR_14']*2.5), entry_p + (c['ATR_14']*5)
            elif p[e_f] >= p[e_s] and c[e_f] < c[e_s] and c['RSI_14'] < 50 and c['ADX_14'] >= adx_threshold and is_bearish:
                in_pos, p_type, entry_p = True, "SHORT", c['Close']
                sl, tp = entry_p + (c['ATR_14']*2.5), entry_p - (c['ATR_14']*5)
        else:
            res, exit_p = "", 0
            if p_type == "LONG":
                if c['Low'] <= sl: res, exit_p = "LOSS", sl
                elif c['High'] >= tp: res, exit_p = "WIN", tp
            else:
                if c['High'] >= sl: res, exit_p = "LOSS", sl
                elif c['Low'] <= tp: res, exit_p = "WIN", tp
            
            if res:
                pnl = ((exit_p - entry_p)/entry_p)*100 if p_type == "LONG" else ((entry_p - exit_p)/entry_p)*100
                trades.append({
                    "Tarih": df_combined.index[i].strftime('%Y-%m-%d %H:%M'), 
                    "Tip": p_type, "Sonuç": res, "PnL_%": round(pnl, 2)
                })
                in_pos = False
    return pd.DataFrame(trades), df_combined.iloc[-1]

# ==========================================
# ANA PANEL
# ==========================================
def main():
    st.title("QUANT LAB V6.1 // LIVE SIGNALS")
    
    with st.sidebar:
        st.header("SİSTEM KONTROLÜ")
        # Daha fazla varlık eklendi
        assets = {
            "TSLA": "TSLA", "NVDA": "NVDA", "BTC-USD": "BTC-USD", 
            "ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD", "AMZN": "AMZN", 
            "AAPL": "AAPL", "GOOGL": "GOOGL", "NFLX": "NFLX"
        }
        sym_label = st.selectbox("Varlık Seçin", list(assets.keys()))
        sym = assets[sym_label]
        
        st.subheader("Küçük Abi (15m) Ayarları")
        f_ema = st.slider("Hızlı EMA", 5, 50, 5)
        s_ema = st.slider("Yavaş EMA", 10, 200, 13)
        adx_t = st.slider("ADX Filtresi", 0, 40, 10) # Tesla için bulduğun tatlı nokta 10'u varsayılan yaptık
        
        st.subheader("Büyük Abi (1h) Ayarları")
        use_mtf = st.checkbox("Büyük Abi Filtresi Aktif", value=True)
        
        st.divider()
        t_count = st.number_input("Backtest İşlem Sayısı", 5, 100, 20)
        btn = st.button("CANLI ANALİZ & TEST BAŞLAT")

    if btn:
        try:
            with st.spinner("Piyasa verileri anlık olarak taranıyor..."):
                df_15m = yf.download(sym, period="60d", interval="15m", progress=False)
                df_1h = yf.download(sym, period="730d", interval="1h", progress=False)
                
                if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.droplevel(1)
                
                df_15m.dropna(inplace=True); df_1h.dropna(inplace=True)
                
                # İndikatörler
                df_15m = calculate_indicators(df_15m, f_ema, s_ema)
                df_1h = calculate_indicators(df_1h, 5, 200, is_trend_check=True)

                # Test & Canlı Durum
                results, last_row = run_elite_backtest(df_15m, df_1h, t_count, f_ema, s_ema, adx_t, use_mtf)

                # --- CANLI SİNYAL PANELİ ---
                st.markdown("### `[CANLI PİYASA DURUMU]`")
                
                # Sinyal mantığı kontrolü
                e_f, e_s = f'EMA_{f_ema}', f'EMA_{s_ema}'
                trend_ok = last_row['Close'] > last_row['BIG_BROTHER_TREND'] if use_mtf else True
                bear_trend_ok = last_row['Close'] < last_row['BIG_BROTHER_TREND'] if use_mtf else True
                
                if last_row[e_f] > last_row[e_s] and last_row['ADX_14'] >= adx_t and trend_ok:
                    st.markdown("<div class='signal-card' style='background-color: #00FF41; color: #0A0A0A;'>🚀 CANLI SİNYAL: LONG (ALIM) ZAMANI</div>", unsafe_allow_html=True)
                    st.success(f"Büyük Abi Onaylı! Fiyat {last_row['Close']:.2f} | Hedef: {(last_row['Close'] + last_row['ATR_14']*5):.2f}")
                elif last_row[e_f] < last_row[e_s] and last_row['ADX_14'] >= adx_t and bear_trend_ok:
                    st.markdown("<div class='signal-card' style='background-color: #FF003C; color: #FFFFFF;'>📉 CANLI SİNYAL: SHORT (SATIŞ) ZAMANI</div>", unsafe_allow_html=True)
                    st.error(f"Düşüş Trendi Onaylı! Fiyat {last_row['Close']:.2f} | Hedef: {(last_row['Close'] - last_row['ATR_14']*5):.2f}")
                else:
                    st.markdown("<div class='signal-card' style='background-color: #333; color: #888;'>⏳ ŞU AN SİNYAL YOK - BEKLEMEDE KAL</div>", unsafe_allow_html=True)
                    st.info(f"Neden Sinyal Yok? ADX Gücü: {last_row['ADX_14']:.1f} / Gerekli: {adx_t} | Büyük Abi: {'UYGUN' if trend_ok or bear_trend_ok else 'UYGUN DEĞİL'}")

                # --- BACKTEST PANELİ ---
                st.divider()
                st.markdown("### `[STRATEJİ PERFORMANS ÖZETİ]`")
                if not results.empty:
                    wins = len(results[results['Sonuç']=='WIN'])
                    wr = (wins / len(results)) * 100
                    pnl = results['PnL_%'].sum()
                    
                    c1, c2, c3 = st.columns(3)
                    c1.metric("İşlem", len(results))
                    c2.metric("Win Rate", f"%{wr:.1f}")
                    c3.metric("Net PnL", f"%{pnl:.2f}")
                    
                    st.table(results.tail(10))
                else:
                    st.warning("Bu ayarlarla geçmişte işlem bulunamadı.")
                    
        except Exception as e:
            st.error(f"Sistem Hatası: {str(e)}")

if __name__ == "__main__":
    main()
