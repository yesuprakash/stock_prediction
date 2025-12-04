# near_entry_screen.py
import os
from datetime import datetime, timedelta
import math
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
# Utilities: flatten columns, find series
# ---------------------------
def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
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
    if df is None or df.empty:
        return None
    lowered = {str(c).lower(): c for c in df.columns}
    # exact match
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

# ---------------------------
# yfinance fetch helpers
# ---------------------------
def _try_yf_history(symbol: str, period="60d", auto_adjust=False):
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=auto_adjust)
        if isinstance(df, pd.DataFrame) and not df.empty:
            df = _flatten_columns(df)
        return df
    except Exception:
        return pd.DataFrame()

def fetch_price_df(symbol: str, period="60d", auto_adjust=False, try_suffixes=True):
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
# Indicator functions
# ---------------------------
def calc_rsi(series: pd.Series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    # Wilder smoothing
    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / roll_down
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_ema(series: pd.Series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_macd(series: pd.Series):
    ema12 = calc_ema(series, 12)
    ema26 = calc_ema(series, 26)
    macd = ema12 - ema26
    signal = calc_ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist

def calc_atr(df: pd.DataFrame, period=14):
    # expects df with 'High', 'Low', 'Close' columns
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return atr

# ---------------------------
# DB helpers
# ---------------------------
def fetch_active_predictions(latest_only=True):
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

def fetch_signal_on_date(symbol: str, on_date: datetime.date):
    q = """
        SELECT trade_signal FROM predictions
        WHERE stock_symbol = %s AND DATE(prediction_date) = %s
        ORDER BY prediction_date DESC LIMIT 1
    """
    try:
        cur_df = pd.read_sql(q, engine, params=(symbol, on_date))
        if cur_df is None or cur_df.empty:
            return None
        return cur_df.iloc[0]['trade_signal']
    except Exception:
        return None

# ---------------------------
# Evaluate per-symbol filters
# ---------------------------
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

# ---------------------------
# Streamlit UI main
# ---------------------------
def run_screen():
    st.set_page_config(page_title="Near-Entry Strong Filters", layout="wide")
    st.title("🔎 Near Entry — Enhanced Quality Filters (RSI+MA+MACD)")

    # Sidebar settings (user-adjustable thresholds)
    st.sidebar.header("Filter settings & toggles")
    max_pred_age = st.sidebar.number_input("Max prediction age (days)", min_value=0, max_value=30, value=3, step=1)
    min_pct_tolerance = st.sidebar.slider("Max distance from entry (%) to include", 0.1, 10.0, 2.0, 0.1)
    min_abs_tolerance = st.sidebar.number_input("Or max absolute distance (price) - 0 to ignore", value=0.0, min_value=0.0, step=1.0)
    # Filter toggles
    use_rsi = st.sidebar.checkbox("Use RSI filter", True)
    use_ma = st.sidebar.checkbox("Use MA20 filter", True)
    use_macd = st.sidebar.checkbox("Use MACD histogram filter", True)
    use_volume = st.sidebar.checkbox("Use Volume filter", True)
    use_rr = st.sidebar.checkbox("Use Reward/Risk filter", True)
    use_gap = st.sidebar.checkbox("Use Gap filter", True)
    use_volatility = st.sidebar.checkbox("Use Volatility (ATR%) filter", True)

    # Thresholds
    rsi_min = st.sidebar.slider("RSI min (for BUY)", 30, 70, 45, 1)
    ma_period = st.sidebar.number_input("MA period (days)", min_value=5, max_value=50, value=20, step=1)
    macd_hist_min = st.sidebar.number_input("MACD hist min (>=)", value=0.0, step=0.1)
    vol_ratio_min = st.sidebar.number_input("Min volume ratio vs 5d avg", value=1.2, step=0.1)
    min_rr = st.sidebar.number_input("Min Reward/Risk (e.g. 1.5)", value=1.5, step=0.1)
    max_gap_pct = st.sidebar.number_input("Max acceptable gap %", value=1.5, step=0.1)
    max_atr_pct = st.sidebar.number_input("Max ATR% (volatility)", value=5.0, step=0.1)

    # Other options
    latest_only = st.sidebar.checkbox("Use only latest prediction per stock", value=True)
    allow_short_on_reversal = st.sidebar.checkbox("Allow 'consider short' when signal reversed (manual review only)", value=False)
    auto_adjust = st.sidebar.checkbox("yfinance auto_adjust (apply dividends/splits)", value=False)
    period_for_history = st.sidebar.selectbox("Price history lookback (days)", options=["60d", "90d", "120d"], index=0)
    show_debug = st.sidebar.checkbox("Show debug info", value=False)
    actionable_only = st.sidebar.checkbox("Show only actionable rows", value=False)

    st.info("This screen applies multiple confirmations (RSI + 20MA + MACD + Volume + RR + Gap + ATR) to shortlist robust trades near entry price.")

    # Build config dict
    config = {
        'max_prediction_age_days': int(max_pred_age),
        'rsi_period': 14,
        'rsi_min': int(rsi_min),
        'use_rsi_filter': use_rsi,
        'ma_period': int(ma_period),
        'use_ma_filter': use_ma,
        'use_macd_filter': use_macd,
        'macd_hist_min': float(macd_hist_min),
        'use_volume_filter': use_volume,
        'min_volume_ratio': float(vol_ratio_min),
        'use_rr_filter': use_rr,
        'min_reward_risk': float(min_rr),
        'use_gap_filter': use_gap,
        'max_gap_pct': float(max_gap_pct),
        'use_volatility_filter': use_volatility,
        'max_atr_pct': float(max_atr_pct),
        'atr_period': 14,
        'allowed_signals': ['Strong Buy', 'Buy', 'Bullish']  # acceptable original signals
    }

    # Fetch predictions
    with st.spinner("Loading predictions..."):
        preds = fetch_active_predictions(latest_only=latest_only)

    if preds is None or preds.empty:
        st.warning("No predictions found in DB.")
        return

    # Cache price fetch
    @st.cache_data(ttl=300)
    def _get_price_df_for_symbol(sym: str):
        return fetch_price_df(sym, period=period_for_history, auto_adjust=auto_adjust, try_suffixes=True)

    rows = []
    total = len(preds)
    progress = st.progress(0)

    for i, r in enumerate(preds.itertuples(index=False), start=1):
        sym = getattr(r, "stock_symbol")
        pred_row = {
            'stock_symbol': sym,
            'prediction_date': getattr(r, "prediction_date"),
            'entry_price': getattr(r, "entry_price"),
            'target_price': getattr(r, "target_price"),
            'stop_loss': getattr(r, "stop_loss"),
            'trade_signal': getattr(r, "trade_signal"),
            'probability_success': getattr(r, "probability_success")
        }

        price_df = _get_price_df_for_symbol(sym)
        eval_res = evaluate_filters_for_symbol(pred_row, price_df, config)

        # proximity filter (tolerance)
        last_price = eval_res.get('last_price')
        pct_diff = eval_res.get('pct_diff')
        abs_diff = eval_res.get('abs_diff')
        proximity_ok = False
        if pct_diff is not None and abs(pct_diff) <= float(min_pct_tolerance):
            proximity_ok = True
        if min_abs_tolerance > 0 and abs_diff is not None and abs(abs_diff) <= float(min_abs_tolerance):
            proximity_ok = True

        # check current signal match (on last price date)
        today_signal = None
        if eval_res.get('last_date') is not None:
            today_signal = fetch_signal_on_date(sym, eval_res.get('last_date').date())

        # decide final suggested action (considering signal reversal if needed)
        signal_status = 'no_current_signal'
        suggested_action = eval_res.get('suggested_action', '')
        if today_signal is None:
            signal_status = 'no_current_signal'
        else:
            orig_signal = pred_row.get('trade_signal')
            if orig_signal is None:
                signal_status = 'orig_missing'
            else:
                if str(orig_signal).strip().lower() == str(today_signal).strip().lower():
                    signal_status = 'match'
                else:
                    signal_status = f'reversed (now: {today_signal})'
                    # if reversed: override suggested_action to skip unless allow_short_on_reversal + today_signal is sell
                    if eval_res.get('overall', False):
                        if allow_short_on_reversal and str(today_signal).strip().lower() in ('sell','strong sell','short'):
                            # mark consider short (manual)
                            suggested_action = 'CONSIDER SHORT (manual review)'
                        else:
                            suggested_action = 'SKIP (signal reversed) - manual review'
                    else:
                        # keep previous suggested_action (likely skip for other filters)
                        pass

        include_row = proximity_ok and (eval_res.get('overall', False))
        # if proximity ok but filters fail, include as flagged row (optional to show)
        # decide final include based on actionable_only toggle
        should_show = False
        if actionable_only:
            should_show = include_row and (signal_status == 'match')
        else:
            # show if proximity ok (even flagged) OR if it passes all filters but maybe signal reversed
            should_show = proximity_ok

        if should_show:
            rows.append({
                'stock_symbol': sym,
                'prediction_date': pred_row.get('prediction_date'),
                'entry_price': pred_row.get('entry_price'),
                'last_price': last_price,
                'last_price_date': eval_res.get('last_date'),
                'pct_diff (%)': round(pct_diff, 4) if pct_diff is not None else None,
                'diff (last-entry)': round(abs_diff, 6) if abs_diff is not None else None,
                'orig_signal': pred_row.get('trade_signal'),
                'current_signal': today_signal,
                'signal_status': signal_status,
                'suggested_action': suggested_action,
                'passes_all_filters': eval_res.get('overall', False),
                'rsi': round(eval_res.get('rsi'), 2) if eval_res.get('rsi') is not None else None,
                'ma20': round(eval_res.get('ma20'), 4) if eval_res.get('ma20') is not None else None,
                'macd_hist': round(eval_res.get('macd_hist'), 6) if eval_res.get('macd_hist') is not None else None,
                'vol_ratio': round(eval_res.get('vol_ratio'), 3) if eval_res.get('vol_ratio') is not None else None,
                'reward_risk': round(eval_res.get('reward_risk'), 3) if eval_res.get('reward_risk') is not None else None,
                'gap_pct': round(eval_res.get('gap_pct'), 3) if eval_res.get('gap_pct') is not None else None,
                'atr_pct': round(eval_res.get('atr_pct'), 3) if eval_res.get('atr_pct') is not None else None,
                'probability': pred_row.get('probability_success')
            })

        progress.progress(int(i / total * 100))

    progress.empty()

    if not rows:
        st.info("No symbols matched proximity/settings.")
        return

    df_res = pd.DataFrame(rows)
    # sort by passes_all_filters desc then proximity
    df_res = df_res.sort_values(by=['passes_all_filters', 'pct_diff (%)'], ascending=[False, True], key=lambda s: s.abs() if s.name == 'pct_diff (%)' else s)

    st.markdown(f"### {len(df_res)} candidates (sorted: actionable first)")
    st.dataframe(df_res.reset_index(drop=True))

    csv = df_res.to_csv(index=False)
    st.download_button("Download CSV", csv, file_name="near_entry_filtered.csv", mime="text/csv")

if __name__ == "__main__":
    run_screen()
