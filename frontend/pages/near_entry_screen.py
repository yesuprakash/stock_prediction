# near_entry_screen.py
import os
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

# ---------------------------
# Config / DB
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
# Helpers (flatten / matching)
# ---------------------------
def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten tuple / MultiIndex columns to readable strings."""
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            col_str = "_".join([str(c) for c in col if c not in (None, "")]).strip("_")
            if col_str == "":
                col_str = "_".join([str(c) for c in col])
        else:
            col_str = str(col)
        new_cols.append(col_str)
    df.columns = new_cols
    return df

def find_series(df: pd.DataFrame, candidates):
    """
    Find best matching numeric Series from df for any of the candidate substrings.
    Returns a pandas Series or None.
    """
    if df is None or df.empty:
        return None

    # exact name (case-insensitive) map
    lowered = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in lowered:
            ser = df[lowered[key]]
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

def _try_yf_history(symbol: str, period="7d", auto_adjust=False):
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=auto_adjust)
        if isinstance(df, pd.DataFrame) and not df.empty:
            df = _flatten_columns(df)
        return df
    except Exception:
        return pd.DataFrame()

def fetch_recent_prices(symbol: str, period="7d", auto_adjust=False, try_suffixes=True):
    """
    Try to fetch recent prices for a symbol; fallback to common suffixes (.NS, .BO) if empty.
    Returns a DataFrame or empty DataFrame.
    """
    df = _try_yf_history(symbol, period=period, auto_adjust=auto_adjust)
    if (df is None or df.empty) and try_suffixes:
        for sfx in ['.NS', '.BO']:
            df2 = _try_yf_history(symbol + sfx, period=period, auto_adjust=auto_adjust)
            if df2 is not None and not df2.empty:
                df = df2
                break
    if isinstance(df, pd.DataFrame) and not df.empty:
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)
        df.index = pd.to_datetime(df.index).normalize()
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

# ---------------------------
# Data query helpers
# ---------------------------
def fetch_active_predictions(latest_only=True):
    """
    Fetch predictions from DB.
    latest_only=True => keep the most recent prediction per stock_symbol
    """
    q = """
        SELECT stock_symbol, prediction_date, entry_price, target_price, stop_loss,
               trade_signal, probability_success
        FROM predictions
    """
    df = pd.read_sql(q, engine)
    if df.empty:
        return df
    df['prediction_date'] = pd.to_datetime(df['prediction_date'])
    if latest_only:
        df = df.sort_values(['stock_symbol', 'prediction_date']).groupby('stock_symbol', as_index=False).last()
    return df

# ---------------------------
# Business logic: near-entry finder
# ---------------------------
def compute_nearness(row, price_ser):
    """
    price_ser: pandas Series (index=dates) representing last/close prices
    returns dict { last_price, abs_diff, pct_diff, last_date }
    """
    if price_ser is None or price_ser.empty:
        return {"last_price": None, "abs_diff": None, "pct_diff": None, "last_date": None}

    last_date = price_ser.index.max()
    last_price = float(price_ser.loc[last_date])
    entry = row.get('entry_price')
    if entry is None or pd.isna(entry):
        return {"last_price": last_price, "abs_diff": None, "pct_diff": None, "last_date": last_date}
    abs_diff = last_price - float(entry)
    pct_diff = (abs_diff / float(entry)) * 100 if float(entry) != 0 else None
    return {"last_price": last_price, "abs_diff": abs_diff, "pct_diff": pct_diff, "last_date": last_date}

# ---------------------------
# Streamlit screen
# ---------------------------
def run_screen():
    st.set_page_config(page_title="Near-Entry Stocks", layout="wide")
    st.title("🔎 Stocks Near Entry Price")

    # UI controls
    st.sidebar.header("Filters / Options")
    tolerance_pct = st.sidebar.slider("Max distance from Entry (percent)", min_value=0.1, max_value=20.0, value=2.0, step=0.1)
    tolerance_abs = st.sidebar.number_input("Or Max distance (absolute price) - leave 0 to ignore", value=0.0, min_value=0.0, step=1.0)
    latest_only = st.sidebar.checkbox("Use only latest prediction per stock", value=True)
    require_signal_match = st.sidebar.checkbox("Require current signal to match prediction signal (recommended)", value=False)
    yf_auto_adjust = st.sidebar.checkbox("yfinance auto_adjust (apply dividends/splits)", value=False)
    period_for_price = st.sidebar.selectbox("Price history window to use for 'current' price", options=["1d", "3d", "7d", "14d"], index=2)
    show_debug = st.sidebar.checkbox("Show debug info", value=False)

    st.info("This screen lists predictions where the latest market price is close to the model's entry price.")

    # Load predictions
    with st.spinner("Fetching predictions from DB..."):
        preds = fetch_active_predictions(latest_only=latest_only)

    if preds is None or preds.empty:
        st.warning("No predictions found in database.")
        return

    # Cache per-symbol price fetch
    @st.cache_data(ttl=300)
    def _get_price_series_for_symbol(sym: str):
        df = fetch_recent_prices(sym, period=period_for_price, auto_adjust=yf_auto_adjust, try_suffixes=True)
        ser = find_series(df, ['close', 'adj close', 'last', 'close_price'])
        return ser

    rows = []
    total = len(preds)
    progress_bar = st.progress(0)
    for idx, r in enumerate(preds.itertuples(index=False), start=1):
        sym = getattr(r, "stock_symbol")
        price_ser = _get_price_series_for_symbol(sym)
        near = compute_nearness(r._asdict(), price_ser)
        last_price = near['last_price']
        pct_diff = near['pct_diff']
        abs_diff = near['abs_diff']
        last_date = near['last_date']

        # If require_signal_match: fetch model signal on last_date
        signal_ok = True
        if require_signal_match and last_date is not None:
            q_sig = """
                SELECT trade_signal FROM predictions
                WHERE stock_symbol = %s AND DATE(prediction_date) = %s
                ORDER BY prediction_date DESC LIMIT 1
            """
            try:
                cur_df = pd.read_sql(q_sig, engine, params=(sym, last_date.date()))
                if cur_df is None or cur_df.empty:
                    signal_ok = False
                else:
                    today_signal = cur_df.iloc[0]['trade_signal']
                    orig_signal = getattr(r, "trade_signal")
                    signal_ok = (str(orig_signal).strip().lower() == str(today_signal).strip().lower())
            except Exception:
                signal_ok = False

        passes_pct = (pct_diff is not None) and (abs(pct_diff) <= float(tolerance_pct))
        passes_abs = (abs_diff is not None) and (abs(abs_diff) <= float(tolerance_abs)) if tolerance_abs > 0 else False
        include = (passes_pct or passes_abs)

        if include and signal_ok:
            rows.append({
                "stock_symbol": sym,
                "prediction_date": getattr(r, "prediction_date"),
                "entry_price": getattr(r, "entry_price"),
                "last_price": last_price,
                "last_price_date": last_date,
                "diff (last-entry)": round(abs_diff, 6) if abs_diff is not None else None,
                "pct_diff (%)": round(pct_diff, 4) if pct_diff is not None else None,
                "trade_signal": getattr(r, "trade_signal"),
                "target_price": getattr(r, "target_price"),
                "stop_loss": getattr(r, "stop_loss"),
                "probability": getattr(r, "probability_success")
            })

        progress_bar.progress(int(idx / total * 100))

    progress_bar.empty()

    if show_debug:
        st.write("Predictions fetched:", len(preds))
    if not rows:
        st.info("No stocks found near their entry price with current filters.")
        return

    result_df = pd.DataFrame(rows)
    # sort by absolute percent proximity ascending
    result_df = result_df.sort_values(by=['pct_diff (%)'], key=lambda s: s.abs())

    st.write(f"### {len(result_df)} stocks near entry (within {tolerance_pct}% or {tolerance_abs} absolute)")
    st.dataframe(result_df.reset_index(drop=True))

    # Export CSV
    csv = result_df.to_csv(index=False)
    st.download_button("Download CSV", csv, file_name="near_entry_stocks.csv", mime="text/csv")

if __name__ == "__main__":
    run_screen()
