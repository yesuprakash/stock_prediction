import os
import sys
import streamlit as st
import pandas as pd
from datetime import datetime

# -------------------------------------------------
# FIX IMPORT PATH (IMPORTANT)
# -------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.near_entry_core import run_near_entry_logic


# -------------------------------------------------
# SCREEN
# -------------------------------------------------
def run_screen():

    st.set_page_config(
        page_title="Near Entry — NEW",
        layout="wide"
    )

    st.title("🔎 Near Entry — Enhanced Quality Filters (RSI+MA+MACD)")

    # -------------------------------------------------
    # SIDEBAR — EXACT MATCH WITH OLD SCREEN
    # -------------------------------------------------
    st.sidebar.header("Filter settings & toggles")

    # Core proximity
    max_pred_age = st.sidebar.number_input(
        "Max prediction age (days)",
        min_value=0, max_value=30,
        value=3, step=1
    )

    min_pct_tolerance = st.sidebar.slider(
        "Max distance from entry (%) to include",
        0.1, 10.0, 2.0, 0.1
    )

    min_abs_tolerance = st.sidebar.number_input(
        "Or max absolute distance (price) - 0 to ignore",
        value=0.0, min_value=0.0, step=1.0
    )

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

    ma_period = st.sidebar.number_input(
        "MA period (days)", min_value=5, max_value=50, value=20, step=1
    )

    macd_hist_min = st.sidebar.number_input(
        "MACD hist min (>=)", value=0.0, step=0.1
    )

    vol_ratio_min = st.sidebar.number_input(
        "Min volume ratio vs 5d avg", value=1.2, step=0.1
    )

    min_rr = st.sidebar.number_input(
        "Min Reward/Risk (e.g. 1.5)", value=1.5, step=0.1
    )

    max_gap_pct = st.sidebar.number_input(
        "Max acceptable gap %", value=1.5, step=0.1
    )

    max_atr_pct = st.sidebar.number_input(
        "Max ATR% (volatility)", value=5.0, step=0.1
    )

    # Other options
    latest_only = st.sidebar.checkbox(
        "Use only latest prediction per stock", value=True
    )

    allow_short_on_reversal = st.sidebar.checkbox(
        "Allow 'consider short' when signal reversed (manual review only)",
        value=False
    )

    auto_adjust = st.sidebar.checkbox(
        "yfinance auto_adjust (apply dividends/splits)",
        value=False
    )

    period_for_history = st.sidebar.selectbox(
        "Price history lookback (days)",
        options=["60d", "90d", "120d"],
        index=0
    )

    show_debug = st.sidebar.checkbox("Show debug info", value=False)

    actionable_only = st.sidebar.checkbox(
        "Show only actionable rows", value=False
    )

    st.info(
        "This screen applies multiple confirmations "
        "(RSI + 20MA + MACD + Volume + RR + Gap + ATR) "
        "to shortlist robust trades near entry price."
    )

    # -------------------------------------------------
    # BUILD CONFIG (SAME STRUCTURE AS OLD)
    # -------------------------------------------------
    config = {
    "max_prediction_age_days": max_pred_age,
    "rsi_period": 14,
    "rsi_min": rsi_min,
    "use_rsi_filter": use_rsi,

    "ma_period": ma_period,
    "use_ma_filter": use_ma,

    "use_macd_filter": use_macd,
    "macd_hist_min": macd_hist_min,

    "use_volume_filter": use_volume,
    "min_volume_ratio": vol_ratio_min,

    "use_rr_filter": use_rr,
    "min_reward_risk": min_rr,

    "use_gap_filter": use_gap,
    "max_gap_pct": max_gap_pct,

    "use_volatility_filter": use_volatility,
    "max_atr_pct": max_atr_pct,

    "atr_period": 14,
    "allowed_signals": ['Strong Buy', 'Buy', 'Bullish'],

    "min_pct_tolerance": min_pct_tolerance,
    "min_abs_tolerance": min_abs_tolerance,
    "latest_only": latest_only,
    "auto_adjust": auto_adjust,
    "period_for_history": period_for_history,
    "actionable_only": actionable_only
}


    # -------------------------------------------------
    # RUN CORE LOGIC
    # -------------------------------------------------
    with st.spinner("Loading predictions..."):
        rows = run_near_entry_logic(config)

    if not rows:
        st.info("No symbols matched proximity/settings.")
        return

    df_res = pd.DataFrame(rows)

    # Same sorting logic as original
    if "pct_diff (%)" in df_res.columns:
        df_res = df_res.sort_values(
            by=["passes_all_filters", "pct_diff (%)"],
            ascending=[False, True],
            key=lambda s: s.abs() if s.name == "pct_diff (%)" else s
        )

    st.markdown(f"### {len(df_res)} candidates (sorted: actionable first)")
    st.dataframe(df_res.reset_index(drop=True), use_container_width=True)

    csv = df_res.to_csv(index=False)
    st.download_button(
        "Download CSV",
        csv,
        file_name="near_entry_filtered.csv",
        mime="text/csv"
    )


# -------------------------------------------------
# EXECUTE
# -------------------------------------------------
if __name__ == "__main__":
    run_screen()
