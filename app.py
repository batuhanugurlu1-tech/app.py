import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from google import genai
import os

# ==========================================
# UI & THEME CONFIGURATION (Modern SaaS UI - image_0.png style)
# ==========================================
st.set_page_config(page_title="QUANTTRADER PRO // AI DASHBOARD", layout="wide")

st.markdown("""
    <style>
    /* Modern SaaS Dark Theme (image_0.png Style) */
    .stApp { background-color: #0E1117; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    h1, h2, h3, h4, h5, h6, p, div, label, span { color: #FFFFFF !important; }
    
    /* Modern Card Styling */
    .quant-card {
        background-color: #1A1D23;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        border: 1px solid #333333;
        margin-bottom: 20px;
    }
    
    /* Vurgu Renkleri */
    .color-long { color: #00FF41 !important; font-weight: bold; } /* Matrix Yeşil */
    .color-short { color: #FF003C !important; font-weight: bold; } /* Kırmızı */
    
    /* Button & Inputs style */
    .stButton>button { 
        background-color: #00FF41 !important; 
        color: #0A0A0A !important; 
        border-radius: 5px !important; 
        font-weight: bold; 
        transition: all 0.2s ease;
    }
    .stButton>button:hover { background-color: #A3FFA3 !important; }
    .stTextInput>div>div>input { background-color: #1A1D23 !important; color: #FFFFFF !important; }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)


# ==========================================
# SCIENTIFIC CALCULATION ENGINE (Quant Methods)
# ==========================================
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Fiyat serileri üzerinden teknik ve istatistiksel metrikleri hesaplar."""
    if df.empty or len(df) < 200: return df

    # EMA, RSI, MACD ve ATR hesaplamaları (Önceki kodla aynı)
    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

    # Golden/Death Cross Sinyalleri
    df['Signal'] = 0
    df.loc[(df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1)), 'Signal'] = 1 # Golden
    df.loc[(df['EMA_50'] < df['EMA_200']) & (df['EMA_50'].shift(1) >= df['EMA_200'].shift(1)), 'Signal'] = -1 # Death

    # RSI (14) - Momentum
    delta = df['Close'].diff(); gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean(); df['RSI_14'] = 100 - (100 / (1 + (gain / loss)))

    # MACD (12, 26, 9) - Trend
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean(); ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26; df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean(); df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # ATR (14) - Volatilite
    df['ATR_14'] = pd.concat([df['High']-df['Low'], np.abs(df['High']-df['Close'].shift()), np.abs(df['Low']-df['Close'].shift())], axis=1).max(axis=1).ewm(alpha=1/14, adjust=False).mean()

    return df

def calculate_position_size(balance: float, risk_pct: float, entry_price: float, stop_loss: float) -> dict:
    """Fixed Fractional risk yönetimi hesaplaması."""
    if entry_price == stop_loss: return {"size_units": 0, "risk_amount": 0}
    risk_amount = balance * (risk_pct / 100)
    risk_per_unit = abs(entry_price - stop_loss)
    size_units = risk_amount / risk_per_unit
    return {"size_units": size_units, "size_usd": size_units * entry_price, "risk_amount": risk_amount}

def get_market_table_data(assets: list) -> pd.DataFrame:
    """Ana market varlıkları için gerçek zamanlı teknik veri çeker."""
    data = []
    for asset in assets:
        try:
            ticker = yf.Ticker(asset)
            df = ticker.history(period="1y", interval="1d", progress=False)
            if df.empty or len(df) < 50: continue
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Trend belirleme (Örnek mantık: EMA 20 vs 50)
            trend = "BULLISH" if latest['EMA_20'] > latest['EMA_50'] else "BEARISH"
            trend_color = "#00FF41" if trend == "BULLISH" else "#FF003C"
            
            # MACD yönü
            macd_dir = "up" if latest['MACD_Hist'] > 0 else "dn"

            data.append({
                "Asset": asset,
                "Fiyat": f"${latest['Close']:.2f}",
                "Deg": f"{((latest['Close'] - prev['Close'])/prev['Close'])*100:.2f}%",
                "RSI": latest['RSI_14'],
                "Trend": f"<span style='color: {trend_color}; font-weight: bold;'>{trend}</span>",
                "MACD": f"{macd_dir} {latest['MACD_Hist']:.4f}"
            })
        except: continue
    return pd.DataFrame(data)

# ==========================================
# AI REASONING MODULE (Gemini GenAI SDK)
# ==========================================
def generate_ai_thesis(api_key: str, symbol: str, current_data: dict) -> str:
    """Yapay Zeka üzerinden 'Chain of Thought' prensibiyle yatırım tezi üretir."""
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""You are a Quant Analist. Use this {symbol} data for a CoT analysis. CURRENT PRICE: {current_data['price']:.2f}, RSI: {current_data['rsi']:.2f}, MACD Hist: {current_data['macd_hist']:.2f}, ATR: {current_data['atr']:.2f}, EMA status: {'above' if current_data['price'] > current_data['ema_20'] else 'below'} EMA 20, EMA 200: {current_data['ema_200']:.2f}. Create a brief, 3-point thesis: 1. Teknik Durum, 2. Risk Faktörü, 3. Aksiyon Planı (Al/Bekle/Sat). Must be in Turkish and professional language."""
        return client.models.generate_content(model='gemini-2.5-flash', contents=prompt).text
    except Exception as e: return f"AI Analizi başarısız oldu. Hata: {str(e)}"


# ==========================================
# MAIN DASHBOARD APPLICATION
# ==========================================
def main():
    # Sayfa Başlığı ve 'CANLI' İndikatörü
    col_title, col_live = st.columns([6, 1])
    with col_title: st.title("QUANTTRADER PRO // AI DASHBOARD")
    with col_live: 
        st.markdown("<div style='background-color: #00FF41; color: #0A0A0A; padding: 5px 10px; border-radius: 5px; font-weight: bold; text-align: center; margin-top: 25px;'>CANLI</div>", unsafe_allow_html=True)

    st.divider()

    # Sidebar: Sistem Girişleri
    with st.sidebar:
        st.markdown("### `[SISTEM PARAMS]`")
        api_key = st.text_input("Gemini API Key", type="password", help="AI Analizi için Google GenAI API anahtarı")
        st.divider()
        symbol = st.selectbox("Asset (Varlık)", ["BTC-USD", "ETH-USD", "NVDA", "TSLA", "AAPL"])
        st.caption("Grafik ve AI analizi için seçilen varlık.")
        
        st.markdown("### `[RISK MANAGEMENT]`")
        risk_balance = 10000.0 # Örnek bakiye
        st.slider("Trade Başına Risk (%)", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
        st.number_input("ATR Çarpanı (Stop Loss)", min_value=1.0, value=2.0, step=0.5)
        st.divider()
        if st.button(">> INITIALIZE AI ANALYSIS"):
             st.rerun() # AI butonunun tetiklenmesini kolaylaştırır.

    # Veri Çekme (Ana Varlık)
    try:
        df = yf.download(symbol, period="2y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
        df.dropna(inplace=True); df = calculate_indicators(df); latest = df.iloc[-1]; prev = df.iloc[-2]
    exceptException as e: st.error(f"Veri hatası: {str(e)}"); return

    # ==========================================
    # SATIR 1: Portföy Özeti, Nakit, Win Rate
    # ==========================================
    st.markdown("<h3 style='margin-bottom: 0;'>Piyasa Özeti (Örnek Portföy)</h3>", unsafe_allow_html=True)
    row1_col1, row1_col2, row1_col3 = st.columns(3)
    
    with row1_col1: # PORTFÖY
        change = (2.34 / 9997.66) * 100 # Örnek veri
        st.markdown(f"<div class='quant-card'><h5 style='color: #888888;'>PORTFÖY</h5><h2 style='margin: 0;'>$9,997.66</h2><p class='color-short'>$-2.34 (-{change:.2f}%)</p></div>", unsafe_allow_html=True)
        
    with row1_col2: # NAKİT
        st.markdown(f"<div class='quant-card'><h5 style='color: #888888;'>NAKİT</h5><h2 style='margin: 0;'>$8,997.89</h2><p style='margin:0;'>1 pozisyon</p></div>", unsafe_allow_html=True)
        
    with row1_col3: # WIN RATE
        st.markdown(f"<div class='quant-card'><h5 style='color: #888888;'>WIN RATE</h5><h2 style='margin: 0; color: #00FF41 !important;'>38%</h2><p style='margin:0;'>PF: 0.85 (8 trade)</p></div>", unsafe_allow_html=True)

    # ==========================================
    # SATIR 2: Açık/Potansiyel Pozisyonlar, Sinyaller
    # ==========================================
    st.markdown("### `[POZİSYONLAR & AI SINYALLERI]`")
    row2_col1, row2_col2 = st.columns([2, 1])

    with row2_col1: # Açık/Potansiyel Pozisyonlar Tablosu
        st.markdown("<div class='quant-card'>", unsafe_allow_html=True)
        st.markdown("#### Potansiyel Pozisyon Detayları")
        
        # Dinamik Risk Analizi (Önceki kodla aynı)
        entry = latest['Close']; trend_up = latest['Close'] > latest['EMA_200']
        if trend_up: sl = entry - (latest['ATR_14']*2); tp = entry + (abs(entry-sl)*2); dir = "LONG"; col = "color-long"
        else: sl = entry + (latest['ATR_14']*2); tp = entry - (abs(entry-sl)*2); dir = "SHORT"; col = "color-short"
        
        # Örnek Pozisyon Tablosu
        pos_data = {
            "Asset": [symbol],
            "Yön": [f"<span class='{col}'>{dir}</span>"],
            "Giriş": [f"${entry:.2f}"],
            "SL": [f"${sl:.2f}"],
            "TP": [f"${tp:.2f}"],
            "AI Skoru": ["82%"], # Örnek veri
            "Güven": ["Yüksek"]   # Örnek veri
        }
        pos_df = pd.DataFrame(pos_data)
        st.write(pos_df.to_html(escape=False, index=False), unsafe_allow_html=True)
        st.caption("*Based on Price vs EMA 200 and ATR Volatility.*")
        st.markdown("</div>", unsafe_allow_html=True)

    with row2_col2: # Sinyaller ve AI Tez
        st.markdown("<div class='quant-card' style='height: 100%;'>", unsafe_allow_html=True)
        st.markdown("#### `[AI TEZ ODASI]`")
        
        if api_key and 'AI_INIT' in st.session_state:
             current_data_dict = {'price': latest['Close'], 'rsi': latest['RSI_14'], 'macd_hist': latest['MACD_Hist'], 'atr': latest['ATR_14'], 'ema_20': latest['EMA_20'], 'ema_200': latest['EMA_200']}
             ai_response = generate_ai_thesis(api_key, symbol, current_data_dict)
             st.markdown(f"<div style='border-left: 2px solid #00FF41; padding-left: 10px; color: #CCCCCC;'>{ai_response}</div>", unsafe_allow_html=True)
        elif not api_key:
             st.warning("AI analizi için API Key girin ve 'Initialize' butonuna basın.")
        else:
             st.caption("AI analizi henüz başlatılmadı.")
             if st.button(">> START AI SINYAL TARAMA", key='ai_start_btn'):
                 st.session_state['AI_INIT'] = True
                 st.rerun()
                 
        st.markdown("</div>", unsafe_allow_html=True)

    # ==========================================
    # SATIR 3: Market Tablosu, Gelişmiş Grafik
    # ==========================================
    st.markdown("### `[MARKET GÖRÜNÜMÜ & GRAFİK ANALİZİ]`")
    row3_col_table, row3_col_chart = st.columns([1, 1.8])

    with row3_col_table: # Market Takip Tablosu
        st.markdown("<div class='quant-card'>", unsafe_allow_html=True)
        st.markdown("#### MARKET")
        market_assets = ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "BTC-USD"]
        with st.spinner("Market verileri senkronize ediliyor..."):
             market_df = get_market_table_data(market_assets)
             st.write(market_df.to_html(escape=False, index=False), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with row3_col_chart: # Candlestick Grafik
        st.markdown("<div class='quant-card'>", unsafe_allow_html=True)
        st.markdown(f"#### `{symbol}` Fiyat Hareketi ve EMA Dinamikleri")
        fig = go.Figure()
        # Candlestick
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='Price', increasing_line_color='#00FF41', decreasing_line_color='#FF003C'))
        # EMAs
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_20'], line=dict(color='#00FFFF', width=1), name='EMA 20'))
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_50'], line=dict(color='#FF00FF', width=1), name='EMA 50'))
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_200'], line=dict(color='#FFA500', width=1.5), name='EMA 200'))
        
        fig.update_layout(template='plotly_dark', paper_bgcolor='#1A1D23', plot_bgcolor='#1A1D23', margin=dict(l=0, r=0, t=20, b=0), xaxis_rangeslider_visible=False, height=500)
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
