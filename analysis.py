import os
import yfinance as yf
import pandas as pd
import numpy as np
import time
import requests
import csv
from datetime import datetime

# --- 1. DATA SOURCES & CACHING ---
def log_trade_to_csv(trade_data):
    file_exists = os.path.isfile('live_trades.csv')
    with open('live_trades.csv', 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=trade_data.keys())
        if not file_exists: writer.writeheader()
        writer.writerow(trade_data)

# ✅ RESTORED: Month Parser
def parse_month_arg(month_str):
    months = {'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06', 
              'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'}
    return months.get(month_str[:3].upper(), None)

def get_sp300_tickers():
    cache_file = "sp300_cache.csv"
    current_time = time.time()
    if os.path.exists(cache_file):
        if current_time - os.path.getmtime(cache_file) < 86400:
            try: return pd.read_csv(cache_file)['Ticker'].tolist()
            except: pass
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        tickers = [t.replace('.', '-') for t in tables[0]['Symbol'].tolist()][:300]
        pd.DataFrame(tickers, columns=['Ticker']).to_csv(cache_file, index=False)
        return tickers
    except:
        return ["NVDA", "TSLA", "AAPL", "MSFT", "AMD", "AMZN", "GOOGL", "META", "SPY", "QQQ"]

def get_dynamic_movers():
    try:
        url = "https://finance.yahoo.com/most-active"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            df = pd.read_html(r.text)[0]
            tickers = [t for t in df['Symbol'].tolist() if "-" not in t and len(t) < 6]
            return tickers[:30]
    except: return ["TSLA", "NVDA", "AMD", "PLTR", "SOFI"]

# --- 2. INDICATOR ENGINE ---
def calculate_indicators(df):
    df['SMA50'] = df['Close'].rolling(window=50).mean()
    df['SMA200'] = df['Close'].rolling(window=200).mean()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    high_low = df['High'] - df['Low']
    ranges = pd.concat([high_low, abs(df['High'] - df['Close'].shift()), abs(df['Low'] - df['Close'].shift())], axis=1)
    df['ATR'] = np.max(ranges, axis=1).rolling(14).mean()
    
    plus_dm = df['High'].diff(); minus_dm = -df['Low'].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    atr = df['ATR'].replace(0, np.nan)
    df['ADX'] = (np.abs((100*plus_dm.rolling(14).mean()/atr) - (100*minus_dm.rolling(14).mean()/atr)) / 
                 ((100*plus_dm.rolling(14).mean()/atr) + (100*minus_dm.rolling(14).mean()/atr)) * 100).rolling(14).mean()
    df['Plus_DI'] = 100 * plus_dm.rolling(14).mean() / atr
    df['Minus_DI'] = 100 * minus_dm.rolling(14).mean() / atr
    
    bb_range = (df['Close'].rolling(20).mean() + (df['Close'].rolling(20).std()*2)) - (df['Close'].rolling(20).mean() - (df['Close'].rolling(20).std()*2))
    df['BB_Position'] = (df['Close'] - (df['Close'].rolling(20).mean() - (df['Close'].rolling(20).std()*2))) / bb_range
    df['BB_Width'] = bb_range / df['Close'].rolling(20).mean()
    
    df['Vol_Avg'] = df['Volume'].rolling(20).mean()
    df['Vol_Ratio'] = df['Volume'] / df['Vol_Avg']
    df['ROC_5'] = ((df['Close'] - df['Close'].shift(5)) / df['Close'].shift(5)) * 100
    df['ROC_10'] = ((df['Close'] - df['Close'].shift(10)) / df['Close'].shift(10)) * 100
    df['ATR_Ratio'] = df['ATR'] / df['ATR'].rolling(20).mean()
    
    df['Recent_High'] = df['High'].rolling(10).max()
    df['Prev_High'] = df['High'].rolling(10).max().shift(10)
    df['Lower_Highs'] = df['Recent_High'] < df['Prev_High']
    df['Recent_Low'] = df['Low'].rolling(20).min()
    df['Support_Break'] = df['Close'] < df['Recent_Low'].shift(1)
    
    df['Body'] = np.abs(df['Close'] - df['Open'])
    df['UpperWick'] = df['High'] - df[['Close', 'Open']].max(axis=1)
    df['LowerWick'] = df[['Close', 'Open']].min(axis=1) - df['Low']
    
    return df

def check_fvg(df):
    if len(df) < 3: return "None", 0
    c1_high = df['High'].iloc[-3]; c1_low = df['Low'].iloc[-3]
    c3_high = df['High'].iloc[-1]; c3_low = df['Low'].iloc[-1]
    if c3_low > c1_high: return "Bullish", c3_low - c1_high
    elif c3_high < c1_low: return "Bearish", c1_low - c3_high
    return "None", 0

def analyze_wick_rejection(row):
    if row['Body'] == 0: return "Doji", 0
    if (row['UpperWick'] / row['Body']) > 2.0: return "Bearish", row['UpperWick'] / row['Body']
    elif (row['LowerWick'] / row['Body']) > 2.0: return "Bullish", row['LowerWick'] / row['Body']
    return "None", 0

# --- 3. SCORING ---
def calculate_bull_score(data, wick_type, wick_strength, fvg_type, fvg_size):
    score = 50
    reasons = []
    if data['Price'] > data['SMA50'] > data['SMA200']: score += 15; reasons.append("Strong Uptrend")
    elif data['Price'] > data['SMA50']: score += 10; reasons.append("Above SMA50")
    elif data['Price'] > data['EMA20']: score += 5
    if data['ADX'] > 25: score += 10; reasons.append("Strong Trend")
    if data['Plus_DI'] > data['Minus_DI'] + 5: score += 5
    if data['RSI'] < 30: score += 25; reasons.append("RSI Oversold")
    elif data['RSI'] < 40: score += 15; reasons.append("RSI Low")
    elif data['RSI'] > 60: score -= 10
    if data['ROC_5'] > 2: score += 5
    if data['BB_Position'] < 0.2: score += 12; reasons.append("BB Low")
    elif data['BB_Position'] < 0.4: score += 6
    if data['BB_Width'] < 0.05: score += 8
    if fvg_type == "Bullish" and fvg_size > data['ATR'] * 0.3: score += 12; reasons.append("Bull FVG")
    if wick_type == "Bullish" and wick_strength > 2.5: score += 13; reasons.append("Wick Reject")
    if data['Vol_Ratio'] > 1.5: score += 10; reasons.append("High Vol")
    elif data['Vol_Ratio'] > 1.2: score += 5
    return max(0, min(100, score)), reasons

def calculate_bear_score(data, wick_type, wick_strength, fvg_type, fvg_size):
    score = 50
    confirms = 0
    reasons = []
    if data['Price'] < data['SMA50'] < data['SMA200']: score -= 20; confirms += 1; reasons.append("Downtrend")
    elif data['Price'] < data['SMA50']: score -= 12; confirms += 1; reasons.append("Below SMA50")
    elif data['Price'] < data['SMA200']: score -= 8
    else: score += 5
    if data['ADX'] > 30: score -= 12; confirms += 1
    elif data['ADX'] > 25: score -= 8
    elif data['ADX'] < 20: score += 10
    if data['Minus_DI'] > data['Plus_DI'] + 10: score -= 10; confirms += 1
    elif data['Minus_DI'] > data['Plus_DI']: score -= 5
    else: score += 5
    if data['RSI'] > 75: score -= 25; confirms += 1; reasons.append("RSI Extreme")
    elif data['RSI'] > 70: score -= 18; confirms += 1; reasons.append("RSI Overbought")
    elif data['RSI'] > 60: score -= 10
    elif data['RSI'] < 50: score += 5
    if data['ROC_5'] < -3: score -= 8; confirms += 1
    if data['ATR_Ratio'] > 1.2: score -= 15; confirms += 1; reasons.append("Vol Expanding")
    elif data['ATR_Ratio'] > 1.1: score -= 8
    else: score += 10
    if data['BB_Position'] > 0.9: score -= 12; confirms += 1
    elif data['BB_Position'] > 0.8: score -= 8
    if data.get('Lower_Highs', False): score -= 10; confirms += 1
    if data.get('Support_Break', False): score -= 10; confirms += 1
    if fvg_type == "Bearish" and fvg_size > data['ATR'] * 0.5: score -= 10; confirms += 1; reasons.append("Bear FVG")
    if wick_type == "Bearish" and wick_strength > 3.0: score -= 10; confirms += 1; reasons.append("Wick Reject")
    if data['Vol_Ratio'] > 2.0: score -= 15; confirms += 1
    
    if confirms < 4: score += 20
    return max(0, min(100, score)), confirms, reasons

# --- 4. OPTIONS STRATEGIST ---
# ✅ RESTORED: force_month argument and logic
def find_option(ticker, direction, atr, current_price, force_month=None):
    try:
        stock = yf.Ticker(ticker)
        exps = stock.options
        if not exps: return None
        today = datetime.now()
        best_expiry = None
        
        # 1. Check for Force Month (e.g., JUN)
        if force_month:
            candidates = [e for e in exps if f"-{force_month}-" in e]
            if candidates:
                # Pick nearest to middle of month (15th)
                candidates.sort(key=lambda x: abs(int(x.split('-')[2]) - 15))
                best_expiry = candidates[0]
        
        # 2. Default (45 Days)
        if not best_expiry:
            valid = {}
            for e in exps:
                try:
                    edate = datetime.strptime(e, "%Y-%m-%d")
                    days = (edate - today).days
                    if 21 <= days <= 60: valid[e] = abs(days - 45)
                except: pass
            if not valid: return None
            best_expiry = min(valid, key=valid.get)

        move = atr * 1.5
        target_strike = current_price + move if direction == "BULL" else current_price - move
        opt = stock.option_chain(best_expiry)
        chain = opt.calls if direction == "BULL" else opt.puts
        
        # Filter for liquidity
        chain = chain[chain['openInterest'] > 50]
        if chain.empty: return None
        
        chain['diff'] = abs(chain['strike'] - target_strike)
        best = chain.sort_values('diff').iloc[0]
        
        return {
            "type": "CALL" if direction=="BULL" else "PUT", 
            "strike": best['strike'], 
            "price": best['lastPrice'], 
            "expiry": best_expiry, 
            "oi": best['openInterest']
        }
    except: return None

# --- 5. ANALYZE STOCK (Fixed 0 issue) ---
def analyze_stock(ticker, strict=True):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="2y")
        if len(df) < 250: return None
        df = calculate_indicators(df)
        latest = df.iloc[-1]
        fvg, fvg_s = check_fvg(df)
        wick, wick_s = analyze_wick_rejection(latest)
        data = {
            'Price': latest['Close'], 'SMA50': latest['SMA50'], 'SMA200': latest['SMA200'], 'EMA20': latest['EMA20'],
            'RSI': latest['RSI'], 'ADX': latest['ADX'], 'Plus_DI': latest['Plus_DI'], 'Minus_DI': latest['Minus_DI'],
            'ATR': latest['ATR'], 'ATR_Ratio': latest['ATR_Ratio'], 'BB_Position': latest['BB_Position'], 'BB_Width': latest['BB_Width'],
            'Vol_Ratio': latest['Vol_Ratio'], 'ROC_5': latest['ROC_5'], 'ROC_10': latest['ROC_10'],
            'Lower_Highs': latest['Lower_Highs'], 'Support_Break': latest['Support_Break']
        }
        bull, bull_reasons = calculate_bull_score(data, wick, wick_s, fvg, fvg_s)
        bear, confirms, bear_reasons = calculate_bear_score(data, wick, wick_s, fvg, fvg_s)
        
        direction = None
        reasons = []
        
        # 1. Determine Direction & Reasons
        if bull >= 75 and latest['ADX'] > 25:
            direction = "BULL"
            reasons = bull_reasons
        elif bear <= 35 and confirms >= 4:
            direction = "BEAR"
            reasons = bear_reasons
            
        # 2. Force Calculation of Stops/Targets (Even for Neutral)
        # If no direction, guess based on score > 50
        calc_direction = direction if direction else ("BULL" if bull >= 50 else "BEAR")
        
        if calc_direction == "BULL":
            stop = latest['Close'] - (latest['ATR'] * 2.5)
            target = latest['Close'] + (latest['ATR'] * 3.5)
        else:
            stop = latest['Close'] + (latest['ATR'] * 2.0)
            target = latest['Close'] - (latest['ATR'] * 4.0)
        
        # 3. Handle Strict Mode (Auto-Scanner)
        if strict and not direction:
            return None

        # 4. Return Data (Manual Check or Valid Signal)
        sig_id = f"#{ticker}_{datetime.now().strftime('%m%d')}"
        
        # If neutral, we still return the calculated "Hypothetical" direction for the UI
        final_dir = direction if direction else "NEUTRAL"
        final_reasons = reasons if reasons else ["No strong signal (Hypothetical Levels)"]
        
        return {
            "ID": sig_id, 
            "Ticker": ticker, 
            "Price": round(latest['Close'], 2),
            "Score": int(bull if calc_direction=="BULL" else (100-bear)), 
            "Direction": final_dir, 
            "Reasons": final_reasons,
            "Stop": round(stop, 2), 
            "Target": round(target, 2), 
            "ATR": latest['ATR']
        }
    except Exception as e: 
        print(f"Error analyzing {ticker}: {e}")
        return None

# --- 5. ANALYZE STOCK (Fixed 0 issue) ---
def analyze_stock(ticker, strict=True):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="2y")
        if len(df) < 250: return None
        df = calculate_indicators(df)
        latest = df.iloc[-1]
        fvg, fvg_s = check_fvg(df)
        wick, wick_s = analyze_wick_rejection(latest)
        data = {
            'Price': latest['Close'], 'SMA50': latest['SMA50'], 'SMA200': latest['SMA200'], 'EMA20': latest['EMA20'],
            'RSI': latest['RSI'], 'ADX': latest['ADX'], 'Plus_DI': latest['Plus_DI'], 'Minus_DI': latest['Minus_DI'],
            'ATR': latest['ATR'], 'ATR_Ratio': latest['ATR_Ratio'], 'BB_Position': latest['BB_Position'], 'BB_Width': latest['BB_Width'],
            'Vol_Ratio': latest['Vol_Ratio'], 'ROC_5': latest['ROC_5'], 'ROC_10': latest['ROC_10'],
            'Lower_Highs': latest['Lower_Highs'], 'Support_Break': latest['Support_Break']
        }
        bull, bull_reasons = calculate_bull_score(data, wick, wick_s, fvg, fvg_s)
        bear, confirms, bear_reasons = calculate_bear_score(data, wick, wick_s, fvg, fvg_s)
        
        direction = None
        reasons = []
        
        # 1. Determine Direction & Reasons
        if bull >= 75 and latest['ADX'] > 25:
            direction = "BULL"
            reasons = bull_reasons
        elif bear <= 35 and confirms >= 4:
            direction = "BEAR"
            reasons = bear_reasons
            
        # 2. Force Calculation of Stops/Targets (Even for Neutral)
        # If no direction, guess based on score > 50
        calc_direction = direction if direction else ("BULL" if bull >= 50 else "BEAR")
        
        if calc_direction == "BULL":
            stop = latest['Close'] - (latest['ATR'] * 2.5)
            target = latest['Close'] + (latest['ATR'] * 3.5)
        else:
            stop = latest['Close'] + (latest['ATR'] * 2.0)
            target = latest['Close'] - (latest['ATR'] * 4.0)
        
        # 3. Handle Strict Mode (Auto-Scanner)
        if strict and not direction:
            return None

        # 4. Return Data (Manual Check or Valid Signal)
        sig_id = f"#{ticker}_{datetime.now().strftime('%m%d')}"
        
        # If neutral, we still return the calculated "Hypothetical" direction for the UI
        final_dir = direction if direction else "NEUTRAL"
        final_reasons = reasons if reasons else ["No strong signal (Hypothetical Levels)"]
        
        return {
            "ID": sig_id, 
            "Ticker": ticker, 
            "Price": round(latest['Close'], 2),
            "Score": int(bull if calc_direction=="BULL" else (100-bear)), 
            "Direction": final_dir, 
            "Reasons": final_reasons,
            "Stop": round(stop, 2), 
            "Target": round(target, 2), 
            "ATR": latest['ATR']
        }
    except Exception as e: 
        print(f"Error analyzing {ticker}: {e}")
        return None