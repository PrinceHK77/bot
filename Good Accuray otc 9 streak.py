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
MAX_MARTINGALE_STAGES = 3  # Limit the number of martingale stages

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
    
async def martingale(client, asset, error_candle, initial_stake, max_stages=MAX_MARTINGALE_STAGES):
    stake = initial_stake
    for stage in range(max_stages):
        outcome = await place_trade_at_next_candle_start(client, asset, error_candle, stake)
        
        # Update stakes based on result
        if outcome == "win":
            trade_summary["total_trades"] += 1
            trade_summary["wins"] += 1
            break
        elif outcome == "loss":
            stake *= MARTINGALE_FACTOR  # Increase stake for the next round
            trade_summary["total_trades"] += 1
            trade_summary["losses"] += 1
        else:
            trade_summary["total_trades"] += 1
            trade_summary["dojis"] += 1

        logging.info(f"Martingale Stage {stage + 1} Complete: New Stake: {stake}")
        await asyncio.sleep(1)  # Short delay between stages

async def place_trade_at_next_candle_start(client, asset, error_candle, stake):
    direction = "put" if error_candle["close"] > error_candle["open"] else "call"
    logging.info(f"Preparing to place an opposite trade for {asset} at the next candle's start: {direction}")

    # Wait until the next candle's start
    current_time = int(time.time())
    seconds_to_next_candle = 56 - (current_time % 60)
    await asyncio.sleep(seconds_to_next_candle)

    # Place the trade
    status, buy_info = await client.buy(stake, asset, direction, 60)
    if status:
        logging.info(f"Trade placed: {direction} | Info: {buy_info}")
        trade_id = buy_info.get("id", None)

        # Check for trade result
        if trade_id:
            if await client.check_win(trade_id):
                logging.info(f"Trade result for {asset}: won! ✅ | Profit: {buy_info['profit']}")
                return "win"
            else:
                logging.info(f"Trade result for {asset}: lost! ❌ | Profit: {buy_info['profit']}")
                return "loss"
        else:
            logging.info(f"Trade result for {asset}: undetermined")
            return "undetermined"
    else:
        logging.error(f"Failed to place trade for {asset}.")
        return None

# Analyze asset
async def analyze_asset(client, asset):
    try:
        current_time = int(time.time())
        candles = await client.get_candles(asset, current_time, 10800, 60)
        if not candles:
            logging.warning(f"No candles available for {asset}.")
            return
        
        trend = identify_trend(candles)
        logging.info(f"{asset} Market Trend: {trend}")

        error_candle = candles[-1]  # Get the latest candle (error candle)
        
        if trend == "Bullish":
            if (
                check_bullish_engulfing(candles) or 
                check_bullish_harami(candles) or 
                check_bullish_pin_bar(candles)
            ):
                logging.info(f"Bullish pattern detected for {asset}.")
                await martingale(client, asset, error_candle, initial_stake=100)
        elif trend == "Bearish":
            if (
                check_bearish_engulfing(candles) or 
                check_bearish_harami(candles) or 
                check_bearish_pin_bar(candles)
            ):
                logging.info(f"Bearish pattern detected for {asset}.")
                await martingale(client, asset, error_candle, initial_stake=100)
        else:
            logging.info(f"No valid trading opportunity for {asset}.")
    except Exception as e:
        logging.error(f"Error analyzing {asset}: {e}")

# Main function
async def main():
    assets = [
    "USDINR_otc","USDMXN_otc", "USDBDT_otc", "USDPKR_otc", "BRLUSD_otc",
    "USDNGN_otc", "USDPHP_otc", "USDTRY_otc", "USDEGP_otc","USDZAR_otc", 
    "USDARS_otc", "USDDZD_otc", "USDIDR_otc"
]
 # Add more assets as needed
    client = Quotex(email, password)
    connected, _ = await client.connect()
    if not connected:
        logging.error("Failed to connect to Quotex.")
        return
    
    logging.info("Connected to Quotex. Starting analysis...")
    try:
        while True:
            for asset in assets:
                await analyze_asset(client, asset)  # Wait for the result of each analysis
                await asyncio.sleep(1)  # Short delay between assets
    except KeyboardInterrupt:
        logging.info("Analysis stopped by user.")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(main())


#"EURUSD", "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF",  # European Pairs
#"USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"