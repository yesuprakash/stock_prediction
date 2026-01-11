# event_day_check_screen.py
import os
import requests
import pandas as pd
import yfinance as yf
import streamlit as st
from datetime import datetime
from dotenv import load_dotenv

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
load_dotenv()
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")

# -------------------------------------------------
# HELPERS
# -------------------------------------------------
def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def fetch_company_events(symbol):
    """Check earnings & corporate actions"""
    try:
        t = yf.Ticker(symbol)
        cal = t.calendar
        if cal is not None and not cal.empty:
            return "EARNINGS / CORPORATE EVENT"
    except Exception:
        pass
    return None

def fetch_news_event(symbol):
    """Check breaking news intensity"""
    if not NEWSAPI_KEY:
        return None

    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": symbol.replace(".NS", ""),
            "from": today_str(),
            "sortBy": "publishedAt",
            "language": "en",
            "apiKey": NEWSAPI_KEY
        }
        r = requests.get(url, params=params, timeout=6)
        data = r.json()
        articles = data.get("articles", [])
        if len(articles) >= 5:
            return f"High news activity ({len(articles)} articles)"
    except Exception:
        pass
    return None

def check_global_market():
    """Simple global stress proxy"""
    try:
        nifty = yf.download("^NSEI", period="2d", progress=False)
        if len(nifty) >= 2:
            pct = (nifty["Close"].iloc[-1] - nifty["Close"].iloc[-2]) / nifty["Close"].iloc[-2] * 100
            if pct <= -1.5:
                return f"NIFTY down {pct:.2f}%"
    except Exception:
        pass
    return None

def check_macro_day():
    """Static macro calendar (extendable)"""
    today = datetime.now().strftime("%m-%d")

    macro_days = {
        "02-01": "Union Budget period",
        "04-01": "RBI Policy window",
        "06-01": "RBI Policy window",
        "09-01": "US Fed / CPI window"
    }

    return macro_days.get(today)

# -------------------------------------------------
# STREAMLIT UI
# -------------------------------------------------
def run_screen():
    st.set_page_config(page_title="Event Day Check", layout="centered")
    st.title("📅 Major Event Day Checker")

    st.info(
        "This screen answers:\n\n"
        "**Is today a major event day (results / budget / RBI / global crash)?**\n\n"
        "Use this **before placing a BUY** from Near Entry screen."
    )

    symbol = st.text_input("Enter stock symbol (e.g. SBILIFE.NS)")

    if st.button("Check Today"):
        if not symbol:
            st.warning("Enter a stock symbol")
            return

        reasons = []

        with st.spinner("Checking events..."):
            company_event = fetch_company_events(symbol)
            if company_event:
                reasons.append(company_event)

            news_event = fetch_news_event(symbol)
            if news_event:
                reasons.append(news_event)

            macro_event = check_macro_day()
            if macro_event:
                reasons.append(macro_event)

            global_event = check_global_market()
            if global_event:
                reasons.append(global_event)

        if reasons:
            st.error("⚠️ **YES — Today is a MAJOR EVENT DAY**")
            st.markdown("### Reasons:")
            for r in reasons:
                st.write(f"- {r}")
            st.warning("Recommendation: Reduce position size or wait for close.")
        else:
            st.success("✅ **NO — Today is NOT a major event day**")
            st.info("Normal technical logic applies.")

# -------------------------------------------------
if __name__ == "__main__":
    run_screen()
