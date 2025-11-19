# predictions_flat_view.py
import sys
import os
import json
import ast
from datetime import datetime

# allow import of backend module if present (same pattern as your other file)
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

st.set_page_config(page_title="📜 Predictions Flat View", layout="wide")
st.title("📜 Predictions — Flat JSON View (two-line per record)")

# Controls
col1, col2, col3 = st.columns([3,1,1])
with col1:
    stock_filter = st.text_input("Filter by Stock Symbol (optional)", "")
with col2:
    limit = st.number_input("Records to show", min_value=10, max_value=2000, value=100, step=10)
    try:
        limit = int(limit)
    except Exception:
        limit = 100
with col3:
    # Option to show a uniform JSON header across all rows (union of keys) or per-row keys
    uniform_json_cols = st.checkbox("Use uniform JSON columns for all rows (union of keys)", value=False)

# Fetch rows from DB
query = "SELECT * FROM predictions"
params = []

if stock_filter:
    query += " WHERE stock_symbol ILIKE %s"
    params.append(f"%{stock_filter}%")

query += " ORDER BY prediction_date DESC LIMIT %s"
params.append(limit)

try:
    with get_conn() as conn:
        df = pd.read_sql(query, conn, params=params)
except Exception as e:
    st.error(f"Database error: {e}")
    st.stop()

if df.empty:
    st.warning("No records found.")
    st.stop()


# Prepare flattened DF for CSV download (DB columns + JSON-expanded columns)
flattened_rows = []
all_json_keys = set()

def parse_raw_data(raw):
    """Try JSON -> ast.literal_eval -> return dict or None"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        txt = raw.strip()
        # Try strict JSON first
        try:
            parsed = json.loads(txt)
            return parsed
        except Exception:
            # Fallback to Python literal
            try:
                parsed = ast.literal_eval(txt)
                return parsed
            except Exception:
                # cannot parse; return raw string
                return txt
    # Other types: return representation
    try:
        return dict(raw)
    except Exception:
        return str(raw)

# Pre-parse all raw_data and optionally collect union of json keys
parsed_jsons = []
for _, row in df.iterrows():
    raw = row.get("raw_data")
    parsed = parse_raw_data(raw)
    parsed_jsons.append(parsed)
    if isinstance(parsed, dict):
        all_json_keys.update(parsed.keys())

# ------------------------------------
# JSON is now parsed → we can filter by upcoming earnings
# ------------------------------------
filter_upcoming = st.checkbox(
    "Show only stocks with upcoming earnings/dividends/corporate actions",
    value=False
)

if filter_upcoming:
    filtered_rows = []
    filtered_jsons = []

    for idx, parsed in enumerate(parsed_jsons):
        row = df.iloc[idx]
        if isinstance(parsed, dict):
            upcoming = parsed.get("Upcoming Earnings/Dividends/Corporate Actions", "")
            if upcoming and upcoming != "No upcoming earnings":
                filtered_rows.append(row)
                filtered_jsons.append(parsed)
        # ignore unparsable JSON

    # Replace df & parsed_jsons with filtered lists
    if filtered_rows:
        df = pd.DataFrame(filtered_rows)
        parsed_jsons = filtered_jsons
    else:
        st.warning("No records with upcoming earnings/dividends/corporate actions.")
        st.stop()


if uniform_json_cols:
    json_key_list = sorted(list(all_json_keys))
else:
    json_key_list = None  # will use per-row keys

# Build flattened rows for CSV export
for idx, (_, row) in enumerate(df.iterrows()):
    parsed = parsed_jsons[idx]
    flat = {}
    # copy DB columns (convert datetimes safely)
    for c in df.columns:
        val = row[c]
        if isinstance(val, (pd.Timestamp, datetime)):
            flat[c] = pd.to_datetime(val).strftime("%Y-%m-%d %H:%M:%S")
        else:
            flat[c] = val
    # expand JSON keys
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            flat[f"json_{k}"] = v
    else:
        # put raw string under a special key
        flat["raw_data_unparsed"] = parsed
    flattened_rows.append(flat)

flat_df = pd.DataFrame(flattened_rows)

# UI: show export button for flattened CSV (DB + JSON expanded)
csv = flat_df.to_csv(index=False).encode("utf-8")
st.download_button(
    label="📥 Download flattened CSV (DB + JSON fields)",
    data=csv,
    file_name="predictions_flattened.csv",
    mime="text/csv",
)

st.markdown("----")
st.write(f"Showing {len(df)} records (most recent first). Scroll vertically to inspect all rows.")

# Render each record as two visual rows:
#  - first: DB columns (header + values)
#  - second: JSON header -> JSON values (either union-of-keys or per-row keys)
for idx, (_, row) in enumerate(df.iterrows()):
    # format prediction_date nicely
    pd_date = row.get("prediction_date")
    if pd.notna(pd_date):
        try:
            pd_disp = pd.to_datetime(pd_date).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pd_disp = str(pd_date)
    else:
        pd_disp = ""

    # Build DB header and values for markdown table
    db_cols = list(df.columns)
    # Optionally remove raw_data from DB columns display to keep it neat (we'll show expanded JSON below)
    if "raw_data" in db_cols:
        db_cols_display = [c for c in db_cols if c != "raw_data"]
    else:
        db_cols_display = db_cols

    # Header row for DB columns (single-line markdown table)
    header_md = "| " + " | ".join(db_cols_display) + " |"
    sep_md = "| " + " | ".join(["---"] * len(db_cols_display)) + " |"

    # Values row: format numbers and datetimes
    val_cells = []
    for c in db_cols_display:
        val = row.get(c)
        if c == "prediction_date":
            val_cells.append(pd_disp)
        else:
            # format floats with 4 significant digits when possible
            if val is None or (isinstance(val, float) and pd.isna(val)):
                val_cells.append("")
            elif isinstance(val, float):
                val_cells.append(f"{val:.4g}")
            else:
                val_cells.append(str(val))
    values_md = "| " + " | ".join(val_cells) + " |"

    # Show DB markdown table (single-row table)
    st.markdown(header_md + "\n" + sep_md + "\n" + values_md)

    # Now JSON row - header and values
    parsed = parsed_jsons[idx]
    if parsed is None:
        st.markdown("> **-> Json data header**  \n\n> No JSON data available")
    elif isinstance(parsed, dict):
        # Determine keys to display
        if uniform_json_cols and json_key_list is not None:
            keys = json_key_list
        else:
            # per-row keys in a stable order
            keys = list(parsed.keys())

        # make header like "->Json data header ATR | RSI | Date | ...."
        json_header_md = "-> **Json data header**  \n\n| " + " | ".join(keys) + " |"
        json_sep_md = "| " + " | ".join(["---"] * len(keys)) + " |"

        # values row with formatting
        val_cells = []
        for k in keys:
            v = parsed.get(k, "")
            # format numbers nicely
            if isinstance(v, float):
                val_cells.append(f"{v:.4g}")
            elif isinstance(v, (int,)):
                val_cells.append(str(v))
            elif isinstance(v, (pd.Timestamp, datetime)):
                try:
                    val_cells.append(pd.to_datetime(v).strftime("%Y-%m-%d %H:%M"))
                except Exception:
                    val_cells.append(str(v))
            else:
                # keep strings shortish
                s = str(v)
                if len(s) > 120:
                    s = s[:117] + "..."
                val_cells.append(s.replace("\n", " "))
        json_values_md = "| " + " | ".join(val_cells) + " |"

        # Render json header+values as a compact markdown table
        st.markdown(json_header_md + "\n" + json_sep_md + "\n" + json_values_md)
    else:
        # raw is a string or list - show as code block
        st.markdown("-> **Json data header**  \n\n")
        st.code(str(parsed)[:2000])

    st.markdown("")  # small spacer

st.write("----")
st.info("Note: You can toggle 'Use uniform JSON columns' to show the union of JSON keys across all rows as fixed columns. This may help scanning if your JSON structure is mostly consistent.")
