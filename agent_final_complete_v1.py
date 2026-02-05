import telebot
import yfinance as yf
import pandas as pd
import numpy as np
import threading
import time
import requests
from datetime import datetime
import pytz
from flask import Flask
import os
# ==========================================
# 🔴 CONFIGURATION
# ==========================================
API_TOKEN = "8308798372:AAHlfoTwHG98Azvd-iY50EDp7bjugBwORAw"
YOUR_CHAT_ID = "7960622303"
# ==========================================

bot = telebot.TeleBot(API_TOKEN)

# --- 1. HELPERS & DATA SOURCES ---
def parse_month_arg(month_str):
    # Converts "June" -> "06" for manual searches
    months = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08', 
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }
    return months.get(month_str[:3].upper(), None)

def get_sp300_tickers():
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        tickers = [t.replace('.', '-') for t in tables[0]['Symbol'].tolist()]
        return tickers[:300]
    except: return ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]

def get_dynamic_movers():
    try:
        url = "https://finance.yahoo.com/most-active"
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers)
        df = pd.read_html(r.text)[0]
        tickers = df['Symbol'].tolist()
        return [t for t in tickers if "-" not in t][:15]
    except: return ["TSLA", "NVDA", "AMD", "PLTR", "SOFI"]

# --- 2. TECHNICAL INDICATORS ---
def check_fvg(df):
    if len(df) < 3: return "None"
    c1_high, c1_low = df['High'].iloc[-3], df['Low'].iloc[-3]
    c3_high, c3_low = df['High'].iloc[-1], df['Low'].iloc[-1]
    
    if c3_low > c1_high: return "🟢 Bullish FVG"
    elif c3_high < c1_low: return "🔴 Bearish FVG"
    return "None"

def calculate_indicators(df):
    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # MACD
    k = df['Close'].ewm(span=12, adjust=False).mean()
    d = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = k - d
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    
    # Trend Lines
    df['SMA200'] = df['Close'].rolling(window=200).mean()
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
    
    # ATR & Volume
    high_low = df['High'] - df['Low']
    ranges = pd.concat([high_low, abs(df['High'] - df['Close'].shift()), abs(df['Low'] - df['Close'].shift())], axis=1)
    df['ATR'] = np.max(ranges, axis=1).rolling(window=14).mean()
    df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
    return df

# --- 3. SMART OPTION STRATEGIST (THE BRAIN) ---
def find_best_option(ticker, direction, atr, current_price, force_month=None):
    try:
        stock = yf.Ticker(ticker)
        exps = stock.options
        if not exps: return None
        
        today = datetime.now()
        target_dte = 45 # Golden Rule: Aim for ~45 Days out
        best_expiry = None
        
        # A) FIND THE EXPIRY DATE
        if force_month:
            # User forced a month (e.g., "06" for June)
            candidates = [e for e in exps if f"-{force_month}-" in e]
            if not candidates: return None
            # Pick date closest to mid-month (15th)
            candidates.sort(key=lambda x: abs(int(x.split('-')[2]) - 15))
            best_expiry = candidates[0]
        else:
            # Auto-Select: Find date closest to 45 days out
            valid_candidates = {}
            for e in exps:
                try:
                    edate = datetime.strptime(e, "%Y-%m-%d")
                    days_out = (edate - today).days
                    if 14 <= days_out <= 150: # Window: 2 weeks to 5 months
                        valid_candidates[e] = abs(days_out - target_dte)
                except: pass
            
            if not valid_candidates: return None
            # Winner is the one with lowest score (closest to 45)
            best_expiry = min(valid_candidates, key=valid_candidates.get)

        # B) CALCULATE DYNAMIC STRIKE (SCALED BY TIME)
        expiry_date = datetime.strptime(best_expiry, "%Y-%m-%d")
        days_to_go = (expiry_date - today).days
        
        # The further out, the bigger the move we expect.
        # Formula: Target moves 0.5 ATR for every 30 days of time.
        time_multiplier = days_to_go / 30
        if time_multiplier < 0.5: time_multiplier = 0.5
        
        dynamic_move = atr * time_multiplier
        
        if direction == "BULL": target_strike = current_price + dynamic_move
        else: target_strike = current_price - dynamic_move

        # C) GET CHAIN & MATCH
        opt = stock.option_chain(best_expiry)
        chain = opt.calls if direction == "BULL" else opt.puts
        
        chain['diff'] = abs(chain['strike'] - target_strike)
        best_contract = chain.sort_values('diff').iloc[0]
        
        return {
            "type": "CALL" if direction == "BULL" else "PUT",
            "strike": best_contract['strike'],
            "price": best_contract['lastPrice'],
            "expiry": best_expiry
        }
    except: return None

# --- 4. FORMATTER (VISUAL TEMPLATE) ---
def generate_alert_message(data, opt_data):
    # Visual Logic
    if data['Score'] >= 60:
        trend_text, trend_emoji = "🐂 STRONG UPTREND", "🐂"
    elif data['Score'] <= 40:
        trend_text, trend_emoji = "🐻 STRONG DOWNTREND", "🐻"
    else:
        trend_text, trend_emoji = "⚖️ CONSOLIDATION", "⚖️"
        
    signal_emoji = "🟢 BUY" if data['Direction'] == "BULL" else "🔴 SELL"
    
    if opt_data:
        strat_line = (f"**{trend_emoji} STRATEGY: Buy {opt_data['type']} | "
                      f"Strike: ${opt_data['strike']} | "
                      f"💲 Price: ${opt_data['price']} | "
                      f"Exp: {opt_data['expiry']}**")
    else:
        strat_line = "⚠️ No Liquid Options Found"

    return (
        f"🚨 **ALERT: {data['Ticker']}** 🚨\n"
        f"Signal: {signal_emoji} (Score: {data['Score']})\n"
        f"Price: ${data['Price']}\n"
        f"Trend: {trend_text}\n"
        f"------------------------\n"
        f"{strat_line}\n\n"
        f"🛑 Stop Loss: ${data['Stop']}\n"
        f"💰 Target: ${data['Target']}"
    )

# --- 5. CORE ANALYSIS ENGINE ---
def analyze_stock(ticker):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if df.empty: return None
        
        df = calculate_indicators(df)
        fvg = check_fvg(df)
        
        # Latest Data Points
        price = df['Close'].iloc[-1]
        rsi = df['RSI'].iloc[-1]
        macd = df['MACD'].iloc[-1]
        sig = df['MACD_Signal'].iloc[-1]
        ema9 = df['EMA9'].iloc[-1]
        atr = df['ATR'].iloc[-1]
        
        # Volatility Ratio (Avoid div by zero)
        vol_avg = df['Vol_Avg'].iloc[-1] if df['Vol_Avg'].iloc[-1] > 0 else 1
        vol_ratio = df['Volume'].iloc[-1] / vol_avg
        
        pct_change = ((price - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
        
        # --- SCORING SYSTEM ---
        score = 50
        if price > df['SMA200'].iloc[-1]: score += 10 # Long Term Bull
        if macd > sig: score += 10 # Momentum Bull
        elif macd < sig: score -= 10 # Momentum Bear
        if price > ema9: score += 10 # Short Term Bull
        else: score -= 10 # Short Term Bear
        if rsi < 30: score += 15 # Oversold
        elif rsi > 70: score -= 15 # Overbought
        if "Bullish" in fvg: score += 10
        if "Bearish" in fvg: score -= 10
        
        # Direction & Levels
        direction = "BULL" if score >= 50 else "BEAR"
        
        if direction == "BULL":
            stop = price - (atr * 2)
            target = price + (atr * 3)
        else:
            stop = price + (atr * 2)
            target = price - (atr * 3)
            
        return {
            "Ticker": ticker, "Price": round(price, 2), "Score": score,
            "RSI": round(rsi, 2), "VolRatio": round(vol_ratio, 2), "FVG": fvg,
            "Pct": round(pct_change, 2), "Stop": round(stop, 2), 
            "Target": round(target, 2), "Direction": direction, "ATR": atr
        }
    except: return None

# --- 6. SCANNERS ---
def scanner_job():
    print("🕰️ Time-Aware Scanner Started...")
    while True:
        try:
            tz = pytz.timezone('US/Eastern')
            now = datetime.now(tz)
            
            # RUN: 6 AM - 5 PM EST
            if 6 <= now.hour < 17:
                sp300 = get_sp300_tickers()
                movers = get_dynamic_movers()
                all_tickers = list(set(sp300 + movers))
                print(f"🌞 Scanning {len(all_tickers)} stocks...")
                
                for ticker in all_tickers:
                    data = analyze_stock(ticker)
                    if not data: continue
                    
                    is_alert = False
                    
                    # 1. S&P 300 Logic
                    if ticker in sp300:
                        if data['Score'] >= 75 or data['Score'] <= 25 or abs(data['Pct']) > 4.0:
                            is_alert = True
                            
                    # 2. Movers Logic
                    if ticker in movers:
                        if data['VolRatio'] > 2.0 or abs(data['Pct']) > 5.0:
                            is_alert = True
                    
                    if is_alert:
                        # Pass ATR and Price to Option Hunter for Dynamic Strikes
                        opt_data = find_best_option(ticker, data['Direction'], data['ATR'], data['Price'])
                        msg = generate_alert_message(data, opt_data)
                        try:
                            bot.send_message(YOUR_CHAT_ID, msg)
                            print(f"✅ Alert Sent: {ticker}")
                        except: pass
                    time.sleep(0.5)
                
                print("💤 Scan Loop Done. Sleeping 15 mins...")
                time.sleep(900)
            else:
                time.sleep(60) # Sleep at night
        except Exception as e:
            print(f"Scanner Error: {e}")
            time.sleep(60)

# --- 7. COMMAND HANDLERS ---
@bot.message_handler(commands=['check'])
def manual_check(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "⚠️ Use: /check [TICKER] [OPTIONAL MONTH]")
            return
            
        ticker = parts[1].upper()
        force_month = parse_month_arg(parts[2]) if len(parts) > 2 else None
        
        status_msg = f"🔍 Checking {ticker}"
        if force_month: status_msg += f" for Month {force_month} options..."
        bot.reply_to(message, status_msg)
        
        data = analyze_stock(ticker)
        if data:
            opt_data = find_best_option(ticker, data['Direction'], data['ATR'], data['Price'], force_month)
            msg = generate_alert_message(data, opt_data)
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "❌ Data Not Found.")
            
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['scan'])
def manual_scan(message):
    bot.reply_to(message, "🦅 Force-Scanning Yahoo Top Movers...")
    movers = get_dynamic_movers()
    count = 0
    for ticker in movers:
        data = analyze_stock(ticker)
        if data and (data['Score'] >= 60 or data['Score'] <= 40 or data['VolRatio'] > 1.5):
            opt_data = find_best_option(ticker, data['Direction'], data['ATR'], data['Price'])
            msg = generate_alert_message(data, opt_data)
            bot.send_message(message.chat.id, msg)
            count += 1
    if count == 0: bot.reply_to(message, "😴 Nothing interesting found right now.")

if __name__ == "__main__":
    t = threading.Thread(target=scanner_job)
    t.start()
    print("🤖 Agent V8 Master is Online...")
    bot.polling()
    # --- 8. CLOUD SERVER KEEPALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is alive!"

def run_bot():
    # Start the Scanner Loop
    t_scanner = threading.Thread(target=scanner_job)
    t_scanner.start()
    
    # Start the Telegram Listener
    print("🤖 Agent V8 Master is Online...")
    bot.infinity_polling()

if __name__ == "__main__":
    # 1. Start the Bot in a background thread
    t_bot = threading.Thread(target=run_bot)
    t_bot.start()
    
    # 2. Start the Fake Web Server (Keeps Render happy)
    # Render assigns a random PORT, we must listen to it
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)