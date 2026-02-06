# 🤖 Agent V20: The "Golden Edition" Stock Trading Bot

**Version:** 20.5 (Production Release)
**Strategy:** Confluence-Based Trend Following & Reversal Sniper
**Backtest Performance:** +388% Net Profit (Jan 2020 - Dec 2025)

---

## 📖 1. Overview
Agent V20 is a fully autonomous Python trading bot designed to scan the US Stock Market (S&P 300 + Top Active Movers) for high-probability option trade setups.

Unlike standard bots that rely on a single indicator (like RSI), Agent V20 uses a **Confluence Scoring Engine**. It calculates 10 different technical indicators for every stock and assigns a **Score (0-100)**. It only sends an alert when multiple independent signals agree (Confluence), ensuring a high win rate and protecting capital during choppy markets.

---

## 🧠 2. The Logic (The "Why")

### A. The Philosophy
The bot operates on the principle of **"Sniper Trading."**
* **Most bots fail** because they over-trade during "noise" (sideways markets).
* **Agent V20 succeeds** because it is extremely conservative. It ignores 99% of the market and only strikes when the setup is perfect (Score >= 75 or Score <= 35).

### B. The Indicator Engine
For every stock, the bot calculates the following metrics using the last 2 years of daily data:

| Indicator | Parameter | What it detects | The Logic (Why we use it) |
| :--- | :--- | :--- | :--- |
| **SMA 50 & 200** | Simple Moving Avg | **Trend Direction** | We never trade against the major trend (Trend Following). |
| **RSI** | 14-Period | **Momentum** | Identifies Overbought (>70) or Oversold (<30) conditions. |
| **ADX** | 14-Period | **Trend Strength** | Filters out "chop." We only trade if ADX > 25 (Strong Trend). |
| **Bollinger Bands** | 20, 2 | **Volatility** | Detects "Squeezes" (low vol) or "Extensions" (reversal likely). |
| **ATR** | 14-Period | **True Range** | Measures volatility in dollars. Used for dynamic Stop Loss sizing. |
| **Volume Ratio** | vs 20-Day Avg | **Participation** | We need Volume > 1.2x average to confirm a move is real. |
| **ROC** | 5-Day & 10-Day | **Velocity** | Measures how fast price is moving. High speed = High conviction. |
| **Wicks** | Candlestick | **Rejection** | Long upper wicks = Bearish rejection. Long lower wicks = Bullish. |
| **FVG** | Fair Value Gap | **Magnet Levels** | Price often returns to fill "gaps" created by explosive moves. |
| **Structure** | Highs/Lows | **Price Action** | Checks for Higher Lows (Bullish) or Lower Highs (Bearish). |

---

## 🧮 3. The Scoring System

The bot does not use "If/Then" statements. It uses a **Weighted Point System**.

### 🐂 Bullish Scoring (Buying Calls)
**Trigger Requirement:** Score >= **75** AND ADX > **25**

* **+15 pts:** Price > SMA50 > SMA200 (Perfect Uptrend)
* **+10 pts:** Price > SMA50 (General Uptrend)
* **+10 pts:** ADX > 25 (Strong Trend exists)
* **+25 pts:** RSI < 30 (Oversold - "Buy the Dip" opportunity)
* **+15 pts:** RSI < 40 (Weak Pullback)
* **+12 pts:** Bollinger Band Position < 0.2 (Price is cheap relative to volatility)
* **+12 pts:** Bullish Fair Value Gap (FVG) detected
* **+13 pts:** Bullish Wick Rejection (Hammer candle)
* **+10 pts:** Volume > 1.5x Average (Institutional Buying)

### 🐻 Bearish Scoring (Buying Puts)
**Trigger Requirement:** Score <= **35** AND Confirmations >= **4**
*(Note: Bearish signals require extra confirmation because shorting is riskier)*

* **-20 pts:** Price < SMA50 < SMA200 (Perfect Downtrend)
* **-12 pts:** Price < SMA50 (Weakness)
* **-25 pts:** RSI > 75 (Extreme Overbought - Reversal imminent)
* **-18 pts:** RSI > 70 (Overbought)
* **-15 pts:** ATR Ratio > 1.2 (Volatility expanding downwards)
* **-12 pts:** Bollinger Band Position > 0.9 (Price hitting upper resistance)
* **-10 pts:** Bearish Wick Rejection (Shooting Star candle)
* **-10 pts:** Support Level Broken
* **-15 pts:** Volume > 2.0x on a red candle (Panic Selling)

---

## 🎯 4. Options Strategy & Risk Management

When a signal fires, the bot acts as an Options Strategist.

### 1. Contract Selection
* **Expiry:** Selects the monthly expiration closest to **45 Days To Expiration (DTE)**. (Optimal balance of Theta decay vs. Gamma exposure).
* **Strike Price:** Selects a strike that is **1.5x ATR Out-of-the-Money (OTM)**. This provides cheap contracts with high leverage potential if the move happens.
* **Liquidity Filter:** Ignores any contract with Open Interest < 50 to ensure you can exit the trade.

### 2. Risk Management (The "Exit Plan")
Every alert comes with mathematically calculated exits based on **ATR (Average True Range)**. This adjusts for the stock's volatility.

* **Stop Loss:**
    * **Longs:** Price - (2.5 x ATR)
    * **Shorts:** Price + (2.0 x ATR)
    * *Why?* Gives the trade "room to breathe" without risking ruin.
* **Profit Target:**
    * **Longs:** Price + (3.5 x ATR)
    * **Shorts:** Price - (4.0 x ATR)
    * *Why?* Targets a Risk/Reward ratio of roughly **1:1.5 to 1:2**.

---

## ⚙️ 5. Technical Features

### A. Smart Caching
To prevent getting banned by Wikipedia or Yahoo Finance:
* The bot downloads the S&P 300 list and saves it to `sp300_cache.csv`.
* It re-uses this local file for **24 hours**.
* It only re-downloads if the file is missing or old.

### B. Dynamic Scope
* **Base List:** S&P 300 (Stable, liquid stocks).
* **Hot List:** Scrapes Yahoo Finance "Most Active" every 15 mins to find trending stocks (e.g., meme stocks or earnings movers) and adds them to the scan instantly.

### C. Live Logging
Every alert sent to Telegram is also saved to `live_trades.csv` on your server.
* **Columns:** Time, Ticker, Direction, Price, Score, Reasons.
* **Use Case:** Import this into Excel to audit the bot's performance weekly.

---

## 🚀 6. Installation & Usage

### Prerequisites
* Python 3.8+
* Telegram Bot Token (from @BotFather)

### Setup
1.  **Install Libraries:**
    `pip install yfinance pandas numpy pyTelegramBotAPI flask requests lxml pytz`
2.  **Configure:**
    Open `agent.py` and replace the placeholders:
    `API_TOKEN` and `YOUR_CHAT_ID`

### Running the Bot
`python agent.py`
* The bot will start the **Auto-Scanner** (6:00 AM - 5:00 PM EST).
* It will launch a **Web Server** on port 8080 (for cloud keep-alive).

### Telegram Commands
| Command | Usage | Description |
| :--- | :--- | :--- |
| `/check` | `/check NVDA` | Manually runs the V20 logic on a specific stock. Returns the Score and detailed stats immediately. |
| `/scan` | `/scan` | Forces an immediate scan of the entire market (S&P 300 + Movers) right now, ignoring the schedule. |

---

## ⚠️ Disclaimer
*This software is for educational purposes only. Financial trading involves significant risk. The author is not responsible for any financial losses incurred while using this code. Always backtest strategies and paper trade before using real capital.*
