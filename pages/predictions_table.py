import os
import psycopg2
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Load DB credentials
load_dotenv()

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

st.set_page_config(page_title="📜 Predictions Table")

st.title("📜 All Predictions History")

# Optional filters
col1, col2 = st.columns(2)
with col1:
    stock_filter = st.text_input("Filter by Stock Symbol (optional)", "")
with col2:
    limit = st.number_input("Number of records to display", min_value=10, max_value=1000, value=100, step=10)

# Build SQL
query = """
    SELECT prediction_date, stock_symbol, trade_signal,
           entry_price, target_price, stop_loss,
           probability_success
           raw_data
    FROM predictions
"""
params = []

if stock_filter:
    query += " WHERE stock_symbol ILIKE %s"
    params.append(f"%{stock_filter}%")

query += " ORDER BY prediction_date DESC LIMIT %s"
params.append(limit)

# Fetch data
conn = get_conn()
df = pd.read_sql(query, conn, params=params)
conn.close()

if df.empty:
    st.warning("No predictions found.")
else:
    # Pretty display
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )

    # Optional: CSV download
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download as CSV",
        data=csv,
        file_name="predictions_history.csv",
        mime="text/csv",
    )
