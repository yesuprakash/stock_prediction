import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

import streamlit as st
import pandas as pd
from datetime import date
import yfinance as yf
from backend.portfolio_functions import add_trade, get_portfolio, update_live_prices

st.set_page_config(page_title="Portfolio Tracker", layout="wide")

st.title("📊 My Portfolio Tracker")

# --- Add new trade ---
with st.expander("➕ Add New Trade", expanded=False):
    col1, col2, col3 = st.columns(3)
    stock = col1.text_input("Stock Symbol (e.g. RELIANCE.NS)")
    trade_type = col2.selectbox("Trade Type", ["BUY", "SELL"])
    qty = col3.number_input("Quantity", min_value=1, step=1)
    
    col4, col5, col6 = st.columns(3)
    buy_price = col4.number_input("Buy Price (₹)", min_value=0.0, step=0.1)
    sell_price = col5.number_input("Sell Price (₹)", min_value=0.0, step=0.1)
    target_price = col6.number_input("Target Price (₹)", min_value=0.0, step=0.1)

    col7, col8 = st.columns(2)
    stop_loss = col7.number_input("Stop Loss (₹)", min_value=0.0, step=0.1)
    notes = col8.text_input("Notes (optional)")

    trade_date = st.date_input("Trade Date", value=date.today())  # 👈 Default today's date

    st.markdown("")  # small spacing

    if st.button("💾 Save Trade"):
        if not stock:
            st.warning("⚠️ Please enter a stock symbol.")
        else:
            add_trade(stock, trade_type, qty, buy_price, sell_price, target_price, stop_loss, trade_date, notes)
            st.success(f"✅ Trade added for {stock} on {trade_date}")

# --- Show portfolio table ---
st.divider()
st.subheader("📈 Portfolio Overview")

if st.button("🔄 Refresh Live Prices"):
    update_live_prices()
    st.success("✅ Live prices updated successfully!")

df = get_portfolio()

if not df.empty:
    try:
        df['Target Achieved (%)'] = (
            ((df['current_price'] - df['buy_price']) / (df['target_price'] - df['buy_price'])) * 100
        ).round(2)
    except Exception as e:
        st.warning(f"⚠️ Could not calculate target %: {e}")

    # Display portfolio data
    st.dataframe(df, width='stretch')

else:
    st.info("No trades added yet.")
