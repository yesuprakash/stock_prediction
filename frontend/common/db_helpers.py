import pandas as pd
from sqlalchemy import create_engine
import os

engine = create_engine(
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

def fetch_active_predictions(latest_only=True):
    q = "SELECT * FROM predictions"
    df = pd.read_sql(q, engine)
    if latest_only and not df.empty:
        df = df.sort_values(['stock_symbol', 'prediction_date']) \
               .groupby('stock_symbol', as_index=False).last()
    return df
