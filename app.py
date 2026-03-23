import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# UI & THEME CONFIGURATION
# ==========================================
st.set_page_config(page_title="QUANT LAB // STRATEGY TESTER", layout="wide")

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
    
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    df['ATR_14'] = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).ewm(alpha=1/14, adjust=False).mean()
    return df

# ==========================================
# AUTO-TRADE BACKTEST SIMULATOR
# ==========================================
def run_backtest(df: pd.DataFrame, target_trades: int, fast_ema: int, slow_ema: int):
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
            if prev[ema_f] <= prev[ema_s] and current[ema_f] > current[ema_s] and current['RSI_14'] > 50:
                in_position = True; pos_type = "LONG"; entry_price = current['Close']
                sl = entry_price - (current['ATR_14'] * 2)
                tp = entry_price + (current['ATR_14'] * 4) 
            elif prev[ema_f] >= prev[ema_s] and current[ema_f] < current[ema_s] and current['RSI_14'] < 50:
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
    with col_title: st.title("QUANT LAB // STRATEGY TESTER")
    with col_live: st.markdown("<div style='background-color: #00FFFF; color: #0A0A0A; padding: 5px; border-radius: 5px; text-align: center; margin-top: 25px;'>LAB MODE</div>", unsafe_allow_html=True)
    st.divider()

    with st.sidebar:
        st.markdown("### `[1. VARLIK & ZAMAN]`")
        symbol = st.selectbox("Varlık (Asset)", ["BTC-USD", "ETH-USD", "NVDA", "TSLA", "AAPL"])
        timeframe = st.selectbox("Zaman Dilimi", ["1d (Günlük)", "1h (Saatlik)", "15m (15 Dakika)"])
        
        tf_code = timeframe.split(" ")[0]
        period_map = {"1d": "5y", "1h": "730d", "15m": "60d"}
        period = period_map[tf_code]
        
        st.markdown("### `[2. STRATEJİ KURALLARI]`")
        fast_ema = st.slider("Hızlı EMA (Trend Girişi)", min_value=5, max_value=50, value=9)
        slow_ema = st.slider("Yavaş EMA (Ana Trend)", min_value=10, max_value=200, value=21)
        trade_count = st.number_input("Hedef İşlem Sayısı", min_value=5, max_value=100, value=20)
        
        st.divider()
        run_sim = st.button(">> TESTİ BAŞLAT")

    if run_sim:
        if fast_ema >= slow_ema:
            st.error("⚠️ Hızlı EMA değeri, Yavaş EMA değerinden küçük olmalıdır!")
            return
            
        with st.spinner(f"Kuantum motoru çalışıyor... {symbol} için {tf_code} grafik verileri indiriliyor ve taranıyor..."):
            try:
                df = yf.download(symbol, period=period, interval=tf_code, progress=False)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                df.dropna(inplace=True); df = calculate_indicators(df, fast_ema, slow_ema)
                
                trades_df = run_backtest(df, target_trades=trade_count, fast_ema=fast_ema, slow_ema=slow_ema)
                
                if trades_df.empty:
                    st.warning("Seçilen zaman diliminde bu EMA ayarlarına uygun kesişim bulunamadı.")
                    return
                
                total_trades = len(trades_df)
                wins = len(trades_df[trades_df['Result'] == 'WIN'])
                losses = len(trades_df[trades_df['Result'] == 'LOSS'])
                win_rate = (wins / total_trades) * 100
                total_pnl = trades_df['PnL_%'].sum()
                
                st.markdown("### `[LABORATUVAR SONUÇLARI]`")
                c1, c2, c3, c4 = st.columns(4)
                wr_color = "#00FF41" if win_rate >= 50 else "#FF003C"
                pnl_color = "#00FF41" if total_pnl > 0 else "#FF003C"
                
                with c1: st.markdown(f"<div class='quant-card'><h5>TOTAL TRADE</h5><h2>{total_trades}</h2></div>", unsafe_allow_html=True)
                with c2: st.markdown(f"<div class='quant-card'><h5>WIN / LOSS</h5><h2><span style='color:#00FF41'>{wins}</span> / <span style='color:#FF003C'>{losses}</span></h2></div>", unsafe_allow_html=True)
                with c3: st.markdown(f"<div class='quant-card'><h5>WIN RATE</h5><h2 style='color:{wr_color};'>{win_rate:.1f}%</h2></div>", unsafe_allow_html=True)
                with c4: st.markdown(f"<div class='quant-card'><h5>TOTAL NET KÂR (%)</h5><h2 style='color:{pnl_color};'>{total_pnl:.2f}%</h2></div>", unsafe_allow_html=True)

                # --- YENİ EKLENEN KOPYALAMA MODÜLÜ ---
                st.markdown("### `[HIZLI RAPOR DIŞA AKTARIMI]`")
                st.caption("Aşağıdaki kutunun sağ üst köşesindeki ikon ile sonuçları kopyalayıp analiste yapıştırın.")
                report_text = f"""📊 QUANT LAB BACKTEST RAPORU
Varlık: {symbol} | Zaman Dilimi: {timeframe}
Strateji: EMA {fast_ema} / EMA {slow_ema} Kesişimi
Hedef İşlem: {trade_count}
-----------------------------------
Gerçekleşen İşlem: {total_trades}
Win/Loss: {wins}W / {losses}L
Kazanma Oranı (Win Rate): %{win_rate:.1f}
Toplam Net Kâr (PnL): %{total_pnl:.2f}
-----------------------------------"""
                st.code(report_text, language="markdown")
                # ------------------------------------

                st.markdown("### `[DETAYLI İŞLEM GEÇMİŞİ]`")
                def color_result(val):
                    color = '#00FF41' if val == 'WIN' else '#FF003C'
                    return f'color: {color}; font-weight: bold;'
                st.dataframe(trades_df.style.map(color_result, subset=['Result']), use_container_width=True)

            except Exception as e:
                st.error(f"Sistem Hatası: {str(e)}")
    else:
        st.info("👈 Yan menüden zaman dilimini ve EMA değerlerini ayarlayıp testi başlatın.")

if __name__ == "__main__":
    main()
