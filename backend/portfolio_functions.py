from sqlalchemy import create_engine
import os
DATABASE_URL = f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
engine = create_engine(DATABASE_URL)
from decimal import Decimal
import yfinance as yf
import pandas as pd
from backend.db import get_connection
from datetime import datetime

def add_trade(stock, trade_type, qty, buy_price, sell_price, target_price, stop_loss, trade_date, notes):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO portfolio_trades 
            (stock_symbol, trade_type, quantity, buy_price, sell_price, target_price, stop_loss, trade_date, notes, holding_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'HOLDING')
        """, (stock, trade_type, qty, buy_price, sell_price, target_price, stop_loss, trade_date, notes))
        conn.commit()
        print(f"✅ Trade added: {stock} ({trade_date})")
    except Exception as e:
        print(f"❌ Error adding trade {stock}: {e}")
    finally:
        cur.close()
        conn.close()

def get_portfolio():
    conn = get_connection()
    df = pd.read_sql("""
        SELECT * FROM portfolio_trades
        ORDER BY trade_date DESC
    """, engine)
    conn.close()
    return df

def update_live_prices():
    """Fetch live prices and update profit/loss safely"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stock_symbol, buy_price, target_price 
        FROM portfolio_trades 
        WHERE holding_status = 'HOLDING'
    """)
    rows = cur.fetchall()

    for trade in rows:
        trade_id, symbol, buy_price, target = trade
        try:
            # Convert Decimal to float if needed
            buy_price = float(buy_price) if isinstance(buy_price, Decimal) else buy_price
            target = float(target) if isinstance(target, Decimal) else target

            # Fetch live price
            ticker = yf.Ticker(symbol)
            current_price = float(ticker.history(period="1d")["Close"].iloc[-1])

            # Update in database — let PostgreSQL handle math
            cur.execute("""
                UPDATE portfolio_trades
                SET 
                    current_price = %s,
                    profit_loss = (CAST(%s AS FLOAT) - CAST(buy_price AS FLOAT)),
                    profit_loss_percent = CASE 
                        WHEN buy_price IS NOT NULL AND buy_price != 0 
                        THEN ((CAST(%s AS FLOAT) - CAST(buy_price AS FLOAT)) / CAST(buy_price AS FLOAT)) * 100
                        ELSE NULL
                    END,
                    notes = CONCAT('Updated on ', CURRENT_DATE, ' | ', COALESCE(notes, ''))
                WHERE id = %s
            """, (current_price, current_price, current_price, trade_id))

            print(f"✅ Updated {symbol}: {current_price}")

        except Exception as e:
            print(f"❌ Failed updating {symbol}: {e}")

    conn.commit()
    cur.close()
    conn.close()