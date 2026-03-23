import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ==========================================
# UI YAPILANDIRMASI (Elite Dark Mode)
# ==========================================
st.set_page_config(page_title="QUANT LAB V6.7 // ALPHA SENTINEL", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .stButton>button { 
        background-color: #00FF41 !important; color: #0A0A0A !important; 
        font-weight: bold; width: 100%; border-radius: 8px; height: 3.5em; border: none;
    }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 10px; padding: 15px; }
    .signal-box { padding: 20px; border-radius: 10px; text-align: center; font-weight: bold; margin-bottom: 20px; border: 1px solid #444; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# QUANT MOTORU: TEKNİK ANALİZ
# ==========================================
def calculate_indicators(df, fast_ema, slow_ema, is_trend_check=False):
    if df.empty or len(df) < 200: return df
    
    # EMAs
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    if is_trend_check:
        df['EMA_200_Trend'] = df['Close'].ewm(span=200, adjust=False).mean()
        return df

    # RSI & ATR
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    df['ATR_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # MOMENTUM (ROC - Rate of Change)
    df['ROC'] = ((df['Close'] - df['Close'].shift(10)) / df['Close'].shift(10)) * 100
    
    # ADX (Trend Strength)
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
# ELITE BACKTEST MOTORU (V6.7)
# ==========================================
def run_sentinel_backtest(df_15m, df_1h, target_trades, fast_ema, slow_ema, adx_threshold, use_mtf, trailing_factor, tp_multiplier):
    trades = []
    in_pos, entry_p, current_sl, tp, p_type = False, 0, 0, 0, ""
    e_f, e_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    df_1h_trend = df_1h[['EMA_200_Trend']].rename(columns={'EMA_200_Trend': 'BIG_BROTHER_TREND'})
    df_combined = df_15m.join(df_1h_trend, how='left').ffill()

    for i in range(max(200, slow_ema), len(df_combined)):
        if len(trades) >= target_trades: break
        c, p = df_combined.iloc[i], df_combined.iloc[i-1]

        is_bullish = c['Close'] > c['BIG_BROTHER_TREND'] if use_mtf else True
        is_bearish = c['Close'] < c['BIG_BROTHER_TREND'] if use_mtf else True
        
        # QUALITY CONTROL: Trendin arkasında gerçek bir güç var mı?
        momentum_ok = abs(c['ROC']) > 0.5 

        if not in_pos:
            # LONG ENTRY
            if p[e_f] <= p[e_s] and c[e_f] > c[e_s] and c['RSI_14'] > 50 and c['ADX_14'] >= adx_threshold and is_bullish and momentum_ok:
                in_pos, p_type, entry_p = True, "LONG", c['Close']
                current_sl = entry_p - (c['ATR_14'] * 2.0)
                tp = entry_p + (c['ATR_14'] * tp_multiplier)
            # SHORT ENTRY
            elif p[e_f] >= p[e_s] and c[e_f] < c[e_s] and c['RSI_14'] < 48 and c['ADX_14'] >= adx_threshold and is_bearish and momentum_ok:
                in_pos, p_type, entry_p = True, "SHORT", c['Close']
                current_sl = entry_p + (c['ATR_14'] * 2.0)
                tp = entry_p - (c['ATR_14'] * tp_multiplier)
        else:
            res = ""
            # Başa baş (Break-even) koruması
            if abs(c['Close'] - entry_p) / c['ATR_14'] > 1.2:
                if p_type == "LONG": current_sl = max(current_sl, entry_p)
                else: current_sl = min(current_sl, entry_p)

            # Exit logic
            if p_type == "LONG":
                new_sl = c['Close'] - (c['ATR_14'] * trailing_factor)
                if new_sl > current_sl: current_sl = new_sl
                if c['Low'] <= current_sl: res, exit_p = "STOP/BE", current_sl
                elif c['High'] >= tp: res, exit_p = "WIN", tp
            else:
                new_sl = c['Close'] + (c['ATR_14'] * trailing_factor)
                if new_sl < current_sl: current_sl = new_sl
                if c['High'] >= current_sl: res, exit_p = "STOP/BE", current_sl
                elif c['Low'] <= tp: res, exit_p = "WIN", tp
            
            if res:
                pnl = ((exit_p - entry_p)/entry_p)*100 if p_type == "LONG" else ((entry_p - exit_p)/entry_p)*100
                trades.append({"Date": df_combined.index[i], "Type": p_type, "Result": res, "PnL_%": round(pnl, 2)})
                in_pos = False
                
    return pd.DataFrame(trades), df_combined.iloc[-1]

# ==========================================
# ANA PANEL (UI)
# ==========================================
def main():
    st.title("QUANT LAB V6.7 // ALPHA SENTINEL")
    
    with st.sidebar:
        st.header("SİSTEM KONTROLÜ")
        sym = st.selectbox("Varlık (Hisse veya Kripto)", ["BTC-USD", "ETH-USD", "SOL-USD", "NVDA", "TSLA"])
        
        st.subheader("Giriş Parametreleri")
        f_ema = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema = st.slider("Yavaş EMA", 10, 200, 21)
        adx_t = st.slider("ADX Eşiği", 0, 40, 15)
        
        st.subheader("Risk & Hasat")
        tp_mult = st.slider("Kâr Hedefi (ATR x)", 1.0, 6.0, 3.5)
        ts_factor = st.slider("Takip Eden Stop (ATR x)", 1.0, 4.0, 1.8)
        
        use_mtf = st.checkbox("Büyük Abi (1h Trend) Aktif", value=True)
        btn = st.button("SENTINEL TESTİNİ BAŞLAT")

    if btn:
        try:
            with st.spinner("Piyasa verileri analiz ediliyor..."):
                df_15m = yf.download(sym, period="60d", interval="15m", progress=False)
                df_1h = yf.download(sym, period="730d", interval="1h", progress=False)
                
                if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.droplevel(1)
                
                df_15m.dropna(inplace=True); df_1h.dropna(inplace=True)
                
                df_15m = calculate_indicators(df_15m, f_ema, s_ema)
                df_1h = calculate_indicators(df_1h, 5, 200, is_trend_check=True)

                results, last_row = run_sentinel_backtest(df_15m, df_1h, 30, f_ema, s_ema, adx_t, use_mtf, ts_factor, tp_mult)

                # --- CANLI SİNYAL ---
                st.markdown("### `[CANLI PİYASA GÖZLEMİ]`")
                trend_direction = "BOĞA (YUKARI)" if last_row['Close'] > last_row['BIG_BROTHER_TREND'] else "AYI (AŞAĞI)"
                st.markdown(f"<div class='signal-box'>Şu Anki Ana Trend: {trend_direction} | Fiyat: {last_row['Close']:.2f}</div>", unsafe_allow_html=True)

                if not results.empty:
                    wins = len(results[results['Result']=='WIN'])
                    total_pnl = results['PnL_%'].sum()
                    
                    st.markdown("### `[STRATEJİ PERFORMANSI]`")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Net Win Rate", f"%{(len(results[results['PnL_%'] > 0])/len(results)*100):.1f}")
                    c2.metric("Toplam PnL", f"%{total_pnl:.2f}")
                    c3.metric("Profit Factor", f"{abs(results[results['PnL_%'] > 0]['PnL_%'].sum() / results[results['PnL_%'] < 0]['PnL_%'].sum()):.2f}" if len(results[results['PnL_%'] < 0]) > 0 else "∞")
                    c4.metric("İşlem Sayısı", len(results))

                    results['Cum_PnL'] = results['PnL_%'].cumsum()
                    fig = go.Figure(go.Scatter(x=results['Date'], y=results['Cum_PnL'], line=dict(color='#00FF41', width=3), fill='tozeroy'))
                    fig.update_layout(title=f"{sym} Kâr Eğrisi (V6.7)", template="plotly_dark", height=300)
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.dataframe(results.sort_index(ascending=False).head(15))
                else:
                    st.warning("Bu ayarlar NVDA/TSLA için çok sıkı olabilir. ADX veya Momentum değerini gevşetmeyi deneyin.")
        except Exception as e:
            st.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    main()
