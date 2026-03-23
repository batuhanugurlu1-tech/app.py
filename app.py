import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from google import genai
import os

# ==========================================
# UI & THEME CONFIGURATION (Brutalist / Minimalist)
# ==========================================
st.set_page_config(page_title="Advanced QuantTrader AI", layout="wide")

st.markdown("""
    <style>
    /* Brutalist & Minimalist Dark Theme */
    .stApp { background-color: #0A0A0A; color: #FFFFFF; font-family: 'Courier New', Courier, monospace; }
    h1, h2, h3, h4, h5, h6, p, div, label, span { color: #FFFFFF !important; }
    
    /* Matrix Green Accents for interactive elements */
    .stButton>button { 
        background-color: #00FF41 !important; 
        color: #0A0A0A !important; 
        border: 2px solid #00FF41 !important;
        border-radius: 0px !important; 
        font-weight: bold; 
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        background-color: #0A0A0A !important;
        color: #00FF41 !important;
    }
    .stTextInput>div>div>input, .stSelectbox>div>div>div {
        background-color: #1A1A1A !important;
        color: #00FF41 !important;
        border: 1px solid #333333 !important;
        border-radius: 0px !important;
    }
    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)


# ==========================================
# SCIENTIFIC CALCULATION ENGINE (Quant Methods)
# ==========================================
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fiyat serileri üzerinden teknik ve istatistiksel metrikleri hesaplar.
    Hata yönetimi için verinin geçerliliği kontrol edilir.
    """
    if df.empty or len(df) < 200:
        return df # Yeterli veri yoksa işlemi atla

    # 1. Exponential Moving Averages (EMA) - Fiyatın ağırlıklı trend yönü
    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

    # Golden Cross / Death Cross Sinyalleri (50 ve 200 günlük EMA kesişimi)
    df['Signal'] = 0
    df.loc[(df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1)), 'Signal'] = 1 # Golden Cross
    df.loc[(df['EMA_50'] < df['EMA_200']) & (df['EMA_50'].shift(1) >= df['EMA_200'].shift(1)), 'Signal'] = -1 # Death Cross

    # 2. Relative Strength Index (RSI 14) - Momentum osilatörü (Wilder's Smoothing)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))

    # 3. MACD (12, 26, 9) - Trend takip eden momentum
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # 4. Average True Range (ATR 14) - Piyasa volatilitesi
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['ATR_14'] = true_range.ewm(alpha=1/14, adjust=False).mean()

    return df

def calculate_position_size(balance: float, risk_pct: float, entry_price: float, stop_loss: float) -> dict:
    """
    Fixed Fractional metodolojisi ile risk yönetimi hesaplaması.
    Sermayenin belirli bir yüzdesini tek bir işleme riske atar.
    """
    if entry_price == stop_loss:
         return {"size_units": 0, "risk_amount": 0}
         
    risk_amount = balance * (risk_pct / 100)
    risk_per_unit = abs(entry_price - stop_loss)
    size_units = risk_amount / risk_per_unit
    
    return {
        "size_units": size_units,
        "size_usd": size_units * entry_price,
        "risk_amount": risk_amount
    }

# ==========================================
# AI REASONING MODULE (Gemini GenAI SDK)
# ==========================================
def generate_ai_thesis(api_key: str, symbol: str, current_data: dict) -> str:
    """
    Nicel verileri alarak Google Gemini modeli üzerinden 
    'Chain of Thought' prensibiyle nitel bir yatırım tezi üretir.
    """
    try:
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        Sen kıdemli bir Quant Analistisin. Aşağıdaki güncel piyasa verilerini kullanarak {symbol} varlığı için 
        "Chain of Thought" (Düşünce Zinciri) metodolojisiyle mantıksal bir analiz yap.
        
        Güncel Veriler:
        - Fiyat: {current_data['price']:.2f}
        - RSI (14): {current_data['rsi']:.2f}
        - MACD Histogram: {current_data['macd_hist']:.2f}
        - ATR (14): {current_data['atr']:.2f}
        - EMA Durumu: Fiyat EMA 20'nin {'üzerinde' if current_data['price'] > current_data['ema_20'] else 'altında'}, EMA 200: {current_data['ema_200']:.2f}
        
        Lütfen analizini kesin, net, profesyonel bir dille ve SADECE şu 3 ana başlık altında formatla (Başka metin ekleme):
        
        1. Teknik Durum (Momentum ve trend okuması)
        2. Risk Faktörü (Volatilite ve tehlikeler)
        3. Aksiyon Planı (Al, Sat veya Bekle stratejisi ve gerekçesi)
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"AI Analizi başarısız oldu. Hata detayı: {str(e)}\nLütfen geçerli bir API Anahtarı girdiğinizden emin olun."


# ==========================================
# MAIN DASHBOARD APPLICATION
# ==========================================
def main():
    st.title("QUANT_TRADER // AI DASHBOARD")
    st.markdown("### `ADVANCED ALGORITHMIC ANALYSIS & RISK MANAGEMENT`")
    st.divider()

    # Sidebar: System Inputs
    with st.sidebar:
        st.markdown("### `[SYSTEM PARAMS]`")
        api_key = st.text_input("Gemini API Key", type="password", help="AI Analizi için Google GenAI API anahtarı")
        
        symbol = st.selectbox("Asset (Varlık)", ["BTC-USD", "ETH-USD", "NVDA", "TSLA", "AAPL"])
        timeframe = st.selectbox("Timeframe (Zaman Dilimi)", ["1d", "1wk", "1mo"])
        period = st.selectbox("Data Period (Veri Derinliği)", ["1y", "2y", "5y", "max"])
        
        st.markdown("### `[RISK MANAGEMENT]`")
        account_balance = st.number_input("Portfolio Balance (USD)", min_value=100.0, value=10000.0, step=1000.0)
        risk_per_trade = st.slider("Risk Per Trade (%)", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
        atr_multiplier = st.number_input("ATR Multiplier (Stop Loss)", min_value=1.0, value=2.0, step=0.5)
        reward_ratio = st.number_input("Risk/Reward Ratio", min_value=1.0, value=2.0, step=0.5)

    # Veri Çekme Katmanı
    with st.spinner(f"`{symbol}` piyasa verileri senkronize ediliyor..."):
        try:
            df = yf.download(symbol, period=period, interval=timeframe, progress=False)
            
            # yfinance MultiIndex column fix for newer versions
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
                
            df.dropna(inplace=True)
            
            if len(df) < 200:
                st.error("⚠️ İstatistiksel analiz (EMA 200 vb.) için yeterli tarihsel veri bulunamadı.")
                return
                
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
        except Exception as e:
            st.error(f"Veri bağlantı hatası: {str(e)}")
            return

    # Ana Metrikler
    col1, col2, col3, col4 = st.columns(4)
    price_change = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
    
    col1.metric("CURRENT PRICE", f"${latest['Close']:.2f}", f"{price_change:.2f}%")
    col2.metric("RSI (14)", f"{latest['RSI_14']:.2f}", "Overbought (>70)" if latest['RSI_14']>70 else "Oversold (<30)" if latest['RSI_14']<30 else "Neutral")
    col3.metric("ATR (14)", f"${latest['ATR_14']:.2f}", "Volatility Indicator")
    col4.metric("MACD HIST", f"{latest['MACD_Hist']:.2f}", "Momentum")

    st.divider()

    # Grafik ve Dinamik Seviyeler
    col_chart, col_risk = st.columns([3, 1])

    with col_chart:
        st.markdown("### `[PRICE ACTION & EMA DYNAMICS]`")
        fig = go.Figure()

        # Candlestick
        fig.add_trace(go.Candlestick(x=df.index,
                        open=df['Open'], high=df['High'],
                        low=df['Low'], close=df['Close'],
                        name='Price',
                        increasing_line_color='#00FF41', decreasing_line_color='#FF003C'))

        # EMAs
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_20'], line=dict(color='#00FFFF', width=1.5), name='EMA 20'))
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_50'], line=dict(color='#FF00FF', width=1.5), name='EMA 50'))
        fig.add_trace(go.Scatter(x=df.index, y=df['EMA_200'], line=dict(color='#FFA500', width=2), name='EMA 200'))

        # Golden/Death Cross İşaretçileri
        golden_crosses = df[df['Signal'] == 1]
        death_crosses = df[df['Signal'] == -1]
        
        fig.add_trace(go.Scatter(x=golden_crosses.index, y=golden_crosses['EMA_50'], mode='markers', 
                                 marker=dict(symbol='triangle-up', size=12, color='#00FF41'), name='Golden Cross'))
        fig.add_trace(go.Scatter(x=death_crosses.index, y=death_crosses['EMA_50'], mode='markers', 
                                 marker=dict(symbol='triangle-down', size=12, color='#FF003C'), name='Death Cross'))

        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='#0A0A0A',
            plot_bgcolor='#0A0A0A',
            margin=dict(l=0, r=0, t=30, b=0),
            xaxis_rangeslider_visible=False,
            height=600
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_risk:
        st.markdown("### `[RISK & EXECUTION]`")
        
        # Dinamik ATR tabanlı Stop Loss ve Take Profit
        trend_is_up = latest['Close'] > latest['EMA_200']
        entry = latest['Close']
        
        if trend_is_up: # Uzun (Long) senaryo
            stop_loss = entry - (latest['ATR_14'] * atr_multiplier)
            take_profit = entry + (abs(entry - stop_loss) * reward_ratio)
            pos_type = "LONG"
            color = "#00FF41"
        else: # Kısa (Short) senaryo
            stop_loss = entry + (latest['ATR_14'] * atr_multiplier)
            take_profit = entry - (abs(entry - stop_loss) * reward_ratio)
            pos_type = "SHORT"
            color = "#FF003C"

        pos_size = calculate_position_size(account_balance, risk_per_trade, entry, stop_loss)

        st.markdown(f"<h2 style='color: {color} !important; margin-bottom: 0;'>{pos_type} BIAS</h2>", unsafe_allow_html=True)
        st.caption("Based on Price vs EMA 200 relation")
        
        st.markdown(f"**Entry:** `${entry:.2f}`")
        st.markdown(f"**Stop-Loss:** `${stop_loss:.2f}` *(ATR Based)*")
        st.markdown(f"**Take-Profit:** `${take_profit:.2f}` *(1:{reward_ratio} RR)*")
        
        st.divider()
        st.markdown("#### Position Sizing")
        st.markdown(f"**Max Risk:** `${pos_size['risk_amount']:.2f}`")
        st.markdown(f"**Position Size:** `${pos_size['size_usd']:.2f}`")
        st.markdown(f"**Units:** `{pos_size['size_units']:.6f}`")

    st.divider()

    # AI Reasoning Katmanı
    st.markdown("### `[AI THESIS GENERATION (CHAIN OF THOUGHT)]`")
    if st.button(">> INITIALIZE AI NEURAL ANALYSIS"):
        if not api_key:
            st.warning("Erişim reddedildi. Lütfen yan menüden bir API Key girin.")
        else:
            with st.spinner("Kuantum analiz ağı çalıştırılıyor... Düşünce zinciri oluşturuluyor..."):
                current_data_dict = {
                    'price': latest['Close'],
                    'rsi': latest['RSI_14'],
                    'macd_hist': latest['MACD_Hist'],
                    'atr': latest['ATR_14'],
                    'ema_20': latest['EMA_20'],
                    'ema_200': latest['EMA_200']
                }
                
                ai_response = generate_ai_thesis(api_key, symbol, current_data_dict)
                
                st.markdown("<div style='border-left: 3px solid #00FF41; padding-left: 15px; background-color: #111111; padding-top: 10px; padding-bottom: 10px;'>", unsafe_allow_html=True)
                st.markdown(ai_response)
                st.markdown("</div>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
