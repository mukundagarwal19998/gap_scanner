"""
Weekly Gap Scanner for NSE F&O Stocks
======================================
What this does (in plain terms):
1. Gets the list of stocks in the NSE F&O (Futures & Options) segment.
2. Downloads each stock's weekly candle data.
3. Compares this week's OPEN price to last week's CLOSE price.
   - If this week opened well ABOVE last week's close  -> "Gap Up"
   - If this week opened well BELOW last week's close  -> "Gap Down"
4. Builds a results table and emails it to you.

You do not need to understand the code to use this. Just follow SETUP.md.
"""

import os
import sys
import time
import io
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

# ----------------------------------------------------------------------------
# SETTINGS — feel free to change these numbers, nothing else needs editing
# ----------------------------------------------------------------------------

# Minimum gap size to count as a "gap". 0.5 means 0.5%.
# A gap smaller than this (just normal daily noise) will be ignored.
GAP_THRESHOLD_PERCENT = 0.5

# Fallback list of NSE F&O stocks (used only if the live list can't be
# fetched, e.g. NSE website blocks the request). Update occasionally by
# checking https://www.nseindia.com/market-data/equity-derivatives-watch
FALLBACK_FNO_LIST = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "ITC", "LT", "AXISBANK", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "NESTLEIND", "WIPRO", "ADANIENT",
    "ADANIPORTS", "ONGC", "NTPC", "POWERGRID", "M&M", "TATAMOTORS", "TATASTEEL",
    "JSWSTEEL", "HCLTECH", "TECHM", "INDUSINDBK", "BAJAJFINSV", "GRASIM", "CIPLA",
    "DRREDDY", "EICHERMOT", "HEROMOTOCO", "BPCL", "COALINDIA", "DIVISLAB",
    "BRITANNIA", "APOLLOHOSP", "TATACONSUM", "SBILIFE", "HDFCLIFE", "BAJAJ-AUTO",
    "UPL", "SHRIRAMFIN", "LTIM", "VEDL", "DLF", "GODREJCP", "PIDILITIND",
    "HAVELLS", "DABUR", "SIEMENS", "ABB", "AMBUJACEM", "ACC", "BANKBARODA",
    "PNB", "CANBK", "IDFCFIRSTB", "FEDERALBNK", "AUBANK", "PEL", "MOTHERSON",
    "BOSCHLTD", "TVSMOTOR", "ASHOKLEY", "BHARATFORG", "BEL", "HAL", "GAIL",
    "IOC", "PETRONET", "TORNTPHARM", "LUPIN", "AUROPHARMA", "BIOCON",
    "ZYDUSLIFE", "ALKEM", "MARICO", "COLPAL", "BERGEPAINT", "VOLTAS",
    "CROMPTON", "PAGEIND", "TRENT", "NAUKRI", "INDIGO", "IRCTC", "ZOMATO",
    "NYKAA", "PAYTM", "POLICYBZR", "DMART", "JUBLFOOD", "MUTHOOTFIN",
    "CHOLAFIN", "LICHSGFIN", "SBICARD", "ICICIGI", "ICICIPRULI", "MFSL",
    "RECLTD", "PFC", "IRFC", "IREDA", "SAIL", "NMDC", "HINDALCO", "NATIONALUM",
    "JINDALSTEL", "RATNAMANI", "APLAPOLLO", "CONCOR", "GMRINFRA", "ADANIGREEN",
    "ADANIPOWER", "ADANIENSOL", "TATAPOWER", "TORNTPOWER", "CESC", "SJVN",
    "NHPC", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS", "OFSS", "TATAELXSI",
    "POLYCAB", "KEI", "DIXON", "AMBER", "SYNGENE", "ESCORTS", "CUMMINSIND",
    "ABFRL", "MANAPPURAM", "DEEPAKNTR", "PIIND", "SRF", "GUJGASLTD", "IGL",
    "MGL", "PETRONET", "OIL", "HINDPETRO", "BANDHANBNK", "IDEA", "GLENMARK",
    "LAURUSLABS", "METROPOLIS", "MAXHEALTH", "FORTIS", "ASTRAL", "SUPREMEIND",
    "WHIRLPOOL", "BLUESTARCO", "RAJESHEXPO", "TIINDIA", "BHARTIHEXA",
]

# ----------------------------------------------------------------------------
# STEP 1: Get the current NSE F&O stock list (tries live data first)
# ----------------------------------------------------------------------------

def get_fno_stock_list():
    """
    Tries to download the official, up-to-date NSE F&O stock list.
    If that fails for any reason (NSE blocks bots sometimes), it falls
    back to the hardcoded list above so the script still works.
    """
    url = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        # The column holding symbols is usually named "SYMBOL " or similar
        symbol_col = [c for c in df.columns if "SYMBOL" in c.upper()][0]
        symbols = (
            df[symbol_col]
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .tolist()
        )
        symbols = [s for s in symbols if s.isupper() and s not in ("SYMBOL",)]
        if len(symbols) > 50:  # sanity check, real list usually 180+
            print(f"Fetched live F&O list: {len(symbols)} stocks")
            return symbols
    except Exception as e:
        print(f"Could not fetch live F&O list ({e}); using fallback list.")

    return FALLBACK_FNO_LIST


# ----------------------------------------------------------------------------
# STEP 2: Check each stock for a weekly gap
# ----------------------------------------------------------------------------

def check_weekly_gap(symbol):
    """
    Downloads weekly candles for one stock and checks if the latest
    week's open gapped up or down versus the previous week's close.
    Returns a dict with the result, or None if there isn't enough data
    or no significant gap.
    """
    ticker = symbol + ".NS"
    try:
        data = yf.Ticker(ticker).history(period="3mo", interval="1wk")
        if len(data) < 2:
            return None

        # Last row = current/most recent week, second-last = previous week
        latest_week = data.iloc[-1]
        previous_week = data.iloc[-2]

        this_open = latest_week["Open"]
        prev_close = previous_week["Close"]

        if prev_close == 0:
            return None

        gap_percent = ((this_open - prev_close) / prev_close) * 100

        if gap_percent >= GAP_THRESHOLD_PERCENT:
            direction = "Gap Up"
        elif gap_percent <= -GAP_THRESHOLD_PERCENT:
            direction = "Gap Down"
        else:
            return None  # no meaningful gap

        return {
            "Symbol": symbol,
            "Direction": direction,
            "Gap %": round(gap_percent, 2),
            "Prev Week Close": round(prev_close, 2),
            "This Week Open": round(this_open, 2),
            "This Week Close": round(latest_week["Close"], 2),
        }
    except Exception as e:
        print(f"  Skipped {symbol}: {e}")
        return None


# ----------------------------------------------------------------------------
# STEP 3: Run the scan across all stocks
# ----------------------------------------------------------------------------

def run_scan():
    symbols = get_fno_stock_list()
    results = []

    print(f"Scanning {len(symbols)} F&O stocks for weekly gaps...")
    for i, symbol in enumerate(symbols, 1):
        result = check_weekly_gap(symbol)
        if result:
            results.append(result)
        if i % 25 == 0:
            print(f"  ...checked {i}/{len(symbols)}")
        time.sleep(0.15)  # be gentle on Yahoo Finance's servers

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(by="Gap %", ascending=False)
    return df


# ----------------------------------------------------------------------------
# STEP 4: Email the results
# ----------------------------------------------------------------------------

def send_email(df):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    sender_email = os.environ.get("GMAIL_ADDRESS")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL", sender_email)

    if not sender_email or not sender_password:
        print("Email credentials not set — skipping email, printing results instead.")
        print(df.to_string(index=False) if not df.empty else "No gaps found.")
        return

    today_str = datetime.now().strftime("%d %b %Y")
    subject = f"Weekly Gap Scanner Results — {today_str}"

    if df.empty:
        body_html = "<p>No significant weekly gap up/down stocks found this week.</p>"
    else:
        gap_up = df[df["Direction"] == "Gap Up"]
        gap_down = df[df["Direction"] == "Gap Down"]

        body_html = f"""
        <h2>Weekly Gap Scanner — {today_str}</h2>
        <p>Threshold used: {GAP_THRESHOLD_PERCENT}%</p>
        <h3>Gap Up ({len(gap_up)})</h3>
        {gap_up.to_html(index=False) if not gap_up.empty else '<p>None</p>'}
        <h3>Gap Down ({len(gap_down)})</h3>
        {gap_down.to_html(index=False) if not gap_down.empty else '<p>None</p>'}
        <p style="color:gray;font-size:12px;">
        This is an automated technical scan, not investment advice.
        Always do your own research before trading.
        </p>
        """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())

    print(f"Email sent to {recipient_email}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    results_df = run_scan()
    print("\n--- RESULTS ---")
    print(results_df.to_string(index=False) if not results_df.empty else "No gaps found.")
    send_email(results_df)
