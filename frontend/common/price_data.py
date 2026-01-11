import yfinance as yf
import pandas as pd
import numpy as np

def flatten_columns(df):
    df.columns = [
        "_".join(map(str, c)).strip("_") if isinstance(c, tuple) else str(c)
        for c in df.columns
    ]
    return df

def fetch_price_df(symbol, period="90d", auto_adjust=False):
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=auto_adjust)
        if df is None or df.empty:
            return pd.DataFrame()
        df = flatten_columns(df)
        df.index = pd.to_datetime(df.index).normalize()
        return df
    except Exception:
        return pd.DataFrame()

def find_series(df, candidates):
    if df is None or df.empty:
        return None
    for c in df.columns:
        for k in candidates:
            if k.lower() in str(c).lower():
                return df[c]
    return None
