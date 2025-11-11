import sys, os, json
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
import psycopg2
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

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

# Filters
col1, col2 = st.columns(2)
with col1:
    stock_filter = st.text_input("Filter by Stock Symbol (optional)", "")
with col2:
    limit = st.number_input("Number of records to display", min_value=10, max_value=1000, value=100, step=10)

# ✅ Query
query = """
    SELECT id, prediction_date, stock_symbol, trade_signal,
           entry_price, target_price, stop_loss,
           probability_success, raw_data
    FROM predictions
"""

params = []
if stock_filter:
    query += " WHERE stock_symbol ILIKE %s"
    params.append(f"%{stock_filter}%")

query += " ORDER BY prediction_date DESC LIMIT %s"
params.append(limit)

conn = get_conn()
df = pd.read_sql(query, conn, params=params)
conn.close()

if df.empty:
    st.warning("No predictions found.")
else:

    # ✅ Display only "table" columns – not raw_data
    table_df = df[[
        "prediction_date", "stock_symbol", "trade_signal",
        "entry_price", "target_price", "stop_loss", "probability_success"
    ]]

    st.subheader("📊 Prediction Records")
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.write("### 🔍 Click below to view full data")

    # ✅ Now show buttons row-by-row
    for index, row in df.iterrows():
        with st.expander(f"{row['stock_symbol']} - {row['prediction_date']}"):
            st.write(f"**Entry Price:** {row['entry_price']}")
            st.write(f"**Target Price:** {row['target_price']}")
            st.write(f"**Stop Loss:** {row['stop_loss']}")
            st.write(f"**Probability Success:** {row['probability_success']}%")

            # ✅ Button to show raw JSON
            if st.button(f"View Raw JSON ({row['id']})", key=f"btn_{row['id']}"):

                raw = row["raw_data"]
                try:
                    # JSONB stored as dict
                    if isinstance(raw, dict):
                        st.json(raw)

                    # JSON stored as string
                    elif isinstance(raw, str):
                        raw_str = raw.strip()
                        parsed = json.loads(raw_str.replace("'", "\""))
                        st.json(parsed)

                    else:
                        st.warning("⚠️ raw_data is missing or not JSON")

                except Exception as e:
                    st.error(f"❌ Could not read raw_data field: {e}")

    # ✅ CSV Download
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download as CSV",
        data=csv,
        file_name="predictions_history.csv",
        mime="text/csv",
    )
