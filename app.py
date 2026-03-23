import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# GÖRSEL TASARIM (ELITE SAAS THEME)
# ==========================================
st.set_page_config(page_title="QUANT LAB V6 // ELITE MTF", layout="wide")

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
        transition: transform 0.2s;
    }
    .stButton>button:hover { transform: scale(1.02); }
    .stMetric { 
        background-color: #1A1D23; 
        border: 1px solid #333; 
        border-radius: 10px; 
        padding: 15px; 
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
        # Büyük Abi: 1 Saatlik EMA 200 (Ana Yön Belirleyici)
        df['EMA_200_Trend'] = df['Close'].ewm(span=200, adjust=False).mean()
        return df

    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    # ATR (14) - Dinamik Zarar Kes/Kar Al
    tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    df['ATR_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # ADX (14) - Trend Gücü
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
# ELITE BACKTEST MOTORU (MTF)
# ==========================================
def run_elite_backtest(df_15m, df_1h, target_trades, fast_ema, slow_ema, adx_threshold, use_mtf):
    trades = []
    in_pos, entry_p, sl, tp, p_type = False, 0, 0, 0, ""
    e_f, e_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    # 1H trend verisini 15m zaman dilimine eşle (Veri sızıntısını engellemek için ffill)
    df_1h_trend = df_1h[['EMA_200_Trend']].rename(columns={'EMA_200_Trend': 'BIG_BROTHER_TREND'})
    df_combined = df_15m.join(df_1h_trend, how='left').ffill()

    for i in range(max(200, slow_ema), len(df_combined)):
        if len(trades) >= target_trades: break
        c, p = df_combined.iloc[i], df_combined.iloc[i-1]

        # Büyük Abi Filtresi: Ana trend ne yönde?
        # Sadece fiyat EMA 200 üzerindeyse LONG, altındaysa SHORT aramasına izin ver.
        is_bullish = c['Close'] > c['BIG_BROTHER_TREND'] if use_mtf else True
        is_bearish = c['Close'] < c['BIG_BROTHER_TREND'] if use_mtf else True

        if not in_pos:
            # LONG GİRİŞ: EMA Cross + RSI + ADX + MTF ONAYI
            if p[e_f] <= p[e_s] and c[e_f] > c[e_s] and c['RSI_14'] > 50 and c['ADX_14'] >= adx_threshold and is_bullish:
                in_pos, p_type, entry_p = True, "LONG", c['Close']
                # Profesyonel R:R (1:2)
                sl, tp = entry_p - (c['ATR_14']*2.5), entry_p + (c['ATR_14']*5)
            
            # SHORT GİRİŞ: EMA Cross + RSI + ADX + MTF ONAYI
            elif p[e_f] >= p[e_s] and c[e_f] < c[e_s] and c['RSI_14'] < 50 and c['ADX_14'] >= adx_threshold and is_bearish:
                in_pos, p_type, entry_p = True, "SHORT", c['Close']
                sl, tp = entry_p + (c['ATR_14']*2.5), entry_p - (c['ATR_14']*5)
        else:
            # ÇIKIŞ KOŞULLARI (STOP LOSS / TAKE PROFIT)
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
    return pd.DataFrame(trades)

# ==========================================
# ANA PANEL (UI)
# ==========================================
def main():
    st.title("QUANT LAB V6 // ELITE MTF")
    st.caption("Büyük Abi (1h EMA 200) Trend Filtresi Aktif")
    
    with st.sidebar:
        st.header("SİSTEM KONTROLÜ")
        sym = st.selectbox("Varlık Seçin", ["TSLA", "BTC-USD", "NVDA", "AAPL", "ETH-USD"])
        
        st.subheader("Küçük Abi (15m) Ayarları")
        f_ema = st.slider("Hızlı EMA", 5, 50, 5)
        s_ema = st.slider("Yavaş EMA", 10, 200, 13)
        adx_t = st.slider("ADX Filtresi", 0, 40, 15)
        
        st.subheader("Büyük Abi (1h) Ayarları")
        use_mtf = st.checkbox("MTF Filtresini Aktif Et", value=True)
        
        st.divider()
        t_count = st.number_input("İşlem Hedefi", 5, 100, 20)
        btn = st.button("ELITE DENEYİ BAŞLAT")

    if btn:
        try:
            with st.spinner("Zaman dilimleri senkronize ediliyor..."):
                # Verileri indir (15m ve 1h)
                df_15m = yf.download(sym, period="60d", interval="15m", progress=False)
                df_1h = yf.download(sym, period="730d", interval="1h", progress=False)
                
                # MultiIndex sütunlarını temizle
                if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.droplevel(1)
                
                df_15m.dropna(inplace=True); df_1h.dropna(inplace=True)
                
                # İndikatörleri hesapla
                df_15m = calculate_indicators(df_15m, f_ema, s_ema)
                df_1h = calculate_indicators(df_1h, 5, 200, is_trend_check=True)

                # Simülasyonu çalıştır
                results = run_elite_backtest(df_15m, df_1h, t_count, f_ema, s_ema, adx_t, use_mtf)

                if results.empty:
                    st.warning("⚠️ Büyük Abi trend yönünde güvenli işlem bulamadı! Filtreyi gevşetmeyi deneyin.")
                else:
                    wins = len(results[results['Sonuç']=='WIN'])
                    wr = (wins / len(results)) * 100
                    pnl = results['PnL_%'].sum()
                    
                    st.markdown("### `[ELITE PERFORMANS ÖZETİ]`")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Toplam İşlem", len(results))
                    c2.metric("Win Rate", f"%{wr:.1f}")
                    c3.metric("Net PnL", f"%{pnl:.2f}")
                    c4.metric("MTF Koruması", "AKTİF" if use_mtf else "KAPALI")
                    
                    st.divider()
                    
                    # Tabloyu Renklendirerek göster
                    def highlight(val):
                        color = '#00FF41' if val == 'WIN' else '#FF003C'
                        return f'color: {color}; font-weight: bold;'
                    st.table(results.style.applymap(highlight, subset=['Sonuç']))
                    
                    # Rapor Dışa Aktarım
                    st.markdown("#### `[ANALİST RAPORU]`")
                    mtf_status = "ON" if use_mtf else "OFF"
                    final_rep = f"VARLIK: {sym} | MTF: {mtf_status} | EMA: {f_ema}/{s_ema} | ADX: >{adx_t}\nTOTAL: {len(results)} | WR: %{wr:.1f} | PnL: %{pnl:.2f}"
                    st.code(final_rep)
                    
        except Exception as e:
            st.error(f"Sistem Hatası: {str(e)}")
    else:
        st.info("👈 'Büyük Abi' filtresiyle Tesla'nın vahşi hareketlerini dizginle. Deneyi başlat!")

if __name__ == "__main__":
    main()
