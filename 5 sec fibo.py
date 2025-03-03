import asyncio
import time
import logging
import datetime
import pytz
from quotexapi.config import email, password
from quotexapi.stable_api import Quotex
from quotexapi.utils.processor import process_candles

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Global variable to control single active trade
global_trade_active = False  

# Convert UTC timestamp to IST
def convert_to_ist(timestamp_utc):
    utc_time = datetime.datetime.utcfromtimestamp(timestamp_utc)
    india_timezone = pytz.timezone('Asia/Kolkata')
    india_time = utc_time.replace(tzinfo=pytz.utc).astimezone(india_timezone)
    return india_time.strftime('%Y-%m-%d %H:%M:%S')

# Calculate Fibonacci retracement levels
def fibonacci_levels(high, low):
    """Returns Fibonacci retracement levels based on high and low."""
    diff = high - low
    return {
        "0.236": high - (diff * 0.236),
        "0.382": high - (diff * 0.382),
        "0.5": high - (diff * 0.5),
        "0.618": high - (diff * 0.618),
        "0.786": high - (diff * 0.786),
    }

async def get_live_candles_and_trade(client, assets):
    """Continuously scan assets and place one trade at a time."""
    global global_trade_active

    while True:
        if global_trade_active:
            logging.info("A trade is active. Waiting for completion before placing a new one.")
            await asyncio.sleep(5)
            continue

        for asset in assets:
            if global_trade_active:  
                break  # Skip to next loop if trade is active

            current_time = time.time()
            candles = await client.get_candles(asset, current_time, 10, 5)

            if candles:
                try:
                    candles_data = process_candles(candles, 5)
                except KeyError:
                    candles_data = [
                        {
                            "time": candle.get("time", 0),
                            "open": candle.get("open", 0),
                            "close": candle.get("close", 0),
                            "high": candle.get("high", 0),
                            "low": candle.get("low", 0),
                        }
                        for candle in candles
                    ]

                if len(candles_data) >= 10:
                    highs = [candle["high"] for candle in candles_data]
                    lows = [candle["low"] for candle in candles_data]

                    swing_high = max(highs)
                    swing_low = min(lows)

                    fib_levels = fibonacci_levels(swing_high, swing_low)

                    latest_candle = candles_data[-1]
                    price = latest_candle["close"]
                    prev_price = candles_data[-2]["close"]

                    logging.info(f"[{asset}] - Fibonacci Levels: {fib_levels}")
                    logging.info(f"[{asset}] - Latest Price: {price} | Swing High: {swing_high} | Swing Low: {swing_low}")

                    direction = None  

                    # 50%-61.8% Retracement Trade (Trend Continuation)
                    if fib_levels["0.5"] <= price <= fib_levels["0.618"]:
                        if prev_price < price:  
                            direction = "call"
                        elif prev_price > price:  
                            direction = "put"

                    # 78.6% Reversal Trade
                    elif price >= fib_levels["0.786"]:
                        if price < swing_high:  
                            direction = "put"
                        elif price > swing_low:  
                            direction = "call"

                    if direction:
                        logging.info(f"[{asset}] - Placing {direction.upper()} Trade")
                        await execute_trade(client, asset, direction)

            await asyncio.sleep(2)  # Short delay before checking the next asset

async def execute_trade(client, asset, direction):
    """Execute a trade and prevent new trades until it finishes."""
    global global_trade_active
    stake = 1  
    duration = 5

    async with asyncio.Lock():  
        if global_trade_active:
            logging.info(f"[{asset}] - Trade already active, skipping.")
            return

        logging.info(f"[{asset}] - Attempting trade | Direction: {direction.upper()} | Stake: {stake}")
        status, buy_info = await client.buy(stake, asset, direction, duration)

        if status:
            logging.info(f"[{asset}] - Trade Successful! Direction: {direction.upper()} | Info: {buy_info}")
            global_trade_active = True  
            await track_trade_result(client, buy_info["id"], duration)  
        else:
            logging.error(f"[{asset}] - Trade Failed! Error: {buy_info}")
            global_trade_active = False  

async def track_trade_result(client, trade_id, duration):
    """Wait for trade result, then allow the next trade."""
    global global_trade_active
    timeout = time.time() + duration + 5  

    logging.info(f"Tracking trade result for Trade ID: {trade_id}")

    while time.time() < timeout:
        await asyncio.sleep(2)  
        result = await client.check_win(trade_id)
        
        if result is not None:  
            logging.info(f"Trade ID {trade_id} Result: {'Win' if result > 0 else 'Loss'} | Payout: {result}")
            break  

    global_trade_active = False  
    logging.info("Trade completed. Ready for next trade.")

async def main():
    client = Quotex(email, password)
    connected, message = await client.connect()

    if connected:
        logging.info("Connected to Quotex API")
        assets = ["BRLUSD_otc", "GBPJPY_otc", "USDINR_otc", "NZDUSD_otc"]

        await get_live_candles_and_trade(client, assets)  
    else:
        logging.error(f"Failed to connect: {message}")

if __name__ == "__main__":
    asyncio.run(main())
