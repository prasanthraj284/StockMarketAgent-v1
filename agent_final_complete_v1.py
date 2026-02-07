import os
import telebot
import threading
import time
from flask import Flask
from datetime import datetime
import pytz
import yfinance as yf
from dotenv import load_dotenv

# LOAD SECRETS
load_dotenv()

# MODULE IMPORTS
from database import TradeManager
from analysis import analyze_stock, find_option, get_sp300_tickers, get_dynamic_movers

# --- CONFIG ---
API_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(API_TOKEN)
app = Flask(__name__)
db = TradeManager()

# GLOBAL STATE
alert_history = {}
current_day = None
last_heartbeat = datetime.now()

# --- COMMANDS ---

@bot.message_handler(commands=['help', 'start'])
def cmd_help(message):
    msg = (
        "🤖 **AGENT COMMAND GUIDE**\n\n"
        
        "1️⃣ **ENTER A TRADE (Bot Alert)**\n"
        "Usage: `/entered [ID] [PRICE] [QTY]`\n"
        "Ex: `/entered #NVDA_0206 5.50 2`\n"
        "*(Use this when the bot sends you an alert)*\n\n"
        
        "2️⃣ **ENTER A MANUAL TRADE**\n"
        "Usage: `/manual [TICKER] [TYPE] [PRICE] [QTY] [STOP] [EXPIRY]`\n"
        "Ex (Option): `/manual AAPL CALL 2.50 5 1.50 2026-06-20`\n"
        "Ex (Share): `/manual TSLA SHARE 150.00 10 140.00`\n"
        "*(Expiry is optional for shares)*\n\n"
        
        "3️⃣ **CLOSE A TRADE**\n"
        "Usage: `/close [TICKER] [TYPE] [EXIT_PRICE]`\n"
        "Ex: `/close AAPL CALL 3.00`\n"
        "Ex: `/close TSLA SHARE 155.00`\n"
        "*(Closes the oldest open position for that type)*\n\n"
        
        "4️⃣ **CHECK A STOCK**\n"
        "Usage: `/check [TICKER]`\n"
        "Ex: `/check AMD`\n"
        "*(Analyzes stock & gives signals + options)*\n\n"
        
        "5️⃣ **VIEW PORTFOLIO**\n"
        "Usage: `/portfolio`\n"
        "*(Shows all currently open positions)*"
    )
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")


@bot.message_handler(commands=['entered'])
def cmd_entered(message):
    try:
        parts = message.text.split()
        if len(parts) < 4: return bot.reply_to(message, "⚠️ Usage: `/entered #ID PRICE QTY`")
        
        sig_id = parts[1]
        signal = db.get_signal_details(sig_id)
        if not signal: return bot.reply_to(message, "❌ Signal ID not found.")
        
        success = db.add_to_portfolio({
            "ID": sig_id, "Ticker": signal['Ticker'], 
            "Type": "CALL" if signal['Direction'] == "BULL" else "PUT",
            "Qty": float(parts[3]), "Price": float(parts[2]), 
            "Stop": signal['Stop'], "Target": signal['Target'], "Source": "Bot_Alert"
        })
        if success: bot.reply_to(message, f"✅ Added {signal['Ticker']} to Active Portfolio.")
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['manual'])
@bot.message_handler(commands=['manual'])
def cmd_manual(message):
    try:
        parts = message.text.split()
        
        # Scenario A: SHARES (6 parts) -> /manual TICKER TYPE PRICE QTY STOP
        # Scenario B: OPTIONS (7 parts) -> /manual TICKER TYPE PRICE QTY STOP EXPIRY
        
        if len(parts) < 6: 
            return bot.reply_to(message, "⚠️ Usage:\nShares: `/manual TICKER SHARE PRICE QTY STOP`\nOptions: `/manual TICKER CALL/PUT PRICE QTY STOP EXPIRY`")
        
        ticker = parts[1].upper()
        type_ = parts[2].upper()
        price = float(parts[3])
        qty = float(parts[4])
        stop = float(parts[5])
        
        # Smart Expiry Logic
        if len(parts) >= 7:
            expiry = parts[6]  # User provided a date
        else:
            expiry = "N/A"     # Default for Shares
            
        # Default Target (20% gain)
        target = round(price * 1.2, 2)
        
        success = db.add_to_portfolio({
            "ID": f"#MAN_{ticker}_{datetime.now().strftime('%M%S')}",
            "Ticker": ticker, 
            "Type": type_,
            "Price": price, 
            "Qty": qty, 
            "Stop": stop,
            "Target": target, 
            "Expiry": expiry,
            "Source": "Manual", 
            "Notes": "User Added"
        })
        
        if success: 
            bot.reply_to(message, f"✅ Added **{type_}** for {ticker}\n📅 Expiry: {expiry}")
        else:
            bot.reply_to(message, "❌ Database Error.")
            
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['close'])
def cmd_close(message):
    try:
        parts = message.text.split()
        # Check if user provided enough arguments
        # Usage: /close TICKER TYPE PRICE
        if len(parts) < 4: 
            return bot.reply_to(message, "⚠️ Usage: `/close TICKER TYPE PRICE`\n\nExamples:\n`/close AAPL SHARE 155.00`\n`/close NVDA CALL 5.20`")
        
        ticker = parts[1].upper()
        type_input = parts[2].upper() # SHARE, CALL, or PUT
        exit_price = float(parts[3])
        
        # 1. Fetch Portfolio
        portfolio = db.get_portfolio()
        
        # 2. Find the SPECIFIC trade (Matching Ticker AND Type)
        # We look for the first match. If you have multiple CALLs, it closes the oldest one.
        target_trade = None
        for t in portfolio:
            if t['Ticker'] == ticker and type_input in t['Type'].upper():
                target_trade = t
                break
        
        if not target_trade:
            return bot.reply_to(message, f"❌ No open **{type_input}** position found for {ticker}.")
            
        # 3. Close it in Database
        success = db.close_position(target_trade['ID'], exit_price, "Manual Close")
        
        if success:
            # PnL Calculation
            entry = float(target_trade['Entry_Price'])
            qty = float(target_trade['Qty'])
            is_long = "CALL" in target_trade['Type'] or "SHARE" in target_trade['Type']
            
            if is_long:
                pnl = (exit_price - entry) * qty
                if "CALL" in target_trade['Type']: pnl *= 100
            else:
                pnl = (entry - exit_price) * qty * 100
                
            icon = "🟢" if pnl > 0 else "🔴"
            
            msg = (f"🔒 **CLOSED: {ticker} {type_input}**\n"
                   f"Entry: ${entry}\n"
                   f"Exit: ${exit_price}\n"
                   f"PnL: {icon} ${pnl:.2f}")
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "⚠️ Database Error: Could not close row.")

    except Exception as e: bot.reply_to(message, f"Error: {e}")


@bot.message_handler(commands=['check'])
def cmd_check(message):
    try:
        parts = message.text.split()
        if len(parts) < 2: return bot.reply_to(message, "⚠️ Usage: /check TICKER")
        
        ticker = parts[1].upper()
        bot.reply_to(message, f"🔍 Analyzing {ticker}...")
        
        # Analyze (Strict=False for manual)
        data = analyze_stock(ticker, strict=False)
        
        if data:
            # 1. Calc Risks
            stop = data['Price'] - (data['ATR']*2.5) if data['Direction'] == "BULL" else data['Price'] + (data['ATR']*2.0)
            target = data['Price'] + (data['ATR']*3.5) if data['Direction'] == "BULL" else data['Price'] - (data['ATR']*4.0)
            
            # 2. Find Option
            opt = find_option(ticker, data['Direction'], data['ATR'], data['Price'])
            opt_txt = f"⚡ **Option:** {opt['expiry']} ${opt['strike']} (Est: ${opt['price']})" if opt else "⚠️ Shares Only"

            # 3. Format Reasons
            reasons_list = data.get('Reasons', [])
            reasons_txt = "\n".join([f"• {r}" for r in reasons_list]) if reasons_list else "• No strong signals."

            # 4. Message
            icon = "🚀" if data['Direction'] == "BULL" else "🔻"
            color = "🟢" if data['Direction'] == "BULL" else "🔴"
            
            msg = (f"{icon} **MANUAL CHECK: {data['Ticker']}**\n"
                   f"Score: {data['Score']}/100 {color}\n"
                   f"Price: ${data['Price']}\n\n"
                   f"📊 **Signals:**\n{reasons_txt}\n\n"
                   f"🛑 Stop: ${stop:.2f}\n"
                   f"💰 Target: ${target:.2f}\n\n"
                   f"{opt_txt}")
            
            bot.send_message(message.chat.id, msg, parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Could not analyze ticker.")
            
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['portfolio'])
def cmd_portfolio(message):
    trades = db.get_portfolio()
    if not trades: return bot.reply_to(message, "📭 Portfolio empty.")
    msg = "💼 **Active Portfolio**\n"
    for t in trades:
        msg += f"• {t['Ticker']} ({t['Type']}) | Entry: ${t['Entry_Price']} | Stop: ${t['Stop_Loss']}\n"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

# --- WATCHDOG ---
def check_portfolio():
    trades = db.get_portfolio()
    if not trades: return
    
    for t in trades:
        try:
            stock = yf.Ticker(t['Ticker'])
            curr = stock.history(period="1d")['Close'].iloc[-1]
            stop = float(t['Stop_Loss'])
            target = float(t['Target'])
            
            is_bull = "CALL" in t['Type'] or "SHARE" in t['Type']
            outcome = None
            
            if is_bull:
                if curr <= stop: outcome = "STOP LOSS 🛑"
                elif curr >= target: outcome = "TAKE PROFIT 💰"
            else: 
                if curr >= stop: outcome = "STOP LOSS 🛑"
                elif curr <= target: outcome = "TAKE PROFIT 💰"
                
            if outcome:
                msg = (f"🚨 **EXIT ALERT: {t['Ticker']}**\n{outcome}\n"
                       f"Price: ${curr:.2f} | Entry: ${t['Entry_Price']}\n"
                       f"Action: Close Position.")
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                db.close_position(t['ID'], curr, outcome)
        except Exception as e: print(f"Watchdog Error {t['Ticker']}: {e}")

# --- SCANNER ---
def scanner_loop():
    print("✅ Agent V21 Started (30min Scan)...")
    global current_day, alert_history, last_heartbeat
    
    while True:
        try:
            tz = pytz.timezone('US/Eastern')
            now = datetime.now(tz)
            
            if current_day != now.day:
                alert_history.clear(); current_day = now.day
            
            check_portfolio()
            
            if 6 <= now.hour < 17 and now.weekday() < 5:
                tickers = list(set(get_sp300_tickers() + get_dynamic_movers()))
                found_new = False
                print(f"🌞 Scanning {len(tickers)} stocks...")
                
                for ticker in tickers:
                    data = analyze_stock(ticker, strict=True)
                    if data:
                        if alert_history.get(ticker) != data['Direction']:
                            # Calc Stops/Targets
                            data['Stop'] = round(data['Price'] - (data['ATR']*2.5) if data['Direction'] == "BULL" else data['Price'] + (data['ATR']*2.0), 2)
                            data['Target'] = round(data['Price'] + (data['ATR']*3.5) if data['Direction'] == "BULL" else data['Price'] - (data['ATR']*4.0), 2)
                            
                            db.log_bot_signal(data)
                            
                            opt = find_option(ticker, data['Direction'], data['ATR'], data['Price'])
                            opt_txt = f"⚡ **Option:** {opt['expiry']} ${opt['strike']} (Est: ${opt['price']})" if opt else "⚠️ Shares Only"
                            
                            reasons = "\n".join([f"• {r}" for r in data['Reasons']])
                            
                            msg = (f"🚀 **{data['Direction']} ALERT: {ticker}**\n"
                                   f"Score: {data['Score']} | Price: ${data['Price']}\n"
                                   f"ID: `{data['ID']}` (Click to Copy)\n\n"
                                   f"📊 **Signals:**\n{reasons}\n\n"
                                   f"🛑 Stop: ${data['Stop']} | 💰 Target: ${data['Target']}\n\n"
                                   f"{opt_txt}\n\n"
                                   f"👉 `/entered {data['ID']} [PRICE] [QTY]`")
                            
                            bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                            alert_history[ticker] = data['Direction']
                            found_new = True
                            time.sleep(1)
                    time.sleep(0.2)

                if (datetime.now() - last_heartbeat).total_seconds() > 3600:
                    status = "✅ Hourly Check. " + ("Alerts sent." if found_new else "No new setups.")
                    bot.send_message(CHAT_ID, status)
                    last_heartbeat = datetime.now()

                sleep_sec = 3600 if now.hour < 9 else 1800
                print(f"💤 Sleeping {sleep_sec}s...")
                time.sleep(sleep_sec)
                
            else:
                if (datetime.now() - last_heartbeat).total_seconds() > 14400:
                    bot.send_message(CHAT_ID, "🌙 Night Mode: Watchdog Active.")
                    last_heartbeat = datetime.now()
                time.sleep(3600)
                
        except Exception as e:
            print(f"Scanner Error: {e}")
            time.sleep(60)

# --- SERVER ---
@app.route('/')
def index(): return "Agent V21 Online", 200

def run_server():
    # Use 8888 locally (Mac fix) or Cloud PORT
    port = int(os.environ.get("PORT", 8888))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    t_scan = threading.Thread(target=scanner_loop)
    t_scan.start()
    t_bot = threading.Thread(target=bot.infinity_polling)
    t_bot.start()
    run_server()