import sys
import os
import json
import ast
from datetime import datetime

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

st.set_page_config(page_title="📜 Predictions Table", layout="wide")
st.title("📜 All Predictions History")

# Filters
col1, col2 = st.columns([3,1])
with col1:
    stock_filter = st.text_input("Filter by Stock Symbol (optional)", "")
with col2:
    # Ensure integer return and sane bounds
    limit = st.number_input("Number of records to display", min_value=10, max_value=1000, value=100, step=10)
    try:
        limit = int(limit)
    except Exception:
        limit = 100

# Build query safely with params
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

# Read from DB using context manager
try:
    with get_conn() as conn:
        df = pd.read_sql(query, conn, params=params)
except Exception as e:
    st.error(f"Database error: {e}")
    st.stop()

if df.empty:
    st.warning("No predictions found.")
    st.stop()

# Convert prediction_date to readable string if it's a datetime
if "prediction_date" in df.columns:
    try:
        df["prediction_date_display"] = pd.to_datetime(df["prediction_date"]).dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        # fallback: convert to str
        df["prediction_date_display"] = df["prediction_date"].astype(str)
else:
    df["prediction_date_display"] = ""

# Display main table (subset of columns)
table_df = df[[
    "prediction_date_display", "stock_symbol", "trade_signal",
    "entry_price", "target_price", "stop_loss", "probability_success"
]].rename(columns={"prediction_date_display":"prediction_date"})

st.subheader("📊 Prediction Records")
st.dataframe(table_df, use_container_width=True, hide_index=True)

st.write("### 🔍 Click below to view full data")

# Ensure session_state key for raw display toggles
if "show_raw_id" not in st.session_state:
    st.session_state.show_raw_id = None

def toggle_show_raw(row_id):
    # toggle show/hide
    if st.session_state.show_raw_id == row_id:
        st.session_state.show_raw_id = None
    else:
        st.session_state.show_raw_id = row_id

# Iterate rows and present expanders; use a button to toggle raw JSON display (keeps UI tidy)
for _, row in df.iterrows():
    display_title = f"{row['stock_symbol']} - {row['prediction_date_display']}"
    with st.expander(display_title):
        st.write(f"**Entry Price:** {row['entry_price']}")
        st.write(f"**Target Price:** {row['target_price']}")
        st.write(f"**Stop Loss:** {row['stop_loss']}")
        # Format probability display
        prob = row.get("probability_success")
        try:
            prob_str = f"{float(prob):.2f}%" if pd.notna(prob) else "N/A"
        except Exception:
            prob_str = str(prob)
        st.write(f"**Probability Success:** {prob_str}")

        # Button to toggle raw JSON display for this row
        btn_key = f"btn_raw_{row['id']}"
        if st.button("View Raw JSON" if st.session_state.show_raw_id != row['id'] else "Hide Raw JSON", key=btn_key):
            toggle_show_raw(row['id'])

        # Show raw JSON if toggled on
        if st.session_state.show_raw_id == row['id']:
            raw = row.get("raw_data")
            if raw is None:
                st.warning("⚠️ raw_data is missing")
            else:
                # If it's already parsed (dict/list) show it
                if isinstance(raw, (dict, list)):
                    st.json(raw)
                elif isinstance(raw, str):
                    raw_str = raw.strip()
                    parsed = None
                    # Try JSON parse
                    try:
                        parsed = json.loads(raw_str)
                        st.json(parsed)
                    except json.JSONDecodeError:
                        # Fallback: maybe Python dict string; use ast.literal_eval safely
                        try:
                            parsed = ast.literal_eval(raw_str)
                            # If parsed is dict/list -> json display
                            if isinstance(parsed, (dict, list)):
                                st.json(parsed)
                            else:
                                st.code(str(parsed))
                        except Exception:
                            # Last resort: display raw text (first 500 chars)
                            st.code(raw_str[:500])
                            st.warning("Raw data could not be parsed as JSON or Python literal; showing raw text.")
                else:
                    # Unknown type — show repr
                    st.write(repr(raw))

# CSV Download of full resultset
csv = df.to_csv(index=False).encode('utf-8')
st.download_button(
    label="📥 Download as CSV",
    data=csv,
    file_name="predictions_history.csv",
    mime="text/csv",
)
