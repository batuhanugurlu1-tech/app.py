import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# UI & THEME CONFIGURATION
# ==========================================
st.set_page_config(page_title="QUANT LAB // STRATEGY TESTER V5", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    h1, h2, h3, h4, h5, h6, p, div, label, span { color: #FFFFFF !important; }
    .quant-card { background-color: #1A1D23; border-radius: 10px; padding: 20px; border: 1px solid #333333; margin-bottom: 20px; }
    .stButton>button { background-color: #00FF41 !important; color: #0A0A0A !important; font-weight: bold; width: 100%; border-radius: 5px; }
    .stButton>button:hover { background-color: #A3FFA3 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SCIENTIFIC CALCULATION ENGINE
# ==========================================
def calculate_indicators(df: pd.DataFrame, fast_ema: int, slow_ema: int) -> pd.DataFrame:
    if df.empty or len(df) < slow_ema: return df
    
    # EMAs
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    # ATR (14)
    tr = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1)
    df['ATR_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # ADX (14) - Trend Gücü Filtresi (Senkronizasyon Düzeltildi)
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    
    # np.where kullanırken index'i korumak hayati önem taşır
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    df['ADX_14'] = dx.fillna(0).ewm(alpha=1/14, adjust=False).mean() # NaN koruması
    
    return df

# ==========================================
# AUTO-TRADE BACKTEST SIMULATOR
# ==========================================
def run_backtest(df: pd.DataFrame, target_trades: int, fast_ema: int, slow_ema: int, adx_threshold: int):
    in_position = False
    entry_price, sl, tp = 0.0, 0.0, 0.0
    pos_type = ""
    trades = []
    
    ema_f = f'EMA_{fast_ema}'
    ema_s = f'EMA_{slow_ema}'
    
    for i in range(slow_ema, len(df)):
        if len(trades) >= target_trades: break
            
        current = df.iloc[i]
        prev = df.iloc[i-1]
        
        if not in_position:
            # GİRİŞ ŞARTLARI: EMA + RSI + ADX Filtresi
            if prev[ema_f] <= prev[ema_s] and current[ema_f] > current[ema_s] and current['RSI_14'] > 50 and current['ADX_14'] >= adx_threshold:
                in_position = True; pos_type = "LONG"; entry_price = current['Close']
                sl = entry_price - (current['ATR_14'] * 2)
                tp = entry_price + (current['ATR_14'] * 4) 
            elif prev[ema_f] >= prev[ema_s] and current[ema_f] < current[ema_s] and current['RSI_14'] < 50 and current['ADX_14'] >= adx_threshold:
                in_position = True; pos_type = "SHORT"; entry_price = current['Close']
                sl = entry_price + (current['ATR_14'] * 2)
                tp = entry_price - (current['ATR_14'] * 4) 
        else:
            exit_price, result = 0.0, ""
            if pos_type == "LONG":
                if current['Low'] <= sl: exit_price, result = sl, "LOSS"
                elif current['High'] >= tp: exit_price, result = tp, "WIN"
            elif pos_type == "SHORT":
                if current['High'] >= sl: exit_price, result = sl, "LOSS"
                elif current['Low'] <= tp: exit_price, result = tp, "WIN"
                
            if result != "":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if pos_type == "LONG" else ((entry_price - exit_price) / entry_price) * 100
                trades.append({
                    "Date": df.index[i].strftime("%Y-%m-%d %H:%M"),
                    "Type": pos_type,
                    "Entry": entry_price,
                    "Exit": exit_price,
                    "Result": result,
                    "PnL_%": pnl_pct
                })
                in_position = False 
                
    return pd.DataFrame(trades)

# ==========================================
# MAIN DASHBOARD APPLICATION
# ==========================================
def main():
    col_title, col_live = st.columns([6, 1])
    with col_title: st.title("QUANT LAB // V5.1 ADX SHIELD")
    with col_live: st.markdown("<div style='background-color: #00FFFF; color: #0A0A0A; padding: 5px; border-radius: 5px; text-align: center; margin-top: 25px;'>LAB MODE</div>", unsafe_allow_html=True)
    st.divider()

    with st.sidebar:
        st.markdown("### `[1. VARLIK & ZAMAN]`")
        symbol = st.selectbox("Varlık (Asset)", ["TSLA", "BTC-USD", "ETH-USD", "NVDA", "AAPL"])
        timeframe = st.selectbox("Zaman Dilimi", ["15m (15 Dakika)", "1h (Saatlik)", "1d (Günlük)"])
        
        tf_code = timeframe.split(" ")[0]
        period_map = {"1d": "5y", "1h": "730d", "15m": "60d"}
