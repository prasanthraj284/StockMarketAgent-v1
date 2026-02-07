import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import os
from datetime import datetime

# --- DATA SOURCES ---
def get_sp300_tickers():
    cache_file = "sp300_cache.csv"
    if os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < 86400:
            try: return pd.read_csv(cache_file)['Ticker'].tolist()
            except: pass
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        tickers = [t.replace('.', '-') for t in tables[0]['Symbol'].tolist()][:300]
        pd.DataFrame(tickers, columns=['Ticker']).to_csv(cache_file, index=False)
        return tickers
    except: return ["NVDA", "TSLA", "AAPL", "MSFT", "AMD", "SPY", "QQQ"]

def get_dynamic_movers():
    try:
        url = "https://finance.yahoo.com/most-active"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        df = pd.read_html(r.text)[0]
        return [t for t in df['Symbol'].tolist() if "-" not in t and len(t) < 6][:30]
    except: return []

# --- INDICATORS ---
def calculate_indicators(df):
    df['SMA50'] = df['Close'].rolling(50).mean()
    df['SMA200'] = df['Close'].rolling(200).mean()
    df['EMA20'] = df['Close'].ewm(span=20).mean()
    
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
    
    # ADX
    plus_dm = df['High'].diff().clip(lower=0)
    minus_dm = -df['Low'].diff().clip(lower=0)
    tr = df['ATR']
    df['ADX'] = (abs((plus_dm.rolling(14).mean() - minus_dm.rolling(14).mean()) / tr) * 100).rolling(14).mean()
    
    # Bollinger
    mean = df['Close'].rolling(20).mean()
    std = df['Close'].rolling(20).std()
    df['BB_Position'] = (df['Close'] - (mean - 2*std)) / (4*std)
    
    return df

# --- SCORING ---
def calculate_score(data):
    bull = 50; bear = 50; reasons = []
    
    # Bull Logic
    if data['Price'] > data['SMA50']: bull += 10
    if data['ADX'] > 25: bull += 10
    if data['RSI'] < 30: bull += 25; reasons.append("RSI Oversold")
    if data['BB_Position'] < 0.2: bull += 12
    if data['Price'] > data['SMA50'] and data['Price'] > data['SMA200']: reasons.append("Strong Uptrend")
    
    # Bear Logic
    if data['Price'] < data['SMA50']: bear -= 15
    if data['Price'] < data['SMA200']: bear -= 15; reasons.append("Downtrend")
    if data['RSI'] > 70: bear -= 18; reasons.append("RSI Overbought")
    
    return bull, bear, reasons

# --- MAIN ANALYSIS ---
def analyze_stock(ticker, strict=True):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if len(df) < 200: return None
        df = calculate_indicators(df)
        curr = df.iloc[-1]
        
        data = {
            'Price': curr['Close'], 'SMA50': curr['SMA50'], 'SMA200': curr['SMA200'],
            'RSI': curr['RSI'], 'ADX': curr['ADX'], 'ATR': curr['ATR'], 'BB_Position': curr['BB_Position']
        }
        
        bull, bear, reasons = calculate_score(data)
        
        direction = None
        if bull >= 75 and curr['ADX'] > 25: direction = "BULL"
        elif bear <= 35: direction = "BEAR"
        
        # Determine strictness return
        if not strict:
            # Return data even if score is low (for manual check)
            return {
                "Ticker": ticker, "Price": round(curr['Close'], 2), 
                "Score": bull if direction=="BULL" else (100-bear), "Direction": direction or "NEUTRAL", 
                "ATR": curr['ATR'], 
                "Reasons": reasons # <--- CRITICAL for /check command
            }

        # For auto-scan, return ONLY if direction found
        if direction:
            sig_id = f"#{ticker}_{datetime.now().strftime('%m%d')}"
            return {
                "ID": sig_id, "Ticker": ticker, "Direction": direction, 
                "Score": bull if direction=="BULL" else (100-bear),
                "Price": round(curr['Close'], 2), "Stop": 0, "Target": 0, # Will calc in main
                "Reasons": reasons, "ATR": curr['ATR']
            }
    except: return None
    return None

def find_option(ticker, direction, atr, price):
    try:
        stock = yf.Ticker(ticker)
        exps = stock.options
        if not exps: return None
        today = datetime.now()
        # Find closest to 45 Days
        best_date = min(exps, key=lambda x: abs((datetime.strptime(x, "%Y-%m-%d") - today).days - 45))
        
        opt = stock.option_chain(best_date)
        chain = opt.calls if direction == "BULL" else opt.puts
        target_strike = price + (atr * 1.5) if direction == "BULL" else price - (atr * 1.5)
        
        # Find closest strike
        best = chain.iloc[(chain['strike'] - target_strike).abs().argsort()[:1]].iloc[0]
        return {"strike": best['strike'], "expiry": best_date, "price": best['lastPrice']}
    except: return None