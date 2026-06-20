"""
FileStoreBot - Production Ready
Multi-user file store with monetization, dashboards, blue tick system
"""

import os
import asyncio
import uuid
import threading
import logging
from datetime import datetime

from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from pyrogram.errors import (
    UserNotParticipant, ChatAdminRequired,
    ChannelPrivate, PeerIdInvalid, FloodWait,
    ChatWriteForbidden, UserIsBlocked, InputUserDeactivated
)
from tinydb import TinyDB, Query

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
API_ID           = int(os.environ.get("API_ID", "0"))
API_HASH         = os.environ.get("API_HASH", "")
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
OWNER_ID         = int(os.environ.get("OWNER_ID", "0"))
STORAGE_CHANNEL  = int(os.environ.get("STORAGE_CHANNEL", "0"))  # Owner ka storage — sabke files yahan jayenge
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "600"))
PORT             = int(os.environ.get("PORT", 8080))

# ==================== DATABASE ====================
db        = TinyDB("filestore.json")
users_tbl = db.table("users")
files_tbl = db.table("files")
stats_tbl = db.table("stats")
promo_tbl = db.table("promo")

U = Query(); F = Query(); S = Query(); P = Query()


def today():
    return datetime.utcnow().strftime("%Y-%m-%d")


def bump(field):
    key = today()
    doc = stats_tbl.get(S._id == key)
    if doc:
        stats_tbl.update({field: doc.get(field, 0) + 1}, S._id == key)
    else:
        stats_tbl.insert({"_id": key, field: 1})


def get_stats_today():
    doc = stats_tbl.get(S._id == today())
    return {
        "new_users": doc.get("new_users", 0) if doc else 0,
        "downloads": doc.get("downloads", 0) if doc else 0,
        "links":     doc.get("links", 0)     if doc else 0,
    }


def get_stats_total():
    all_docs = stats_tbl.all()
    return {
        "new_users": sum(d.get("new_users", 0) for d in all_docs),
        "downloads": sum(d.get("downloads", 0) for d in all_docs),
        "links":     sum(d.get("links", 0)     for d in all_docs),
    }


# -------- user ops --------
def upsert_user(user_id, username=None, name=None):
    existing = users_tbl.get(U._id == user_id)
    if not existing:
        users_tbl.insert({
            "_id":        user_id,
            "username":   username,
            "name":       name,
            "banned":     False,
            "blue_tick":  False,
            "channels":   [],
            "storage_ch": None,
            "joined_at":  datetime.utcnow().isoformat(),
        })
        bump("new_users")
        return True
    else:
        users_tbl.update({"username": username, "name": name}, U._id == user_id)
        return False


def get_user(user_id):
    return users_tbl.get(U._id == user_id)


def all_users():
    return users_tbl.all()


def is_banned(user_id):
    u = get_user(user_id)
    return bool(u and u.get("banned"))


def ban_user(uid):
    if get_user(uid):
        users_tbl.update({"banned": True}, U._id == uid)
    else:
        users_tbl.insert({"_id": uid, "banned": True, "blue_tick": False,
                          "channels": [], "storage_ch": None,
                          "joined_at": datetime.utcnow().isoformat()})


def unban_user(uid):
    users_tbl.update({"banned": False}, U._id == uid)


def set_blue_tick(uid, val: bool):
    if get_user(uid):
        users_tbl.update({"blue_tick": val}, U._id == uid)
    else:
        users_tbl.insert({"_id": uid, "banned": False, "blue_tick": val,
                          "channels": [], "storage_ch": None,
                          "joined_at": datetime.utcnow().isoformat()})


def has_blue_tick(uid):
    u = get_user(uid)
    return bool(u and u.get("blue_tick"))


def set_storage_channel(uid, ch_id):
    users_tbl.update({"storage_ch": ch_id}, U._id == uid)


def add_fsub_channel(uid, ch_id):
    u = get_user(uid)
    if not u:
        return False
    chs = u.get("channels", [])
    if ch_id not in chs:
        chs.append(ch_id)
        users_tbl.update({"channels": chs}, U._id == uid)
        return True
    return False


def remove_fsub_channel(uid, ch_id):
    u = get_user(uid)
    if not u:
        return False
    chs = [c for c in u.get("channels", []) if c != ch_id]
    users_tbl.update({"channels": chs}, U._id == uid)
    return True


# -------- file ops --------
def save_file(file_id, owner_id, message_id, storage_ch, file_name, password=None):
    files_tbl.insert({
        "_id":        file_id,
        "owner_id":   owner_id,
        "message_id": message_id,
        "storage_ch": storage_ch,
        "file_name":  file_name,
        "password":   password,
        "downloads":  0,
        "created_at": datetime.utcnow().isoformat(),
    })
    bump("links")


def get_file(file_id):
    return files_tbl.get(F._id == file_id)


def get_user_files(owner_id, limit=20):
    fs = [f for f in files_tbl.all() if f.get("owner_id") == owner_id]
    fs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return fs[:limit]


def total_files():
    return len(files_tbl.all())


def increment_dl(file_id):
    doc = files_tbl.get(F._id == file_id)
    if doc:
        files_tbl.update({"downloads": doc.get("downloads", 0) + 1}, F._id == file_id)
    bump("downloads")


def set_file_password(file_id, pwd):
    files_tbl.update({"password": pwd}, F._id == file_id)


def remove_file_password(file_id):
    files_tbl.update({"password": None}, F._id == file_id)


def delete_file_record(file_id):
    files_tbl.remove(F._id == file_id)


# -------- promo ops --------
def get_promo():
    return promo_tbl.get(P._id == "promo")


def set_promo(text, photo_id=None, button_text=None, button_url=None):
    doc = {
        "_id":         "promo",
        "text":        text,
        "photo_id":    photo_id,
        "button_text": button_text,
        "button_url":  button_url,
    }
    if get_promo():
        promo_tbl.update(doc, P._id == "promo")
    else:
        promo_tbl.insert(doc)


# ==================== BOT ====================
bot = Client(
    "filestorebot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

pending_upload      = {}
pending_upload_mode = {}   # uid -> True (waiting for actual file after /uploadfile)
pending_pw_set      = {}
pending_pw_check    = {}
pending_storage_set = {}
pending_fsub_add    = {}
pending_promo_set   = {}
promo_draft         = {}

SKIP_CMDS = [
    "start", "help", "myfiles", "addchannel", "removechannel",
    "setstorage", "stats", "broadcast", "ban", "unban",
    "bluetick", "removeblue", "setpromo", "sendpromo", "skip",
    "dashboard", "cancel", "deletelink", "setpass", "removepass",
    "uploadfile",
]


def get_file_name(msg):
    if msg.document: return msg.document.file_name or "document"
    if msg.video:    return msg.video.file_name or "video.mp4"
    if msg.audio:    return msg.audio.file_name or "audio.mp3"
    if msg.photo:    return "photo.jpg"
    return "file"


# ==================== FIXED BOTTOM KEYBOARD ====================
def get_reply_keyboard(is_owner: bool):
    """Niche fixed keyboard — har screen pe chipka rahega"""
    if is_owner:
        rows = [
            [KeyboardButton("📊 Dashboard"), KeyboardButton("📁 My Files")],
            [KeyboardButton("📢 My Channels"), KeyboardButton("👥 All Users")],
            [KeyboardButton("📣 Send Promo"), KeyboardButton("✏️ Set Promo")],
            [KeyboardButton("❓ Help"), KeyboardButton("ℹ️ About Bot")],
        ]
    else:
        rows = [
            [KeyboardButton("📊 Dashboard"), KeyboardButton("📁 My Files")],
            [KeyboardButton("📢 My Channels"), KeyboardButton("📊 My Stats")],
            [KeyboardButton("➕ Add Channel"), KeyboardButton("❌ Remove Channel")],
            [KeyboardButton("❓ Help"), KeyboardButton("ℹ️ About Bot")],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)



async def is_subscribed(client, user_id, channels):
    for ch in channels:
        try:
            m = await client.get_chat_member(ch, user_id)
            if m.status.value in ("banned", "left", "restricted"):
                return False, ch
        except UserNotParticipant:
            return False, ch
        except Exception as e:
            logger.warning(f"FSUB check error {ch}: {e}")
    return True, None


async def get_join_buttons(client, channels, file_id):
    buttons = []
    for ch in channels:
        try:
            chat = await client.get_chat(ch)
            if chat.username:
                link = f"https://t.me/{chat.username}"
            else:
                inv  = await client.create_chat_invite_link(ch)
                link = inv.invite_link
            buttons.append([InlineKeyboardButton(f"📢 Join {chat.title}", url=link)])
        except Exception as e:
            logger.error(f"Join button error {ch}: {e}")
    buttons.append([InlineKeyboardButton("✅ I Joined — Verify", callback_data=f"verify_{file_id}")])
    return buttons


# ==================== PROMO SENDER ====================
async def send_promo(client, chat_id):
    promo = get_promo()
    if not promo or not promo.get("text"):
        return False
    try:
        markup = None
        if promo.get("button_text") and promo.get("button_url"):
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(promo["button_text"], url=promo["button_url"])
            ]])
        if promo.get("photo_id"):
            await client.send_photo(
                chat_id, promo["photo_id"],
                caption=promo["text"],
                reply_markup=markup
            )
        else:
            await client.send_message(chat_id, promo["text"], reply_markup=markup)
        return True
    except Exception as e:
        logger.warning(f"Promo send failed {chat_id}: {e}")
        return False


# ==================== /start ====================
@bot.on_message(filters.command("start"))
async def start_handler(client, message):
    if message.chat.type.value != "private":
        me = await client.get_me()
        return await message.reply(
            f"👋 Private me open karo: [Click Here](https://t.me/{me.username}?start=hello)",
            disable_web_page_preview=True
        )

    user   = message.from_user
    is_new = upsert_user(user.id, user.username, user.first_name)

    if is_banned(user.id):
        return await message.reply("🚫 Aap is bot se banned hain.")

    if is_new:
        asyncio.create_task(send_promo(client, user.id))

    if len(message.command) == 1 or message.command[1] == "hello":
        return await show_dashboard(client, message, user.id)

    await handle_file_request(client, message, user.id, message.command[1])


# ==================== DASHBOARD ====================
async def show_dashboard(client, message, user_id):
    u        = get_user(user_id)
    is_owner = (user_id == OWNER_ID)
    blue     = " 🔵" if u and u.get("blue_tick") else ""
    name     = message.from_user.first_name

    if is_owner:
        t   = get_stats_today()
        tot = get_stats_total()
        blue_tick_users = sum(1 for x in all_users() if x.get("blue_tick"))
        banned_users    = sum(1 for x in all_users() if x.get("banned"))
        text = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 **ADMIN DASHBOARD**{blue}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 **AAJ KI STATS:**\n"
            f"┣ 👤 Naye Users: **{t['new_users']}**\n"
            f"┣ ⬇️ Downloads: **{t['downloads']}**\n"
            f"┗ 🔗 Links Bane: **{t['links']}**\n\n"
            "📈 **TOTAL STATS:**\n"
            f"┣ 👥 Total Users: **{len(all_users())}**\n"
            f"┣ 📁 Total Files: **{total_files()}**\n"
            f"┣ ⬇️ Total Downloads: **{tot['downloads']}**\n"
            f"┣ 🔗 Total Links: **{tot['links']}**\n"
            f"┣ 🔵 Blue Tick Users: **{blue_tick_users}**\n"
            f"┗ 🚫 Banned Users: **{banned_users}**\n\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        buttons = [
            [InlineKeyboardButton("📁 My Files", callback_data="my_files"),
             InlineKeyboardButton("📢 My Channels", callback_data="my_channels")],
            [InlineKeyboardButton("👥 All Users", callback_data="all_users_list"),
             InlineKeyboardButton("📊 Refresh Stats", callback_data="full_stats")],
            [InlineKeyboardButton("📣 Send Promo", callback_data="send_promo_now"),
             InlineKeyboardButton("✏️ Set Promo", callback_data="set_promo_msg")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast_guide"),
             InlineKeyboardButton("📤 Upload Guide", callback_data="upload_guide")],
            [InlineKeyboardButton("❓ Help", callback_data="help_cb"),
             InlineKeyboardButton("ℹ️ About Bot", callback_data="about_cb")],
        ]
    else:
        uch        = u.get("channels", []) if u else []
        is_blue    = u and u.get("blue_tick")
        user_files = get_user_files(user_id)
        total_dl   = sum(f.get("downloads", 0) for f in user_files)
        # Most downloaded file
        top_file   = max(user_files, key=lambda f: f.get("downloads", 0)) if user_files else None

        blue_status = "✅ Active — Promo nahi aayega" if is_blue else "❌ Not Active"
        blue_badge  = "🔵 " if is_blue else ""

        text = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **{blue_badge}MY DASHBOARD**\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 Hello, **{name}**!\n\n"
            "📊 **MY STATS:**\n"
            f"┣ 📁 Total Files: **{len(user_files)}**\n"
            f"┣ ⬇️ Total Downloads: **{total_dl}**\n"
            f"┣ 🔗 Active Links: **{len(user_files)}**\n"
            f"┗ 📢 FSUB Channels: **{len(uch)}**\n\n"
        )
        if top_file:
            text += (
                "🏆 **TOP FILE:**\n"
                f"┣ 📄 {top_file.get('file_name', 'file')}\n"
                f"┗ ⬇️ {top_file.get('downloads', 0)} downloads\n\n"
            )
        text += (
            f"🔵 **Blue Tick:** {blue_status}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        buttons = [
            [InlineKeyboardButton("📁 My Files", callback_data="my_files"),
             InlineKeyboardButton("📢 My Channels", callback_data="my_channels")],
            [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel_guide"),
             InlineKeyboardButton("❌ Remove Channel", callback_data="remove_channel_cb")],
            [InlineKeyboardButton("📤 Upload File", callback_data="upload_guide"),
             InlineKeyboardButton("📊 My Stats", callback_data="user_stats")],
            [InlineKeyboardButton("❓ Help", callback_data="help_cb"),
             InlineKeyboardButton("ℹ️ About Bot", callback_data="about_cb")],
        ]
        if not is_blue:
            buttons.append([InlineKeyboardButton(
                "🔵 Buy Blue Tick — No More Promos!", callback_data="buy_blue_tick"
            )])

    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons))
    # Niche fixed keyboard bhi set karo (ek hi message dono types nahi le sakta)
    await message.reply(
        "👇 Quick Menu neeche bhi available hai",
        reply_markup=get_reply_keyboard(is_owner)
    )


# ==================== FILE REQUEST ====================
async def handle_file_request(client, message, user_id, file_id):
    file_doc = get_file(file_id)
    if not file_doc:
        return await message.reply("❌ Invalid ya expired link.")

    owner    = get_user(file_doc["owner_id"])
    fsub_chs = owner.get("channels", []) if owner else []

    if fsub_chs:
        ok, _ = await is_subscribed(client, user_id, fsub_chs)
        if not ok:
            btns = await get_join_buttons(client, fsub_chs, file_id)
            return await message.reply(
                "⚠️ **File ke liye pehle join karo:**",
                reply_markup=InlineKeyboardMarkup(btns)
            )

    if file_doc.get("password"):
        pending_pw_check[user_id] = file_id
        return await message.reply("🔒 Password protected file hai. Password bhejo:")

    await deliver_file(client, message.chat.id, file_doc)


async def deliver_file(client, chat_id, file_doc):
    try:
        increment_dl(file_doc["_id"])
        sent = await client.copy_message(
            chat_id         = chat_id,
            from_chat_id    = file_doc["storage_ch"],
            message_id      = file_doc["message_id"],
            protect_content = True,
        )
        if AUTO_DELETE_TIME > 0:
            notice = await client.send_message(
                chat_id,
                f"⏳ File **{AUTO_DELETE_TIME // 60} min** me delete hogi. Save kar lo!"
            )
            async def _del():
                await asyncio.sleep(AUTO_DELETE_TIME)
                try:
                    await sent.delete()
                    await notice.delete()
                except: pass
            asyncio.create_task(_del())
    except Exception as e:
        await client.send_message(chat_id, f"❌ File deliver nahi hui.\nError: `{e}`")
        logger.error(f"Deliver error: {e}")


# ==================== VERIFY CALLBACK ====================
@bot.on_callback_query(filters.regex(r"^verify_"))
async def verify_handler(client, cq):
    user_id  = cq.from_user.id
    file_id  = cq.data.split("_", 1)[1]
    file_doc = get_file(file_id)
    if not file_doc:
        return await cq.answer("❌ Invalid link.", show_alert=True)

    owner    = get_user(file_doc["owner_id"])
    fsub_chs = owner.get("channels", []) if owner else []

    ok, _ = await is_subscribed(client, user_id, fsub_chs)
    if not ok:
        return await cq.answer("❌ Saare channels join karo pehle!", show_alert=True)

    await cq.message.delete()

    if file_doc.get("password"):
        pending_pw_check[user_id] = file_id
        await client.send_message(user_id, "🔒 Password bhejo:")
        return

    await deliver_file(client, user_id, file_doc)
    await cq.answer("✅ File aa rahi hai!", show_alert=False)


# ==================== FILE UPLOAD ====================
# Owner ka STORAGE_CHANNEL sab users ke files ke liye use hoga

@bot.on_message(filters.command("uploadfile") & filters.private)
async def uploadfile_cmd(client, message):
    uid = message.from_user.id
    if is_banned(uid): return
    upsert_user(uid, message.from_user.username, message.from_user.first_name)

    if STORAGE_CHANNEL == 0:
        return await message.reply(
            "❌ **Storage channel setup nahi hai abhi.**\n"
            "Admin se contact karo."
        )

    pending_upload_mode[uid] = True
    await message.reply(
        "📤 **File Upload Mode ON**\n\n"
        "Ab jo bhi file/photo/video/document bhejoge, woh save ho jayegi.\n\n"
        "/cancel se cancel karo."
    )


@bot.on_message(filters.private & filters.media)
async def file_upload_handler(client, message):
    user_id = message.from_user.id
    if is_banned(user_id): return

    upsert_user(user_id, message.from_user.username, message.from_user.first_name)

    # Sirf tab file store hogi jab user ne /uploadfile se mode ON kiya ho
    if user_id not in pending_upload_mode:
        return  # Normal media — kisi aur flow (jaise promo photo) ke liye chhod do

    del pending_upload_mode[user_id]

    if STORAGE_CHANNEL == 0:
        return await message.reply(
            "❌ **Storage channel setup nahi hai abhi.**\n"
            "Admin se contact karo."
        )

    try:
        status = await message.reply("⏳ Uploading...")
        copied = await message.copy(STORAGE_CHANNEL)
        pending_upload[user_id] = {
            "message_id": copied.id,
            "file_name":  get_file_name(message),
            "storage_ch": STORAGE_CHANNEL,
        }
        await status.edit(
            "✅ **File saved!**\n\n"
            "🔒 Password set karna hai? Abhi type karke bhejo.\n"
            "➡️ Nahi chahiye? /skip bhejo."
        )
    except ChatAdminRequired:
        await message.reply("❌ Storage channel me bot admin nahi hai. Admin se baat karo.")
    except Exception as e:
        await message.reply(f"❌ Upload failed!\nError: `{e}`")
        logger.error(f"Upload error uid={user_id}: {e}")


@bot.on_message(filters.command("skip") & filters.private)
async def skip_handler(client, message):
    uid = message.from_user.id

    # skip in promo setup
    if uid == OWNER_ID and uid in pending_promo_set:
        step = pending_promo_set[uid]
        if step == "photo":
            pending_promo_set[uid] = "button"
            return await message.reply(
                "Skipped photo.\n\n"
                "**Step 3/3:** Button bhejo:\n"
                "`Button Text | https://link.com`\n"
                "Ya /skip"
            )
        elif step == "button":
            draft = promo_draft.get(uid, {})
            set_promo(text=draft.get("text", ""), photo_id=draft.get("photo_id"),
                      button_text=None, button_url=None)
            del pending_promo_set[uid]
            promo_draft.pop(uid, None)
            return await message.reply("✅ Promo saved (no button)! /sendpromo se bhejo.")

    # skip password for file upload
    if uid in pending_upload:
        await finalize_upload(client, message, uid, password=None)
    else:
        await message.reply("⚠️ Koi pending action nahi.")


async def finalize_upload(client, message, uid, password):
    data    = pending_upload.pop(uid)
    file_id = uuid.uuid4().hex[:10]
    save_file(file_id, uid, data["message_id"], data["storage_ch"], data["file_name"], password)

    me   = await client.get_me()
    link = f"https://t.me/{me.username}?start={file_id}"

    if password:
        extra = f"🔒 Password: `{password}`"
    else:
        extra = "🔓 No Password"

    await message.reply(
        f"✅ **Link Ready!**\n\n"
        f"🔗 {link}\n\n"
        f"📄 File: `{data['file_name']}`\n"
        f"🆔 ID: `{file_id}`\n"
        f"{extra}"
    )


# ==================== SETSTORAGE ====================
@bot.on_message(filters.command("setstorage") & filters.user(OWNER_ID) & filters.private)
async def setstorage_cmd(client, message):
    pending_storage_set[OWNER_ID] = True
    await message.reply(
        "📤 **Storage Channel Setup (Admin)**\n\n"
        "1. Private channel banao\n"
        "2. Bot ko admin banao (Post Messages)\n"
        "3. Us channel se koi **message forward karo** yahan\n\n"
        "/cancel se cancel karo"
    )


# ==================== ADD/REMOVE CHANNEL ====================
@bot.on_message(filters.command("addchannel") & filters.private)
async def addchannel_cmd(client, message):
    uid = message.from_user.id
    if is_banned(uid): return
    pending_fsub_add[uid] = True
    await message.reply(
        "📢 **Add FSUB Channel/Group**\n\n"
        "Apne channel/group se koi **message forward karo** yahan.\n"
        "(Bot us channel/group ka member hona chahiye)\n\n"
        "/cancel se cancel karo"
    )


@bot.on_message(filters.command("removechannel") & filters.private)
async def removechannel_cmd(client, message):
    uid = message.from_user.id
    u   = get_user(uid)
    if not u or not u.get("channels"):
        return await message.reply("❌ Koi FSUB channel set nahi hai.")

    buttons = []
    lines   = ["📢 **Your FSUB Channels:**\n"]
    for ch_id in u["channels"]:
        try:
            chat = await client.get_chat(ch_id)
            name = chat.title
        except:
            name = str(ch_id)
        lines.append(f"• {name} (`{ch_id}`)")
        buttons.append([InlineKeyboardButton(f"❌ Remove {name}", callback_data=f"rmch_{ch_id}")])

    await message.reply("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


@bot.on_callback_query(filters.regex(r"^rmch_"))
async def rmch_cb(client, cq):
    uid   = cq.from_user.id
    ch_id = int(cq.data.split("_", 1)[1])
    remove_fsub_channel(uid, ch_id)
    await cq.answer("✅ Channel removed!", show_alert=True)
    await cq.message.delete()


# ==================== MY FILES ====================
@bot.on_message(filters.command("myfiles") & filters.private)
async def myfiles_cmd(client, message):
    uid   = message.from_user.id
    files = get_user_files(uid, limit=20)
    if not files:
        return await message.reply("❌ Koi file nahi hai. Pehle upload karo.")

    me    = await client.get_me()
    lines = [f"📁 **Your Files ({len(files)}):**\n"]
    for f in files:
        link = f"https://t.me/{me.username}?start={f['_id']}"
        lock = "🔒" if f.get("password") else "🔓"
        lines.append(
            f"{lock} `{f.get('file_name','file')}`\n"
            f"🔗 {link}\n"
            f"🆔 `{f['_id']}` | ⬇️ {f.get('downloads',0)}\n"
        )

    full = "\n".join(lines)
    for i in range(0, len(full), 4096):
        await message.reply(full[i:i+4096])


@bot.on_message(filters.command("deletelink") & filters.private)
async def deletelink_cmd(client, message):
    uid = message.from_user.id
    if len(message.command) < 2:
        return await message.reply("Usage: `/deletelink file_id`")
    fid  = message.command[1]
    fdoc = get_file(fid)
    if not fdoc:
        return await message.reply("❌ File ID nahi mila.")
    if fdoc["owner_id"] != uid and uid != OWNER_ID:
        return await message.reply("❌ Ye file aapki nahi hai.")
    delete_file_record(fid)
    await message.reply("✅ Link delete ho gaya.")


# ==================== PASSWORD COMMANDS ====================
@bot.on_message(filters.command("setpass") & filters.private)
async def setpass_cmd(client, message):
    uid = message.from_user.id
    if len(message.command) < 3:
        return await message.reply("Usage: `/setpass file_id password`")
    fid = message.command[1]
    pwd = message.text.split(maxsplit=2)[2]
    f   = get_file(fid)
    if not f:
        return await message.reply("❌ File ID nahi mila.")
    if f["owner_id"] != uid and uid != OWNER_ID:
        return await message.reply("❌ Ye file aapki nahi.")
    set_file_password(fid, pwd)
    await message.reply(f"🔒 Password set: `{pwd}`")


@bot.on_message(filters.command("removepass") & filters.private)
async def removepass_cmd(client, message):
    uid = message.from_user.id
    if len(message.command) < 2:
        return await message.reply("Usage: `/removepass file_id`")
    fid = message.command[1]
    f   = get_file(fid)
    if not f:
        return await message.reply("❌ File ID nahi mila.")
    if f["owner_id"] != uid and uid != OWNER_ID:
        return await message.reply("❌ Ye file aapki nahi.")
    remove_file_password(fid)
    await message.reply("🔓 Password remove ho gaya.")


# ==================== ADMIN: STATS ====================
@bot.on_message(filters.command("stats") & filters.user(OWNER_ID) & filters.private)
async def stats_cmd(client, message):
    t   = get_stats_today()
    tot = get_stats_total()
    await message.reply(
        "📊 **Bot Statistics**\n\n"
        "**Aaj:**\n"
        f"  👤 Naye Users: **{t['new_users']}**\n"
        f"  ⬇️ Downloads: **{t['downloads']}**\n"
        f"  🔗 Links Bane: **{t['links']}**\n\n"
        "**All Time:**\n"
        f"  👥 Total Users: **{len(all_users())}**\n"
        f"  📁 Total Files: **{total_files()}**\n"
        f"  ⬇️ Total Downloads: **{tot['downloads']}**\n"
        f"  🔗 Total Links: **{tot['links']}**"
    )


# ==================== ADMIN: BAN/UNBAN ====================
@bot.on_message(filters.command(["ban", "unban"]) & filters.user(OWNER_ID) & filters.private)
async def ban_cmd(client, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/ban user_id` ya `/unban user_id`")
    target = int(message.command[1])
    if message.command[0] == "ban":
        ban_user(target)
        await message.reply(f"🚫 User `{target}` banned.")
    else:
        unban_user(target)
        await message.reply(f"✅ User `{target}` unbanned.")


# ==================== ADMIN: BLUE TICK ====================
@bot.on_message(filters.command("bluetick") & filters.user(OWNER_ID) & filters.private)
async def bluetick_cmd(client, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/bluetick user_id`")
    uid = int(message.command[1])
    set_blue_tick(uid, True)
    await message.reply(f"🔵 Blue tick diya: `{uid}`\nUnke channels me promo nahi jayega.")


@bot.on_message(filters.command("removeblue") & filters.user(OWNER_ID) & filters.private)
async def removeblue_cmd(client, message):
    if len(message.command) != 2:
        return await message.reply("Usage: `/removeblue user_id`")
    uid = int(message.command[1])
    set_blue_tick(uid, False)
    await message.reply(f"❌ Blue tick remove kiya: `{uid}`")


# ==================== ADMIN: SET PROMO ====================
@bot.on_message(filters.command("setpromo") & filters.user(OWNER_ID) & filters.private)
async def setpromo_cmd(client, message):
    pending_promo_set[OWNER_ID] = "text"
    promo_draft[OWNER_ID]       = {}
    await message.reply(
        "✏️ **Promo Setup — Step 1/3**\n\n"
        "Promo ka **text** bhejo (Markdown ok).\n"
        "/cancel se band karo."
    )


# ==================== ADMIN: SEND PROMO ====================
@bot.on_message(filters.command("sendpromo") & filters.user(OWNER_ID) & filters.private)
async def sendpromo_cmd(client, message):
    promo = get_promo()
    if not promo or not promo.get("text"):
        return await message.reply("❌ Pehle promo set karo: /setpromo")

    users_list   = all_users()
    all_channels = set()
    for u in users_list:
        if u.get("blue_tick"):
            continue
        for ch in u.get("channels", []):
            all_channels.add(ch)

    status = await message.reply(
        f"📣 Promo bhej raha hoon...\n"
        f"👥 Users: {len(users_list)}\n"
        f"📢 Channels: {len(all_channels)}\n"
        f"(Blue tick wale skip honge)"
    )

    sent_u = failed_u = sent_c = failed_c = 0

    for u in users_list:
        try:
            ok = await send_promo(client, u["_id"])
            if ok: sent_u += 1
            else:  failed_u += 1
        except Exception:
            failed_u += 1
        await asyncio.sleep(0.05)

    for ch in all_channels:
        try:
            ok = await send_promo(client, ch)
            if ok: sent_c += 1
            else:  failed_c += 1
        except Exception:
            failed_c += 1
        await asyncio.sleep(0.1)

    await status.edit(
        f"✅ **Promo Sent!**\n\n"
        f"👤 Users: ✔️ {sent_u} | ❌ {failed_u}\n"
        f"📢 Channels: ✔️ {sent_c} | ❌ {failed_c}"
    )


# ==================== ADMIN: BROADCAST ====================
@bot.on_message(filters.command("broadcast") & filters.user(OWNER_ID) & filters.private)
async def broadcast_cmd(client, message):
    if not message.reply_to_message:
        return await message.reply("Kisi message ko reply karke /broadcast bhejo.")

    users_list = all_users()
    status     = await message.reply(f"📣 Broadcasting to {len(users_list)} users...")
    sent = failed = 0

    for u in users_list:
        try:
            await message.reply_to_message.copy(u["_id"])
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit(f"✅ Done!\n✔️ {sent} sent | ❌ {failed} failed")


# ==================== REPLY-KEYBOARD HELPER FUNCTIONS ====================
async def send_my_files_text(client, message, uid):
    files = get_user_files(uid, limit=15)
    if not files:
        return await message.reply(
            "📁 **Koi file nahi hai abhi.**\n\n"
            "Seedha koi file/photo/video bhejo — link mil jayega!"
        )
    me    = await client.get_me()
    lines = [f"📁 **MY FILES ({len(files)}):**\n━━━━━━━━━━━━━━━━━━━━\n"]
    buttons = []
    for f in files:
        link  = f"https://t.me/{me.username}?start={f['_id']}"
        lock  = "🔒" if f.get("password") else "🔓"
        fname = f.get('file_name', 'file')[:20]
        dl    = f.get('downloads', 0)
        lines.append(
            f"{lock} **{fname}**\n"
            f"┣ ⬇️ Downloads: {dl}\n"
            f"┣ 🆔 `{f['_id']}`\n"
            f"┗ 🔗 {link}\n"
        )
        pw_btn = (
            InlineKeyboardButton(f"🔓 Remove Pass", callback_data=f"rmpw_{f['_id']}")
            if f.get("password") else
            InlineKeyboardButton(f"🔒 Set Pass",    callback_data=f"setpw_{f['_id']}")
        )
        buttons.append([pw_btn, InlineKeyboardButton("🗑 Delete", callback_data=f"delfile_{f['_id']}")])

    full = "\n".join(lines)
    if len(full) > 4000:
        for i in range(0, len(full), 4000):
            await message.reply(full[i:i+4000])
        await message.reply("⚙️ **File Actions:**", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply(full, reply_markup=InlineKeyboardMarkup(buttons))


async def send_my_channels_text(client, message, uid):
    u   = get_user(uid)
    chs = u.get("channels", []) if u else []

    if not chs:
        return await message.reply(
            "📢 **Koi FSUB Channel Set Nahi Hai**\n\n"
            "Channel add karo — jab bhi koi tumhari file download karne aayega,\n"
            "pehle tumhara channel join karna hoga. 🔒\n\n"
            "Niche **➕ Add Channel** button dabao."
        )

    lines   = [f"📢 **MY FSUB CHANNELS ({len(chs)}):**\n━━━━━━━━━━━━━━━━━━━━\n"]
    buttons = []
    for ch_id in chs:
        try:
            chat    = await client.get_chat(ch_id)
            ch_name = chat.title
            ch_type = "📢 Channel" if chat.type.value == "channel" else "👥 Group"
            members = chat.members_count or "?"
            lines.append(f"{ch_type} **{ch_name}**\n┣ 🆔 `{ch_id}`\n┗ 👥 Members: {members}\n")
        except Exception:
            ch_name = str(ch_id)
            lines.append(f"❓ `{ch_id}` (access nahi)\n")
        buttons.append([InlineKeyboardButton(f"❌ Remove: {ch_name[:20]}", callback_data=f"rmch_{ch_id}")])

    await message.reply("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))


async def send_user_stats_text(client, message, uid):
    user_files = get_user_files(uid)
    total_dl   = sum(f.get("downloads", 0) for f in user_files)
    u          = get_user(uid)
    chs        = u.get("channels", []) if u else []
    is_blue    = u and u.get("blue_tick")
    top3       = sorted(user_files, key=lambda f: f.get("downloads", 0), reverse=True)[:3]
    me         = await client.get_me()

    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **MY STATS**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📁 Total Files: **{len(user_files)}**\n"
        f"⬇️ Total Downloads: **{total_dl}**\n"
        f"🔗 Active Links: **{len(user_files)}**\n"
        f"📢 FSUB Channels: **{len(chs)}**\n"
        f"🔵 Blue Tick: **{'✅ Active' if is_blue else '❌ None'}**\n\n"
    )
    if top3:
        text += "🏆 **TOP FILES:**\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, f in enumerate(top3):
            link  = f"https://t.me/{me.username}?start={f['_id']}"
            fname = f.get('file_name', 'file')[:18]
            text += f"{medals[i]} **{fname}** — ⬇️{f.get('downloads',0)}\n🔗 {link}\n"
    text += "━━━━━━━━━━━━━━━━━━━━"

    await message.reply(text)


async def send_all_users_text(client, message):
    users_list = all_users()
    blue_count = sum(1 for u in users_list if u.get("blue_tick"))
    ban_count  = sum(1 for u in users_list if u.get("banned"))

    lines = [
        f"👥 **ALL USERS ({len(users_list)})**\n"
        f"🔵 Blue Tick: {blue_count} | 🚫 Banned: {ban_count}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]
    buttons = []
    for u in users_list[:20]:
        blue  = "🔵" if u.get("blue_tick") else ""
        ban   = "🚫" if u.get("banned")    else ""
        name  = u.get("name", "Unknown")
        uid_  = u["_id"]
        uname = f"@{u['username']}" if u.get("username") else f"ID:{uid_}"
        lines.append(f"{blue}{ban} **{name}** ({uname})\n🆔 `{uid_}`\n")
        buttons.append([InlineKeyboardButton(f"⚙️ Manage: {name[:15]}", callback_data=f"manage_user_{uid_}")])

    if len(users_list) > 20:
        lines.append(f"\n_...aur {len(users_list)-20} more users_")

    await message.reply("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))



@bot.on_callback_query(filters.regex("^my_files$"))
async def cb_myfiles(client, cq):
    await cq.answer()
    uid   = cq.from_user.id
    files = get_user_files(uid, limit=15)
    if not files:
        return await cq.message.reply(
            "📁 **Koi file nahi hai abhi.**\n\n"
            "Seedha koi file/photo/video bhejo — link mil jayega!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back_dashboard")
            ]])
        )
    me    = await client.get_me()
    lines = [f"📁 **MY FILES ({len(files)}):**\n━━━━━━━━━━━━━━━━━━━━\n"]
    buttons = []
    for f in files:
        link  = f"https://t.me/{me.username}?start={f['_id']}"
        lock  = "🔒" if f.get("password") else "🔓"
        fname = f.get('file_name', 'file')[:20]
        dl    = f.get('downloads', 0)
        lines.append(
            f"{lock} **{fname}**\n"
            f"┣ ⬇️ Downloads: {dl}\n"
            f"┣ 🆔 `{f['_id']}`\n"
            f"┗ 🔗 {link}\n"
        )
        # Per-file action buttons
        pw_btn = (
            InlineKeyboardButton(f"🔓 Remove Pass", callback_data=f"rmpw_{f['_id']}")
            if f.get("password") else
            InlineKeyboardButton(f"🔒 Set Pass",    callback_data=f"setpw_{f['_id']}")
        )
        buttons.append([
            pw_btn,
            InlineKeyboardButton("🗑 Delete", callback_data=f"delfile_{f['_id']}")
        ])

    buttons.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")])

    full = "\n".join(lines)
    # Send text first (may be long), then buttons
    if len(full) > 4000:
        for i in range(0, len(full), 4000):
            await cq.message.reply(full[i:i+4000])
        await cq.message.reply("⚙️ **File Actions:**", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await cq.message.reply(full, reply_markup=InlineKeyboardMarkup(buttons))


@bot.on_callback_query(filters.regex(r"^setpw_"))
async def cb_setpw(client, cq):
    uid    = cq.from_user.id
    fid    = cq.data.split("setpw_")[1]
    fdoc   = get_file(fid)
    if not fdoc or fdoc["owner_id"] != uid:
        return await cq.answer("❌ Permission nahi!", show_alert=True)
    await cq.answer()
    pending_pw_set[uid] = fid
    await cq.message.reply(
        f"🔒 **Password Set Karo**\n\n"
        f"File: `{fdoc.get('file_name','file')}`\n\n"
        "Password type karke bhejo:\n"
        "/cancel se cancel karo."
    )


@bot.on_callback_query(filters.regex(r"^rmpw_"))
async def cb_rmpw(client, cq):
    uid  = cq.from_user.id
    fid  = cq.data.split("rmpw_")[1]
    fdoc = get_file(fid)
    if not fdoc or fdoc["owner_id"] != uid:
        return await cq.answer("❌ Permission nahi!", show_alert=True)
    remove_file_password(fid)
    await cq.answer("🔓 Password remove ho gaya!", show_alert=True)


@bot.on_callback_query(filters.regex(r"^delfile_"))
async def cb_delfile(client, cq):
    uid  = cq.from_user.id
    fid  = cq.data.split("delfile_")[1]
    fdoc = get_file(fid)
    if not fdoc or (fdoc["owner_id"] != uid and uid != OWNER_ID):
        return await cq.answer("❌ Permission nahi!", show_alert=True)
    delete_file_record(fid)
    await cq.answer("🗑 Link delete ho gaya!", show_alert=True)


@bot.on_callback_query(filters.regex("^my_channels$"))
async def cb_mychannels(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    u   = get_user(uid)
    chs = u.get("channels", []) if u else []

    if not chs:
        return await cq.message.reply(
            "📢 **Koi FSUB Channel Set Nahi Hai**\n\n"
            "Channel add karo — jab bhi koi tumhari file download karne aayega,\n"
            "pehle tumhara channel join karna hoga. 🔒",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel_guide")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_dashboard")],
            ])
        )

    lines   = [f"📢 **MY FSUB CHANNELS ({len(chs)}):**\n━━━━━━━━━━━━━━━━━━━━\n"]
    buttons = []
    for ch_id in chs:
        try:
            chat = await client.get_chat(ch_id)
            ch_name  = chat.title
            ch_type  = "📢 Channel" if chat.type.value == "channel" else "👥 Group"
            members  = chat.members_count or "?"
            lines.append(
                f"{ch_type} **{ch_name}**\n"
                f"┣ 🆔 `{ch_id}`\n"
                f"┗ 👥 Members: {members}\n"
            )
        except Exception:
            ch_name = str(ch_id)
            lines.append(f"❓ `{ch_id}` (access nahi)\n")
        buttons.append([InlineKeyboardButton(
            f"❌ Remove: {ch_name[:20]}", callback_data=f"rmch_{ch_id}"
        )])

    buttons.append([InlineKeyboardButton("➕ Add Channel", callback_data="add_channel_guide")])
    buttons.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")])

    await cq.message.reply(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@bot.on_callback_query(filters.regex("^set_storage_guide$"))
async def cb_set_storage(client, cq):
    await cq.answer()
    pending_storage_set[cq.from_user.id] = True
    await cq.message.reply(
        "📤 **Storage Channel Setup**\n\n"
        "1. Private channel banao\n"
        "2. Bot ko admin banao (Post Messages)\n"
        "3. Us channel se koi **message forward karo** yahan\n\n"
        "/cancel"
    )


@bot.on_callback_query(filters.regex("^add_channel_guide$"))
async def cb_add_channel(client, cq):
    await cq.answer()
    pending_fsub_add[cq.from_user.id] = True
    await cq.message.reply(
        "📢 **Add FSUB Channel/Group**\n\n"
        "Apne channel/group se koi **message forward karo** yahan.\n\n"
        "/cancel"
    )


@bot.on_callback_query(filters.regex("^remove_channel_cb$"))
async def cb_remove_ch_btn(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    u   = get_user(uid)
    if not u or not u.get("channels"):
        return await cq.message.reply("❌ Koi FSUB channel nahi hai.")

    buttons = []
    for ch_id in u["channels"]:
        try:
            chat = await client.get_chat(ch_id)
            name = chat.title
        except:
            name = str(ch_id)
        buttons.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"rmch_{ch_id}")])

    await cq.message.reply("Kaun sa remove karna hai?", reply_markup=InlineKeyboardMarkup(buttons))


@bot.on_callback_query(filters.regex("^upload_guide$"))
async def cb_upload(client, cq):
    await cq.answer()
    uid = cq.from_user.id
    if STORAGE_CHANNEL == 0:
        return await cq.message.reply("❌ Storage channel setup nahi hai. Admin se contact karo.")
    pending_upload_mode[uid] = True
    await cq.message.reply(
        "📤 **File Upload Mode ON**\n\n"
        "Ab file/photo/video/document bhejo — save ho jayegi.\n"
        "Phir password set karo ya /skip karo.\n\n"
        "/cancel se cancel karo."
    )


@bot.on_callback_query(filters.regex("^send_promo_now$"))
async def cb_send_promo(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer("Sending...")

    promo = get_promo()
    if not promo or not promo.get("text"):
        return await cq.message.reply("❌ Pehle promo set karo: /setpromo")

    users_list   = all_users()
    all_channels = set()
    for u in users_list:
        if u.get("blue_tick"):
            continue
        for ch in u.get("channels", []):
            all_channels.add(ch)

    status = await cq.message.reply(
        f"📣 Bhej raha hoon...\n"
        f"👥 Users: {len(users_list)} | 📢 Channels: {len(all_channels)}"
    )

    sent_u = failed_u = sent_c = failed_c = 0
    for u in users_list:
        try:
            ok = await send_promo(client, u["_id"])
            if ok: sent_u += 1
            else:  failed_u += 1
        except: failed_u += 1
        await asyncio.sleep(0.05)

    for ch in all_channels:
        try:
            ok = await send_promo(client, ch)
            if ok: sent_c += 1
            else:  failed_c += 1
        except: failed_c += 1
        await asyncio.sleep(0.1)

    await status.edit(
        f"✅ **Done!**\n"
        f"👤 Users: ✔️{sent_u} ❌{failed_u}\n"
        f"📢 Channels: ✔️{sent_c} ❌{failed_c}"
    )


@bot.on_callback_query(filters.regex("^set_promo_msg$"))
async def cb_set_promo(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer()
    pending_promo_set[OWNER_ID] = "text"
    promo_draft[OWNER_ID]       = {}
    await cq.message.reply(
        "✏️ **Promo Setup — Step 1/3**\n\n"
        "Promo ka **text** bhejo (Markdown ok).\n"
        "/cancel"
    )


@bot.on_callback_query(filters.regex("^full_stats$"))
async def cb_full_stats(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer("Refreshing...")
    t          = get_stats_today()
    tot        = get_stats_total()
    users_list = all_users()
    blue_count = sum(1 for u in users_list if u.get("blue_tick"))
    ban_count  = sum(1 for u in users_list if u.get("banned"))

    await cq.message.reply(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **FULL STATS**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "**AAJ:**\n"
        f"┣ 👤 Naye Users: **{t['new_users']}**\n"
        f"┣ ⬇️ Downloads: **{t['downloads']}**\n"
        f"┗ 🔗 Links Bane: **{t['links']}**\n\n"
        "**ALL TIME:**\n"
        f"┣ 👥 Total Users: **{len(users_list)}**\n"
        f"┣ 📁 Total Files: **{total_files()}**\n"
        f"┣ ⬇️ Total Downloads: **{tot['downloads']}**\n"
        f"┣ 🔗 Total Links: **{tot['links']}**\n"
        f"┣ 🔵 Blue Tick Users: **{blue_count}**\n"
        f"┗ 🚫 Banned Users: **{ban_count}**\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")
        ]])
    )


@bot.on_callback_query(filters.regex("^all_users_list$"))
async def cb_all_users(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer()
    users_list = all_users()
    blue_count = sum(1 for u in users_list if u.get("blue_tick"))
    ban_count  = sum(1 for u in users_list if u.get("banned"))

    lines = [
        f"👥 **ALL USERS ({len(users_list)})**\n"
        f"🔵 Blue Tick: {blue_count} | 🚫 Banned: {ban_count}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
    ]
    buttons = []
    for u in users_list[:20]:
        blue = "🔵" if u.get("blue_tick") else ""
        ban  = "🚫" if u.get("banned")    else ""
        name = u.get("name", "Unknown")
        uid  = u["_id"]
        uname = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
        lines.append(f"{blue}{ban} **{name}** ({uname})\n🆔 `{uid}`\n")
        buttons.append([InlineKeyboardButton(
            f"⚙️ Manage: {name[:15]}", callback_data=f"manage_user_{uid}"
        )])

    if len(users_list) > 20:
        lines.append(f"\n_...aur {len(users_list)-20} more users_")

    await cq.message.reply(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons + [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]
        ])
    )


@bot.on_callback_query(filters.regex(r"^manage_user_"))
async def cb_manage_user(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer()

    target_id = int(cq.data.split("manage_user_")[1])
    u = get_user(target_id)
    if not u:
        return await cq.message.reply("❌ User nahi mila.")

    blue    = "🔵 Active" if u.get("blue_tick") else "❌ None"
    banned  = "🚫 Banned" if u.get("banned")    else "✅ Active"
    name    = u.get("name", "Unknown")
    uname   = f"@{u['username']}" if u.get("username") else "N/A"
    ufiles  = get_user_files(target_id)
    total_dl = sum(f.get("downloads", 0) for f in ufiles)
    chs     = len(u.get("channels", []))

    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ **USER DETAILS**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Name: **{name}**\n"
        f"📛 Username: {uname}\n"
        f"🆔 ID: `{target_id}`\n\n"
        f"📁 Files: **{len(ufiles)}**\n"
        f"⬇️ Downloads: **{total_dl}**\n"
        f"📢 Channels: **{chs}**\n\n"
        f"🔵 Blue Tick: **{blue}**\n"
        f"🔐 Status: **{banned}**\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    blue_btn = (
        InlineKeyboardButton("❌ Remove Blue Tick", callback_data=f"rmblue_{target_id}")
        if u.get("blue_tick") else
        InlineKeyboardButton("🔵 Give Blue Tick",   callback_data=f"addblue_{target_id}")
    )
    ban_btn = (
        InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{target_id}")
        if u.get("banned") else
        InlineKeyboardButton("🚫 Ban User",   callback_data=f"ban_{target_id}")
    )

    await cq.message.reply(
        text,
        reply_markup=InlineKeyboardMarkup([
            [blue_btn],
            [ban_btn],
            [InlineKeyboardButton("🔙 Back to Users", callback_data="all_users_list")],
        ])
    )


@bot.on_callback_query(filters.regex(r"^addblue_"))
async def cb_addblue(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    uid = int(cq.data.split("addblue_")[1])
    set_blue_tick(uid, True)
    await cq.answer("🔵 Blue Tick diya gaya!", show_alert=True)
    # Refresh user panel
    u    = get_user(uid)
    name = u.get("name", str(uid)) if u else str(uid)
    await cq.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Remove Blue Tick", callback_data=f"rmblue_{uid}")],
            [InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{uid}")
             if u and not u.get("banned") else
             InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{uid}")],
            [InlineKeyboardButton("🔙 Back to Users", callback_data="all_users_list")],
        ])
    )
    # Notify user
    try:
        await client.send_message(
            uid,
            "🎉 **Congratulations!**\n\n"
            "🔵 Aapko **Blue Tick** mil gaya!\n\n"
            "Ab aapke channels me admin ka promo message **nahi** aayega. ✅"
        )
    except Exception:
        pass


@bot.on_callback_query(filters.regex(r"^rmblue_"))
async def cb_rmblue(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    uid = int(cq.data.split("rmblue_")[1])
    set_blue_tick(uid, False)
    await cq.answer("Blue Tick remove kiya!", show_alert=True)
    u = get_user(uid)
    await cq.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Give Blue Tick", callback_data=f"addblue_{uid}")],
            [InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{uid}")
             if u and not u.get("banned") else
             InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{uid}")],
            [InlineKeyboardButton("🔙 Back to Users", callback_data="all_users_list")],
        ])
    )
    try:
        await client.send_message(uid, "ℹ️ Aapka Blue Tick remove kar diya gaya.")
    except Exception:
        pass


@bot.on_callback_query(filters.regex(r"^ban_\d+$"))
async def cb_ban_user(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    uid = int(cq.data.split("ban_")[1])
    ban_user(uid)
    await cq.answer("🚫 User Banned!", show_alert=True)
    await cq.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Give Blue Tick", callback_data=f"addblue_{uid}")],
            [InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{uid}")],
            [InlineKeyboardButton("🔙 Back to Users", callback_data="all_users_list")],
        ])
    )


@bot.on_callback_query(filters.regex(r"^unban_\d+$"))
async def cb_unban_user(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    uid = int(cq.data.split("unban_")[1])
    unban_user(uid)
    await cq.answer("✅ User Unbanned!", show_alert=True)
    await cq.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Give Blue Tick", callback_data=f"addblue_{uid}")],
            [InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{uid}")],
            [InlineKeyboardButton("🔙 Back to Users", callback_data="all_users_list")],
        ])
    )


@bot.on_callback_query(filters.regex(r"^buy_blue_tick$"))
async def cb_buy_blue(client, cq):
    await cq.answer()
    me = await client.get_me()
    # Admin ka username fetch karo
    try:
        admin = await client.get_users(OWNER_ID)
        admin_link = f"@{admin.username}" if admin.username else f"tg://user?id={OWNER_ID}"
        dm_url     = f"https://t.me/{admin.username}" if admin.username else None
    except Exception:
        admin_link = "Admin"
        dm_url     = None

    buttons = []
    if dm_url:
        buttons.append([InlineKeyboardButton("💬 DM Admin", url=dm_url)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_dashboard")])

    await cq.message.reply(
        "🔵 **Blue Tick Kya Hai?**\n\n"
        "Blue Tick lene ke baad:\n"
        "✅ Aapke channels me admin ka promo **nahi** aayega\n"
        "✅ Exclusive badge milega\n\n"
        f"💬 **Admin se contact karo:** {admin_link}\n\n"
        "_Pricing aur plans ke liye directly message karo._",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@bot.on_callback_query(filters.regex(r"^back_dashboard$"))
async def cb_back_dashboard(client, cq):
    await cq.answer()
    uid  = cq.from_user.id
    user = await client.get_users(uid)

    class FakeMsg:
        from_user = user
        reply     = cq.message.reply

    await show_dashboard(client, FakeMsg(), uid)


@bot.on_callback_query(filters.regex(r"^user_stats$"))
async def cb_user_stats(client, cq):
    await cq.answer()
    uid        = cq.from_user.id
    user_files = get_user_files(uid)
    total_dl   = sum(f.get("downloads", 0) for f in user_files)
    u          = get_user(uid)
    chs        = u.get("channels", []) if u else []
    is_blue    = u and u.get("blue_tick")

    # Top 3 files by downloads
    top3 = sorted(user_files, key=lambda f: f.get("downloads", 0), reverse=True)[:3]
    me   = await client.get_me()

    text = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 **MY STATS**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📁 Total Files: **{len(user_files)}**\n"
        f"⬇️ Total Downloads: **{total_dl}**\n"
        f"🔗 Active Links: **{len(user_files)}**\n"
        f"📢 FSUB Channels: **{len(chs)}**\n"
        f"🔵 Blue Tick: **{'✅ Active' if is_blue else '❌ None'}**\n\n"
    )

    if top3:
        text += "🏆 **TOP FILES:**\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, f in enumerate(top3):
            link   = f"https://t.me/{me.username}?start={f['_id']}"
            fname  = f.get('file_name', 'file')[:18]
            text  += f"{medals[i]} **{fname}** — ⬇️{f.get('downloads',0)}\n🔗 {link}\n"

    text += "━━━━━━━━━━━━━━━━━━━━"

    await cq.message.reply(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")
        ]])
    )


@bot.on_callback_query(filters.regex(r"^broadcast_guide$"))
async def cb_broadcast_guide(client, cq):
    if cq.from_user.id != OWNER_ID:
        return await cq.answer("❌ Only admin!", show_alert=True)
    await cq.answer()
    await cq.message.reply(
        "📢 **Broadcast Kaise Kare:**\n\n"
        "Koi bhi message bhejo ya forward karo, phir reply karke:\n"
        "`/broadcast`\n\n"
        "Woh message sabhi users ko jayega."
    )


# ==================== /cancel ====================
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    uid     = message.from_user.id
    cleared = []
    if uid in pending_upload:      del pending_upload[uid];      cleared.append("upload")
    if uid in pending_upload_mode: del pending_upload_mode[uid]; cleared.append("upload mode")
    if uid in pending_storage_set: del pending_storage_set[uid]; cleared.append("storage setup")
    if uid in pending_fsub_add:    del pending_fsub_add[uid];    cleared.append("channel add")
    if uid in pending_promo_set:   del pending_promo_set[uid];   cleared.append("promo setup")
    if uid in pending_pw_set:      del pending_pw_set[uid];      cleared.append("password set")
    promo_draft.pop(uid, None)

    await message.reply(
        f"✅ Cancelled: {', '.join(cleared)}" if cleared else "ℹ️ Koi pending action nahi tha."
    )


# ==================== /help ====================
def get_help_text(uid):
    if uid == OWNER_ID:
        return (
            "👑 **Admin Commands:**\n\n"
            "📊 `/stats` — stats\n"
            "🔵 `/bluetick uid` — blue tick do\n"
            "❌ `/removeblue uid` — blue tick lo\n"
            "🚫 `/ban uid` — ban\n"
            "✅ `/unban uid` — unban\n"
            "✏️ `/setpromo` — promo set karo\n"
            "📣 `/sendpromo` — promo bhejo (blue tick skip)\n"
            "📢 `/broadcast` — reply karke broadcast\n\n"
            "**File Commands:**\n"
            "📤 `/uploadfile` — upload mode ON karo, phir file bhejo\n"
            "📁 `/myfiles` — files dekho\n"
            "🗄 `/setstorage` — storage channel set karo (admin only)\n"
            "➕ `/addchannel` — FSUB channel add\n"
            "❌ `/removechannel` — channel remove\n"
            "🔒 `/setpass fid pass` — password\n"
            "🔓 `/removepass fid` — password hatao\n"
            "🗑 `/deletelink fid` — link delete\n"
            "❌ `/cancel` — action cancel"
        )
    return (
        "👤 **Commands:**\n\n"
        "📊 `/dashboard` — dashboard\n"
        "📤 `/uploadfile` — upload mode ON karo, phir file bhejo\n"
        "📁 `/myfiles` — apni files\n"
        "➕ `/addchannel` — FSUB channel\n"
        "❌ `/removechannel` — channel hatao\n"
        "🔒 `/setpass fid pass` — password\n"
        "🔓 `/removepass fid` — password hatao\n"
        "🗑 `/deletelink fid` — link delete\n"
        "❌ `/cancel` — cancel"
    )


ABOUT_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━\n"
    "ℹ️ **ABOUT THIS BOT**\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "📦 **File Store Bot**\n\n"
    "✨ **Features:**\n"
    "┣ 📤 Apni file upload karo, link banao\n"
    "┣ 🔒 Password protection\n"
    "┣ 📢 FSUB — apne channel/group join karwao\n"
    "┣ 📊 Real-time stats aur downloads\n"
    "┗ 🔵 Blue Tick — premium ad-free experience\n\n"
    "🛠 Powered by Pyrogram\n"
    "━━━━━━━━━━━━━━━━━━━━"
)


@bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    await message.reply(get_help_text(message.from_user.id))


@bot.on_callback_query(filters.regex("^help_cb$"))
async def cb_help(client, cq):
    await cq.answer()
    await cq.message.reply(
        get_help_text(cq.from_user.id),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")
        ]])
    )


@bot.on_callback_query(filters.regex("^about_cb$"))
async def cb_about(client, cq):
    await cq.answer()
    await cq.message.reply(
        ABOUT_TEXT,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")
        ]])
    )


@bot.on_message(filters.command("dashboard") & filters.private)
async def dashboard_cmd(client, message):
    upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await show_dashboard(client, message, message.from_user.id)


# ==================== TEXT HANDLER ====================
@bot.on_message(filters.private & ~filters.command(SKIP_CMDS))
async def text_handler(client, message):
    uid = message.from_user.id
    if is_banned(uid): return

    # -------- Niche wale fixed keyboard buttons handle karo --------
    # (Sirf jab koi pending action na chal raha ho, taaki password/forward flow na tute)
    no_pending = (
        uid not in pending_storage_set and
        uid not in pending_fsub_add and
        uid not in pending_upload and
        uid not in pending_upload_mode and
        uid not in pending_pw_set and
        uid not in pending_pw_check and
        not (uid == OWNER_ID and uid in pending_promo_set)
    )

    if no_pending and message.text:
        txt = message.text.strip()

        if txt == "📊 Dashboard":
            upsert_user(uid, message.from_user.username, message.from_user.first_name)
            return await show_dashboard(client, message, uid)

        if txt == "📁 My Files":
            return await send_my_files_text(client, message, uid)

        if txt == "📢 My Channels":
            return await send_my_channels_text(client, message, uid)

        if txt == "➕ Add Channel":
            pending_fsub_add[uid] = True
            return await message.reply(
                "📢 **Add FSUB Channel/Group**\n\n"
                "Apne channel/group se koi **message forward karo** yahan.\n\n"
                "/cancel se cancel karo"
            )

        if txt == "❌ Remove Channel":
            return await removechannel_cmd(client, message)

        if txt == "📊 My Stats":
            return await send_user_stats_text(client, message, uid)

        if txt == "👥 All Users" and uid == OWNER_ID:
            return await send_all_users_text(client, message)

        if txt == "📣 Send Promo" and uid == OWNER_ID:
            return await sendpromo_cmd(client, message)

        if txt == "✏️ Set Promo" and uid == OWNER_ID:
            return await setpromo_cmd(client, message)

        if txt == "❓ Help":
            return await message.reply(get_help_text(uid))

        if txt == "ℹ️ About Bot":
            return await message.reply(ABOUT_TEXT)

    # Storage channel setup via forward
    if uid in pending_storage_set:
        if message.forward_from_chat:
            ch_id = message.forward_from_chat.id
            try:
                await client.get_chat(ch_id)
                set_storage_channel(uid, ch_id)
                del pending_storage_set[uid]
                await message.reply(
                    f"✅ **Storage set!**\n\n"
                    f"Channel ID: `{ch_id}`\n\n"
                    "Ab file bhejo! 📤"
                )
            except ChatAdminRequired:
                await message.reply("❌ Bot admin nahi hai us channel me. Admin banao pehle.")
            except Exception as e:
                await message.reply(f"❌ Channel access nahi hua.\nError: `{e}`")
        else:
            await message.reply("⚠️ Channel ka **message forward** karo, text nahi.")
        return

    # FSUB channel add via forward
    if uid in pending_fsub_add:
        if message.forward_from_chat:
            ch_id = message.forward_from_chat.id
            added = add_fsub_channel(uid, ch_id)
            del pending_fsub_add[uid]
            if added:
                await message.reply(
                    f"✅ **Channel added!**\n\n"
                    f"ID: `{ch_id}`\n\n"
                    "Ab is channel ko join karna hoga visitors ko."
                )
            else:
                await message.reply("ℹ️ Ye channel already add hai.")
        else:
            await message.reply("⚠️ Channel ka **message forward** karo.")
        return

    # Promo setup flow (owner only)
    if uid == OWNER_ID and uid in pending_promo_set:
        step = pending_promo_set[uid]

        if step == "text" and message.text:
            promo_draft[uid]["text"] = message.text
            pending_promo_set[uid]   = "photo"
            await message.reply(
                "✅ Text saved!\n\n"
                "**Step 2/3:** Photo bhejo (optional).\n"
                "/skip se skip karo."
            )
            return

        if step == "photo":
            if message.photo:
                promo_draft[uid]["photo_id"] = message.photo.file_id
            pending_promo_set[uid] = "button"
            await message.reply(
                "✅ Photo saved!\n\n"
                "**Step 3/3:** Button bhejo:\n"
                "`Button Text | https://url.com`\n\n"
                "/skip se skip karo."
            )
            return

        if step == "button":
            if message.text and "|" in message.text:
                parts = message.text.split("|", 1)
                promo_draft[uid]["button_text"] = parts[0].strip()
                promo_draft[uid]["button_url"]  = parts[1].strip()
            draft = promo_draft.get(uid, {})
            set_promo(
                text        = draft.get("text", ""),
                photo_id    = draft.get("photo_id"),
                button_text = draft.get("button_text"),
                button_url  = draft.get("button_url"),
            )
            del pending_promo_set[uid]
            promo_draft.pop(uid, None)
            await message.reply("✅ **Promo saved!** /sendpromo se bhejo.")
            return

    # File upload → password
    if uid in pending_upload and message.text:
        await finalize_upload(client, message, uid, password=message.text.strip())
        return

    # Set password via dashboard button
    if uid in pending_pw_set and message.text:
        fid  = pending_pw_set.pop(uid)
        fdoc = get_file(fid)
        if fdoc and fdoc["owner_id"] == uid:
            set_file_password(fid, message.text.strip())
            await message.reply(
                f"🔒 **Password Set!**\n\n"
                f"File: `{fdoc.get('file_name','file')}`\n"
                f"Password: `{message.text.strip()}`"
            )
        else:
            await message.reply("❌ File nahi mili.")
        return

    # File download → password check
    if uid in pending_pw_check and message.text:
        fid  = pending_pw_check[uid]
        fdoc = get_file(fid)
        if fdoc and message.text.strip() == fdoc.get("password"):
            del pending_pw_check[uid]
            await deliver_file(client, uid, fdoc)
        else:
            await message.reply("❌ Galat password. Dobara try karo:")
        return


# ==================== FLASK (24/7) ====================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ FileStoreBot running!", 200

@flask_app.route("/health")
def health():
    return {"status": "ok", "users": len(all_users()), "files": total_files()}, 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# ==================== MAIN ====================
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("FileStoreBot Starting...")
    logger.info(f"Owner  : {OWNER_ID}")
    logger.info(f"AutoDel: {AUTO_DELETE_TIME}s")
    logger.info("=" * 50)

    if not BOT_TOKEN: logger.error("BOT_TOKEN missing!")
    if OWNER_ID == 0:       logger.error("OWNER_ID missing!")
    if STORAGE_CHANNEL == 0: logger.error("STORAGE_CHANNEL missing! Users cannot upload files.")

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask on port {PORT}")
    bot.run()
