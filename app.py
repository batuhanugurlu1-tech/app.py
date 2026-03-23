import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ==========================================
# UI YAPILANDIRMASI (Elite SaaS Teması)
# ==========================================
st.set_page_config(page_title="QUANT LAB V6.2 // OPTIMIZER", layout="wide")

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
        border: 2px solid #333;
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
                    "Date": df_combined.index[i],
                    "DateStr": df_combined.index[i].strftime('%Y-%m-%d %H:%M'), 
                    "Type": p_type, "Result": res, "PnL_%": round(pnl, 2)
                })
                in_pos = False
    return pd.DataFrame(trades), df_combined.iloc[-1]

# ==========================================
# ANA PANEL
# ==========================================
def main():
    st.title("QUANT LAB V6.2 // THE OPTIMIZER")
    
    with st.sidebar:
        st.header("SİSTEM KONTROLÜ")
        assets = {"NVDA": "NVDA", "TSLA": "TSLA", "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD"}
        sym_label = st.selectbox("Varlık Seçin", list(assets.keys()))
        sym = assets[sym_label]
        
        st.subheader("Küçük Abi (15m)")
        f_ema = st.slider("Hızlı EMA", 5, 50, 9) # NVDA için 9 daha dengelidir
        s_ema = st.slider("Yavaş EMA", 10, 200, 21) # NVDA için 21 daha güvenlidir
        adx_t = st.slider("ADX Filtresi", 0, 40, 15) # NVDA momentum sever, 15 idealdir
        
        st.subheader("Büyük Abi (1h)")
        use_mtf = st.checkbox("MTF Filtresi Aktif", value=True)
        
        st.divider()
        t_count = st.number_input("İşlem Hedefi", 5, 100, 20)
        btn = st.button("SİSTEMİ OPTİMİZE ET & ÇALIŞTIR")

    if btn:
        try:
            with st.spinner("Piyasa 'DNA'sı analiz ediliyor..."):
                df_15m = yf.download(sym, period="60d", interval="15m", progress=False)
                df_1h = yf.download(sym, period="730d", interval="1h", progress=False)
                
                if isinstance(df_15m.columns, pd.MultiIndex): df_15m.columns = df_15m.columns.droplevel(1)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.droplevel(1)
                
                df_15m.dropna(inplace=True); df_1h.dropna(inplace=True)
                
                df_15m = calculate_indicators(df_15m, f_ema, s_ema)
                df_1h = calculate_indicators(df_1h, 5, 200, is_trend_check=True)

                results, last_row = run_elite_backtest(df_15m, df_1h, t_count, f_ema, s_ema, adx_t, use_mtf)

                # --- CANLI DURUM ---
                st.markdown("### `[CANLI SİNYAL DURUMU]`")
                e_f, e_s = f'EMA_{f_ema}', f'EMA_{s_ema}'
                trend_ok = last_row['Close'] > last_row['BIG_BROTHER_TREND'] if use_mtf else True
                bear_trend_ok = last_row['Close'] < last_row['BIG_BROTHER_TREND'] if use_mtf else True
                
                if last_row[e_f] > last_row[e_s] and last_row['ADX_14'] >= adx_t and trend_ok:
                    st.markdown(f"<div class='signal-card' style='background-color: #00FF4122; border-color: #00FF41; color: #00FF41;'>🚀 {sym} CANLI SİNYAL: LONG</div>", unsafe_allow_html=True)
                elif last_row[e_f] < last_row[e_s] and last_row['ADX_14'] >= adx_t and bear_trend_ok:
                    st.markdown(f"<div class='signal-card' style='background-color: #FF003C22; border-color: #FF003C; color: #FF003C;'>📉 {sym} CANLI SİNYAL: SHORT</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='signal-card' style='color: #888;'>⏳ {sym} ŞU AN BEKLEMEDE (SİNYAL YOK)</div>", unsafe_allow_html=True)

                # --- PERFORMANS ANALİZİ ---
                if not results.empty:
                    wins = len(results[results['Result']=='WIN'])
                    wr = (wins / len(results)) * 100
                    total_pnl = results['PnL_%'].sum()
                    
                    st.markdown("### `[STRATEJİ PERFORMANSI]`")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Toplam İşlem", len(results))
                    c2.metric("Kazanma Oranı", f"%{wr:.1f}")
                    c3.metric("Net Kâr/Zarar", f"%{total_pnl:.2f}")
                    c4.metric("Ort. İşlem", f"%{(total_pnl/len(results)):.2f}")

                    # --- KUMÜLATİF KÂR GRAFİĞİ ---
                    results['Cum_PnL'] = results['PnL_%'].cumsum()
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=results['Date'], y=results['Cum_PnL'], mode='lines+markers', name='Kümülatif Kâr', line=dict(color='#00FF41', width=3)))
                    fig.update_layout(title=f"{sym} Strateji Gelişim Eğrisi", template="plotly_dark", height=400, margin=dict(l=20, r=20, t=50, b=20))
                    st.plotly_chart(fig, use_container_width=True)

                    st.markdown("### `[DETAYLI İŞLEM GÜNLÜĞÜ]`")
                    st.dataframe(results[['DateStr', 'Type', 'Result', 'PnL_%']].sort_index(ascending=False), use_container_width=True)
                else:
                    st.warning("Bu ayarlarla geçmişte uygun işlem bulunamadı. Lütfen EMA veya ADX değerlerini gevşetin.")
                    
        except Exception as e:
            st.error(f"Sistem Hatası: {str(e)}")

if __name__ == "__main__":
    main()
