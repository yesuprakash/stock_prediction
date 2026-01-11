import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf

from near_entry_screen import (
    fetch_active_predictions,
    fetch_price_df,
    calc_rsi,
    calc_macd,
    calc_atr,
    find_series
)

# ---------------------------
# Streamlit UI
# ---------------------------
def run_market_correction_screen():
    st.set_page_config(page_title="Market Correction Selector", layout="wide")
    st.title("📉 Market Correction — Leadership Buy Candidates")

    st.info("""
    This screen identifies **high-quality stocks that corrected with the market
    but are still structurally strong**.  
    Use this to *prepare* buys — not chase entries.
    """)

    # ---------------------------
    # Sidebar — Market Inputs
    # ---------------------------
    st.sidebar.header("Market Context")
    nifty_high = st.sidebar.number_input("Recent NIFTY High", value=26000, step=50)
    nifty_current = st.sidebar.number_input("Current NIFTY Level", value=25650, step=50)

    market_drop_pct = ((nifty_current - nifty_high) / nifty_high) * 100
    st.sidebar.metric("Market Correction (%)", f"{market_drop_pct:.2f}%")

    min_market_drop = st.sidebar.slider(
        "Min market correction to activate screen (%)",
        -5.0, -0.5, -1.5, 0.1
    )

    if market_drop_pct > min_market_drop:
        st.warning("Market correction not deep enough yet. Screen is in observation mode.")

    # ---------------------------
    # Sidebar — Stock Filters
    # ---------------------------
    st.sidebar.header("Stock Filters")

    min_stock_drop = st.sidebar.slider("Min stock correction (%)", -30.0, -5.0, -8.0, 0.5)
    max_stock_drop = st.sidebar.slider("Max stock correction (%)", -30.0, -5.0, -20.0, 0.5)

    rsi_min = st.sidebar.slider("RSI min (trend safety)", 30, 60, 40, 1)
    max_atr_pct = st.sidebar.slider("Max ATR% (volatility)", 3.0, 8.0, 5.5, 0.1)
    min_rr = st.sidebar.slider("Min Reward/Risk", 1.2, 3.0, 1.8, 0.1)

    lookback = st.sidebar.selectbox("Price history", ["60d", "90d"], index=1)

    # ---------------------------
    # Load Predictions
    # ---------------------------
    preds = fetch_active_predictions(latest_only=True)
    if preds.empty:
        st.warning("No predictions found.")
        return

    rows = []

    for r in preds.itertuples(index=False):
        sym = r.stock_symbol
        price_df = fetch_price_df(sym, period=lookback)

        if price_df is None or price_df.empty:
            continue

        close = find_series(price_df, ['close'])
        high = find_series(price_df, ['high'])
        low = find_series(price_df, ['low'])

        if close is None or high is None:
            continue

        last_price = float(close.iloc[-1])
        high_30d = float(high.tail(30).max())

        stock_drop_pct = ((last_price - high_30d) / high_30d) * 100

        # Stock correction filter
        if not (max_stock_drop <= stock_drop_pct <= min_stock_drop):
            continue

        # RSI
        rsi = calc_rsi(close, 14).iloc[-1]
        if rsi < rsi_min:
            continue

        # MACD
        _, _, hist = calc_macd(close)
        if hist.iloc[-1] < 0:
            continue

        # ATR%
        atr = calc_atr(pd.DataFrame({'High': high, 'Low': low, 'Close': close}))
        atr_pct = (atr.iloc[-1] / last_price) * 100
        if atr_pct > max_atr_pct:
            continue

        # Reward / Risk
        entry = r.entry_price
        target = r.target_price
        stop = r.stop_loss
        if entry and target and stop:
            rr = (target - entry) / (entry - stop) if (entry - stop) > 0 else None
        else:
            rr = None

        if rr is None or rr < min_rr:
            continue

        rows.append({
            "Stock": sym,
            "Last Price": round(last_price, 2),
            "30D High": round(high_30d, 2),
            "Stock Correction (%)": round(stock_drop_pct, 2),
            "RSI": round(rsi, 2),
            "ATR %": round(atr_pct, 2),
            "Reward/Risk": round(rr, 2),
            "Trade Signal": r.trade_signal,
            "Probability": r.probability_success
        })

    # ---------------------------
    # Display
    # ---------------------------
    if not rows:
        st.info("No stocks qualify yet. Be patient.")
        return

    df = pd.DataFrame(rows)
    df = df.sort_values(
        by=["Stock Correction (%)", "Reward/Risk"],
        ascending=[True, False]
    )

    st.markdown(f"### ✅ {len(df)} Market-Correction Buy Candidates")
    st.dataframe(df, use_container_width=True)

    st.download_button(
        "Download CSV",
        df.to_csv(index=False),
        "market_correction_candidates.csv",
        "text/csv"
    )

if __name__ == "__main__":
    run_market_correction_screen()

