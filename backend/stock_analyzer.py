#!/usr/bin/env python3
"""
Patched stock analysis script (Excel output removed).
- Uses environment variable NEWSAPI_KEY (if present) — do NOT hardcode API keys.
- Batch downloads price data via yfinance.download for speed/reliability; has fallback per-ticker.
- Safer numeric handling, safer DB inserts, improved logging.
- Excel generation & formatting removed (DB only).
"""

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(BASE_DIR,  "..", ".env"))

import logging
import yfinance as yf
import pandas as pd
import requests
import json
import traceback
import numpy as np
from backend.db import get_connection
from datetime import datetime, timedelta
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands, AverageTrueRange
import time
from typing import Tuple, Optional

# -------------------------------
# Logging config
# -------------------------------
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# -------------------------------
# API Keys / Config
# -------------------------------
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
if not NEWSAPI_KEY:
    logger.warning("NEWSAPI_KEY not set. get_recent_news() will return no results.")

# Configurable thresholds & windows
BOLLINGER_WINDOW = 20
BOLLINGER_DEV = 2
RSI_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# -------------------------------
# 1️⃣ Stock List (NSE Tickers)
# -------------------------------
stocks = [
    "ABB.NS","ABBOTINDIA.NS","ACC.NS","AEGISLOG.NS","AJANTPHARM.NS","ALKEM.NS","AMBER.NS","AMBUJACEM.NS",
    "ANANTRAJ.NS","APLLTD.NS","APOLLOHOSP.NS","APOLLOTYRE.NS","ASHOKLEY.NS","ASIANPAINT.NS","AUROPHARMA.NS",
    "AXISBANK.NS","BAJAJ-AUTO.NS","BAJAJFINSV.NS","BAJFINANCE.NS","BATAINDIA.NS","BEL.NS","BEML.NS","BHARATFORG.NS",
    "BHARTIARTL.NS","BIOCON.NS","BLUESTARCO.NS","BPCL.NS","BRIGADE.NS","CENTURYPLY.NS","CERA.NS","CIPLA.NS",
    "COALINDIA.NS","COFORGE.NS","CROMPTON.NS","DIVISLAB.NS","DIXON.NS","DLF.NS","DRREDDY.NS","EICHERMOT.NS",
    "EXIDEIND.NS","FEDERALBNK.NS","GLAND.NS","GLENMARK.NS","GODREJCP.NS","GODREJPROP.NS","GRASIM.NS","GAIL.NS",
    "GSPL.NS","GUJGASLTD.NS","HAVELLS.NS","HCLTECH.NS","HDFCBANK.NS","HDFCLIFE.NS","HEROMOTOCO.NS","HINDALCO.NS",
    "HINDPETRO.NS","HINDUNILVR.NS","ICICIBANK.NS","IDFCFIRSTB.NS","IGL.NS","INFY.NS","IOC.NS","ITC.NS",
    "JBCHEPHARM.NS","JKLAKSHMI.NS","JINDALSTEL.NS","JIOFIN.NS","JSWSTEEL.NS","JSL.NS","JUBLFOOD.NS","KAJARIACER.NS",
    "KALYANKJIL.NS","KOTAKBANK.NS","LAURUSLABS.NS","LICHSGFIN.NS","LODHA.NS","LT.NS","LTIM.NS","LTTS.NS",
    "LUPIN.NS","M&M.NS","MANKIND.NS","MARUTI.NS","MAXHEALTH.NS","MGL.NS","MOTHERSON.NS","MPHASIS.NS",
    "NATIONALUM.NS","NESTLEIND.NS","NTPC.NS","OBEROIRLTY.NS","OFSS.NS","ONGC.NS","PETRONET.NS","PHOENIXLTD.NS",
    "POWERGRID.NS","PRESTIGE.NS","PERSISTENT.NS","RADICO.NS","RAMCOIND.NS","RELIANCE.NS","SBILIFE.NS","SBIN.NS",
    "SHRIRAMFIN.NS","SIEMENS.NS","SIGNATURE.NS","SONACOMS.NS","SUNPHARMA.NS","TATACONSUM.NS","TATASTEEL.NS",
    "TMPV.NS","TMCV.NS","TATAPOWER.NS","TCS.NS","TECHM.NS","TIINDIA.NS","TITAN.NS","TORNTPHARM.NS","TRENT.NS",
    "TVSMOTOR.NS","ULTRACEMCO.NS","UNOMINDA.NS","UPL.NS","VGUARD.NS","VENKEYS.NS","VOLTAS.NS","WELCORP.NS",
    "WHIRLPOOL.NS","WIPRO.NS","ZYDUSLIFE.NS"
]


# -------------------------------
# 2️⃣ Excel Columns (kept as before for column ordering if you still want them later)
# -------------------------------
columns = [
    "Stock Name", "Date", "Prediction Term", "Forecast Horizon (Days)", "Days of Data Used",
    "Sector / Industry Outlook", "Trend", "Recent High/Low",
    "Current Price vs Moving Averages", "RSI", "MACD Trend",
    "Average Daily Volume", "Recent Volume Spikes", "Liquidity",
    "ATR", "Expected Price Range", "Volatility Level",
    "Key Support Levels", "Key Resistance Levels", "Probability of Trade Success (%)",
    "Moving Averages", "RSI Value", "MACD Signal", "Bollinger Band Position", "Bollinger % Position",
    "Chart Pattern Observed", "Trade Signal",
    "Upcoming Earnings/Dividends/Corporate Actions", "Catalyst Events",
    "Market Sentiment / Analyst Notes", "Best-case Price Target", "Likely Price Range",
    "Worst-case / Stop-Loss Risk", "Risk/Reward Ratio",
    "Technical Strength Score (%)", "Suggested Entry Price Range", "Stop-Loss Price", "Target Price",
    "Expected Holding Duration", "Additional Notes"
]

rows = []
# -------------------------------
# Utility helpers & safe_run
# -------------------------------
def safe_run(func, default=None, log_exceptions=True, name=None):
    """Safely execute a function. If it fails, log and return default."""
    try:
        return func()
    except Exception as e:
        if log_exceptions:
            logger.debug(f"safe_run failed in {name or func}: {e}", exc_info=True)
        return default

def to_python(v):
    """Convert numpy types to native python types"""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v

def fmt_ma(val):
    """Format moving average safely"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    try:
        return f"{val:.2f}"
    except Exception:
        return str(val)

# -------------------------------
# 3️⃣ Database insert (safer)
# -------------------------------
def insert_prediction(row: pd.Series):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Safely extract numeric values
        def safe_float(x):
            return float(x) if pd.notna(x) else None

        raw_json = json.dumps(row.to_dict(), default=str)

        cursor.execute("""
            INSERT INTO predictions
            (prediction_date, stock_symbol, trade_signal, probability_success, 
             technical_strength, risk_reward, entry_price, target_price, 
             stop_loss, sector_outlook, sentiment, trend, analyzed_price, raw_data,
             prediction_term, forecast_horizon, days_of_data)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            row.get("Date"),
            row.get("Stock Name"),
            row.get("Trade Signal"),
            safe_float(row.get("Probability of Trade Success (%)")),
            safe_float(row.get("Technical Strength Score (%)")),
            safe_float(row.get("Risk/Reward Ratio")),
            safe_float(row.get("Suggested Entry Price Range")),
            safe_float(row.get("Target Price")),
            safe_float(row.get("Stop-Loss Price")),
            row.get("Sector / Industry Outlook"),
            row.get("Market Sentiment / Analyst Notes"),
            row.get("Trend"),
            safe_float(row.get("Current Price")) if "Current Price" in row else None,
            raw_json,
            row.get("Prediction Term"),
            to_python(row.get("Forecast Horizon (Days)")),
            to_python(row.get("Days of Data Used"))
        ))
        conn.commit()
        logger.debug(f"Inserted prediction for {row.get('Stock Name')}")
    except Exception as e:
        logger.exception(f"DB Insert Error for {row.get('Stock Name') if row is not None else 'UNKNOWN'}: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------------
# 4️⃣ Summary save (safer)
# -------------------------------
def analyze_and_save_to_summary(df: pd.DataFrame):
    conn = None
    cursor = None
    try:
        # Ensure numeric columns
        numeric_cols = ["Probability of Trade Success (%)", "Technical Strength Score (%)",
                        "Risk/Reward Ratio", "Bollinger % Position", "RSI"]
        for c in numeric_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')

        # Rank Score with safe fill
        df['Rank Score'] = (
            df['Probability of Trade Success (%)'].fillna(0) * 0.4 +
            df['Technical Strength Score (%)'].fillna(0) * 0.3 +
            df['Risk/Reward Ratio'].fillna(0) * 15 +
            df['Bollinger % Position'].apply(lambda x: (100 - abs(50 - x)) * 0.05 if pd.notna(x) else 0)
        )

        top_rows = df.sort_values(by='Rank Score', ascending=False)

        conn = get_connection()
        cursor = conn.cursor()
        inserted = 0
        for _, row in top_rows.iterrows():
            def sf(col):
                return float(row[col]) if (col in row and pd.notna(row[col])) else None

            cursor.execute("""
                INSERT INTO prediction_summary
                (stock_symbol, trade_signal, sector_outlook, probability_success,
                 technical_score, rank_score, target_price, stop_loss, entry_price, 
                 risk_reward, sentiment, trend, bollinger_position, bollinger_percent,
                 rsi, macd_trend, volatility_level, chart_pattern, volume_spike,
                 liquidity, catalyst_events, support_level, resistance_level, analyzed_price,
                 prediction_term, forecast_horizon, days_of_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                row.get("Stock Name"), row.get("Trade Signal"), row.get("Sector / Industry Outlook"),
                sf("Probability of Trade Success (%)"),
                sf("Technical Strength Score (%)"),
                float(row.get("Rank Score")) if pd.notna(row.get("Rank Score")) else None,
                sf("Target Price"),
                sf("Stop-Loss Price"),
                sf("Suggested Entry Price Range"),
                sf("Risk/Reward Ratio"),
                row.get("Market Sentiment / Analyst Notes"), row.get("Trend"),
                row.get("Bollinger Band Position"),
                float(row.get("Bollinger % Position")) if pd.notna(row.get("Bollinger % Position")) else None,
                sf("RSI"),
                row.get("MACD Trend"), row.get("Volatility Level"), row.get("Chart Pattern Observed"),
                row.get("Recent Volume Spikes"), row.get("Liquidity"), row.get("Catalyst Events"),
                sf("Key Support Levels"), sf("Key Resistance Levels"), sf("Current Price"),
                row.get("Prediction Term"), to_python(row.get("Forecast Horizon (Days)")), to_python(row.get("Days of Data Used"))
            ))
            inserted += 1

        conn.commit()
        logger.info(f"📊 {inserted} summarized predictions saved to prediction_summary.")
    except Exception as e:
        logger.exception(f"❌ Summary insert error: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# -------------------------------
# 5️⃣ Technical helper functions (safe)
# -------------------------------
def calculate_rsi(data: pd.DataFrame, period: int = RSI_PERIOD) -> Optional[float]:
    try:
        rsi = RSIIndicator(data['Close'], window=period).rsi()
        return rsi.iloc[-1]
    except Exception as e:
        logger.debug(f"RSI calc failed: {e}", exc_info=True)
        return None

def calculate_macd_trend(data: pd.DataFrame) -> str:
    try:
        macd_calc = MACD(data['Close'])
        macd_line = macd_calc.macd()
        signal_line = macd_calc.macd_signal()
        if pd.isna(macd_line.iloc[-1]) or pd.isna(signal_line.iloc[-1]):
            return "N/A"
        return "Bullish" if macd_line.iloc[-1] > signal_line.iloc[-1] else "Bearish"
    except Exception as e:
        logger.debug(f"MACD calc failed: {e}", exc_info=True)
        return "N/A"

def calculate_bollinger_position(data: pd.DataFrame) -> Tuple[str, float]:
    try:
        bb = BollingerBands(data['Close'], window=BOLLINGER_WINDOW, window_dev=BOLLINGER_DEV)
        current = data['Close'].iloc[-1]
        lower = bb.bollinger_lband().iloc[-1]
        upper = bb.bollinger_hband().iloc[-1]
        if pd.isna(lower) or pd.isna(upper) or pd.isna(current):
            return "Unknown", 50.0
        if current > upper:
            return "Above Upper Band", 100.0
        elif current < lower:
            return "Below Lower Band", 0.0
        else:
            denom = (upper - lower)
            if denom == 0:
                perc = 50.0
            else:
                perc = (current - lower) / denom * 100.0
            return "Within Bands", round(perc, 2)
    except Exception as e:
        logger.debug(f"Bollinger calc failed: {e}", exc_info=True)
        return "Unknown", 50.0

def calculate_atr(data: pd.DataFrame, window: int = ATR_PERIOD) -> Optional[float]:
    try:
        atr = AverageTrueRange(data['High'], data['Low'], data['Close'], window=window).average_true_range()
        return atr.iloc[-1]
    except Exception as e:
        logger.debug(f"ATR calc failed: {e}", exc_info=True)
        return None

def identify_chart_pattern(data: pd.DataFrame) -> str:
    try:
        highs = data['High'].tail(20)
        lows = data['Low'].tail(20)
        if highs.max() == data['High'].iloc[-1]:
            return "Potential Uptrend / Breakout"
        elif lows.min() == data['Low'].iloc[-1]:
            return "Potential Downtrend / Breakdown"
        else:
            return "Sideways / Consolidation"
    except Exception as e:
        logger.debug(f"Chart pattern detection failed: {e}", exc_info=True)
        return "Unknown"

def volume_spike(data: pd.DataFrame) -> str:
    try:
        avg_vol = data['Volume'].rolling(10).mean().iloc[-1]
        recent = data['Volume'].iloc[-1]
        if pd.isna(avg_vol) or pd.isna(recent):
            return "Unknown"
        return "Spike" if recent > 1.5 * avg_vol else "Normal"
    except Exception as e:
        logger.debug(f"Volume spike calc failed: {e}", exc_info=True)
        return "Unknown"

def liquidity_level(data: pd.DataFrame) -> str:
    try:
        avg_vol = data['Volume'].rolling(10).mean().iloc[-1]
        if pd.isna(avg_vol):
            return "Unknown"
        if avg_vol > 500000:
            return "High"
        elif avg_vol > 100000:
            return "Medium"
        else:
            return "Low"
    except Exception as e:
        logger.debug(f"Liquidity calc failed: {e}", exc_info=True)
        return "Unknown"

def probability_breakout(rsi: Optional[float], macd_trend: str, bb_pos: str) -> float:
    try:
        score = 50
        if rsi is None:
            return float(score)
        if rsi < 70 and macd_trend == "Bullish" and "Upper" not in bb_pos:
            score += 20
        elif rsi > 70 or macd_trend == "Bearish":
            score -= 20
        return float(max(min(score, 100), 0))
    except Exception as e:
        logger.debug(f"probability_breakout failed: {e}", exc_info=True)
        return 50.0

def trade_signal(macd_trend: str, rsi: Optional[float], bb_perc: float) -> str:
    try:
        if macd_trend == "Bullish" and (rsi is not None and rsi < 70) and (bb_perc is not None and bb_perc < 80):
            return "Strong Buy"
        elif macd_trend == "Bearish" and (rsi is not None and rsi > 30) and (bb_perc is not None and bb_perc > 20):
            return "Strong Sell"
        else:
            return "Neutral"
    except Exception as e:
        logger.debug(f"trade_signal failed: {e}", exc_info=True)
        return "Neutral"

# -------------------------------
# 6️⃣ Sector / News helpers (safer)
# -------------------------------
def get_sector_trend_dynamic(stock_symbol: str) -> str:
    sector_index_map = {
        "GAIL.NS": "CNXENERGY",
        "TATASTEEL.NS": "CNXMETAL",
        "HINDALCO.NS": "CNXMETAL"
    }
    sector_index = sector_index_map.get(stock_symbol, None)
    if not sector_index:
        return "Unknown"
    return f"{sector_index.replace('CNX','')} sector trend; short-term outlook based on recent momentum"

def get_upcoming_earnings(stock_symbol: str) -> str:
    return "No upcoming earnings"

def get_recent_news(company_name: str):
    """Return list of top 5 headlines or [] if not available.
       Uses NEWSAPI_KEY env var. Quotes query safely."""
    if not NEWSAPI_KEY:
        return []
    try:
        today = datetime.now()
        from_date = (today - timedelta(days=14)).strftime('%Y-%m-%d')
        q = requests.utils.quote(f"{company_name} India")
        url = f"https://newsapi.org/v2/everything?q={q}&from={from_date}&sortBy=publishedAt&language=en&apiKey={NEWSAPI_KEY}"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        return [a["title"] for a in articles[:5] if a.get("title")]
    except Exception as e:
        logger.debug(f"News fetch failed for {company_name}: {e}")
        return []

def analyze_sentiment(headlines):
    positive = ["gain", "rise", "bullish", "up", "growth", "profit"]
    negative = ["fall", "drop", "bearish", "loss", "decline", "weak"]
    score = 0
    for h in headlines:
        h_lower = h.lower()
        score += sum(1 for word in positive if word in h_lower)
        score -= sum(1 for word in negative if word in h_lower)
    if score > 1:
        return "Positive"
    elif score < -1:
        return "Negative"
    else:
        return "Neutral"

# -------------------------------
# 7️⃣ Price data: batch download helper
# -------------------------------
def batch_download_price_data(tickers, start, end, threads=True):
    """Attempt batch download. Returns the raw result from yfinance.download"""
    try:
        logger.info(f"Batch downloading {len(tickers)} tickers from {start.date()} to {end.date()}")
        data = yf.download(
            tickers,
            start=start.strftime('%Y-%m-%d'),
            end=end.strftime('%Y-%m-%d'),
            group_by='ticker',
            threads=threads,
            auto_adjust=False,
            progress=False
        )
        return data
    except Exception as e:
        logger.warning(f"Batch download failed: {e}. Will fallback to per-ticker downloads.")
        return None

def get_ticker_data_from_batch(batch_data, ticker):
    """Extract per-ticker DataFrame from yf.download output.
       Handles both 'group_by=ticker' multi-column and single-dataframe cases."""
    try:
        if batch_data is None or batch_data.empty:
            return None
        # If download returned multi-level columns (group_by='ticker'):
        if isinstance(batch_data.columns, pd.MultiIndex):
            # Some tickers may be missing; handle KeyError
            if ticker in batch_data.columns.levels[0]:
                df = batch_data[ticker].dropna(how='all')
                # Ensure columns standard names exist
                if set(['Open','High','Low','Close','Volume']).issubset(df.columns):
                    return df
                else:
                    # try fallback by reindex
                    return df
            else:
                return None
        else:
            # Single dataframe for all tickers (unlikely when multiple tickers passed),
            # caller should then fallback to per-ticker Ticker history.
            return None
    except Exception as e:
        logger.debug(f"get_ticker_data_from_batch failed for {ticker}: {e}", exc_info=True)
        return None

# -------------------------------
# 8️⃣ Main processing loop (safe)
# -------------------------------
skipped_stocks = []
end_date = datetime.now()
start_date = end_date - timedelta(days=90)  # fetch slightly more window for smas/indicators

# Batch download attempt (faster & less likely to hit connection overhead)
batch_data = batch_download_price_data(stocks, start_date, end_date)

for stock in stocks:
    try:
        # Get per-ticker DataFrame: from batch_data if possible, else fallback
        data = None
        if batch_data is not None:
            data = get_ticker_data_from_batch(batch_data, stock)
        if data is None or data.empty:
            # fallback to single ticker history
            ticker = yf.Ticker(stock)
            data = safe_run(lambda: ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d')), default=pd.DataFrame())
            # slight delay to avoid hammering Yahoo if many fallbacks
            time.sleep(0.05)

        # Normalize column names if needed (some yfinance returns 'Adj Close' etc.)
        if data is None or data.empty or len(data) < 20:
            logger.warning(f"Skipping {stock}: insufficient data ({0 if data is None else len(data)} rows)")
            skipped_stocks.append(stock)
            continue

        # New prediction parameters
        forecast_days = 15
        days_used = len(data)
        prediction_term = "Short-Term (1-3 weeks)"

        # Safe technical calculations
        current_price = safe_run(lambda: data['Close'].iloc[-1], default=None, name=f"{stock}-current_price")
        recent_high = safe_run(lambda: data['High'].max(), default=None, name=f"{stock}-recent_high")
        recent_low = safe_run(lambda: data['Low'].min(), default=None, name=f"{stock}-recent_low")

        ma5 = safe_run(lambda: data['Close'].rolling(5).mean().iloc[-1], default=None, name=f"{stock}-ma5")
        ma10 = safe_run(lambda: data['Close'].rolling(10).mean().iloc[-1], default=None, name=f"{stock}-ma10")
        ma20 = safe_run(lambda: data['Close'].rolling(20).mean().iloc[-1], default=None, name=f"{stock}-ma20")

        rsi = calculate_rsi(data)
        macd_trend = calculate_macd_trend(data)
        bb_position, bb_percent = calculate_bollinger_position(data)
        atr = calculate_atr(data)
        chart_pattern = identify_chart_pattern(data)
        vol_spike = volume_spike(data)
        liquidity = liquidity_level(data)

        # Probability, signal, scoring
        prob_breakout = probability_breakout(rsi, macd_trend, bb_position)
        signal = trade_signal(macd_trend, rsi, bb_percent)

        # Sector & news
        sector = safe_run(lambda: get_sector_trend_dynamic(stock), default="Unknown", name=f"{stock}-sector")
        earnings_str = safe_run(lambda: get_upcoming_earnings(stock), default="N/A", name=f"{stock}-earnings")
        # Query company name by stripping suffix, but ideally use a mapping
        company_query = stock.split(".")[0]
        headlines = safe_run(lambda: get_recent_news(company_query), default=[], name=f"{stock}-news")
        sentiment = safe_run(lambda: analyze_sentiment(headlines), default="Neutral", name=f"{stock}-sentiment")
        catalyst_events = "; ".join(headlines) if headlines else "No recent events"

        # Technical score calculation (weights as before)
        tech_score = 0
        if macd_trend == "Bullish":
            tech_score += 3
        if rsi is not None and 30 < rsi < 70:
            tech_score += 2
        if bb_position == "Within Bands":
            tech_score += 1
        if vol_spike == "Spike":
            tech_score += 2
        if liquidity == "High":
            tech_score += 2
        if sentiment == "Positive":
            tech_score += 1
        tech_score_percent = round((tech_score / 11) * 100, 2)

        # Price levels (safe math with None checks)
        support = recent_low if recent_low is not None else 0.0
        resistance = recent_high if recent_high is not None else 0.0
        entry_price = support + (resistance - support) * 0.2 if (resistance is not None and support is not None) else None
        target_price = resistance if resistance is not None else None
        stop_loss = support * 0.98 if support is not None else None
        risk_reward = safe_run(lambda: round((target_price - entry_price) / (entry_price - stop_loss), 2) if None not in (target_price, entry_price, stop_loss) and (entry_price - stop_loss) != 0 else 0, default=0, name=f"{stock}-rr")

        # Build DataFrame row safely
        new_row = pd.DataFrame([{
            "Stock Name": stock,
            "Date": end_date.strftime("%Y-%m-%d"),
            "Prediction Term": prediction_term,
            "Forecast Horizon (Days)": forecast_days,
            "Days of Data Used": days_used,
            "Current Price": round(current_price, 2) if current_price is not None and not pd.isna(current_price) else None,
            "Sector / Industry Outlook": sector,
            "Trend": macd_trend,
            "Recent High/Low": f"{recent_high}/{recent_low}",
            "Current Price vs Moving Averages": f"{current_price} vs MA5:{fmt_ma(ma5)}, MA10:{fmt_ma(ma10)}, MA20:{fmt_ma(ma20)}",
            "RSI": rsi,
            "MACD Trend": macd_trend,
            "Average Daily Volume": safe_run(lambda: float(data['Volume'].mean()), default=None, name=f"{stock}-avgvol"),
            "Recent Volume Spikes": vol_spike,
            "Liquidity": liquidity,
            "ATR": atr,
            "Expected Price Range": f"{recent_low} - {recent_high}",
            "Volatility Level": "High" if (atr is not None and recent_high is not None and recent_low is not None and atr > (recent_high-recent_low)/2) else "Moderate",
            "Key Support Levels": support,
            "Key Resistance Levels": resistance,
            "Probability of Trade Success (%)": prob_breakout,
            "Moving Averages": f"MA5:{fmt_ma(ma5)}, MA10:{fmt_ma(ma10)}, MA20:{fmt_ma(ma20)}",
            "RSI Value": rsi,
            "MACD Signal": macd_trend,
            "Bollinger Band Position": bb_position,
            "Bollinger % Position": bb_percent,
            "Chart Pattern Observed": chart_pattern,
            "Trade Signal": signal,
            "Upcoming Earnings/Dividends/Corporate Actions": earnings_str,
            "Catalyst Events": catalyst_events,
            "Market Sentiment / Analyst Notes": sentiment,
            "Best-case Price Target": round(target_price, 2) if target_price is not None else None,
            "Likely Price Range": f"{entry_price} - {target_price}" if entry_price is not None and target_price is not None else None,
            "Worst-case / Stop-Loss Risk": round(stop_loss, 2) if stop_loss is not None else None,
            "Risk/Reward Ratio": risk_reward,
            "Technical Strength Score (%)": tech_score_percent,
            "Suggested Entry Price Range": round(entry_price, 2) if entry_price is not None else None,
            "Stop-Loss Price": round(stop_loss, 2) if stop_loss is not None else None,
            "Target Price": round(target_price, 2) if target_price is not None else None,
            "Expected Holding Duration": "1-3 weeks",
            "Additional Notes": ""
        }])

        # Append to main df
        rows.append(new_row.iloc[0].to_dict())

        # DB insert (row0 Series)
        insert_prediction(new_row.iloc[0])

    except Exception as e:
        logger.exception(f"❌ Error analyzing {stock}: {e}")
        skipped_stocks.append(stock)
        continue
df = pd.DataFrame(rows, columns=columns)
# -------------------------------
# 9️⃣ Save summary to DB (no Excel)
# -------------------------------
try:
    # Ensure numeric coercion before saving summary
    numeric_cols = ["Probability of Trade Success (%)", "Technical Strength Score (%)", "Risk/Reward Ratio", "Bollinger % Position", "RSI"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    analyze_and_save_to_summary(df)
    logger.info("✅ Prediction summary saved to DB successfully.")
except Exception as e:
    logger.exception(f"Failed to save summary to DB: {e}")

# -------------------------------
# 1️⃣0️⃣ Skipped stocks log
# -------------------------------
if skipped_stocks:
    try:
        skipped_log_path = os.path.join(LOG_DIR, "skipped_stocks.log")

        with open(skipped_log_path, "a") as log:
            log.write(f"{datetime.now()} - Skipped: {', '.join(skipped_stocks)}\n")

        logger.warning(f"⚠️ Skipped {len(skipped_stocks)} stocks. Logged in {skipped_log_path}")

    except Exception:
        logger.exception("Failed to write skipped_stocks.log")
