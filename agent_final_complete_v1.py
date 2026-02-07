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
# ✅ IMPORT parse_month_arg so we can understand "JUN", "SEP", etc.
from database import TradeManager
from analysis import analyze_stock, find_option, get_sp300_tickers, get_dynamic_movers, parse_month_arg

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

# --- HELPER: FORMAT MESSAGE ---
def format_alert(data, opt, is_auto=False):
    icon = "🚀" if data['Direction'] == "BULL" else "🔻"
    color = "🟢" if data['Direction'] == "BULL" else "🔴"
    opt_type = "CALL" if data['Direction'] == "BULL" else "PUT"
    
    # Format Reasons
    reasons_list = data.get('Reasons', [])
    reasons_txt = "\n".join([f"• {r}" for r in reasons_list]) if reasons_list else "• Technical Pattern"

    # Format Option Section
    if opt:
        opt_txt = (f"⚡ Option: {opt_type} ${opt['strike']}\n"
                   f"📅 {opt['expiry']} (OI: {opt.get('oi', 'N/A')})\n"
                   f"💲 Est Cost: ${opt['price']}")
    else:
        opt_txt = "⚡ Option: Shares Only"

    # Base Message
    title = f"{icon} {data['Direction']} ALERT: {data['Ticker']}" if is_auto else f"{icon} MANUAL CHECK: {data['Ticker']}"
    
    msg = (f"{title}\n"
           f"Score: {data['Score']}/100 {color}\n"
           f"Price: ${data['Price']}\n\n"
           f"📊 Signals:\n{reasons_txt}\n\n"
           f"{opt_txt}\n\n"
           f"🛑 Stop: ${data['Stop']}\n"
           f"💰 Target: ${data['Target']}")
           
    # Add Tracking ID only for Auto Alerts
    if is_auto:
        msg += f"\n\n👇 **To Track:**\n`/entered {data['ID']} {opt['price'] if opt else data['Price']} [QTY]`"
        
    return msg

# --- COMMANDS ---

@bot.message_handler(commands=['entered'])
def cmd_entered(message):
    try:
        parts = message.text.split()
        if len(parts) < 4: return bot.reply_to(message, "⚠️ Usage: `/entered [ID] [PRICE] [QTY]`")
        
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
def cmd_manual(message):
    try:
        parts = message.text.split()
        if len(parts) < 6: 
            return bot.reply_to(message, "⚠️ Usage:\nShares: `/manual TICKER SHARE PRICE QTY STOP`\nOptions: `/manual TICKER CALL/PUT PRICE QTY STOP EXPIRY`")
        
        ticker = parts[1].upper()
        type_ = parts[2].upper()
        price = float(parts[3])
        qty = float(parts[4])
        stop = float(parts[5])
        
        if len(parts) >= 7: expiry = parts[6]
        else: expiry = "N/A"
            
        target = round(price * 1.2, 2)
        
        success = db.add_to_portfolio({
            "ID": f"#MAN_{ticker}_{datetime.now().strftime('%M%S')}",
            "Ticker": ticker, "Type": type_, "Price": price, "Qty": qty, 
            "Stop": stop, "Target": target, "Expiry": expiry,
            "Source": "Manual", "Notes": "User Added"
        })
        if success: bot.reply_to(message, f"✅ Added **{type_}** for {ticker}\n📅 Expiry: {expiry}")
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['scan'])
def cmd_scan(message):
    bot.reply_to(message, "🔍 **Force Scan Initiated...**\nChecking Watchdog & scanning 300+ tickers.")
    
    # 1. Run Watchdog (Check active trades)
    check_portfolio()
    
    # 2. Run Scanner (Force run regardless of time)
    # We run this in a thread so it doesn't freeze the bot
    def run_force_scan():
        tickers = list(set(get_sp300_tickers() + get_dynamic_movers()))
        found = 0
        for ticker in tickers:
            # strict=True means only return valid setups
            data = analyze_stock(ticker, strict=True)
            if data:
                # Log to DB
                db.log_bot_signal(data)
                
                # Find Option
                opt = find_option(ticker, data['Direction'], data['ATR'], data['Price'])
                
                # Format & Send Alert
                msg = format_alert(data, opt, is_auto=True)
                bot.send_message(CHAT_ID, msg, parse_mode="Markdown")
                found += 1
                time.sleep(1) # Anti-spam delay
            time.sleep(0.1) # Respect API limits
        
        bot.send_message(CHAT_ID, f"✅ **Scan Complete.** Found {found} setups.")

    # Start the thread
    threading.Thread(target=run_force_scan).start()

@bot.message_handler(commands=['check'])
def cmd_check(message):
    try:
        parts = message.text.split()
        if len(parts) < 2: return bot.reply_to(message, "⚠️ Usage: `/check TICKER [MONTH]`")
        
        ticker = parts[1].upper()
        
        # ✅ NEW: Check if user typed a month (e.g., "JUN")
        force_month = None
        if len(parts) >= 3:
            raw_month = parts[2]
            force_month = parse_month_arg(raw_month) # Converts "JUN" -> "06"
            if not force_month:
                bot.reply_to(message, f"⚠️ Unknown month: {raw_month}. Using default 45 days.")
        
        bot.reply_to(message, f"🔍 Analyzing {ticker}...")
        
        # Analyze
        data = analyze_stock(ticker, strict=False)
        
        if data:
            # ✅ PASS force_month to the finder
            opt = find_option(ticker, data['Direction'], data['ATR'], data['Price'], force_month=force_month)
            
            # Use the Shared Format Function
            msg = format_alert(data, opt, is_auto=False)
            
            bot.send_message(message.chat.id, msg, parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Could not analyze ticker.")
            
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['close'])
def cmd_close(message):
    try:
        parts = message.text.split()
        if len(parts) < 4: 
            return bot.reply_to(message, "⚠️ Usage: `/close TICKER TYPE PRICE`")
        
        ticker = parts[1].upper()
        type_input = parts[2].upper()
        exit_price = float(parts[3])
        
        portfolio = db.get_portfolio()
        target_trade = None
        for t in portfolio:
            if t['Ticker'] == ticker and type_input in t['Type'].upper():
                target_trade = t
                break
        
        if not target_trade:
            return bot.reply_to(message, f"❌ No open **{type_input}** position found for {ticker}.")
            
        success = db.close_position(target_trade['ID'], exit_price, "Manual Close")
        
        if success:
            entry = float(target_trade['Entry_Price'])
            qty = float(target_trade['Qty'])
            is_long = "CALL" in target_trade['Type'] or "SHARE" in target_trade['Type']
            
            if is_long: pnl = (exit_price - entry) * qty
            else: pnl = (entry - exit_price) * qty
            
            if "CALL" in target_trade['Type'] or "PUT" in target_trade['Type']: pnl *= 100
                
            icon = "🟢" if pnl > 0 else "🔴"
            msg = (f"🔒 **CLOSED: {ticker} {type_input}**\nEntry: ${entry}\nExit: ${exit_price}\nPnL: {icon} ${pnl:.2f}")
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "⚠️ Database Error.")
    except Exception as e: bot.reply_to(message, f"Error: {e}")

@bot.message_handler(commands=['portfolio'])
def cmd_portfolio(message):
    trades = db.get_portfolio()
    if not trades: return bot.reply_to(message, "📭 Portfolio empty.")
    msg = "💼 **Active Portfolio**\n"
    for t in trades:
        msg += f"• {t['Ticker']} ({t['Type']}) | Entry: ${t['Entry_Price']} | Stop: ${t['Stop_Loss']}\n"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(commands=['help', 'start'])
def cmd_help(message):
    msg = ("🤖 **COMMAND GUIDE**\n\n"
           "`/check TICKER [MONTH]` - Analyze stock\n"
           "`/manual TICKER TYPE PRICE QTY STOP` - Add trade\n"
           "`/close TICKER TYPE PRICE` - Close trade\n"
           "`/portfolio` - View active trades")
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
                            # ✅ FIX: Don't calc stops here, use what analyze_stock gave us
                            db.log_bot_signal(data)
                            opt = find_option(ticker, data['Direction'], data['ATR'], data['Price'])
                            
                            msg = format_alert(data, opt, is_auto=True)
                            
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
    port = int(os.environ.get("PORT", 8888))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    t_scan = threading.Thread(target=scanner_loop)
    t_scan.start()
    t_bot = threading.Thread(target=bot.infinity_polling)
    t_bot.start()
    run_server()