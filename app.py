import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ==========================================
# UI YAPILANDIRMASI
# ==========================================
st.set_page_config(page_title="QUANT LAB V6.6 // VOLATILITY SHIELD", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .stButton>button { 
        background-color: #00FF41 !important; color: #0A0A0A !important; 
        font-weight: bold; width: 100%; border-radius: 8px; height: 3.5em; border: none;
    }
    .stMetric { background-color: #1A1D23; border: 1px solid #333; border-radius: 10px; padding: 15px; }
    h1, h2, h3 { color: #00FF41 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# TEKNİK ANALİZ MOTORU
# ==========================================
def calculate_indicators(df, fast_ema, slow_ema, is_trend_check=False):
    if df.empty or len(df) < (200 if is_trend_check else slow_ema): 
        return df
    
    # EMA Hesaplamaları
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    if is_trend_check:
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
    
    # Volatilite Filtresi: Anlık ATR / Tarihsel ATR (200 periyot)
    df['ATR_MA_200'] = df['ATR_14'].rolling(window=200).mean()
    df['Vol_Ratio'] = df['ATR_14'] / df['ATR_MA_200']
    
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
# BACKTEST MOTORU (V6.6 - VOLATILITY SHIELD)
# ==========================================
def run_elite_backtest(df_15m, df_1h, target_trades, fast_ema, slow_ema, adx_threshold, use_mtf, trailing_factor, tp_multiplier, be_trigger, vol_threshold):
    trades = []
    in_pos, entry_p, current_sl, tp, p_type = False, 0, 0, 0, ""
    e_f, e_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    df_1h_trend = df_1h[['EMA_200_Trend']].rename(columns={'EMA_200_Trend': 'BIG_BROTHER_TREND'})
    df_combined = df_15m.join(df_1h_trend, how='left').ffill()

    start_idx = max(200, slow_ema)
    if len(df_combined) <= start_idx:
        return pd.DataFrame()

    for i in range(start_idx, len(df_combined)):
        if len(trades) >= target_trades: break
        c, p = df_combined.iloc[i], df_combined.iloc[i-1]

        is_bullish = c['Close'] > c['BIG_BROTHER_TREND'] if use_mtf else True
        is_bearish = c['Close'] < c['BIG_BROTHER_TREND'] if use_mtf else True
        
        # VOLATILITY SHIELD: Piyasa yeterince hareketli mi? (Çok düşük veya aşırı yüksek volatilitede girmeyi engeller)
        vol_ok = 0.8 <= c['Vol_Ratio'] <= vol_threshold

        if not in_pos:
            # LONG GİRİŞ
            if p[e_f] <= p[e_s] and c[e_f] > c[e_s] and c['RSI_14'] > 52 and c['ADX_14'] >= adx_threshold and is_bullish and vol_ok:
                in_pos, p_type, entry_p = True, "LONG", c['Close']
                current_sl = entry_p - (c['ATR_14'] * 2.0)
                tp = entry_p + (c['ATR_14'] * tp_multiplier)
            # SHORT GİRİŞ
            elif p[e_f] >= p[e_s] and c[e_f] < c[e_s] and c['RSI_14'] < 48 and c['ADX_14'] >= adx_threshold and is_bearish and vol_ok:
                in_pos, p_type, entry_p = True, "SHORT", c['Close']
                current_sl = entry_p + (c['ATR_14'] * 2.0)
                tp = entry_p - (c['ATR_14'] * tp_multiplier)
        else:
            res = ""
            current_profit_atr = abs(c['Close'] - entry_p) / c['ATR_14']
            
            # BREAK-EVEN (Başa Baş)
            if current_profit_atr >= be_trigger:
                if p_type == "LONG": current_sl = max(current_sl, entry_p)
                else: current_sl = min(current_sl, entry_p)

            # RSI ERKEN HASAT (Scalp)
            if p_type == "LONG" and c['RSI_14'] < 50:
                res, exit_p = "RSI/EXIT", c['Close']
            elif p_type == "SHORT" and c['RSI_14'] > 50:
                res, exit_p = "RSI/EXIT", c['Close']

            if not res:
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
                
    return pd.DataFrame(trades)

# ==========================================
# ANA PANEL
# ==========================================
def main():
    st.title("QUANT LAB V6.6 // VOLATILITY SHIELD")
    
    with st.sidebar:
        st.header("SİSTEM KONTROLÜ")
        sym = st.selectbox("Varlık", ["NVDA", "TSLA", "BTC-USD"])
        
        st.subheader("Giriş Filtreleri")
        f_ema = st.slider("Hızlı EMA", 5, 50, 9)
        s_ema = st.slider("Yavaş EMA", 10, 200, 21)
        adx_t = st.slider("ADX Filtresi", 0, 40, 18)
        vol_t = st.slider("Volatilite Sınırı (Ratio)", 1.0, 5.0, 2.0, help="Piyasa aşırı oynaksa (kaotikse) işleme girmeyi engeller.")
        
        st.subheader("Hasat & Risk")
        tp_mult = st.slider("Kâr Hedefi (ATR x)", 1.0, 5.0, 2.0)
        be_trig = st.slider("BE Aktif Etme (ATR x)", 0.5, 2.5, 0.8)
        ts_factor = st.slider("Takip Eden Stop (ATR x)", 1.0, 3.0, 1.2)
        
        use_mtf = st.checkbox("Büyük Abi (1h) Aktif", value=True)
        
        btn = st.button("KALKANLI TESTİ BAŞLAT")

    if btn:
        try:
            with st.spinner("Volatilite kalkanı ayarlanıyor..."):
                df_15m = yf.download(sym, period="60d", interval="15m", progress=False)
                df_1h = yf.download(sym, period="730d", interval="1h", progress=False)
                
                if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.droplevel(1)
                
                df_15m.dropna(inplace=True); df_1h.dropna(inplace=True)
                
                df_15m = calculate_indicators(df_15m, f_ema, s_ema)
                df_1h = calculate_indicators(df_1h, 5, 200, is_trend_check=True)

                results = run_elite_backtest(df_15m, df_1h, 25, f_ema, s_ema, adx_t, use_mtf, ts_factor, tp_mult, be_trig, vol_t)

                if results is not None and not results.empty:
                    pos_trades = results[results['PnL_%'] > 0]
                    total_pnl = results['PnL_%'].sum()
                    
                    neg_pnl = results[results['PnL_%'] < 0]['PnL_%'].sum()
                    profit_factor = abs(results[results['PnL_%'] > 0]['PnL_%'].sum() / neg_pnl) if neg_pnl != 0 else 0
                    
                    st.markdown("### `[VOLATILITY SHIELD ANALİZİ]`")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Net Win Rate", f"%{(len(pos_trades)/len(results)*100):.1f}")
                    c2.metric("Toplam PnL", f"%{total_pnl:.2f}")
                    c3.metric("Profit Factor", f"{profit_factor:.2f}")
                    c4.metric("İşlem Sayısı", len(results))

                    results['Cum_PnL'] = results['PnL_%'].cumsum()
                    fig = go.Figure(go.Scatter(x=results['Date'], y=results['Cum_PnL'], line=dict(color='#00FF41', width=3), fill='tozeroy'))
                    fig.update_layout(title=f"{sym} Kümülatif Getiri (V6.6)", template="plotly_dark", height=300)
                    st.plotly_chart(fig, use_container_width=True)
                    
                    st.dataframe(results.tail(15))
                else:
                    st.warning("Bu kalkan seviyesinde uygun trend bulunamadı. Volatilite Ratio veya ADX değerini gevşetin.")
        except Exception as e:
            st.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    main()
