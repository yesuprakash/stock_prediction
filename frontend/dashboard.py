# dashboard.py
import os
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
import streamlit as st
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

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ---------------------------
# DB helpers
# ---------------------------
def get_stock_list():
    query = "SELECT DISTINCT stock_symbol FROM predictions ORDER BY stock_symbol"
    df = pd.read_sql(query, engine)
    return df['stock_symbol'].tolist() if not df.empty else []

def get_predictions(stock_symbol, days=60):
    query = """
        SELECT prediction_date, entry_price, target_price, stop_loss,
               trade_signal, probability_success
        FROM predictions
        WHERE stock_symbol = %s
          AND prediction_date >= CURRENT_DATE - (%s || ' days')::interval
        ORDER BY prediction_date
    """
    df = pd.read_sql(query, engine, params=(stock_symbol, int(days)))
    if 'prediction_date' in df.columns:
        df['prediction_date'] = pd.to_datetime(df['prediction_date']).dt.normalize()
    return df

# ---------------------------
# Price fetching & normalization
# ---------------------------
def _flatten_columns(df):
    """Flatten tuple/MultiIndex columns to readable strings."""
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            col_str = "_".join([str(c) for c in col if c not in (None, '')]).strip("_")
            if col_str == "":
                col_str = "_".join([str(c) for c in col])
        else:
            col_str = str(col)
        new_cols.append(col_str)
    df.columns = new_cols
    return df

def _try_yf_download(symbol, start, end, auto_adjust=False):
    try:
        data = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=auto_adjust)
        return data
    except Exception:
        return pd.DataFrame()

def get_price_history(stock_symbol, days=90, auto_adjust=False, try_suffixes=True):
    """
    Fetch price history and normalize index & columns.
    If empty and try_suffixes=True, attempt with common suffixes (.NS, .BO).
    """
    end = datetime.now()
    start = end - timedelta(days=days)
    data = _try_yf_download(stock_symbol, start, end, auto_adjust=auto_adjust)

    # fallback to common suffixes for Indian markets
    if (data is None or data.empty) and try_suffixes:
        suffixes = ['.NS', '.BO']
        for sfx in suffixes:
            alt = _try_yf_download(stock_symbol + sfx, start, end, auto_adjust=auto_adjust)
            if alt is not None and not alt.empty:
                data = alt
                break

    if data is None or data.empty:
        return pd.DataFrame()

    # Normalize index timezone and to date
    if hasattr(data.index, "tz") and data.index.tz is not None:
        data.index = data.index.tz_convert(None)
    data.index = pd.to_datetime(data.index).normalize()

    # Flatten columns (handles tuples / MultiIndex)
    data = _flatten_columns(data)

    return data

# ---------------------------
# Helpers to find numeric series
# ---------------------------
def find_series(df, candidates):
    """
    Find best matching numeric series in df given candidate substrings.
    Returns a pandas Series or None.
    """
    if df is None or df.empty:
        return None

    # exact match map (lowercase -> actual)
    lowered = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lowered:
            ser = df[lowered[cand.lower()]]
            if isinstance(ser, pd.DataFrame):
                numeric = ser.select_dtypes(include=[np.number]).columns
                if len(numeric) > 0:
                    return ser[numeric[0]]
                return ser.iloc[:, 0]
            return ser

    # substring match
    for col in df.columns:
        col_l = str(col).lower()
        for cand in candidates:
            if cand.lower() in col_l:
                ser = df[col]
                if isinstance(ser, pd.DataFrame):
                    numeric = ser.select_dtypes(include=[np.number]).columns
                    if len(numeric) > 0:
                        return ser[numeric[0]]
                    return ser.iloc[:, 0]
                return ser

    # fallback: first numeric column
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        return df[numeric_cols[0]]

    return None

# ---------------------------
# Accuracy calculation (robust)
# ---------------------------
def calculate_accuracy(pred_df, price_df):
    wins = losses = open_trades = 0
    total = len(pred_df)

    # pre-find series
    high_ser = find_series(price_df, ['high'])
    low_ser = find_series(price_df, ['low'])

    for _, row in pred_df.iterrows():
        if pd.isna(row.get('prediction_date')):
            open_trades += 1
            continue

        pred_date = pd.to_datetime(row['prediction_date']).normalize()

        # ensure TP/SL exist and we have series
        if high_ser is None or low_ser is None or pd.isna(row.get('target_price')) or pd.isna(row.get('stop_loss')):
            open_trades += 1
            continue

        mask = price_df.index >= pred_date
        if not mask.any():
            open_trades += 1
            continue

        high_after = np.asarray(high_ser.loc[mask])
        low_after = np.asarray(low_ser.loc[mask])

        if high_after.size == 0 or low_after.size == 0:
            open_trades += 1
            continue

        tp = float(row['target_price'])
        sl = float(row['stop_loss'])

        # scalar booleans using numpy.any
        hit_target = bool(np.any(high_after >= tp))
        hit_stop = bool(np.any(low_after <= sl))

        if hit_target and not hit_stop:
            wins += 1
        elif hit_stop and not hit_target:
            losses += 1
        elif hit_target and hit_stop:
            losses += 1
        else:
            open_trades += 1

    accuracy = (wins / total * 100) if total > 0 else 0.0
    return wins, losses, open_trades, total, round(accuracy, 2)

# ---------------------------
# Plotting (robust)
# ---------------------------
def plot_predictions(price_data, pred_df):
    fig = go.Figure()

    close_ser = find_series(price_data, ['close', 'adj close', 'close_price'])
    if close_ser is None:
        fig.update_layout(title="No price 'Close' series found to plot.")
        return fig

    # Price line
    fig.add_trace(go.Scatter(
        x=price_data.index,
        y=close_ser.values,
        mode='lines',
        name='Close Price'
    ))

    # Batch markers into single traces for cleanliness
    entries_x, entries_y, entries_text = [], [], []
    targets_x, targets_y = [], []
    stops_x, stops_y = [], []

    for _, row in pred_df.iterrows():
        date = row.get('prediction_date')
        if pd.isna(date):
            continue
        date = pd.to_datetime(date).normalize()

        entry = row.get('entry_price')
        target = row.get('target_price')
        stop = row.get('stop_loss')
        signal = row.get('trade_signal', '')
        prob = row.get('probability_success', '')

        if pd.notna(entry):
            entries_x.append(date); entries_y.append(float(entry))
            entries_text.append(f"{signal}<br>{prob}%")
        if pd.notna(target):
            targets_x.append(date); targets_y.append(float(target))
        if pd.notna(stop):
            stops_x.append(date); stops_y.append(float(stop))

    if entries_x:
        fig.add_trace(go.Scatter(
            x=entries_x, y=entries_y, mode='markers+text',
            name='Entry', marker=dict(symbol='circle', size=9),
            text=entries_text, textposition="top center",
            hovertemplate="Entry: %{y}<br>Date: %{x}<extra></extra>"
        ))
    if targets_x:
        fig.add_trace(go.Scatter(
            x=targets_x, y=targets_y, mode='markers',
            name='Target', marker=dict(symbol='triangle-up', size=8),
            hovertemplate="Target: %{y}<br>Date: %{x}<extra></extra>"
        ))
    if stops_x:
        fig.add_trace(go.Scatter(
            x=stops_x, y=stops_y, mode='markers',
            name='Stop Loss', marker=dict(symbol='x', size=8),
            hovertemplate="Stop: %{y}<br>Date: %{x}<extra></extra>"
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
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Stock Prediction Dashboard", layout="wide")
st.title("📊 Stock Prediction Accuracy Dashboard")

# Debug toggle
show_debug = st.sidebar.checkbox("Show debug info", value=False)

stocks = get_stock_list()
if not stocks:
    st.error("No stock symbols found in database (predictions table).")
    st.stop()

selected_stock = st.selectbox("Select Stock", stocks)

days = st.slider("Lookback (days)", min_value=15, max_value=360, value=60, step=5)
auto_adjust_toggle = st.sidebar.checkbox("yfinance auto_adjust (apply dividends/splits)", value=False)

if selected_stock:
    with st.spinner("Loading data..."):
        pred_df = get_predictions(selected_stock, days)
        price_df = get_price_history(selected_stock, days + 30, auto_adjust=auto_adjust_toggle, try_suffixes=True)

    if show_debug:
        st.write("Price DF empty:", price_df is None or price_df.empty)
        st.write("price_df columns:", price_df.columns.tolist() if price_df is not None else [])
        st.write(price_df.head(5))
        st.write("Predictions rows:", len(pred_df))
        st.write(pred_df.head(5))

    if pred_df is not None and not pred_df.empty:
        wins, losses, open_trades, total, accuracy = calculate_accuracy(pred_df, price_df)

        st.subheader(f"📈 Accuracy Summary for {selected_stock}")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Predictions", total)
        col2.metric("Wins (Target Hit)", wins)
        col3.metric("Losses (Stop Hit)", losses)
        col4.metric("Open Trades", open_trades)
        col5.metric("Accuracy %", f"{accuracy}%")

        fig = plot_predictions(price_df, pred_df)
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("📜 View Raw Prediction Data"):
            st.dataframe(pred_df)
    else:
        st.warning("No predictions found for the selected stock.")
