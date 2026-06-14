import os
import asyncio
import uuid
import threading
from datetime import datetime

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
import pymongo

# ================== CONFIG - Render ke "Environment" tab me ye sab set karo ==================
API_ID = int(os.environ.get("API_ID", "0"))        # my.telegram.org se
API_HASH = os.environ.get("API_HASH", "")          # my.telegram.org se
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")        # @BotFather se

OWNER_ID = int(os.environ.get("OWNER_ID", "0"))    # aapka Telegram user ID - @idbot se /id

DB_URI = os.environ.get("DB_URI", "")              # MongoDB Atlas connection string
DB_NAME = os.environ.get("DB_NAME", "filestorebot")

STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL", "0"))  # private storage channel ID

# Space-separated 5 chat IDs: 2 groups + 3 channels, e.g.
# "-1001111111111 -1002222222222 -1003333333333 -1004444444444 -1005555555555"
FSUB_CHATS = [int(x) for x in os.environ.get("FSUB_CHATS", "").split() if x]

AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "600"))  # seconds, 0 = never
# ===============================================================================================

PORT = int(os.environ.get("PORT", 8080))  # Render isko automatically set karta hai

# ---------------- Database ----------------
mongo_client = pymongo.MongoClient(DB_URI)
db = mongo_client[DB_NAME]
users_col = db["users"]
files_col = db["files"]
daily_col = db["daily_stats"]


def today_key():
    return datetime.utcnow().strftime("%Y-%m-%d")


def bump_daily(field):
    daily_col.update_one({"_id": today_key()}, {"$inc": {field: 1}}, upsert=True)


def get_today_stats():
    doc = daily_col.find_one({"_id": today_key()})
    return {
        "new_users": doc.get("new_users", 0) if doc else 0,
        "downloads": doc.get("downloads", 0) if doc else 0,
    }


def add_user(user_id):
    if not users_col.find_one({"_id": user_id}):
        users_col.insert_one(
            {"_id": user_id, "banned": False, "joined_at": datetime.utcnow()}
        )
        bump_daily("new_users")


def is_banned(user_id):
    user = users_col.find_one({"_id": user_id})
    return bool(user and user.get("banned", False))


def ban_user(user_id):
    users_col.update_one({"_id": user_id}, {"$set": {"banned": True}}, upsert=True)


def unban_user(user_id):
    users_col.update_one({"_id": user_id}, {"$set": {"banned": False}}, upsert=True)


def get_all_users():
    return [u["_id"] for u in users_col.find({})]


def total_users():
    return users_col.count_documents({})


def save_file(file_id, message_id, file_name, password=None):
    files_col.insert_one(
        {
            "_id": file_id,
            "message_id": message_id,
            "file_name": file_name,
            "password": password,
            "downloads": 0,
            "created_at": datetime.utcnow(),
        }
    )


def get_file(file_id):
    return files_col.find_one({"_id": file_id})


def get_all_files(limit=20):
    return list(files_col.find({}).sort("created_at", -1).limit(limit))


def total_files():
    return files_col.count_documents({})


def increment_download(file_id):
    files_col.update_one({"_id": file_id}, {"$inc": {"downloads": 1}})
    bump_daily("downloads")


def set_password(file_id, password):
    return files_col.update_one({"_id": file_id}, {"$set": {"password": password}}).modified_count


def remove_password(file_id):
    return files_col.update_one({"_id": file_id}, {"$set": {"password": None}}).modified_count


# ---------------- Bot ----------------
bot = Client(
    "filestorebot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

pending_password_set = {}    # {OWNER_ID: {"message_id": int, "file_name": str}}
pending_password_check = {}  # {user_id: file_id}

ADMIN_COMMANDS = [
    "start", "broadcast", "stats", "ban", "unban", "skip",
    "help", "myfiles", "setpass", "removepass",
]


def get_file_name(message):
    if message.document:
        return message.document.file_name or "document"
    if message.video:
        return message.video.file_name or "video.mp4"
    if message.audio:
        return message.audio.file_name or "audio.mp3"
    if message.photo:
        return "photo.jpg"
    return "file"


async def is_subscribed(client, user_id):
    for chat_id in FSUB_CHATS:
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in ("kicked", "left"):
                return False
        except UserNotParticipant:
            return False
        except Exception:
            return False
    return True


async def get_fsub_buttons(client):
    buttons = []
    for chat_id in FSUB_CHATS:
        try:
            chat = await client.get_chat(chat_id)
            if chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                invite = await client.create_chat_invite_link(chat_id)
                link = invite.invite_link
            buttons.append([InlineKeyboardButton(f"📢 Join {chat.title}", url=link)])
        except Exception:
            continue
    return buttons


# ---------------- /start ----------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    add_user(user_id)

    if is_banned(user_id):
        return await message.reply("🚫 Aap is bot se banned ho.")

    if len(message.command) == 1:
        if user_id == OWNER_ID:
            return await message.reply(
                "👋 Welcome back boss!\n\n"
                "Koi file bhejo, link mil jayega. Saare commands ke liye /help bhejo."
            )
        return await message.reply(
            "👋 Welcome!\n\nYe ek private file store bot hai. "
            "Aapko ek link diya jayega jisse file milegi."
        )

    file_id = message.command[1]

    if FSUB_CHATS and not await is_subscribed(client, user_id):
        buttons = await get_fsub_buttons(client)
        buttons.append([InlineKeyboardButton("🔄 Try Again", callback_data=f"check_{file_id}")])
        return await message.reply(
            "⚠️ File access karne se pehle neeche diye gaye sabhi groups/channels "
            "join karo, fir 'Try Again' dabao:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    await deliver_file(client, message, file_id)


async def deliver_file(client, message, file_id):
    user_id = message.from_user.id
    file_doc = get_file(file_id)
    if not file_doc:
        return await message.reply("❌ Invalid ya expired link.")

    if file_doc.get("password"):
        pending_password_check[user_id] = file_id
        return await message.reply("🔒 Ye file password protected hai. Password bhejo:")

    await send_stored_file(client, message.chat.id, file_doc)


async def send_stored_file(client, chat_id, file_doc):
    increment_download(file_doc["_id"])

    sent = await client.copy_message(
        chat_id=chat_id,
        from_chat_id=STORAGE_CHANNEL,
        message_id=file_doc["message_id"],
        protect_content=True,
    )

    if AUTO_DELETE_TIME > 0:
        notice = await client.send_message(
            chat_id,
            f"⏳ Ye file {AUTO_DELETE_TIME // 60} minute me automatically delete ho jayegi. "
            "Save kar lo.",
        )

        async def delete_later():
            await asyncio.sleep(AUTO_DELETE_TIME)
            try:
                await sent.delete()
                await notice.delete()
            except Exception:
                pass

        asyncio.create_task(delete_later())


# ---------------- "Try Again" button ----------------
@bot.on_callback_query(filters.regex(r"^check_"))
async def recheck_handler(client, callback_query):
    user_id = callback_query.from_user.id
    file_id = callback_query.data.split("_", 1)[1]

    if FSUB_CHATS and not await is_subscribed(client, user_id):
        return await callback_query.answer(
            "Pehle saare groups/channels join karo!", show_alert=True
        )

    await callback_query.message.delete()
    await deliver_file(client, callback_query.message, file_id)


# ---------------- Owner uploads a file ----------------
@bot.on_message(filters.private & filters.user(OWNER_ID) & filters.media)
async def save_file_handler(client, message):
    copied = await message.copy(STORAGE_CHANNEL)

    pending_password_set[OWNER_ID] = {
        "message_id": copied.id,
        "file_name": get_file_name(message),
    }

    await message.reply(
        "✅ File storage channel me save ho gayi.\n\n"
        "🔒 Is file ke liye password set karna hai? Password type karke bhejo, "
        "ya bina password ke link chahiye to /skip bhejo."
    )


@bot.on_message(filters.command("skip") & filters.user(OWNER_ID) & filters.private)
async def skip_password(client, message):
    if OWNER_ID not in pending_password_set:
        return await message.reply("Koi pending file nahi hai.")
    await finalize_file(client, message, password=None)


async def finalize_file(client, message, password):
    data = pending_password_set.pop(OWNER_ID)
    file_id = uuid.uuid4().hex[:10]
    save_file(file_id, data["message_id"], data["file_name"], password)

    me = await client.get_me()
    link = f"https://t.me/{me.username}?start={file_id}"

    text = f"🔗 Yahan aapka link hai:\n{link}"
    if password:
        text += f"\n🔒 Password: `{password}`"
    await message.reply(text)


# ---------------- Admin commands ----------------
@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID) & filters.private)
async def broadcast_handler(client, message):
    if not message.reply_to_message:
        return await message.reply(
            "Broadcast karne ke liye kisi message ko reply karo: `/broadcast`"
        )

    users = get_all_users()
    status = await message.reply(f"📣 Broadcasting to {len(users)} users...")

    sent = failed = 0
    for user_id in users:
        try:
            await message.reply_to_message.copy(user_id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit(f"✅ Broadcast complete.\n✔️ Sent: {sent}\n❌ Failed: {failed}")


@bot.on_message(filters.command("stats") & filters.user(OWNER_ID) & filters.private)
async def stats_handler(client, message):
    today = get_today_stats()
    await message.reply(
        "📊 **Bot Stats**\n\n"
        f"👥 Total users: {total_users()}\n"
        f"📁 Total files: {total_files()}\n\n"
        f"🆕 Naye users aaj: {today['new_users']}\n"
        f"⬇️ Downloads aaj: {today['downloads']}"
    )


@bot.on_message(filters.command(["ban", "unban"]) & filters.user(OWNER_ID) & filters.private)
async def ban_handler(client, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/ban user_id` ya `/unban user_id`")

    target = int(message.command[1])
    if message.command[0] == "ban":
        ban_user(target)
        await message.reply(f"🚫 User `{target}` banned.")
    else:
        unban_user(target)
        await message.reply(f"✅ User `{target}` unbanned.")


@bot.on_message(filters.command("help") & filters.private)
async def help_handler(client, message):
    if message.from_user.id == OWNER_ID:
        await message.reply(
            "🛠 **Owner Commands**\n\n"
            "📤 Koi bhi file/photo/video bhejo - bot save karke link dega\n"
            "🔗 `/myfiles` - apne saare files, links, password status aur downloads dekho\n"
            "🔒 `/setpass file_id password` - file pe password set/change karo\n"
            "🔓 `/removepass file_id` - file se password hata do\n"
            "📣 `/broadcast` - kisi message ko reply karke sabhi users ko bhejo\n"
            "📊 `/stats` - total + aaj ke users aur downloads\n"
            "🚫 `/ban user_id` - kisi user ko block karo\n"
            "✅ `/unban user_id` - block hata do"
        )
    else:
        await message.reply(
            "👋 Ye ek private file store bot hai.\n\n"
            "Aapko file lene ke liye ek link diya jayega - usi link ko open karo."
        )


@bot.on_message(filters.command("myfiles") & filters.user(OWNER_ID) & filters.private)
async def myfiles_handler(client, message):
    files = get_all_files(limit=20)
    if not files:
        return await message.reply("Abhi tak koi file save nahi hui.")

    me = await client.get_me()
    lines = ["🗂 **Aapki last 20 files:**\n"]
    for f in files:
        link = f"https://t.me/{me.username}?start={f['_id']}"
        lock = "🔒 Password set" if f.get("password") else "🔓 No password"
        downloads = f.get("downloads", 0)
        lines.append(
            f"📄 {f.get('file_name', 'file')}\n"
            f"🆔 `{f['_id']}`\n"
            f"{link}\n"
            f"{lock} | ⬇️ {downloads} downloads\n"
        )

    await message.reply("\n".join(lines))


@bot.on_message(filters.command("setpass") & filters.user(OWNER_ID) & filters.private)
async def setpass_handler(client, message):
    if len(message.command) < 3:
        return await message.reply("Usage: `/setpass file_id new_password`")

    file_id = message.command[1]
    password = message.text.split(maxsplit=2)[2]

    if not get_file(file_id):
        return await message.reply("❌ Ye file_id nahi mila. `/myfiles` se check karo.")

    set_password(file_id, password)
    await message.reply(f"🔒 Password set ho gaya is file ke liye:\n`{password}`")


@bot.on_message(filters.command("removepass") & filters.user(OWNER_ID) & filters.private)
async def removepass_handler(client, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/removepass file_id`")

    file_id = message.command[1]
    if not get_file(file_id):
        return await message.reply("❌ Ye file_id nahi mila. `/myfiles` se check karo.")

    remove_password(file_id)
    await message.reply("🔓 Password hata diya gaya, file ab kisi ke liye bhi open hai.")


# ---------------- Password handling (plain text messages) ----------------
@bot.on_message(filters.private & filters.text & ~filters.command(ADMIN_COMMANDS))
async def text_handler(client, message):
    user_id = message.from_user.id

    if user_id == OWNER_ID and OWNER_ID in pending_password_set:
        await finalize_file(client, message, password=message.text.strip())
        return

    if user_id in pending_password_check:
        file_id = pending_password_check[user_id]
        file_doc = get_file(file_id)
        if file_doc and message.text.strip() == file_doc.get("password"):
            del pending_password_check[user_id]
            await send_stored_file(client, message.chat.id, file_doc)
        else:
            await message.reply("❌ Galat password, dobara try karo:")
        return


# ---------------- Flask server (Render ke liye) ----------------
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def run_flask():
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run()
