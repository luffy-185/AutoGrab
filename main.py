import asyncio, json, os, random, logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto


# ==== CONFIG ====
API_ID_ENV = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION = os.getenv("SESSION")
ADMIN_ID = 8183639661

if not API_ID_ENV or not API_HASH or not SESSION:
    raise RuntimeError("Missing API_ID, API_HASH or SESSION environment variables.")

API_ID = int(API_ID_ENV)

BOT_USERNAME = "@Slave_waifu_bot"
DB_FILE = "characters2.json"
RARITY_FILE = "rarities2.json"
GROUP_FILE = "groups2.json"

# ==== STATE ====
spamming = {}
spam_tasks = {}
spam_texts = {}
spam_intervals = {}
database = {}
active_rarities = set()
groups = set()


# ==== LOGGING ====
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ==== SAFE MESSAGE SENDER ====
async def send_safe(func, *args, **kwargs):
    """Wrapper to avoid FloodWaitError or RpcError on send/reply/respond."""
    while True:
        try:
            return await func(*args, **kwargs)
        except FloodWaitError as e:
            wait = getattr(e, "seconds", 10)
            log.warning(f"⚠️ FloodWaitError: sleeping {wait}s")
            await asyncio.sleep(wait)
        except RPCError as e:
            log.error(f"RPC error: {e}")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"Unexpected send error: {e}")
            await asyncio.sleep(3)

# ==== HELPERS ====
def save_json(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                log.warning(f"Corrupted JSON: {path}, resetting.")
                save_json(path, default)
                return default
    save_json(path, default)
    return default

# ==== INITIALIZE FILES ====
for path, default in [(DB_FILE, {}), (RARITY_FILE, []), (GROUP_FILE, [])]:
    if not os.path.exists(path):
        save_json(path, default)

raw_db = load_json(DB_FILE, {})
database = {int(k): v for k, v in raw_db.items()}
log.info(f"📁 Loaded {len(database)} characters from {DB_FILE}")

_raw_rarities = load_json(RARITY_FILE, [])
active_rarities = {int(v) for v in _raw_rarities if str(v).isdigit()}

_raw_groups = load_json(GROUP_FILE, {})
if isinstance(_raw_groups, list):
    groups = {int(g): 1.5 for g in _raw_groups}
else:
    groups = {int(k): float(v) for k, v in _raw_groups.items()}

client = TelegramClient(
    StringSession(SESSION),
    API_ID,
    API_HASH,
    connection_retries=-1,   # keep retrying forever
    retry_delay=5,           # seconds between reconnect attempts
    request_retries=5,       # retry failed API requests
    timeout=10,              # API call timeout
)

# ==== RARITY MAP ====
RARITY_MAP = {
    "common": 1, "medium": 2, "rare": 3, "legendary": 4, "unique": 5,
    "celestial": 6, "neon": 7, "manga": 8, "cross verse": 9, "winter": 10,
    "valentine": 11, "summer": 12, "halloween": 13, "christmas": 14,
    "limited": 15, "special": 16, "divine": 17,
}
RARITY_NAME_MAP = {v: k for k, v in RARITY_MAP.items()}

# ==== FUNCTIONS ====

async def stop_spam(chat_id, reason="Spam stopped"):
    spamming[chat_id] = False
    task = spam_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    spam_tasks[chat_id] = None
    await send_safe(client.send_message, chat_id, f"🛑 {reason}")

async def spam_loop(chat_id):
    while spamming.get(chat_id, False):
        try:
            text = spam_texts.get(chat_id, "")
            interval = spam_intervals.get(chat_id, (1.2, 2.2))
            await send_safe(client.send_message, chat_id, text)
            await asyncio.sleep(random.uniform(*interval))
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"[Spam error in {chat_id}]: {e}")
            await asyncio.sleep(3)


# ==== COMMANDS ====
@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/addg"))
async def add_group(event):
    args = event.raw_text.split()
    if len(args) < 2:
        return await send_safe(event.reply, "❌ Usage: `/addg <chat_id>`")

    gid = int(args[1])
    groups[gid] = 1.5
    save_json(GROUP_FILE, groups)
    await send_safe(event.reply, f"✅ Added group `{gid}` with default delay **1.5s**.")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/rmg"))
async def remove_group(event):
    args = event.raw_text.split()
    if len(args) < 2:
        return await send_safe(event.reply, "❌ Usage: `/rmg <chat_id>`")

    try:
        gid = int(args[1])
        if gid not in groups:
            return await send_safe(event.reply, f"⚠️ Group `{gid}` not found.")
        groups.pop(gid)
        save_json(GROUP_FILE, groups)
        await send_safe(event.reply, f"✅ Removed group `{gid}` from auto-grab list.")
    except Exception as e:
        await send_safe(event.reply, f"❌ Error: {e}")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/delay"))
async def set_delay(event):
    args = event.raw_text.split()
    if len(args) == 2:
        chat_id = event.chat_id
        delay = float(args[1])
    elif len(args) == 3:
        chat_id = int(args[1])
        delay = float(args[2])
    else:
        return await send_safe(event.reply, "❌ Usage: `/delay <seconds>` or `/delay <chat_id> <seconds>`")

    if chat_id not in groups:
        return await send_safe(event.reply, "⚠️ Group not found. Use `/addg <chat_id>` first.")

    groups[chat_id] = delay
    save_json(GROUP_FILE, groups)
    await send_safe(event.reply, f"✅ Delay for `{chat_id}` set to **{delay} sec**.")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/as"))
async def start_spam(event):
    args = event.raw_text.split(maxsplit=3)

    # stop commands
    if len(args) == 2 and args[1].lower() == "off":
        return await stop_spam(event.chat_id)
    if len(args) == 2 and args[1].lower() == "offall":
        for gid in list(spamming.keys()):
            if spamming.get(gid):
                await stop_spam(gid, "Global stop")
        return

    # /as <text> <min> <max>
    if len(args) < 4:
        return await send_safe(event.reply, "❌ Usage: `/as <text> <min> <max>`")

    text = args[1]
    try:
        low, high = float(args[2]), float(args[3])
    except ValueError:
        return await send_safe(event.reply, "❌ Invalid interval numbers.")

    chat_id = event.chat_id

    # stop existing spam first
    if chat_id in spam_tasks and spam_tasks[chat_id]:
        await stop_spam(chat_id, "Restarting spam")

    spamming[chat_id] = True
    spam_texts[chat_id] = text
    spam_intervals[chat_id] = (low, high)
    spam_tasks[chat_id] = asyncio.create_task(spam_loop(chat_id))

    await send_safe(event.reply, f"✅ Spam started ({low}-{high}s).")
    

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/addr"))
async def add_rarity(event):
    args = event.raw_text.split()
    if len(args) < 2 or not args[1].isdigit():
        return await send_safe(event.reply, "❌ Usage: `/addr <number>`")
    rarity_num = int(args[1])
    active_rarities.add(rarity_num)
    save_json(RARITY_FILE, sorted(list(active_rarities)))
    await send_safe(event.reply, f"✅ Added rarity #{rarity_num}.")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/rmr"))
async def remove_rarity(event):
    args = event.raw_text.split()
    if len(args) < 2 or not args[1].isdigit():
        return await send_safe(event.reply, "❌ Usage: `/rmr <number>`")
    rarity_num = int(args[1])
    if rarity_num not in active_rarities:
        return await send_safe(event.reply, f"⚠️ Rarity #{rarity_num} not active.")
    active_rarities.discard(rarity_num)
    save_json(RARITY_FILE, sorted(list(active_rarities)))
    await send_safe(event.reply, f"✅ Removed rarity #{rarity_num}.")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/addc"))
async def add_character(event):
    if not event.is_reply:
        return await send_safe(event.reply, "❌ Reply to an image with `/addc <name> , <rarity>`")
    reply = await event.get_reply_message()
    if not reply or not isinstance(reply.media, MessageMediaPhoto):
        return await send_safe(event.reply, "❌ Must reply to an image!")
    args = event.raw_text.split(maxsplit=1)
    if len(args) < 2 or ',' not in args[1]:
        return await send_safe(event.reply, "❌ Format: `/addc name , rarity`")
    try:
        name, rarity_str = [p.strip() for p in args[1].split(',', 1)]
        if not rarity_str.isdigit():
            return await send_safe(event.reply, "❌ Rarity must be a number.")
        rarity_num = int(rarity_str)
        pid = int(reply.media.photo.id)
        if pid in database:
            old = database[pid]
            return await send_safe(event.reply, f"⚠️ Already exists:\nName: {old[0]}\nRarity: {old[1]}")
        database[pid] = [name.lower(), rarity_num]
        save_json(DB_FILE, database)
        await send_safe(event.reply, f"✅ Added!\nName: {name}\nRarity: {rarity_num}\nID: `{pid}`")
    except Exception as e:
        await send_safe(event.reply, f"❌ Error: {e}")

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/stats$"))
async def stats_handler(event):
    rarity_counts = {}
    for _, data in database.items():
        rarity = data[1]
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
    msg = (
        f"📊 **Stats**\n"
        f"Entries: `{len(database)}`\n"
        f"Active rarities: `{len(active_rarities)}`\n"
        f"Groups: `{len(groups)}`\n"
    )
    await send_safe(event.reply, msg)

@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/r$"))
async def rarity_summary(event):
    rarity_counts = {}
    for _, data in database.items():
        rarity = data[1]
        rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
    if not rarity_counts:
        return await send_safe(event.reply, "📭 No rarity data yet.")
    lines = []
    for num in sorted(rarity_counts):
        name = RARITY_NAME_MAP.get(num, "Unknown")
        lines.append(f"💎 {name} → #{num} → {rarity_counts[num]} entries")
    await send_safe(event.reply, "📜 **Rarity Report**\n" + "\n".join(lines))

@client.on(events.NewMessage(from_users=BOT_USERNAME))
async def bot_image_handler(event):
    chat_id = event.chat_id
    if chat_id not in groups:
        return
    if not event.media or not isinstance(event.media, MessageMediaPhoto):
        return

    caption = (event.message.message or "").lower()
    spawn_patterns = ["ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ", "ᴜꜱᴇ /grab", "ᴀᴘᴘᴇᴀʀᴇᴅ!"]
    if not caption or not any(p in caption for p in spawn_patterns):
        return

    try:
        pid = int(event.media.photo.id)
        entry = database.get(pid)

        if not entry:
            # forward unknowns to a private log channel if you want (replace with your own ID)
            # asyncio.create_task(send_safe(client.forward_messages, -1003220290496, event.message))
            return

        name, rarity_num = entry
        if rarity_num not in active_rarities:
            return

        delay = groups.get(chat_id, 0)
        if delay > 0:
            await asyncio.sleep(delay)

        await send_safe(event.respond, f"/grab {name.lower()}")
    except Exception as e:
        log.error(f"[FastGrab error in {chat_id}]: {e}")

# ==== CONFIG ====
NAME_CHAT_ID = -1001234567890  # Replace with chat ID where others can use /n
NAME_ACCESS_ENABLED = True     # Toggle with /naccess on|off

# ==== /n COMMAND ====
@client.on(events.NewMessage(pattern=r"^/n"))
async def name_lookup(event):
    global NAME_ACCESS_ENABLED

    # Owner always allowed
    is_owner = (event.sender_id == ADMIN_ID)
    # Others allowed only if feature ON and in allowed chat
    if not is_owner:
        if not NAME_ACCESS_ENABLED or event.chat_id != NAME_CHAT_ID:
            return

    # Must reply to an image
    if not event.is_reply:
        return await send_safe(event.reply, "❌ Reply to an image with `/n`")

    reply = await event.get_reply_message()
    if not reply or not isinstance(reply.media, MessageMediaPhoto):
        return await send_safe(event.reply, "❌ That’s not an image!")

    pid = int(reply.media.photo.id)
    entry = database.get(pid)

    if not entry:
        await send_safe(event.reply, "🤔 idk")
    else:
        name, rarity_num = entry
        await send_safe(event.reply, f"{name.title()}")

# ==== /naccess COMMAND ====
@client.on(events.NewMessage(from_users=ADMIN_ID, pattern=r"^/naccess"))
async def toggle_name_access(event):
    global NAME_ACCESS_ENABLED
    args = event.raw_text.split()
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        return await send_safe(event.reply, "❌ Usage: `/naccess on|off`")

    NAME_ACCESS_ENABLED = (args[1].lower() == "on")
    state = "✅ Enabled" if NAME_ACCESS_ENABLED else "🛑 Disabled"
    await send_safe(event.reply, f"{state} global access for `/n` command.")
    
# ==== MAIN (for background worker) ====
async def main():
    await client.start()
    me = await client.get_me()
    log.info(f"✅ Logged in as {me.username or me.first_name}")
    log.info(f"👑 Admin: {ADMIN_ID}")
    log.info(f"💬 Watching groups: {list(groups)}")
    log.info(f"⭐ Active rarities: {list(active_rarities)}")
    log.info(f"📁 Loaded {len(database)} characters.")
    # keeps running until disconnected – perfect for a worker
    async with client:
        await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
