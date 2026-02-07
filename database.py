import gspread
import os
import json
from datetime import datetime

# CONFIGURATION
SHEET_NAME = "Gemini Trading Agent Tracker"

class TradeManager:
    def __init__(self):
        self.gc = None
        self.sh = None
        self._authenticate()

    def _authenticate(self):
        try:
            # 1. Try Cloud Env Var (Render)
            json_creds = os.environ.get("GOOGLE_CREDENTIALS")
            if json_creds:
                creds_dict = json.loads(json_creds)
                self.gc = gspread.service_account_from_dict(creds_dict)
            # 2. Try Local File (Testing)
            elif os.path.exists("service_account.json"):
                self.gc = gspread.service_account(filename="service_account.json")
            else:
                print("❌ No Google Credentials found.")
                return

            self.sh = self.gc.open(SHEET_NAME)
            self._init_tabs()
            print("✅ Connected to Google Sheets.")
        except Exception as e:
            print(f"❌ Database Connection Error: {e}")

    def _init_tabs(self):
        """Creates headers if tabs are empty"""
        # 1. Audit Log
        sig_ws = self._get_ws("Bot_Signals")
        if not sig_ws.get_all_values():
            sig_ws.append_row(["ID", "Date", "Ticker", "Direction", "Score", "Signal_Price", "Signal_Stop", "Signal_Target", "Reasons"])

        # 2. Watchdog
        port_ws = self._get_ws("Active_Portfolio")
        if not port_ws.get_all_values():
            port_ws.append_row(["ID", "Entry_Date", "Ticker", "Type", "Qty", "Entry_Price", "Stop_Loss", "Target", "Expiry", "Source", "Notes"])

        # 3. History
        jour_ws = self._get_ws("Trade_Journal")
        if not jour_ws.get_all_values():
            jour_ws.append_row(["ID", "Entry_Date", "Exit_Date", "Ticker", "Type", "Qty", "Entry_Price", "Exit_Price", "PnL", "Days_Held", "Exit_Reason", "Source"])

    def _get_ws(self, name):
        try: return self.sh.worksheet(name)
        except: return self.sh.add_worksheet(title=name, rows=100, cols=20)

    # --- ACTIONS ---
    def log_bot_signal(self, data):
        """Logs auto-alerts to Bot_Signals"""
        try:
            ws = self._get_ws("Bot_Signals")
            row = [
                data['ID'], datetime.now().strftime("%Y-%m-%d %H:%M"),
                data['Ticker'], data['Direction'], data['Score'],
                data['Price'], data['Stop'], data['Target'], "; ".join(data['Reasons'])
            ]
            ws.append_row(row)
        except Exception as e: print(f"⚠️ Log Error: {e}")

    def add_to_portfolio(self, data):
        """Adds trade to Active_Portfolio"""
        try:
            ws = self._get_ws("Active_Portfolio")
            row = [
                data['ID'], datetime.now().strftime("%Y-%m-%d %H:%M"),
                data['Ticker'], data['Type'], data['Qty'],
                data['Price'], data['Stop'], data['Target'],
                data.get('Expiry', 'N/A'), data['Source'], data.get('Notes', '')
            ]
            ws.append_row(row)
            return True
        except Exception as e:
            print(f"⚠️ Add Portfolio Error: {e}"); return False

    def get_portfolio(self):
        try: return self._get_ws("Active_Portfolio").get_all_records()
        except: return []

    def close_position(self, trade_id, exit_price, reason):
        """Moves from Portfolio -> Journal"""
        try:
            p_ws = self._get_ws("Active_Portfolio")
            j_ws = self._get_ws("Trade_Journal")
            
            cell = p_ws.find(trade_id)
            if not cell: return False
            
            row_num = cell.row
            row_data = p_ws.row_values(row_num)
            headers = p_ws.row_values(1)
            
            def get_val(col): 
                try: return row_data[headers.index(col)]
                except: return ""

            # Calculate PnL
            qty = float(get_val("Qty"))
            entry = float(get_val("Entry_Price"))
            type_ = get_val("Type").upper()
            
            pnl = 0
            if "CALL" in type_ or "SHARE" in type_:
                pnl = (exit_price - entry) * qty
                if "CALL" in type_: pnl *= 100
            elif "PUT" in type_:
                pnl = (entry - exit_price) * qty * 100
                
            # Date Math
            entry_str = get_val("Entry_Date")
            try: days = (datetime.now() - datetime.strptime(entry_str, "%Y-%m-%d %H:%M")).days
            except: days = 0

            journal_row = [
                get_val("ID"), entry_str, datetime.now().strftime("%Y-%m-%d %H:%M"),
                get_val("Ticker"), get_val("Type"), qty, entry, exit_price,
                round(pnl, 2), days, reason, get_val("Source")
            ]
            j_ws.append_row(journal_row)
            p_ws.delete_rows(row_num)
            return True
        except Exception as e:
            print(f"⚠️ Close Error: {e}"); return False

    def get_signal_details(self, signal_id):
        try:
            ws = self._get_ws("Bot_Signals")
            cell = ws.find(signal_id)
            if cell:
                row = ws.row_values(cell.row)
                h = ws.row_values(1)
                return {
                    "Ticker": row[h.index("Ticker")], "Direction": row[h.index("Direction")],
                    "Stop": row[h.index("Signal_Stop")], "Target": row[h.index("Signal_Target")]
                }
        except: return None