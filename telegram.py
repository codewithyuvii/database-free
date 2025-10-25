import asyncio, time, logging, json, os, random
from datetime import datetime
from telethon import TelegramClient, errors

# ---------------- CONFIG - EDIT THESE ----------------
API_ID = 20804419
API_HASH = "68d03f3719fde9a18b927fff935d640f"
SESSION_NAME = "session_final_auto"

# Target receiver
TARGET = "@fakemailbot"  # <-- change to your target username

# Email sequence
EMAIL_PREFIX = "jasus"
EMAIL_DOMAIN = "@hi2.in"
START_NUM = 1
END_NUM = 500  # Stop tool after this number
WIDTH = 2      # number of digits, e.g., 1 -> 0001

# Safety / throttling
MAX_PER_MIN = 40
SHORT_BUFFER = 0.3
JITTER = 0.45
BURST_BREAKER_CONSEC = 5
BURST_BREAKER_PAUSE = 20
TEN_GAP_SECONDS = 5
BACKOFF_CAP_SECONDS = 60 * 60  # 1 hour max backoff

# State file to resume
STATE_FILE = "state.json"

# Base delays for 3-step pattern
BASE_DELAYS = [1.0, 3.0, 2.0]

# ----------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

def make_email(prefix, num, width, domain):
    return f"{prefix}{str(num).zfill(width)}{domain}"

def load_state():
    """Load last sent number from state file. Returns last_num (int) or None if not found."""
    try:
        if not os.path.exists(STATE_FILE):
            return None
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        last = data.get("last_num")
        if isinstance(last, int):
            logging.info(f"Loaded state: last_num={last}")
            return last
    except Exception as e:
        logging.warning(f"Failed to load state file: {e}")
    return None

def save_state(last_num):
    """Atomically write state to STATE_FILE."""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_num": last_num}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
        logging.debug(f"Saved state last_num={last_num}")
    except Exception as e:
        logging.warning(f"Failed to save state: {e}")

def generate_dynamic_cycle():
    """Generate one cycle of 3 delays with jitter and occasional swap of 2nd/3rd."""
    delays = [max(0.4, d + random.uniform(-JITTER, JITTER)) for d in BASE_DELAYS]
    if random.random() < 0.5:
        delays[1], delays[2] = delays[2], delays[1]
    return delays

async def send_loop():
    if not TARGET:
        logging.error("TARGET not set. Edit TARGET variable at top of script.")
        return

    # resume from state or start fresh
    last_num = load_state()
    current_num = START_NUM if last_num is None else last_num + 1

    sent_timestamps = []      # for sliding 60s rate window
    consecutive_sends = 0     # burst breaker
    flood_count = 0           # FloodWait backoff
    total_successful = 0      # 10-send gap

    logging.info(f"Starting send loop. Resuming from number: {current_num}")

    while True:
        cycle_delays = generate_dynamic_cycle()
        for base_delay in cycle_delays:
            now = time.time()
            sent_timestamps = [t for t in sent_timestamps if now - t < 60]
            if len(sent_timestamps) >= MAX_PER_MIN:
                oldest = min(sent_timestamps)
                to_wait = 60 - (now - oldest) + 0.5
                logging.info(f"Rate cap reached ({MAX_PER_MIN}/60s). Sleeping {to_wait:.1f}s")
                await asyncio.sleep(to_wait)
                now = time.time()
                sent_timestamps = [t for t in sent_timestamps if now - t < 60]

            delay = max(0.3, base_delay + random.uniform(-JITTER/2, JITTER/2))
            logging.info(f"Sleeping {delay:.2f}s before sending number {current_num}")
            await asyncio.sleep(delay)

            email = make_email(EMAIL_PREFIX, current_num, WIDTH, EMAIL_DOMAIN)

            try:
                await client.send_message(TARGET, email)
                sent_timestamps.append(time.time())
                consecutive_sends += 1
                total_successful += 1
                flood_count = 0
                logging.info(f"Sent -> {email} to {TARGET} at {datetime.now().isoformat()}")
                save_state(current_num)
                await asyncio.sleep(SHORT_BUFFER)
            except errors.FloodWaitError as e:
                flood_count += 1
                base_wait = e.seconds if hasattr(e, 'seconds') else 10
                wait = min(base_wait * (2 ** (flood_count - 1)), BACKOFF_CAP_SECONDS)
                logging.warning(f"FloodWaitError ({base_wait}s). Backing off {wait}s.")
                await asyncio.sleep(wait + 1)
                continue
            except Exception as e:
                logging.error(f"Error sending {email}: {e}. Backing off 5s.")
                await asyncio.sleep(5)
                continue

            current_num += 1

            # STOP TOOL after END_NUM
            if current_num > END_NUM:
                logging.info(f"Reached END_NUM={END_NUM}. Stopping the tool.")
                await client.disconnect()
                logging.info("Client disconnected. Bye.")
                return

            if total_successful % 10 == 0:
                logging.info(f"{total_successful} messages sent — taking TEN_GAP_SECONDS={TEN_GAP_SECONDS}s gap.")
                await asyncio.sleep(TEN_GAP_SECONDS)

            if consecutive_sends >= BURST_BREAKER_CONSEC:
                logging.info(f"Burst breaker triggered after {consecutive_sends} sends. Pausing {BURST_BREAKER_PAUSE}s.")
                await asyncio.sleep(BURST_BREAKER_PAUSE)
                consecutive_sends = 0

async def main():
    logging.info("Starting Telethon client... (may prompt for phone+code first time)")
    await client.start()
    try:
        await send_loop()
    except KeyboardInterrupt:
        logging.info("Interrupted by user (Ctrl+C). Exiting.")
    finally:
        await client.disconnect()
        logging.info("Client disconnected. Bye.")

if __name__ == "__main__":
    asyncio.run(main())
