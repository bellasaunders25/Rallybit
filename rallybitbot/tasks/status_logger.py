
import json
import os
import time
import asyncio
from discord.ext import tasks
from config.config import DATA_DIR

HISTORY_FILE = os.path.join(DATA_DIR, "status_history.json")
MAX_DAYS = 5
INTERVAL_MINUTES = 30

@tasks.loop(minutes=INTERVAL_MINUTES)
async def status_logger_loop(bot):
    """
    Logs the current bot latency and status to a JSON file.
    Keeps only the last 5 days of data.
    """
    try:
        # 1. Gather Data
        current_time = int(time.time())
        latency = round(bot.latency * 1000) if (bot.latency is not None and bot.latency != float('inf')) else 0
        status = "online" if latency > 0 else "offline"
        
        entry = {
            "time": current_time,
            "status": status,
            "ping": latency
        }

        # 2. Load Existing Data
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except json.JSONDecodeError:
                history = []
        
        # 3. Append New Entry
        history.append(entry)

        # 4. Prune Old Data
        cutoff_time = current_time - (MAX_DAYS * 24 * 60 * 60)
        history = [h for h in history if h["time"] >= cutoff_time]

        # 5. Save
        # Ensure directory exists
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=None)  # Minified to save space
            
        print(f"📊 Status Logged: {status.upper()} | {latency}ms")

    except Exception as e:
        print(f"❌ Error in status logger: {e}")

@status_logger_loop.before_loop
async def before_status_logger():
    await asyncio.sleep(10) # Wait for bot to fully connect

def setup_status_task(bot):
    status_logger_loop.start(bot)
