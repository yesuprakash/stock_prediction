import os
import json
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine

# ---------------------------
# 🔐 Load DB config
# ---------------------------
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# ✅ Use SQLAlchemy for stable Pandas DB read
engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ---------------------------
# 📊 Fetch list of stocks
# ---------------------------
def get_stock_list():
    query = "SELECT DISTINCT stock_symbol FROM predictions ORDER BY stock_symbol"
    df = pd.read_sql(query, engine)
    return df['stock_symbol'].tolist()

# ---------------------------
# 📈 Fetch predictions
# ---------------------------
def get_predictions(stock_symbol, days=60):
    query = """
        SELECT prediction_date, entry_price, target_price, stop_loss,
               trade_signal, probability_success
        FROM predictions
        WHERE stock_symbol = %s
          AND prediction_date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY prediction_date
    """
    df = pd.read_sql(query, engine, params=(stock_symbol, days))
    return df


# ---------------------------
# 💹 Fetch historical prices
# ---------------------------
def get_price_history(stock_symbol, days=90):
    end = datetime.now()
    start = end - timedelta(days=days)
    data = yf.download(stock_symbol, start=start, end=end)
    return data

# ---------------------------
# 🧠 Calculate Accuracy
# ---------------------------
def calculate_accuracy(pred_df, price_df):
    wins = 0
    losses = 0
    open_trades = 0
    total = len(pred_df)

    for _, row in pred_df.iterrows():
        pred_date = pd.to_datetime(row['prediction_date'])
        after = price_df.loc[pred_date:]

        # Skip if price data or required values are missing
        if after.empty or pd.isna(row['target_price']) or pd.isna(row['stop_loss']):
            open_trades += 1
            continue

        tp = float(row['target_price'])
        sl = float(row['stop_loss'])

        # ✅ Explicit .values avoids ambiguous Series
        hit_target = (after['High'].values >= tp).any()
        hit_stop = (after['Low'].values <= sl).any()

        # ✅ Ensure clean Python bool
        hit_target = bool(hit_target.item() if hasattr(hit_target, "item") else hit_target)
        hit_stop = bool(hit_stop.item() if hasattr(hit_stop, "item") else hit_stop)

        if hit_target and not hit_stop:
            wins += 1
        elif hit_stop and not hit_target:
            losses += 1
        elif hit_target and hit_stop:
            losses += 1   # conservative
        else:
            open_trades += 1

    accuracy = (wins / total * 100) if total > 0 else 0
    return wins, losses, open_trades, total, round(accuracy, 2)


# ---------------------------
# 📊 Plot chart
# ---------------------------
def plot_predictions(price_data, pred_df):
    fig = go.Figure()

    # Price line
    fig.add_trace(go.Scatter(
        x=price_data.index, y=price_data['Close'],
        mode='lines', name='Close Price', line=dict(color='blue')
    ))

    # Prediction markers
    for _, row in pred_df.iterrows():
        date = row['prediction_date']
        entry = row['entry_price']
        target = row['target_price']
        stop = row['stop_loss']
        signal = row['trade_signal']
        prob = row['probability_success']

        fig.add_trace(go.Scatter(
            x=[date], y=[entry], mode='markers+text', name='Entry',
            marker=dict(color='orange', size=10),
            text=[f"{signal}<br>{prob}%"], textposition="top center"
        ))

        fig.add_trace(go.Scatter(
            x=[date], y=[target], mode='markers', name='Target',
            marker=dict(color='green', size=8)
        ))

        fig.add_trace(go.Scatter(
            x=[date], y=[stop], mode='markers', name='Stop Loss',
            marker=dict(color='red', size=8)
        ))

    fig.update_layout(
        title="📊 Prediction vs Actual Price Movement",
        xaxis_title="Date",
        yaxis_title="Price",
        legend_title="Legend",
        hovermode="x unified",
        template="plotly_white"
    )
    return fig

# ---------------------------
# 🌐 Streamlit UI
# ---------------------------
st.set_page_config(page_title="Stock Prediction Dashboard", layout="wide")

st.title("📊 Stock Prediction Accuracy Dashboard")

# Stock selector
stocks = get_stock_list()
selected_stock = st.selectbox("Select Stock", stocks)

days = st.slider("Lookback (days)", min_value=15, max_value=180, value=60, step=5)

if selected_stock:
    with st.spinner("Loading data..."):
        pred_df = get_predictions(selected_stock, days)
        price_df = get_price_history(selected_stock, days + 30)

    if not pred_df.empty:
        # ✅ Accuracy calculation
        wins, losses, open_trades, total, accuracy = calculate_accuracy(pred_df, price_df)

        st.subheader(f"📈 Accuracy Summary for {selected_stock}")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Predictions", total)
        col2.metric("Wins (Target Hit)", wins)
        col3.metric("Losses (Stop Hit)", losses)
        col4.metric("Open Trades", open_trades)
        col5.metric("Accuracy %", f"{accuracy}%")

        # 📊 Chart
        fig = plot_predictions(price_df, pred_df)
        st.plotly_chart(fig, use_container_width=True)

        # 🧾 Table
        with st.expander("📜 View Raw Prediction Data"):
            st.dataframe(pred_df)
    else:
        st.warning("No predictions found for the selected stock.")
