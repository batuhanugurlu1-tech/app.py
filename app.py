import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import time

# ==========================================
# UI & THEME CONFIGURATION
# ==========================================
st.set_page_config(page_title="QUANT LAB V5.3", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; }
    .quant-card { background-color: #1A1D23; border-radius: 10px; padding: 20px; border: 1px solid #333333; margin-bottom: 20px; }
    .stButton>button { background-color: #00FF41 !important; color: #0A0A0A !important; font-weight: bold; width: 100%; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SCIENTIFIC CALCULATION ENGINE
# ==========================================
def calculate_indicators(df, fast_ema, slow_ema):
    try:
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
        
        # ADX (14)
        up_move = df['High'] - df['High'].shift(1)
        down_move = df['Low'].shift(1) - df['Low']
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
        minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        df['ADX_14'] = dx.fillna(0).ewm(alpha=1/14, adjust=False).mean()
        return df
    except Exception as e:
        st.error(f"Hesaplama hatası: {e}")
        return df

def run_backtest(df, target_trades, fast_ema, slow_ema, adx_threshold):
    trades = []
    in_position = False
    entry_price, sl, tp = 0, 0, 0
    pos_type = ""
    ema_f, ema_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    for i in range(slow_ema, len(df)):
        if len(trades) >= target_trades: break
        curr, prev = df.iloc[i], df.iloc[i-1]

        if not in_position:
            # LONG
            if prev[ema_f] <= prev[ema_s] and curr[ema_f] > curr[ema_s] and curr['RSI_14'] > 50 and curr['ADX_14'] >= adx_threshold:
                in_position, pos_type, entry_price = True, "LONG", curr['Close']
                sl, tp = entry_price - (curr['ATR_14']*2), entry_price + (curr['ATR_14']*4)
            # SHORT
            elif prev[ema_f] >= prev[ema_s] and curr[ema_f] < curr[ema_s] and curr['RSI_14'] < 50 and curr['ADX_14'] >= adx_threshold:
                in_position, pos_type, entry_price = True, "SHORT", curr['Close']
                sl, tp = entry_price + (curr['ATR_14']*2), entry_price - (curr['ATR_14']*4)
        else:
            res = ""
            if pos_type == "LONG":
                if curr['Low'] <= sl: res, exit_p = "LOSS", sl
                elif curr['High'] >= tp: res, exit_p = "WIN", tp
            else:
                if curr['High'] >= sl: res, exit_p = "LOSS", sl
                elif curr['Low'] <= tp: res, exit_p = "WIN", tp
            
            if res:
                pnl = ((exit_p - entry_price) / entry_price) * 100 if pos_type == "LONG" else ((entry_price - exit_p) / entry_price) * 100
                trades.append({"Date": df.index[i], "Type": pos_type, "Result": res, "PnL_%": pnl})
                in_position = False
    return pd.DataFrame(trades)

# ==========================================
# MAIN APP
# ==========================================
def main():
    st.title("QUANT LAB V5.3 // STABLE")
    
    with st.sidebar:
        st.header("SİSTEM AYARLARI")
        symbol = st.selectbox("Varlık", ["TSLA", "BTC-USD", "NVDA", "AAPL"])
        timeframe = st.selectbox("Zaman Dilimi", ["15m", "1h", "1d"])
        fast_ema = st.slider("Hızlı EMA", 5, 50, 5)
        slow_ema = st.slider("Yavaş EMA", 10, 200, 13)
        adx_thresh = st.slider("ADX Filtresi", 0, 40, 15)
        trade_count = st.number_input("Hedef İşlem", 5, 100, 20)
        run = st.button("TESTİ BAŞLAT")

    if run:
        try:
            p_map = {"1d": "5y", "1h": "730d", "15m": "60d"}
            with st.spinner("Veri çekiliyor..."):
                df = yf.download(symbol, period=p_map[timeframe], interval=timeframe, progress=False)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                df.dropna(inplace=True)
            
            if df.empty:
                st.error("Veri alınamadı. Lütfen varlığı veya zaman dilimini değiştirin.")
                return

            df = calculate_indicators(df, fast_ema, slow_ema)
            results = run_backtest(df, trade_count, fast_ema, slow_ema, adx_thresh)

            if results.empty:
                st.warning("Bu şartlarda işlem bulunamadı. Filtreyi gevşetin.")
            else:
                win_rate = (len(results[results['Result']=='WIN']) / len(results)) * 100
                pnl = results['PnL_%'].sum()
                
                c1, c2, c3 = st.columns(3)
                c1.metric("İşlem Sayısı", len(results))
                c2.metric("Win Rate", f"%{win_rate:.1f}")
                c3.metric("Toplam PnL", f"%{pnl:.2f}")
                
                st.code(f"RAPOR: {symbol} | WR: %{win_rate:.1f} | PnL: %{pnl:.2f}")
                st.table(results)
        except Exception as e:
            st.error(f"Sistem Hatası: {e}")

if __name__ == "__main__":
    main()
```
eof

### 3. Adım: `requirements.txt` Kontrolü
Dosyanın içinde şu satırların olduğundan emin ol (fazlasına gerek yok):
```text
streamlit
pandas
numpy
yfinance
