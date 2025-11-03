import pandas as pd
import streamlit as st
from sqlalchemy import create_engine
import os
from dotenv import load_dotenv

# Load DB config
load_dotenv()
engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

st.set_page_config(page_title="Top Picks", layout="wide")
st.title("🏆 Top Stock Predictions")

# Filters
days = st.slider("Show last X days", 1, 30, 1)
signal_filter = st.selectbox("Trade Signal", ["All", "Strong Buy", "Neutral", "Strong Sell"])

query = f"""
    SELECT stock_symbol, trade_signal, rank_score,
           probability_success, technical_score, risk_reward,
           analyzed_price, target_price, stop_loss,  analysis_date
    FROM prediction_summary
    WHERE analysis_date >= CURRENT_DATE - INTERVAL '{days} days'
"""

if signal_filter != "All":
    query += f" AND trade_signal = '{signal_filter}'"

query += " ORDER BY rank_score DESC"

df = pd.read_sql(query, engine)

if not df.empty:
    st.dataframe(df, use_container_width=True)
else:
    st.warning("No prediction summary data found.")
