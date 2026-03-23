import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# ==========================================
# UI & THEME CONFIGURATION (Modern SaaS UI)
# ==========================================
st.set_page_config(page_title="QUANTTRADER AI // AUTO-BOT", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    h1, h2, h3, h4, h5, h6, p, div, label, span { color: #FFFFFF !important; }
    .quant-card { background-color: #1A1D23; border-radius: 10px; padding: 20px; border: 1px solid #333333; margin-bottom: 20px; }
    .color-long { color: #00FF41 !important; font-weight: bold; }
    .color-short { color: #FF003C !important; font-weight: bold; }
    .stButton>button { background-color: #00FF41 !important; color: #0A0A0A !important; font-weight: bold; width: 100%; border-radius: 5px; }
    .stButton>button:hover { background-color: #A3FFA3 !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SCIENTIFIC CALCULATION ENGINE
# ==========================================
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 200: return df
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    df['ATR_14'] = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).ewm(alpha=1/14, adjust=False).mean()
    return df

# ==========================================
# AUTO-TRADE BACKTEST SIMULATOR
# ==========================================
def run_backtest(df: pd.DataFrame, target_trades: int = 20):
    """Geçmiş veriler üzerinde botun stratejisini simüle eder."""
    in_position = False
    entry_price = 0.0
    sl = 0.0
    tp = 0.0
    pos_type = ""
    
    trades = []
    
    for i in range(200, len(df)):
        if len(trades) >= target_trades:
            break
            
        current = df.iloc[i]
        prev = df.iloc[i-1]
        
        # EĞER POZİSYONDA DEĞİLSEK (GİRİŞ ARARIZ)
        if not in_position:
            # LONG Şartı: EMA50, EMA200'ü yukarı kesti ve RSI > 50
            if prev['EMA_50'] <= prev['EMA_200'] and current['EMA_50'] > current['EMA_200'] and current['RSI_14'] > 50:
                in_position = True; pos_type = "LONG"; entry_price = current['Close']
                sl = entry_price - (current['ATR_14'] * 2)
                tp = entry_price + (current['ATR_14'] * 4) # 1:2 Risk/Reward
            
            # SHORT Şartı: EMA50, EMA200'ü aşağı kesti ve RSI < 50
            elif prev['EMA_50'] >= prev['EMA_200'] and current['EMA_50'] < current['EMA_200'] and current['RSI_14'] < 50:
                in_position = True; pos_type = "SHORT"; entry_price = current['Close']
                sl = entry_price + (current['ATR_14'] * 2)
                tp = entry_price - (current['ATR_14'] * 4) # 1:2 Risk/Reward
                
        # EĞER POZİSYONDAYSAK (ÇIKIŞ ARARIZ)
        else:
            exit_price = 0.0
            result = ""
            
            if pos_type == "LONG":
                if current['Low'] <= sl: exit_price = sl; result = "LOSS"
                elif current['High'] >= tp: exit_price = tp; result = "WIN"
            elif pos_type == "SHORT":
                if current['High'] >= sl: exit_price = sl; result = "LOSS"
                elif current['Low'] <= tp: exit_price = tp; result = "WIN"
                
            if result != "":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if pos_type == "LONG" else ((entry_price - exit_price) / entry_price) * 100
                trades.append({
                    "Date": df.index[i].strftime("%Y-%m-%d"),
                    "Type": pos_type,
                    "Entry": entry_price,
                    "Exit": exit_price,
                    "Result": result,
                    "PnL_%": pnl_pct
                })
                in_position = False # Pozisyonu kapat
                
    return pd.DataFrame(trades)

# ==========================================
# MAIN DASHBOARD APPLICATION
# ==========================================
def main():
    col_title, col_live = st.columns([6, 1])
    with col_title: st.title("QUANT AUTO-BOT // BACKTEST ENGINE")
    with col_live: 
        st.markdown("<div style='background-color: #FFA500; color: #0A0A0A; padding: 5px; border-radius: 5px; text-align: center; margin-top: 25px;'>SIMULATION MODE</div>", unsafe_allow_html=True)

    st.divider()

    with st.sidebar:
        st.markdown("### `[BOT CONFIG]`")
        symbol = st.selectbox("Test Varlığı", ["BTC-USD", "ETH-USD", "NVDA", "TSLA", "AAPL"])
        trade_count = st.number_input("Test Edilecek İşlem Sayısı", min_value=5, max_value=50, value=20)
        st.caption("Strateji: EMA 50/200 Cross + RSI + 1:2 RR Dinamik ATR Stop.")
        st.divider()
        run_sim = st.button(">> BOTU TEST ET")

    if run_sim:
        with st.spinner(f"{symbol} geçmiş verileri taranıyor ve bot işlemleri simüle ediliyor..."):
            try:
                # Günlük veriyi 5 yıllık çekiyoruz ki yeterince kesişim bulabilelim
                df = yf.download(symbol, period="5y", interval="1d", progress=False)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                df.dropna(inplace=True); df = calculate_indicators(df)
                
                trades_df = run_backtest(df, target_trades=trade_count)
                
                if trades_df.empty:
                    st.warning("Seçilen zaman diliminde bot işlem şartlarını sağlayan formasyon bulamadı.")
                    return
                
                # İstatistikleri Hesapla
                total_trades = len(trades_df)
                wins = len(trades_df[trades_df['Result'] == 'WIN'])
                losses = len(trades_df[trades_df['Result'] == 'LOSS'])
                win_rate = (wins / total_trades) * 100
                total_pnl = trades_df['PnL_%'].sum()
                
                st.markdown("### `[TEST SONUÇLARI]`")
                c1, c2, c3, c4 = st.columns(4)
                
                wr_color = "#00FF41" if win_rate >= 50 else "#FF003C"
                pnl_color = "#00FF41" if total_pnl > 0 else "#FF003C"
                
                with c1: st.markdown(f"<div class='quant-card'><h5>TOTAL TRADE</h5><h2>{total_trades}</h2></div>", unsafe_allow_html=True)
                with c2: st.markdown(f"<div class='quant-card'><h5>WIN / LOSS</h5><h2><span style='color:#00FF41'>{wins}</span> / <span style='color:#FF003C'>{losses}</span></h2></div>", unsafe_allow_html=True)
                with c3: st.markdown(f"<div class='quant-card'><h5>WIN RATE</h5><h2 style='color:{wr_color};'>{win_rate:.1f}%</h2></div>", unsafe_allow_html=True)
                with c4: st.markdown(f"<div class='quant-card'><h5>TOTAL NET KÂR (%)</h5><h2 style='color:{pnl_color};'>{total_pnl:.2f}%</h2></div>", unsafe_allow_html=True)

                st.markdown("### `[BOT İŞLEM GEÇMİŞİ (TRADE LOG)]`")
                # Tabloyu renklendir
                def color_result(val):
                    color = '#00FF41' if val == 'WIN' else '#FF003C'
                    return f'color: {color}; font-weight: bold;'
                
                st.dataframe(trades_df.style.map(color_result, subset=['Result']), use_container_width=True)

            except Exception as e:
                st.error(f"Simülasyon Hatası: {str(e)}")
    else:
        st.info("Sol menüden varlığı seçin ve '>> BOTU TEST ET' butonuna basarak geriye dönük test motorunu çalıştırın.")

if __name__ == "__main__":
    main()
