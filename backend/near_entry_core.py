# backend/near_entry_core.py

import os
import sys
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf
from dotenv import load_dotenv

from backend.db import get_connection
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def flatten_columns(df):
    df.columns = [
        "_".join(map(str, c)).strip("_") if isinstance(c, tuple) else str(c)
        for c in df.columns
    ]
    return df


def find_series(df, candidates):
    if df is None or df.empty:
        return None

    lowered = {str(c).lower(): c for c in df.columns}

    # exact match
    for cand in candidates:
        if cand.lower() in lowered:
            return df[lowered[cand.lower()]]

    # substring
    for col in df.columns:
        for cand in candidates:
            if cand.lower() in str(col).lower():
                return df[col]

    # fallback numeric
    nums = df.select_dtypes(include=[np.number]).columns
    return df[nums[0]] if len(nums) else None

def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    new_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            col_str = "_".join(
                [str(c) for c in col if c not in (None, "")]
            ).strip("_")
            if col_str == "":
                col_str = "_".join([str(c) for c in col])
        else:
            col_str = str(col)
        new_cols.append(col_str)

    df.columns = new_cols
    return df


def _try_yf_history(symbol: str, period="60d", auto_adjust=False):
    try:
        df = yf.download(
            symbol,
            period=period,
            progress=False,
            auto_adjust=auto_adjust
        )
        if isinstance(df, pd.DataFrame) and not df.empty:
            df = _flatten_columns(df)
        return df
    except Exception:
        return pd.DataFrame()


def fetch_price_df(
    symbol: str,
    period="60d",
    auto_adjust=False,
    try_suffixes=True
):
    df = _try_yf_history(symbol, period=period, auto_adjust=auto_adjust)

    if (df is None or df.empty) and try_suffixes:
        for sfx in [".NS", ".BO"]:
            df2 = _try_yf_history(
                symbol + sfx,
                period=period,
                auto_adjust=auto_adjust
            )
            if df2 is not None and not df2.empty:
                df = df2
                break

    if isinstance(df, pd.DataFrame) and not df.empty:
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert(None)

        df.index = pd.to_datetime(df.index).normalize()

    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()



def calc_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / roll_down
    return 100 - (100 / (1 + rs))


def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def calc_macd(series):
    ema12 = calc_ema(series, 12)
    ema26 = calc_ema(series, 26)
    macd = ema12 - ema26
    signal = calc_ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist



def calc_atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(alpha=1/period, adjust=False).mean()


# ---------------------------------------------------------
# DB
# ---------------------------------------------------------

def fetch_active_predictions(latest_only=True):
    conn = get_connection()

    df = pd.read_sql("""
        SELECT stock_symbol, prediction_date,
               entry_price, target_price, stop_loss,
               trade_signal, probability_success
        FROM predictions
    """, conn)

    conn.close()

    if df.empty:
        return df

    df["prediction_date"] = pd.to_datetime(df["prediction_date"])

    if latest_only:
        df = (
            df.sort_values(["stock_symbol", "prediction_date"])
              .groupby("stock_symbol", as_index=False)
              .last()
        )

    return df



# ---------------------------------------------------------
# Core Logic
# ---------------------------------------------------------

def evaluate_filters_for_symbol(pred_row: dict,
                                price_df: pd.DataFrame,
                                config: dict):
    """
    pred_row: dict with prediction fields
    price_df: historical price df (enough lookback for indicators)
    config: dict with thresholds and toggles
    returns dict with metrics and booleans for each filter
    """
    result = {
        'last_price': None, 'last_date': None,
        'pct_diff': None, 'abs_diff': None,
        'rsi': None, 'ma20': None, 'macd_hist': None,
        'vol_ratio': None, 'reward_risk': None,
        'gap_pct': None, 'atr_pct': None,
        'passes': {}, 'overall': False, 'suggested_action': None
    }

    if price_df is None or price_df.empty:
        result['suggested_action'] = 'no_price_data'
        return result

    # get series
    close_ser = find_series(price_df, ['close', 'adj close', 'last'])
    high_ser = find_series(price_df, ['high'])
    low_ser = find_series(price_df, ['low'])
    open_ser = find_series(price_df, ['open'])
    vol_ser = find_series(price_df, ['volume'])

    if close_ser is None:
        result['suggested_action'] = 'no_close_series'
        return result

    last_date = close_ser.index.max()
    last_price = float(close_ser.loc[last_date])
    result['last_price'] = last_price
    result['last_date'] = last_date

    entry = pred_row.get('entry_price')
    target = pred_row.get('target_price')
    stop = pred_row.get('stop_loss')

    # proximity
    if entry is None or pd.isna(entry):
        result['suggested_action'] = 'no_entry_price'
        return result

    abs_diff = last_price - float(entry)
    pct_diff = (abs_diff / float(entry)) * 100 if float(entry) != 0 else None
    result['abs_diff'] = abs_diff
    result['pct_diff'] = pct_diff

    # indicators require enough history — ensure at least 50 days
    lookback_needed = max(50, config.get('rsi_period', 14) * 3)
    if len(close_ser) < lookback_needed:
        # still proceed with what we have, but indicators may be NaN
        pass

    # RSI
    try:
        rsi = calc_rsi(close_ser, period=config.get('rsi_period', 14))
        result['rsi'] = float(rsi.loc[last_date]) if last_date in rsi.index and not pd.isna(rsi.loc[last_date]) else None
    except Exception:
        result['rsi'] = None

    # MA20
    try:
        ma20 = close_ser.rolling(window=config.get('ma_period', 20)).mean()
        result['ma20'] = float(ma20.loc[last_date]) if last_date in ma20.index and not pd.isna(ma20.loc[last_date]) else None
    except Exception:
        result['ma20'] = None

    # MACD hist
    try:
        _, _, hist = calc_macd(close_ser)
        result['macd_hist'] = float(hist.loc[last_date]) if last_date in hist.index and not pd.isna(hist.loc[last_date]) else None
    except Exception:
        result['macd_hist'] = None

    # Volume ratio vs 5-day average
    try:
        if vol_ser is not None and last_date in vol_ser.index:
            vol_last = float(vol_ser.loc[last_date])
            vol_avg5 = float(vol_ser.rolling(window=5).mean().loc[last_date]) if len(vol_ser) >= 5 else float(vol_ser.mean())
            result['vol_ratio'] = (vol_last / vol_avg5) if vol_avg5 and vol_avg5 > 0 else None
        else:
            result['vol_ratio'] = None
    except Exception:
        result['vol_ratio'] = None

    # Reward / Risk ratio (for BUY context)
    try:
        if target is not None and stop is not None and not pd.isna(target) and not pd.isna(stop):
            # For buy: reward = target - entry; risk = entry - stop (both positive expected)
            reward = float(target) - float(entry)
            risk = float(entry) - float(stop)
            if risk <= 0:
                rr = None
            else:
                rr = reward / risk
            result['reward_risk'] = rr
        else:
            result['reward_risk'] = None
    except Exception:
        result['reward_risk'] = None

    # Gap percent: need open and prev close
    try:
        if open_ser is not None and last_date in open_ser.index:
            today_open = float(open_ser.loc[last_date])
            prev_idx = close_ser.index.get_loc(last_date) - 1
            if prev_idx >= 0:
                prev_close = float(close_ser.iloc[prev_idx])
                gap_pct = ((today_open - prev_close) / prev_close) * 100 if prev_close != 0 else None
                result['gap_pct'] = gap_pct
            else:
                result['gap_pct'] = None
        else:
            result['gap_pct'] = None
    except Exception:
        result['gap_pct'] = None

    # ATR percent
    try:
        if high_ser is not None and low_ser is not None:
            df_for_atr = pd.DataFrame({'High': high_ser, 'Low': low_ser, 'Close': close_ser})
            atr = calc_atr(df_for_atr, period=config.get('atr_period', 14))
            atr_last = float(atr.loc[last_date]) if last_date in atr.index and not pd.isna(atr.loc[last_date]) else None
            result['atr_pct'] = (atr_last / last_price) * 100 if atr_last is not None and last_price != 0 else None
        else:
            result['atr_pct'] = None
    except Exception:
        result['atr_pct'] = None

    # Evaluate each filter boolean based on config thresholds
    passes = {}

    # Prediction age filter
    pred_date = pred_row.get('prediction_date')
    if pd.isna(pred_date):
        pred_age_days = None
    else:
        pred_age_days = (datetime.now().date() - pd.to_datetime(pred_date).date()).days
    passes['age'] = (pred_age_days is not None) and (pred_age_days <= config.get('max_prediction_age_days', 3))
    result['pred_age_days'] = pred_age_days

    # Signal strength filter (original signal must be acceptable)
    orig_signal = pred_row.get('trade_signal')
    if orig_signal is None:
        passes['signal_strength'] = False
    else:
        s = str(orig_signal).strip().lower()
        # allowed set: strong buy, buy, bullish
        allowed = config.get('allowed_signals', ['strong buy', 'buy', 'bullish'])
        passes['signal_strength'] = any(a.lower() == s for a in allowed)
    result['orig_signal'] = orig_signal

    # Trend filters: RSI, MA20, MACD histogram
    # For BUY: require rsi > rsi_min, price > ma20, macd_hist > 0
    price_above_ma = (result['ma20'] is not None) and (last_price > result['ma20'])
    passes['ma20'] = price_above_ma if config.get('use_ma_filter', True) else True

    rsi_ok = (result['rsi'] is not None) and (result['rsi'] >= config.get('rsi_min', 45))
    passes['rsi'] = rsi_ok if config.get('use_rsi_filter', True) else True

    macd_ok = (result['macd_hist'] is not None) and (result['macd_hist'] >= config.get('macd_hist_min', 0))
    passes['macd'] = macd_ok if config.get('use_macd_filter', True) else True

    # Volume filter
    vol_ok = (result['vol_ratio'] is not None) and (result['vol_ratio'] >= config.get('min_volume_ratio', 1.2))
    passes['volume'] = vol_ok if config.get('use_volume_filter', True) else True

    # Reward / Risk
    rr_ok = (result['reward_risk'] is not None) and (result['reward_risk'] >= config.get('min_reward_risk', 1.5))
    passes['reward_risk'] = rr_ok if config.get('use_rr_filter', True) else True

    # Gap filter
    gap_ok = True
    gp = result.get('gap_pct')
    if gp is None:
        gap_ok = True
    else:
        gap_ok = abs(gp) <= config.get('max_gap_pct', 1.5)
    passes['gap'] = gap_ok if config.get('use_gap_filter', True) else True

    # Volatility filter (ATR%)
    atr_ok = True
    ap = result.get('atr_pct')
    if ap is None:
        atr_ok = True
    else:
        atr_ok = ap <= config.get('max_atr_pct', 5.0)
    passes['volatility'] = atr_ok if config.get('use_volatility_filter', True) else True

    result['passes'] = passes
    # overall: all enabled filters must pass
    overall = all(v for v in passes.values())
    result['overall'] = overall

    # suggested_action
    if not passes['age']:
        suggested_action = 'SKIP (prediction too old)'
    elif not passes['signal_strength']:
        suggested_action = 'SKIP (weak original signal)'
    elif not passes['ma20'] or not passes['rsi'] or not passes['macd']:
        suggested_action = 'SKIP (trend not confirmed)'
    elif not passes['volume']:
        suggested_action = 'SKIP (low volume)'
    elif not passes['reward_risk']:
        suggested_action = 'SKIP (reward-risk too low)'
    elif not passes['gap']:
        suggested_action = 'SKIP (gap too large)'
    elif not passes['volatility']:
        suggested_action = 'SKIP (high volatility)'
    else:
        suggested_action = 'ACTIONABLE (passes all filters)'

    result['suggested_action'] = suggested_action

    return result

def run_near_entry_logic(config):

    preds = fetch_active_predictions(config["latest_only"])
    rows = []

    for r in preds.itertuples(index=False):

        pred_row = {
            "stock_symbol": r.stock_symbol,
            "prediction_date": r.prediction_date,
            "entry_price": r.entry_price,
            "target_price": r.target_price,
            "stop_loss": r.stop_loss,
            "trade_signal": r.trade_signal,
            "probability_success": r.probability_success
        }

        price_df = fetch_price_df(
            r.stock_symbol,
            period=config["period_for_history"],
            auto_adjust=config["auto_adjust"],
            try_suffixes=True
        )

        eval_res = evaluate_filters_for_symbol(
            pred_row,
            price_df,
            config
        )

        last_price = eval_res.get("last_price")
        pct_diff = eval_res.get("pct_diff")
        abs_diff = eval_res.get("abs_diff")

        # ----- PROXIMITY (EXACT STREAMLIT LOGIC)
        proximity_ok = False

        if pct_diff is not None and abs(pct_diff) <= config["min_pct_tolerance"]:
            proximity_ok = True

        if config["min_abs_tolerance"] > 0:
            if abs_diff is not None and abs(abs_diff) <= config["min_abs_tolerance"]:
                proximity_ok = True

        if not proximity_ok:
            continue

        passes_all = eval_res.get("overall", False)
        suggested_action = eval_res.get("suggested_action")

        if config["actionable_only"] and not passes_all:
            continue

        rows.append({
            "stock_symbol": r.stock_symbol,
            "prediction_date": r.prediction_date,
            "entry_price": r.entry_price,
            "last_price": last_price,
            "pct_diff": pct_diff,
            "diff": abs_diff,
            "orig_signal": r.trade_signal,
            "suggested_action": suggested_action,
            "passes_all_filters": passes_all,
            "rsi": eval_res.get("rsi"),
            "ma20": eval_res.get("ma20"),
            "macd_hist": eval_res.get("macd_hist"),
            "vol_ratio": eval_res.get("vol_ratio"),
            "reward_risk": eval_res.get("reward_risk"),
            "gap_pct": eval_res.get("gap_pct"),
            "atr_pct": eval_res.get("atr_pct"),
            "probability": r.probability_success
        })

    # ----- EXACT SAME SORTING AS OLD SCREEN
    rows = sorted(
        rows,
        key=lambda x: (
            not x["passes_all_filters"],
            abs(x["pct_diff"]) if x["pct_diff"] is not None else 999
        )
    )

    return rows


