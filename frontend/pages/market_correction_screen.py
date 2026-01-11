# market_correction_screen.py
import os
import pandas as pd
import yfinance as yf
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

# -------------------------------------------------
# ENV / DB
# -------------------------------------------------
load_dotenv()

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

# -------------------------------------------------
# HARD SAFETY: force scalar
# -------------------------------------------------
def safe_float(x):
    try:
        if x is None:
            return None
        if isinstance(x, (pd.Series, pd.DataFrame)):
            x = x.iloc[-1]
        return float(x)
    except Exception:
        return None

# -------------------------------------------------
# PRICE FETCH (LIVE API)
# -------------------------------------------------
def fetch_price_df(symbol, period="90d"):
    try:
        df = yf.download(
            symbol,
            period=period,
            progress=False,
            auto_adjust=True
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index).normalize()
        return df
    except Exception:
        return pd.DataFrame()

# -------------------------------------------------
# INDICATORS
# -------------------------------------------------
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd_hist(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd - signal

def calc_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(alpha=1/period, adjust=False).mean()

# -------------------------------------------------
# DB FETCH
# -------------------------------------------------
def fetch_latest_predictions():
    df = pd.read_sql("""
        SELECT stock_symbol, entry_price, target_price,
               stop_loss, trade_signal, probability_success
        FROM predictions
        ORDER BY stock_symbol, prediction_date DESC
    """, engine)

    if df.empty:
        return df

    return df.groupby("stock_symbol", as_index=False).first()

# -------------------------------------------------
# STREAMLIT SCREEN
# -------------------------------------------------
def run_screen():
    st.set_page_config(page_title="Market Correction (Close + Intraday)", layout="wide")
    st.title("📉 Market Correction — Close vs Intraday (Dynamic Lookback)")

    # ---------------- Sidebar Filters ----------------
    st.sidebar.header("Filters")

    correction_window = st.sidebar.selectbox(
        "Correction window (days)",
        [7, 15, 30, 45, 60, 90],
        index=2
    )

    min_corr = st.sidebar.slider("Min correction (%)", -30.0, -5.0, -8.0)
    max_corr = st.sidebar.slider("Max correction (%)", -30.0, -5.0, -20.0)
    rsi_min = st.sidebar.slider("RSI min", 30, 60, 40)
    max_atr_pct = st.sidebar.slider("Max ATR %", 3.0, 8.0, 5.5)
    min_rr = st.sidebar.slider("Min Reward/Risk", 1.2, 3.0, 1.8)

    # Always fetch enough data for indicators
    fetch_period = "120d"

    preds = fetch_latest_predictions()
    if preds.empty:
        st.warning("No predictions found.")
        return

    rows = []

    for r in preds.itertuples(index=False):
        df = fetch_price_df(r.stock_symbol, period=fetch_period)
        if df.empty or len(df) < correction_window:
            continue

        close = df['Close']
        high = df['High']

        last_price = safe_float(close.iloc[-1])

        high_close = safe_float(close.tail(correction_window).max())
        high_intraday = safe_float(high.tail(correction_window).max())

        if None in (last_price, high_close, high_intraday):
            continue

        corr_close = ((last_price - high_close) / high_close) * 100
        corr_intraday = ((last_price - high_intraday) / high_intraday) * 100

        if not (max_corr <= corr_close <= min_corr):
            continue

        rsi_val = safe_float(calc_rsi(close).iloc[-1])
        if rsi_val is None or rsi_val < rsi_min:
            continue

        macd_val = safe_float(calc_macd_hist(close).iloc[-1])
        if macd_val is None or macd_val < 0:
            continue

        atr_val = safe_float(calc_atr(df).iloc[-1])
        if atr_val is None:
            continue

        atr_pct = (atr_val / last_price) * 100
        if atr_pct > max_atr_pct:
            continue

        if not (r.entry_price and r.target_price and r.stop_loss):
            continue

        rr = (r.target_price - r.entry_price) / (r.entry_price - r.stop_loss)
        if rr < min_rr:
            continue

        rows.append({
            "Stock": r.stock_symbol,
            "Last Price": round(last_price, 2),
            f"{correction_window}D High (Close)": round(high_close, 2),
            f"{correction_window}D High (Intraday)": round(high_intraday, 2),
            "Correction % (Close)": round(corr_close, 2),
            "Correction % (Intraday)": round(corr_intraday, 2),
            "RSI": round(rsi_val, 2),
            "ATR %": round(atr_pct, 2),
            "Reward/Risk": round(rr, 2),
            "Signal": r.trade_signal,
            "Probability": r.probability_success
        })

    if not rows:
        st.info("No stocks qualify yet.")
        return

    df_out = pd.DataFrame(rows).sort_values(
        by=["Correction % (Close)", "Reward/Risk"],
        ascending=[True, False]
    )

    st.markdown(f"### ✅ {len(df_out)} Candidates ({correction_window}-Day Window)")
    st.dataframe(df_out, use_container_width=True)

# -------------------------------------------------
if __name__ == "__main__":
    run_screen()
