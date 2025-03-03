import asyncio
import logging
import time
import numpy as np
from quotexapi.config import email, password
from quotexapi.stable_api import Quotex

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# Trade summary
trade_summary = {"total_trades": 0, "wins": 0, "losses": 0, "dojis": 0}

# Moving Average Parameters
SHORT_TERM_PERIOD = 5
LONG_TERM_PERIOD = 20
RSI_PERIOD = 14  # Standard RSI period

# Martingale Parameters
MARTINGALE_FACTOR = 2  # Multiply bet after each loss
MAX_MARTINGALE_STAGES = 2  # Limit the number of martingale stages

# User input parameters
initial_balance = 0
initial_stake = 0
target_profit = 0
stop_loss = 0

# Dictionary to track Martingale stakes per asset
martingale_stakes = {}

# Function to calculate the moving average
def calculate_moving_average(data, period):
    close_prices = np.array([candle["close"] for candle in data])
    return np.mean(close_prices[-period:])

# Function to calculate RSI
def calculate_rsi(data, period=RSI_PERIOD):
    close_prices = np.array([candle["close"] for candle in data])
    gains = []
    losses = []
    
    for i in range(1, len(close_prices)):
        change = close_prices[i] - close_prices[i - 1]
        gains.append(max(change, 0))
        losses.append(-min(change, 0))
    
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100  # Overbought condition
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# Function to identify the trend
def identify_trend(data, short_term_period=SHORT_TERM_PERIOD, long_term_period=LONG_TERM_PERIOD):
    short_term_ma = calculate_moving_average(data, short_term_period)
    long_term_ma = calculate_moving_average(data, long_term_period)
    if short_term_ma > long_term_ma:
        return "Bullish"
    elif short_term_ma < long_term_ma:
        return "Bearish"
    else:
        return "Sideways"

# Pattern checking functions (same as before)
def check_bullish_engulfing(data):
    if len(data) < 2:
        return False
    if data[-2]["close"] < data[-2]["open"] and data[-1]["close"] > data[-1]["open"]:
        return data[-1]["close"] > data[-2]["open"] and data[-1]["open"] < data[-2]["close"]
    return False

def check_bearish_engulfing(data):
    if len(data) < 2:
        return False
    if data[-2]["close"] > data[-2]["open"] and data[-1]["close"] < data[-1]["open"]:
        return data[-1]["close"] < data[-2]["open"] and data[-1]["open"] > data[-2]["close"]
    return False

def check_bullish_harami(data):
    if len(data) < 2:
        return False
    if data[-2]["close"] > data[-2]["open"] and data[-1]["close"] < data[-1]["open"]:
        return data[-1]["close"] > data[-2]["open"] and data[-1]["open"] < data[-2]["close"]
    return False

def check_bearish_harami(data):
    if len(data) < 2:
        return False
    if data[-2]["close"] < data[-2]["open"] and data[-1]["close"] > data[-1]["open"]:
        return data[-1]["close"] < data[-2]["open"] and data[-1]["open"] > data[-2]["close"]
    return False

# Function to identify a bullish pin bar
def check_bullish_pin_bar(data):
    if len(data) < 1:
        return False
    candle = data[-1]  # Current candle
    body_size = abs(candle["close"] - candle["open"])
    lower_wick = candle["low"] - min(candle["close"], candle["open"])
    upper_wick = candle["high"] - max(candle["close"], candle["open"])

    # Criteria for a bullish pin bar
    return (
        lower_wick > 2 * body_size and  # Lower wick is much longer than the body
        upper_wick < body_size and  # Upper wick is smaller
        candle["close"] > candle["open"]  # Bullish candle
    )

# Function to identify a bearish pin bar
def check_bearish_pin_bar(data):
    if len(data) < 1:
        return False
    candle = data[-1]  # Current candle
    body_size = abs(candle["close"] - candle["open"])
    lower_wick = candle["low"] - min(candle["close"], candle["open"])
    upper_wick = candle["high"] - max(candle["close"], candle["open"])

    # Criteria for a bearish pin bar
    return (
        upper_wick > 2 * body_size and  # Upper wick is much longer than the body
        lower_wick < body_size and  # Lower wick is smaller
        candle["close"] < candle["open"]  # Bearish candle
    )
    
async def check_balance(client):
    global initial_balance
    
    balance = await client.get_balance()  # Properly awaiting the coroutine
    
    logging.info(f"💰 Current Balance: {balance}")

    if balance >= target_profit:
        logging.info("🎯 Target Profit Reached! Stopping trading.")
        return True
    if balance <= stop_loss:
        logging.info("❌ Stop Loss Reached! Stopping trading.")
        return True
    return False

# Global Martingale Variables
current_stake = None  # Will be initialized in main()
martingale_stage = 0  # Track consecutive losses

async def apply_trade(client, asset, error_candle):
    global current_stake, martingale_stage, initial_stake
    
    # Ensure current_stake is correctly initialized
    if current_stake is None:
        current_stake = initial_stake

    outcome = await place_trade_at_next_candle_start(client, asset, error_candle, current_stake)

    trade_summary["total_trades"] += 1
    if outcome == "win":
        trade_summary["wins"] += 1
        current_stake = initial_stake  # ✅ Reset stake on win
        martingale_stage = 0  # ✅ Reset Martingale stage
        logging.info(f"✅ Trade WON! 🎉 Stake reset to {initial_stake}")
    
    elif outcome == "loss":
        trade_summary["losses"] += 1
        
        if martingale_stage <= MAX_MARTINGALE_STAGES:
            current_stake *= MARTINGALE_FACTOR  # ✅ Increase stake
            martingale_stage += 1
            logging.info(f"❌ Trade LOST. Next stake: {current_stake} (Martingale Stage: {martingale_stage})")
        else:
            current_stake = initial_stake  # ✅ Reset stake after max Martingale stage
            martingale_stage = 0
            logging.info(f"❌ Trade LOST. Max Martingale stage reached! Resetting stake to {initial_stake}")

    else:
        trade_summary["dojis"] += 1
        logging.info(f"⏸️ Trade resulted in a Doji. Stake remains at {current_stake}")

    # Print trade summary
    logging.info(f"📊 Total Trades: {trade_summary['total_trades']} | Wins: {trade_summary['wins']} | Losses: {trade_summary['losses']} | Dojis: {trade_summary['dojis']}")
    
    # Stop trading if balance limit is reached
    stop_trading = await check_balance(client)
    if stop_trading:
        logging.info("🚀 Trading Session Ended.")
        exit()


import time
import asyncio
import logging
import numpy as np

async def place_trade_at_next_candle_start(client, asset, error_candle, stake):
    direction = "put" if error_candle["close"] > error_candle["open"] else "call"
    logging.info(f"🚀 Preparing to place a trade for {asset} at the next candle's start: {direction} | Stake: {stake}")

    # Wait until the next candle start
    current_time = int(time.time())
    seconds_to_next_candle = 59 - (current_time % 60)
    await asyncio.sleep(seconds_to_next_candle)

    logging.info(f"📌 Placing trade for {asset} at next candle start: {direction} with stake {stake}")

    # Place the trade
    status, buy_info = await client.buy(stake, asset, direction, 60)
    if not status:
        logging.error(f"❌ Trade placement failed for {asset}.")
        return None

    trade_id = buy_info.get("id", None)
    if not trade_id:
        logging.error(f"⚠️ Trade ID missing. Could not verify trade outcome for {asset}.")
        return "undetermined"

    # Check trade outcome
    await asyncio.sleep(61)  # Wait for the trade duration
    win_status = await client.check_win(trade_id) 

    if win_status is True:  # Explicitly check for True
        logging.info(f"✅ Win!!! 🎉 We won, buddy!!! Profit: {buy_info['profit']}")
        return "win"
    elif win_status is False:
        loss = -stake
        logging.info(f"❌ Loss!!! 😢 We lost, buddy!!! Loss: R$ {loss}")
        return "loss"
    else:
        logging.error(f"⚠️ Unexpected trade result for {asset}: {win_status}")
        return "undetermined"

# Function to check if the market is too volatile
def is_market_volatile(data):
    if len(data) < 5:
        return False
    avg_body = np.mean([abs(candle["close"] - candle["open"]) for candle in data[-5:]])
    avg_wick = np.mean([(candle["high"] - candle["low"]) for candle in data[-5:]])
    
    # If wick is significantly larger than body, consider it volatile
    return avg_wick > (2 * avg_body)

# Function to check if the last candle is a Doji
def is_doji(candle):
    body_size = abs(candle["close"] - candle["open"])
    total_range = candle["high"] - candle["low"]
    
    # A Doji is when the body is very small compared to the total range
    return body_size < (0.1 * total_range)

# Function to check if the last three candles are all the opposite direction
def check_three_opposite_candles(data, direction):
    if len(data) < 3:
        return False  # Not enough data

    last_three = data[-3:]  # Get the last three candles
    
    # Check if all last three candles are green (bullish)
    if direction == "put" and all(candle["close"] > candle["open"] for candle in last_three):
        logging.info("🚫 Three consecutive GREEN candles detected. Skipping PUT trade.")
        return True
    
    # Check if all last three candles are red (bearish)
    if direction == "call" and all(candle["close"] < candle["open"] for candle in last_three):
        logging.info("🚫 Three consecutive RED candles detected. Skipping CALL trade.")
        return True

    return False  # No filter triggered

async def analyze_asset(client, asset):
    try:
        current_time = int(time.time())
        candles = await client.get_candles(asset, current_time, 10800, 60)
        if not candles:
            logging.warning(f"No candles available for {asset}.")
            return
        
        trend = identify_trend(candles)
        logging.info(f"{asset} Market Trend: {trend}")

        error_candle = candles[-1]  # Latest candle

        # Determine the direction for filtering opposite candles
        direction = "put" if error_candle["close"] > error_candle["open"] else "call"

        # Apply the new filters
        if is_market_volatile(candles):
            logging.info(f"🚫 {asset} Market is too volatile. Skipping trade.")
            return
        
        if is_doji(error_candle):
            logging.info(f"🚫 {asset} Doji detected. Skipping trade.")
            return
        
        if check_three_opposite_candles(candles, direction):  # Now correctly passing 'direction'
            logging.info(f"🚫 {asset} Last 3 candles are {direction.upper()}. Avoiding trade.")
            return

        # Trade only if the pattern matches the trend
        if trend == "Bullish":
            if (
                check_bullish_engulfing(candles) or 
                check_bullish_harami(candles) or 
                check_bullish_pin_bar(candles)
            ):
                logging.info(f"📈 Bullish pattern detected for {asset}. Entering trade.")
                await apply_trade(client, asset, error_candle)
        elif trend == "Bearish":
            if (
                check_bearish_engulfing(candles) or 
                check_bearish_harami(candles) or 
                check_bearish_pin_bar(candles)
            ):
                logging.info(f"📉 Bearish pattern detected for {asset}. Entering trade.")
                await apply_trade(client, asset, error_candle)
        else:
            logging.info(f"🚫 No valid trading opportunity for {asset}.")
    except Exception as e:
        logging.error(f"Error analyzing {asset}: {e}")


async def main():
    global initial_balance, initial_stake, target_profit, stop_loss
    assets = ["BRLUSD_otc","CADCHF_otc", "GBPJPY_otc", "USDIDR_otc",
              "NZDUSD_otc", "GBPCHF_otc", "USDINR_otc", "NZDJPY_otc",
              "NZDCAD_otc", "USDMXN_otc", "USDBDT_otc", "USDPKR_otc", 
              "USDNGN_otc", "USDPHP_otc", "USDTRY_otc", "USDEGP_otc", 
              "USDZAR_otc", "USDARS_otc", "USDDZD_otc"]

    client = Quotex(email, password)
    connected, _ = await client.connect()
    if not connected:
        logging.error("❌ Failed to connect to Quotex.")
        return

    # Fetch initial balance
    initial_balance = await client.get_balance()
    logging.info(f"💰 Initial Account Balance: {initial_balance}")

    # Get user inputs
    initial_stake = float(input("Enter Initial Stake: "))
    target_profit = float(input("Enter Target Profit Amount: "))
    stop_loss = float(input("Enter Stop Loss Amount: "))

    while True:
        for asset in assets:
            await analyze_asset(client, asset)
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())

#need too add 1️⃣ Add a Volatility Filter 📊 5️⃣ Use a Time-Based Filter ⏰