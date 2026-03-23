import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# ==========================================
# UI CONFIGURATION (Modern SaaS Style)
# ==========================================
st.set_page_config(page_title="QUANT LAB V5.6 // STABLE", layout="wide")

st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .quant-card { background-color: #1A1D23; border-radius: 10px; padding: 20px; border: 1px solid #333333; margin-bottom: 20px; }
    .stButton>button { background-color: #00FF41 !important; color: #0A0A0A !important; font-weight: bold; width: 100%; border-radius: 5px; border: none; }
    .stButton>button:hover { background-color: #A3FFA3 !important; }
    h1, h2, h3, h4, h5 { color: #FFFFFF !important; }
    .stMetric { background-color: #1A1D23; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SCIENTIFIC CALCULATION ENGINE
# ==========================================
def calculate_indicators(df, fast_ema, slow_ema):
    if df.empty or len(df) < slow_ema: return df
    
    # EMA Hesaplamaları
    df[f'EMA_{fast_ema}'] = df['Close'].ewm(span=fast_ema, adjust=False).mean()
    df[f'EMA_{slow_ema}'] = df['Close'].ewm(span=slow_ema, adjust=False).mean()
    
    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))
    
    # ATR (14) - Volatilite Ölçümü
    tr = pd.concat([df['High']-df['Low'], (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    df['ATR_14'] = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # ADX (14) - Trend Gücü Filtresi
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / df['ATR_14'])
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df['ADX_14'] = dx.fillna(0).ewm(alpha=1/14, adjust=False).mean()
    return df

def run_backtest(df, target_trades, fast_ema, slow_ema, adx_threshold):
    trades = []
    in_pos, entry_p, sl, tp, p_type = False, 0, 0, 0, ""
    e_f, e_s = f'EMA_{fast_ema}', f'EMA_{slow_ema}'

    for i in range(slow_ema, len(df)):
        if len(trades) >= target_trades: break
        c, p = df.iloc[i], df.iloc[i-1]

        if not in_pos:
            # GİRİŞ ŞARTLARI
            if p[e_f] <= p[e_s] and c[e_f] > c[e_s] and c['RSI_14'] > 50 and c['ADX_14'] >= adx_threshold:
                in_pos, p_type, entry_p = True, "LONG", c['Close']
                sl, tp = entry_p - (c['ATR_14']*2), entry_p + (c['ATR_14']*4)
            elif p[e_f] >= p[e_s] and c[e_f] < c[e_s] and c['RSI_14'] < 50 and c['ADX_14'] >= adx_threshold:
                in_pos, p_type, entry_p = True, "SHORT", c['Close']
                sl, tp = entry_p + (c['ATR_14']*2), entry_p - (c['ATR_14']*4)
        else:
            # ÇIKIŞ ŞARTLARI
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
                    "Tarih": df.index[i].strftime('%Y-%m-%d %H:%M'), 
                    "Tip": p_type, 
                    "Sonuç": res, 
                    "PnL_%": round(pnl, 2)
                })
                in_pos = False
    return pd.DataFrame(trades)

# ==========================================
# MAIN APPLICATION
# ==========================================
def main():
    st.title("QUANT LAB V5.6 // BACKTEST ENGINE")
    
    with st.sidebar:
        st.header("STRATEJİ AYARLARI")
        sym = st.selectbox("Varlık Seçin", ["TSLA", "BTC-USD", "NVDA", "AAPL", "ETH-USD"])
        tf = st.selectbox("Zaman Dilimi", ["15m", "1h", "1d"])
        f_ema = st.slider("Hızlı EMA", 5, 50, 5)
        s_ema = st.slider("Yavaş EMA", 10, 200, 13)
        adx_t = st.slider("ADX Filtresi (Trend Gücü)", 0, 40, 15)
        t_count = st.number_input("İşlem Hedefi", 5, 100, 20)
        st.divider()
        btn = st.button("DENEYİ BAŞLAT")

    if btn:
        p_map = {"1d": "5y", "1h": "730d", "15m": "60d"}
        try:
            with st.spinner(f"{sym} verileri analiz ediliyor..."):
                df = yf.download(sym, period=p_map[tf], interval=tf, progress=False)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                df.dropna(inplace=True)
                
                if df.empty:
                    st.error("Veri çekilemedi. Lütfen tekrar deneyin.")
                    return

                df = calculate_indicators(df, f_ema, s_ema)
                results = run_backtest(df, t_count, f_ema, s_ema, adx_t)

                if results.empty:
                    st.warning("Bu ayarlarla uygun işlem bulunamadı. ADX filtresini gevşetmeyi deneyin.")
                else:
                    # İstatistikler
                    wins = len(results[results['Sonuç']=='WIN'])
                    wr = (wins / len(results)) * 100
                    pnl = results['PnL_%'].sum()
                    
                    st.markdown("### `[LABORATUVAR RAPORU]`")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("İşlem Sayısı", len(results))
                    c2.metric("Win Rate", f"%{wr:.1f}")
                    c3.metric("Net PnL", f"%{pnl:.2f}")
                    
                    st.divider()
                    st.markdown("#### Detaylı İşlem Günlüğü")
                    # Tabloyu renklendirme
                    def highlight(val):
                        color = '#00FF41' if val == 'WIN' else '#FF003C'
                        return f'color: {color}; font-weight: bold;'
                    
                    st.table(results.style.applymap(highlight, subset=['Sonuç']))
                    
                    # Rapor Dışa Aktarımı
                    st.markdown("#### Hızlı Kopyala")
                    report_text = f"VARLIK: {sym} | TF: {tf} | EMA: {f_ema}/{s_ema} | ADX: >{adx_t}\n"
                    report_text += f"TOTAL: {len(results)} | WR: %{wr:.1f} | PnL: %{pnl:.2f}"
                    st.code(report_text)
                    
        except Exception as e:
            st.error(f"Sistem Hatası: {e}")
    else:
        st.info("👈 Yan menüden ayarları yapıp deneyi başlatın. Tesla için 15m, 5/13 EMA ve 15 ADX önerilir.")

if __name__ == "__main__":
    main()
