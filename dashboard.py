import os
import json
import psycopg2
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta
from dotenv import load_dotenv

# --- Load DB config ---
load_dotenv()

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

# --- Fetch list of stocks ---
def get_stock_list():
    conn = get_conn()
    df = pd.read_sql("SELECT DISTINCT stock_symbol FROM predictions ORDER BY stock_symbol", conn)
    conn.close()
    return df['stock_symbol'].tolist()

# --- Fetch predictions ---
def get_predictions(stock_symbol, days=60):
    conn = get_conn()
    query = """
        SELECT prediction_date, entry_price, target_price, stop_loss,
               trade_signal, probability_success
        FROM predictions
        WHERE stock_symbol = %s
          AND prediction_date >= CURRENT_DATE - INTERVAL '%s days'
        ORDER BY prediction_date
    """
    df = pd.read_sql(query, conn, params=[stock_symbol, days])
    conn.close()
    return df

# --- Fetch historical prices ---
def get_price_history(stock_symbol, days=90):
    end = datetime.now()
    start = end - timedelta(days=days)
    data = yf.download(stock_symbol, start=start, end=end)
    return data

# --- Calculate accuracy ---
def calculate_accuracy(pred_df, price_df):
    wins = 0
    losses = 0
    total = len(pred_df)
    for _, row in pred_df.iterrows():
        after = price_df.loc[row['prediction_date']:]
        if after.empty:
            continue
        if (after['High'] >= row['target_price']).any():
            wins += 1
        elif (after['Low'] <= row['stop_loss']).any():
            losses += 1
    accuracy = (wins / total * 100) if total > 0 else 0
    return wins, losses, total, round(accuracy, 2)

# --- Plot chart ---
def plot_predictions(price_data, pred_df):
    fig = go.Figure()

    # Price line
    fig.add_trace(go.Scatter(
        x=price_data.index, y=price_data['Close'],
        mode='lines', name='Close Price', line=dict(color='blue')
    ))

    # Prediction markers
    for i, row in pred_df.iterrows():
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
        title="Prediction vs Actual Price Movement",
        xaxis_title="Date",
        yaxis_title="Price",
        legend_title="Legend",
        hovermode="x unified",
        template="plotly_white"
    )
    return fig

# --- Streamlit UI ---
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
        # Accuracy calculation
        wins, losses, total, accuracy = calculate_accuracy(pred_df, price_df)
        st.subheader(f"📈 Accuracy Summary for {selected_stock}")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Predictions", total)
        col2.metric("Wins (Target Hit)", wins)
        col3.metric("Losses (Stop Hit)", losses)
        col4.metric("Accuracy %", f"{accuracy}%")

        # Chart
        fig = plot_predictions(price_df, pred_df)
        st.plotly_chart(fig, use_container_width=True)

        # Show table
        with st.expander("📜 View Raw Prediction Data"):
            st.dataframe(pred_df)
    else:
        st.warning("No predictions found for the selected stock.")
