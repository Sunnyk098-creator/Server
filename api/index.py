import os
import json
import threading
import asyncio
import logging
import re
import uuid
import urllib.request
import urllib.parse
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest, TelegramError

# ==========================================
# CONFIGURATION
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8"
MAIN_ADMIN_ID = 8522410574
FIREBASE_URL = "https://task-pay-f7f88-default-rtdb.europe-west1.firebasedatabase.app/user_data.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

db_lock = threading.Lock()

# ==========================================
# FONT MAPPING (ADMIN ONLY)
# ==========================================
def to_font(text):
    if not isinstance(text, str):
        return text
    urls = re.findall(r'https?://[^\s]+', text)
    for i, url in enumerate(urls):
        text = text.replace(url, f"__URL_{i}__")
    usernames = re.findall(r'@[a-zA-Z0-9_]+', text)
    for i, uname in enumerate(usernames):
        text = text.replace(uname, f"__UNAME_{i}__")
    mapping = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢ")
    text = text.translate(mapping)
    for i, uname in enumerate(usernames):
        text = text.replace(f"__UNAME_{i}__", uname)
    for i, url in enumerate(urls):
        text = text.replace(f"__URL_{i}__", url)
    return text

def font_markup(markup):
    if not markup: return None
    rows = getattr(markup, 'inline_keyboard', markup)
    new_kb = []
    for row in rows:
        new_row = []
        for btn in row:
            kwargs = {'text': to_font(btn.text)}
            if getattr(btn, 'callback_data', None): kwargs['callback_data'] = btn.callback_data
            if getattr(btn, 'url', None): kwargs['url'] = btn.url
            new_row.append(InlineKeyboardButton(**kwargs))
        new_kb.append(new_row)
    return InlineKeyboardMarkup(new_kb)

# ==========================================
# JSON DATABASE SYSTEM (FIREBASE INTEGRATED)
# ==========================================
def default_db():
    return {
        "users": [], "admins": [MAIN_ADMIN_ID],
        "gateways": [{"name": "UPI", "enabled": 1, "min_w": 1, "max_w": 100, "tax": 0}],
        "api_withdraw_gateways": [], "banned_wallets": [], "add_fund_gateways": [],
        "add_fund_requests": [], "buyable_gift_cards": [], "gift_codes": [],
        "gift_redemptions": [], "channels": [], "user_channels": [],
        "tasks": [], "task_submissions": [],
        "settings": {
            'bot_status': 'Active', 'withdraw_status': 'Both On', 'min_withdraw': '1', 
            'max_withdraw': '100', 'withdraw_tax': '0', 'pay_to_user_tax': '0', 
            'cfg_refer_amount': '0', 'new_user_notify': 'Active', 'payout_channel': 'Not Set',
            'bot_off_text': '🙇 Bot is not active', 'withdraw_off_text': '🚫 Withdraw is currently off',
            'support_text': 'Not configured', 'welcome_message': '✨ Welcome To Tasks Payment Bot',
            'keyboard': [
                {"id": "acc", "text": "👤 My Account"}, {"id": "mgc", "text": "🎟️ My Gift card"},
                {"id": "gfc", "text": "🎁 Gift Code"}, {"id": "ptu", "text": "🎉 Pay to User"},
                {"id": "tsk", "text": "📋 Task section"}, {"id": "wtd", "text": "🚀 Withdraw"},
                {"id": "bgc", "text": "🛒 Buy Gift Card"}
            ]
        },
        "admin_actions": [], "transactions": [], "withdrawals": [],
        "auto_inc": {"tasks": 1, "task_submissions": 1, "admin_actions": 1, "transactions": 1, "withdrawals": 1, "add_fund_requests": 1, "buyable_gift_cards": 1}
    }

def read_db():
    with db_lock:
        try:
            res = requests.get(FIREBASE_URL, timeout=10)
            if res.status_code == 200 and res.json() is not None:
                data = res.json()
                if 'api_withdraw_gateways' not in data: data['api_withdraw_gateways'] = []
                if 'banned_wallets' not in data: data['banned_wallets'] = []
                if 'keyboard' not in data.get('settings', {}):
                    data['settings']['keyboard'] = default_db()['settings']['keyboard']
                if 'welcome_message' not in data['settings']:
                    data['settings']['welcome_message'] = '✨ Welcome To Tasks Payment Bot'
                return data
            else:
                data = default_db()
                requests.put(FIREBASE_URL, json=data, timeout=10)
                return data
        except Exception as e:
            logger.error(f"Firebase Read Error: {e}")
            return default_db()

def write_db(data):
    with db_lock:
        try:
            requests.put(FIREBASE_URL, json=data, timeout=10)
        except Exception as e:
            logger.error(f"Firebase Write Error: {e}")

def get_set(db, key, default=None): return db['settings'].get(key, default)
def is_admin(db, user_id): return user_id == MAIN_ADMIN_ID or user_id in db['admins']
def gen_txid(): return uuid.uuid4().hex[:24]

def record_tx(db, user_id, amount, tx_type, desc, related_user=None):
    user = next((u for u in db['users'] if u['user_id'] == user_id), None)
    if not user: return None
    bal_before = user['balance']
    bal_after = bal_before + amount
    user['balance'] = bal_after
    tx_id = gen_txid()
    tx = {
        "id": db['auto_inc']['transactions'], "tx_id": tx_id, "user_id": user_id,
        "related_user": related_user, "amount": amount, "type": tx_type, "desc": desc,
        "bal_before": bal_before, "bal_after": bal_after, "status": "✅ Successful",
        "created_at": str(datetime.now())[:19]
    }
    db['transactions'].append(tx)
    db['auto_inc']['transactions'] += 1
    return tx_id 

def mask_string(s):
    s = str(s)
    if '@' in s:
        parts = s.split('@')
        name = parts[0]
        name = name[:3] + '*' * 5 if len(name) > 3 else name[:1] + '*' * 5
        return name + '@' + parts[1]
    else:
        if len(s) >= 10: return s[:3] + '*' * 5 + s[-3:]
        return s

def call_gateway_api(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        return str(e)

def normalize_channel(ch):
    ch = ch.strip()
    if ch.startswith("https://t.me/"): ch = "@" + ch.split("/")[-1]
    elif not ch.startswith("@"): ch = "@" + ch
    return ch

# ==========================================
# MIDDLEWARE & HELPERS
# ==========================================
async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE, db) -> bool:
    uid = update.effective_user.id
    if not db['channels']: return True
    missing = False
    for ch in db['channels']:
        ch_id = ch['channel_id']
        if ch['type'] == 'checked':
            try:
                member = await context.bot.get_chat_member(chat_id=ch_id, user_id=uid)
                if member.status not in ['creator', 'administrator', 'member', 'restricted']: 
                    missing = True
                    break
            except Exception: 
                missing = True
                break
        else:
            if not any(uc['user_id'] == uid and uc['channel_id'] == ch_id for uc in db['user_channels']):
                missing = True
                break
    if missing:
        txt = f"👋 Hey There {update.effective_user.first_name} Welcome To Bot !\n\n🛑 Must Join Total Channel To Use Our Bot\n\n💣 After Joining Click Claim"
        kb = []
        row = []
        for ch in db['channels']:
            ch_id = ch['channel_id']
            url = f"https://t.me/{ch_id.replace('@', '')}"
            row.append(InlineKeyboardButton("📢 Join", url=url))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("✅ Claim", callback_data="usr_claim_join")])
        if update.callback_query:
            try:
                await update.callback_query.answer("🛑 Please join all channels first!", show_alert=True)
                await update.callback_query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
            except: 
                await context.bot.send_message(uid, txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return False
    return True 

async def check_user(update: Update, context: ContextTypes.DEFAULT_TYPE, is_start=False) -> bool:
    uid = update.effective_user.id
    u = update.effective_user
    db = read_db()
    user = next((x for x in db['users'] if x['user_id'] == uid), None)
    if not user and not is_start:
        db['users'].append({"user_id": uid, "username": u.username, "first_name": u.first_name, "balance": 0.0, "joined_at": str(datetime.now())[:19], "verified": 0, "referred_by": None, "wallet_banned": 0, "is_banned": 0})
        write_db(db)
        user = db['users'][-1]
    if not await check_force_join(update, context, db): return False
    if get_set(db, 'bot_status') != 'Active' and not is_admin(db, uid):
        off_txt = get_set(db, 'bot_off_text', '🙇 Bot is not active')
        if update.callback_query:
            try: await update.callback_query.answer(off_txt, show_alert=True)
            except: pass
        else:
            await context.bot.send_message(uid, off_txt)
        return False
    if user and user.get('is_banned') and not is_admin(db, uid):
        if update.callback_query:
            try: await update.callback_query.answer("❌ You are banned from using this bot.", show_alert=True)
            except: pass
        else:
            await context.bot.send_message(uid, "❌ You are banned from using this bot.")
        return False
    return True 

def get_back_btn(target="adm_main"): return InlineKeyboardMarkup([[InlineKeyboardButton(to_font("🔙 Back"), callback_data=target)]])

def cancel_state(context: ContextTypes.DEFAULT_TYPE):
    for k in list(context.user_data.keys()):
        if k.startswith("WAITING_"): del context.user_data[k]

def get_main_keyboard():
    db = read_db()
    kb_data = get_set(db, 'keyboard')
    kb = []
    row = []
    for btn in kb_data:
        row.append(btn['text'])
        if len(row) == 2:
            kb.append(row)
            row = []
    if row: kb.append(row)
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def get_cancel_keyboard(): return ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)

async def safe_edit_or_send(context, uid, mid, text, markup=None, parse_mode='Markdown', use_font=False):
    if use_font:
        text = to_font(text)
        markup = font_markup(markup)
    try:
        await context.bot.edit_message_text(text, chat_id=uid, message_id=mid, reply_markup=markup, parse_mode=parse_mode, disable_web_page_preview=True)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            try: await context.bot.delete_message(chat_id=uid, message_id=mid)
            except: pass
            try:
                m = await context.bot.send_message(uid, text, reply_markup=markup, parse_mode=parse_mode, disable_web_page_preview=True)
                context.user_data['edit_msg_id'] = m.message_id
            except: pass

# ==========================================
# ADMIN UI PANELS
# ==========================================
async def render_adm_banusers(uid, mid, context):
    db = read_db()
    banned = [u for u in db['users'] if u.get('is_banned')]
    txt = "🚫 **Manage Banned Users**\n\n"
    kb = []
    for u in banned[:10]: 
        kb.append([InlineKeyboardButton(f"User: {u['user_id']}", callback_data="ignore"), InlineKeyboardButton("✅ Unban", callback_data=f"utgl_unban_{u['user_id']}")])
    kb.append([InlineKeyboardButton("🚫 Ban New User", callback_data="adm_ban_btn")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_adm_gc_main(uid, mid, context):
    db = read_db()
    codes = [c for c in db['gift_codes'] if c['is_active'] == 1][:10]
    txt = "🎁 **Manage Gift Codes**\n\nHere are the active gift codes:"
    kb = []
    for c in codes:
        kb.append([InlineKeyboardButton(f"{c['code']}", callback_data=f"gft_st_{c['code']}"), InlineKeyboardButton("❌", callback_data=f"gft_del_{c['code']}")])
    kb.append([InlineKeyboardButton("➕ Create Gift Code", callback_data="gft_add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_adm_bgc_main(uid, mid, context):
    db = read_db()
    cards = [c for c in db['buyable_gift_cards'] if c['is_bought'] == 0][:10]
    txt = "🛍️ **Manage Buyable Gift Cards**\n\n"
    kb = []
    for c in cards:
        kb.append([InlineKeyboardButton(f"{c['platform']} (₹{c['amount']})", callback_data=f"bgcvw_{c['id']}"), InlineKeyboardButton("❌", callback_data=f"bgcdel_{c['id']}")])
    kb.append([InlineKeyboardButton("➕ Add Gift Card", callback_data="bgc_add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_usr_gc_main(uid, mid, context):
    db = read_db()
    codes = [c for c in db['gift_codes'] if c['is_active'] == 1 and c['creator_id'] == uid][:10]
    if not codes: txt = "🔍 You don’t have any active Gift Codes.\n\nCreate your first one now!"
    else: txt = "🎁 **Your Gift Codes**\n\n"
    kb = []
    for c in codes:
        kb.append([InlineKeyboardButton(f"{c['code']}", callback_data=f"ugft_st_{c['code']}"), InlineKeyboardButton("❌", callback_data=f"ugft_del_{c['code']}")])
    kb.append([InlineKeyboardButton("➕ Create Gift Code", callback_data="usr_gc_create")])
    kb.append([InlineKeyboardButton("🎁 Claim Gift Code", callback_data="usr_gc_claim")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb))

async def render_addf_gateways_panel(uid, mid, context):
    db = read_db()
    gws = db.get('add_fund_gateways', [])
    txt = "🏧 **Manage Add Fund Gateways**\n\n"
    kb = []
    for g in gws:
        kb.append([InlineKeyboardButton(f"{g['name']}", callback_data=f"adf_vw_{g['name']}"), InlineKeyboardButton("❌", callback_data=f"adf_del_{g['name']}")])
    kb.append([InlineKeyboardButton("➕ Add Gateway", callback_data="adf_add_btn")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admg_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)
    
async def render_admt_main(uid, mid, context):
    db = read_db()
    tks = [t for t in db['tasks'] if t['active'] == 1]
    txt = "📋 **Manage Tasks**"
    kb = []
    for t in tks:
        kb.append([InlineKeyboardButton(f"{t['title']}", callback_data=f"tsk_vw_{t['id']}"), InlineKeyboardButton("❌", callback_data=f"tsk_del_{t['id']}")])
    kb.append([InlineKeyboardButton("➕ Add task", callback_data="tsk_add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_adm_bwallet(uid, mid, context):
    db = read_db()
    bans = db['banned_wallets']
    txt = "🚫 **Manage Banned Wallets**\n\n"
    kb = []
    for b in bans:
        kb.append([InlineKeyboardButton(f"{b}", callback_data="ignore"), InlineKeyboardButton("❌", callback_data=f"bwd_{b}")])
    kb.append([InlineKeyboardButton("➕ Ban Wallet", callback_data="adm_bwallet_add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_admg_main(uid, mid, context):
    txt = "🚧 **Gateway Setup Hub**\n\nSelect the type of gateway you would like to configure:"
    kb = [
        [InlineKeyboardButton("📤 API Withdraw Gateways", callback_data="admw_api_st")],
        [InlineKeyboardButton("📥 Add Fund Gateways", callback_data="adm_addf_main")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]
    ]
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_state(context)
    uid = update.effective_user.id
    msg_id = update.callback_query.message.message_id if update.callback_query else context.user_data.get('edit_msg_id')
    db = read_db()

    bot_stat_emoji = '✅ Active' if get_set(db, 'bot_status') == 'Active' else '❌ OFF' 
    w_mode = get_set(db, 'withdraw_status', 'Both On')
    w_emoji = '✅'
    if w_mode == 'Both Off': w_emoji = '❌'
    elif w_mode == 'Upi Only': w_emoji = '💳'
    elif w_mode == 'Wallet Only': w_emoji = '💼'
    
    upi_g = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
    api_gws = db['api_withdraw_gateways']
    upi_min = upi_g['min_w'] if upi_g else 0
    upi_max = upi_g['max_w'] if upi_g else 0
    upi_tax = upi_g['tax'] if upi_g else 0
    wallet_min = min([g['min_w'] for g in api_gws]) if api_gws else 0
    wallet_max = max([g['max_w'] for g in api_gws]) if api_gws else 0
    ptutax = get_set(db, 'pay_to_user_tax', '0')
    ptutax_str = f"✅ {ptutax}%" if float(ptutax) > 0 else "❎ Not Set"

    t = f"""🔍 Welcome To Admin Panel

💡 Review Bot Details

    👩‍💻 Main Owner ~ {MAIN_ADMIN_ID}
    🤖 Bot On/Off ~ {bot_stat_emoji}
    📤 Withdraw Mode ~ {w_emoji} {w_mode}
    ❇️ Payout Channel ~ {get_set(db, 'payout_channel')}
    🎫 Minimum Withdraw ~ {upi_min} - {wallet_min}
    🎟 Maximum Withdraw ~ {upi_max} - {wallet_max}
    📛 Withdraw Tax Amount ~ {upi_tax}
    💸 Pay To User Tax ~ {ptutax_str}"""

    kb = [ 
        [InlineKeyboardButton("📤 Payout Channel", callback_data="adm_pchannel")], 
        [InlineKeyboardButton("👨‍💻 Manage Admins", callback_data="adm_admins"), InlineKeyboardButton("🚫 Manage Ban Users", callback_data="adm_banusers")], 
        [InlineKeyboardButton(f"Bot Status :- {bot_stat_emoji}", callback_data="adm_bstatus")],
        [InlineKeyboardButton(f"Withdraw status :- {w_emoji} {w_mode}", callback_data="adm_wdtgl")],
        [InlineKeyboardButton("🕵️ Verify User", callback_data="adm_vuser"), InlineKeyboardButton("👮 Manage Ban Wallet", callback_data="adm_bwallet")], 
        [InlineKeyboardButton("➕ Add Balance", callback_data="adm_addbal"), InlineKeyboardButton("➖ Remove Balance", callback_data="adm_rembal")], 
        [InlineKeyboardButton("📢 Manage Your Channels", callback_data="adm_channels")], 
        [InlineKeyboardButton("⚠️ Reset Balance", callback_data="adm_rstbal"), InlineKeyboardButton("🎨 Customize Your Theme", callback_data="adm_theme")], 
        [InlineKeyboardButton("🎙️ Broadcast", callback_data="adm_broadcast"), InlineKeyboardButton("💬 Talk With User", callback_data="adm_talk")], 
        [InlineKeyboardButton("⚙️ Manage Withdraw", callback_data="admw_main")], 
        [InlineKeyboardButton("🚹 Find User Details", callback_data="adm_findu"), InlineKeyboardButton("📊 Statistics", callback_data="adm_stats")], 
        [InlineKeyboardButton(f"💸 Pay To User Tax ~ {ptutax}%", callback_data="adm_putax")], 
        [InlineKeyboardButton("🚧 Gateway Setup", callback_data="admg_main"), InlineKeyboardButton("🎉 Gift Codes", callback_data="adm_gc_main")], 
        [InlineKeyboardButton(f"🔔 New User Notification ~ {get_set(db, 'new_user_notify')}", callback_data="adm_nunotify")], 
        [InlineKeyboardButton("📋 Manage Task", callback_data="admt_main"), InlineKeyboardButton("🛍️ Manage Gift card", callback_data="adm_bgc_main")] 
    ]
    if update.callback_query:
        await safe_edit_or_send(context, uid, msg_id, t, InlineKeyboardMarkup(kb), use_font=True)
    else:
        t_font = to_font(t)
        m = await context.bot.send_message(uid, t_font, reply_markup=font_markup(kb))
        context.user_data['edit_msg_id'] = m.message_id 

async def render_admw_main(uid, mid, context):
    db = read_db()
    g = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
    api_gws = db['api_withdraw_gateways']
    
    t = f"🚀 Send New Withdraw Settings:\n\n✅ Cooldown Status: Enabled\n🔁 Limit: 3 Withdraw(s)\n⏱ Time: 1 Hour(s)\n\n💳 Upi Withdraw\n• Min: ₹{g['min_w']}\n• Max: ₹{g['max_w']}\n• Tax: {g['tax']}%\n\n"
    if api_gws:
        t += f"💰 Wallet Withdraw\n"
        for ag in api_gws: t += f"• {ag['domain']} Min: ₹{ag['min_w']} | Max: ₹{ag['max_w']} | Tax: {ag['tax']}%\n"
    t += "\n💡 To Set Withdraw Limits:\n⚠️ Use This Format:\nName:Minimum-Maximum--Tax\n📌 Example:\nUPI:5-500--0\n"
    for ag in api_gws: t += f"{ag['domain']}:1-100--0\n"
    t += "\n💡 Or All In One Line:\nUPI:5-500--0 " + " ".join([f"{ag['domain']}:1-100--0" for ag in api_gws])
    kb = [[InlineKeyboardButton("📋 View Requests", callback_data="admw_reqs")], [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]
    context.user_data['state'] = "WAITING_WD_LIMITS_BULK"
    await safe_edit_or_send(context, uid, mid, t, InlineKeyboardMarkup(kb), use_font=True) 

async def render_admw_api_st(uid, mid, context):
    db = read_db()
    gws = db['api_withdraw_gateways']
    txt = "🔌 **Manage API Withdraw Gateways**\n\n"
    kb = []
    for g in gws:
        kb.append([InlineKeyboardButton(f"{g['domain']}", callback_data=f"awapi_vw_{g['domain']}"), InlineKeyboardButton("❌", callback_data=f"awapi_del_{g['domain']}")])
    kb.append([InlineKeyboardButton("➕ Add Gateway", callback_data="awapi_add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admg_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_kb_settings(uid, mid, context):
    db = read_db()
    kb_data = get_set(db, 'keyboard')
    txt = "🎛️ KEYBOARD SETTINGS\n\nCurrent Order:\n"
    for i, btn in enumerate(kb_data): txt += f"{i+1}. {btn['text']}\n"
    kb = []
    for i, btn in enumerate(kb_data):
        kb.append([InlineKeyboardButton(btn['text'], callback_data="ignore"), InlineKeyboardButton("⬆️", callback_data=f"kb_up_{i}"), InlineKeyboardButton("⬇️", callback_data=f"kb_dn_{i}"), InlineKeyboardButton("✏️", callback_data=f"kb_ed_{i}")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_theme")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

async def render_channels_panel(uid, mid, context):
    db = read_db()
    chk = [c for c in db['channels'] if c['type'] == 'checked']
    uchk = [c for c in db['channels'] if c['type'] == 'unchecked']
    txt = "📋 Manage Channels Panel\n\n✅ Checked Channels\n🔘 Unchecked Channels\n\n🔎 Click 👀 to view info and ❌ to delete."
    kb = []
    for c in chk: kb.append([InlineKeyboardButton(f"👀 {c['channel_id']}", callback_data=f"chv_{c['channel_id']}"), InlineKeyboardButton("❌", callback_data=f"chd_{c['channel_id']}")])
    for c in uchk: kb.append([InlineKeyboardButton(f"👀 {c['channel_id']}", callback_data=f"chv_{c['channel_id']}"), InlineKeyboardButton("❌", callback_data=f"chd_{c['channel_id']}")])
    kb.append([InlineKeyboardButton("✅ Check Channel", callback_data="adm_ch_addchk")])
    kb.append([InlineKeyboardButton("⚙️ Uncheck Channel", callback_data="adm_ch_adduchk")])
    kb.append([InlineKeyboardButton("🔙 Back to Main Panel", callback_data="adm_main")])
    await safe_edit_or_send(context, uid, mid, txt, InlineKeyboardMarkup(kb), use_font=True)

# ==========================================
# USER & ADMIN COMMANDS
# ==========================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = update.effective_user
    db = read_db()
    user = next((x for x in db['users'] if x['user_id'] == uid), None)
    if not user:
        ref_id = None
        if context.args and context.args[0].isdigit():
            ref_id = int(context.args[0])
            if ref_id == uid: ref_id = None
        db['users'].append({"user_id": uid, "username": u.username, "first_name": u.first_name, "balance": 0.0, "joined_at": str(datetime.now())[:19], "verified": 0, "referred_by": ref_id, "wallet_banned": 0, "is_banned": 0})
        ref_amt = float(get_set(db, 'cfg_refer_amount', 0))
        if ref_id and ref_amt > 0:
            record_tx(db, ref_id, ref_amt, '👥 Referral Reward', f'Bonus for {uid}', related_user=uid)
            try: await context.bot.send_message(ref_id, f"{uid} Got Invited By Your Url +{ref_amt}")
            except: pass
        if get_set(db, 'new_user_notify') == 'Active':
            try: await context.bot.send_message(MAIN_ADMIN_ID, f"🔔 New User: {u.first_name} ({uid})")
            except: pass
        write_db(db)
        
    if not await check_user(update, context, is_start=True): return
    wmsg = get_set(db, 'welcome_message', "✨ Welcome To Tasks Payment Bot")
    await context.bot.send_message(uid, wmsg, reply_markup=get_main_keyboard()) 

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = read_db()
    if not is_admin(db, update.effective_user.id): return await update.message.reply_text("❌ Access Denied!")
    await render_admin_panel(update, context)

# ==========================================
# CALLBACKS & INPUT HANDLERS
# ==========================================
async def handle_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    mid = q.message.message_id
    db = read_db()
    
    if not is_admin(db, uid) and not d.startswith("ch_wd_"): return await q.answer(to_font("❌ Access Denied!"), show_alert=True)
    if d.startswith("ch_wd_"):
        if not is_admin(db, uid): return await q.answer(to_font("❌ Access Denied!"), show_alert=True)
        action = d.split("_")[2]
        wid = int(d.split("_")[3])
        w = next((x for x in db['withdrawals'] if x['id'] == wid), None)
        if not w or w['status'] not in ['⏳ Pending', 'PENDING']: return await q.answer(to_font("❌ Already processed."), show_alert=True)
        if action == "app":
            w['status'] = '✅ Approved'
            w['admin_note'] = f"By {update.effective_user.username or uid}"
            w['updated_at'] = str(datetime.now())[:19]
            tx = next((t for t in db['transactions'] if t['tx_id'] == w['tx_id']), None)
            if tx: tx['status'] = '✅ Successful'
            write_db(db)
            try: await context.bot.send_message(w['user_id'], f"✅ Your Withdrawal Request Of ₹{w['amount']} Has Been Approved!")
            except: pass
            new_text = to_font(f"⚠️ New UPI Payout Request! (#{wid})\n\nUser : {w['user_id']}\nRequest Amount : ₹ {w['amount']}\nAmount After Tax ({w['tax']}) : ₹{w['final_amount']}\nUPI ID : {mask_string(w['payout_details'])}\nTransaction ID : {w['tx_id']}\n\n✅ Approved by @{update.effective_user.username or uid} at {w['updated_at']}")
            try: await q.edit_message_text(new_text)
            except: pass
        elif action == "rej":
            w['status'] = '❌ Rejected'
            w['admin_note'] = f"Rejected By Channel"
            w['updated_at'] = str(datetime.now())[:19]
            record_tx(db, w['user_id'], w['amount'], '↩️ Withdrawal Refund', f'Rejected WD #{wid}', related_user=uid)
            tx = next((t for t in db['transactions'] if t['tx_id'] == w['tx_id']), None)
            if tx: tx['status'] = '❌ Rejected'
            write_db(db)
            try: await context.bot.send_message(w['user_id'], f"❌ Your Withdrawal Request Of ₹{w['amount']} Has Been Rejected! The Amount Has Been Refunded To Your Balance.")
            except: pass
            new_text = to_font(f"⚠️ New UPI Payout Request! (#{wid})\n\nUser : {w['user_id']}\nRequest Amount : ₹{w['amount']}\nAmount After Tax ({w['tax']}) : ₹{w['final_amount']}\nUPI ID : {mask_string(w['payout_details'])}\nTransaction ID : {w['tx_id']}\n\n❌ Rejected by @{update.effective_user.username or uid} at {w['updated_at']}")
            try: await q.edit_message_text(new_text)
            except: pass
        return cancel_state(context)
        
    context.user_data['edit_msg_id'] = mid
    async def reply_admin_cb(text, markup=None): await safe_edit_or_send(context, uid, mid, text, markup, use_font=True)
        
    try:
        if d == "adm_main": await render_admin_panel(update, context)
        elif d == "admg_main": await render_admg_main(uid, mid, context)
        elif d == "adm_pchannel":
            pchan = get_set(db, 'payout_channel', 'Not Set')
            txt = f"📢 **Payout Channel Configuration**\n\n📌 **Current Channel:**\n{pchan}\n\n👇 Click the button below to set or change your payout channel."
            kb = [[InlineKeyboardButton("🔄 Change Payout Channel", callback_data="adm_pch_change")], [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]
            await reply_admin_cb(txt, InlineKeyboardMarkup(kb))
        elif d == "adm_pch_change":
            context.user_data['state'] = "WAITING_PCHAN_NEW"
            await reply_admin_cb("📢 **Change Payout Channel**\n\nPlease send the exact channel username you want to set as your payout channel.\n\n💡 Example: `@YourChannel`", get_back_btn("adm_pchannel"))
        elif d == "adm_bstatus":
            curr = get_set(db, 'bot_status')
            db['settings']['bot_status'] = 'Off' if curr == 'Active' else 'Active'
            write_db(db)
            await render_admin_panel(update, context)
        elif d == "adm_wdtgl":
            curr = get_set(db, 'withdraw_status', 'Both On')
            order = ['Both On', 'Both Off', 'Upi Only', 'Wallet Only']
            idx = order.index(curr) if curr in order else 0
            db['settings']['withdraw_status'] = order[(idx + 1) % len(order)]
            write_db(db)
            await render_admin_panel(update, context)
        elif d == "admw_main": await render_admw_main(uid, mid, context)
        elif d == "admw_api_st": await render_admw_api_st(uid, mid, context)
        elif d == "awapi_add":
            context.user_data['state'] = "WAITING_AWAPI_URL"
            await reply_admin_cb("🔌 **Add API Gateway**\n\nPlease enter the API URL for the new withdrawal gateway.", get_back_btn("admw_api_st"))
        elif d.startswith("awapi_del_"):
            dom = d.split("_", 2)[2]
            db['api_withdraw_gateways'] = [x for x in db['api_withdraw_gateways'] if x['domain'] != dom]
            write_db(db)
            await render_admw_api_st(uid, mid, context)
            return
        elif d.startswith("awapi_vw_"):
            dom = d.split("_", 2)[2]
            g = next((x for x in db['api_withdraw_gateways'] if x['domain'] == dom), None)
            if not g: return await q.answer(to_font("Not found"), show_alert=True)
            link = g.get('link', 'Not Available')
            txt = f"🔌 **Gateway Name:** `{g['domain']}`\n🔗 **Link:** `{link}`\n🔑 **API URL:** `{g['url']}`"
            await reply_admin_cb(txt, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admw_api_st")]]))
        elif d == "admw_reqs":
            reqs = [w for w in db['withdrawals'] if w['status'] in ['⏳ Pending', 'PENDING']]
            reqs.sort(key=lambda x: x['id'], reverse=True)
            reqs = reqs[:8]
            kb = [[InlineKeyboardButton(f"#{r['id']} | ₹{r['amount']} | U:{r['user_id']}", callback_data=f"awd_vw_{r['id']}")] for r in reqs]
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="admw_main")])
            await reply_admin_cb(f"📋 **Pending Withdrawals**", InlineKeyboardMarkup(kb))
        elif d.startswith("awd_vw_"):
            wid = int(d.split("_")[2])
            r = next((w for w in db['withdrawals'] if w['id'] == wid), None)
            if not r: return await q.answer(to_font("Not found"), show_alert=True)
            txt = f"📤 **Request #{r['id']}**\n👤 User: {r['user_id']}\n💳 GW: {r['gateway']}\n💰 Amt: ₹{r['amount']}\n💸 Tax: ₹{r['tax']}\n💵 Final: ₹{r['final_amount']}\n📝 Details: `{r['payout_details']}`\n📌 Status: {r['status']}\n🆔 TX: {r['tx_id']}"
            kb = []
            if r['status'] in ['⏳ Pending', 'PENDING']:
                kb.append([InlineKeyboardButton("✅ Approve", callback_data=f"awd_ac_app_{wid}"), InlineKeyboardButton("❌ Reject", callback_data=f"awd_ac_rej_{wid}")])
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="admw_reqs")])
            await reply_admin_cb(txt, InlineKeyboardMarkup(kb))
        elif d.startswith("awd_ac_"):
            _, _, action, wid = d.split("_")
            wid = int(wid)
            r = next((w for w in db['withdrawals'] if w['id'] == wid), None)
            if not r or r['status'] not in ['⏳ Pending', 'PENDING']: return await reply_admin_cb("❌ Already processed.", get_back_btn("admw_reqs"))
            if action == "rej":
                context.user_data['state'] = f"WAITING_WREJ_{wid}"
                return await reply_admin_cb("✏️ **Reject Withdrawal**\n\nEnter the rejection reason (or type 'none'):", get_back_btn(f"awd_vw_{wid}"))
            if action == "app":
                r['status'] = '✅ Approved'
                r['admin_note'] = f"By {uid}"
                r['updated_at'] = str(datetime.now())[:19]
                tx = next((t for t in db['transactions'] if t['tx_id'] == r['tx_id']), None)
                if tx: tx['status'] = '✅ Successful'
                write_db(db)
                try: await context.bot.send_message(r['user_id'], f"✅ Your Withdrawal Request Of ₹{r['amount']} Has Been Approved!")
                except: pass
                await reply_admin_cb(f"✅ Marked as Approved.", get_back_btn("admw_reqs"))
        elif d == "adm_theme":
            txt = "🎨 **Customize Bot Theme**\n\nSelect an interface element to customize:"
            kb = [
                [InlineKeyboardButton("Bot off text", callback_data="adm_theme_botoff")], [InlineKeyboardButton("Withdraw off text", callback_data="adm_theme_wdoff")],
                [InlineKeyboardButton("Support message", callback_data="adm_theme_support")], [InlineKeyboardButton("Welcome message", callback_data="adm_theme_welcome"), InlineKeyboardButton("Bot keyboard", callback_data="adm_theme_kbd")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]
            ]
            await reply_admin_cb(txt, InlineKeyboardMarkup(kb))
        elif d in ["adm_theme_botoff", "adm_theme_wdoff", "adm_theme_support", "adm_theme_welcome"]:
            state_map = {"adm_theme_botoff": ("WAITING_THEME_BOTOFF", "Bot Offline Text", "bot_off_text"), "adm_theme_wdoff": ("WAITING_THEME_WDOFF", "Withdraw Offline Text", "withdraw_off_text"), "adm_theme_support": ("WAITING_THEME_SUPPORT", "Support Message", "support_text"), "adm_theme_welcome": ("WAITING_THEME_WELCOME", "Welcome Message", "welcome_message")}
            state, title, key = state_map[d]
            context.user_data['state'] = state
            curr = get_set(db, key, "Not configured")
            await reply_admin_cb(f"🎨 **Edit {title}**\n\n**Current Value:**\n`{curr}`\n\n✏️ Please send the new text message:", get_back_btn("adm_theme"))
        elif d == "adm_theme_kbd": await render_kb_settings(uid, mid, context)
        elif d.startswith("kb_up_") or d.startswith("kb_dn_"):
            idx = int(d.split("_")[2])
            kb_data = get_set(db, 'keyboard')
            if d.startswith("kb_up_") and idx > 0: kb_data[idx], kb_data[idx-1] = kb_data[idx-1], kb_data[idx]
            elif d.startswith("kb_dn_") and idx < len(kb_data) - 1: kb_data[idx], kb_data[idx+1] = kb_data[idx+1], kb_data[idx]
            db['settings']['keyboard'] = kb_data
            write_db(db)
            await render_kb_settings(uid, mid, context)
        elif d.startswith("kb_ed_"):
            idx = int(d.split("_")[2])
            context.user_data['state'] = f"WAITING_KB_EDIT_{idx}"
            await reply_admin_cb("✏️ Please send the new text for this button.", get_back_btn("adm_theme_kbd"))
        elif d in ["adm_gtax", "adm_putax"]:
            mapping = {"adm_gtax": ("WAITING_GTAX", "withdraw_tax", "Global Withdraw Tax %"), "adm_putax": ("WAITING_PUTAX", "pay_to_user_tax", "Pay to User Tax %")}
            state, key, name = mapping[d]
            context.user_data['state'] = state
            await reply_admin_cb(f"⚙️ **Configure Tax Settings**\n\n**Current {name}:** `{get_set(db, key)}%`\n\n✏️ Send the new tax percentage:", get_back_btn("adm_main"))
        elif d == "adm_setref":
            context.user_data['state'] = "WAITING_SETREF"
            await reply_admin_cb("💰 **Referral Amount Configuration**\n\n👥 Enter the exact amount users will earn for every successful referral.", get_back_btn("adm_main"))
        elif d in ["adm_nunotify"]:
            opts = {"adm_nunotify": ("new_user_notify", ['Active', 'Off'])}
            key, choices = opts[d]
            kb = [[InlineKeyboardButton(c, callback_data=f"set_{key}_{c}")] for c in choices]
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
            await reply_admin_cb(f"⚙️ **Settings Configuration**\n\nCurrent Status: `{get_set(db, key)}`\n\nSelect a new value below:", InlineKeyboardMarkup(kb))
        elif d.startswith("set_"):
            parts = d.split("_", 2)
            raw = d[4:]
            for k in ["new_user_notify"]:
                if raw.startswith(k):
                    db['settings'][k] = raw[len(k)+1:]
                    write_db(db)
                    break
            await render_admin_panel(update, context)
        elif d == "adm_admins":
            adms = db['admins']
            kb = []
            for a in adms:
                if a == MAIN_ADMIN_ID: kb.append([InlineKeyboardButton(f"👑 Main Owner: {a}", callback_data="ignore")])
                else: kb.append([InlineKeyboardButton(f"👤 Admin: {a}", callback_data="ignore"), InlineKeyboardButton("❌", callback_data=f"adm_rema_{a}")])
            kb.append([InlineKeyboardButton("➕ Add New Admin", callback_data="adm_adda_btn")])
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="adm_main")])
            await reply_admin_cb("👑 **Manage Bot Administrators**\n\nCurrent Admins:\n\n", InlineKeyboardMarkup(kb))
        elif d == "adm_adda_btn":
            context.user_data['state'] = "WAITING_ADDA"
            await reply_admin_cb("👑 **Add New Admin**\n\nPlease send the Telegram User ID(s) you wish to promote to admin.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_admins")]]))
        elif d.startswith("adm_rema_"):
            target_id = int(d.replace("adm_rema_", ""))
            if target_id == MAIN_ADMIN_ID: return await q.answer(to_font("Cannot remove Master Admin"), show_alert=True)
            if target_id in db['admins']:
                db['admins'].remove(target_id)
                write_db(db)
                await render_admin_panel(update, context)
            return
        elif d == "adm_banusers": await render_adm_banusers(uid, mid, context)
        elif d == "adm_ban_btn":
            context.user_data['state'] = "WAITING_BANU"
            await reply_admin_cb("🚫 **Ban Users**\n\nPlease send the Telegram User ID(s) you wish to ban from the bot.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_banusers")]]))
        elif d.startswith("utgl_unban_"):
            target = int(d.split("_")[2])
            user = next((u for u in db['users'] if u['user_id'] == target), None)
            if user:
                user['is_banned'] = 0
                write_db(db)
            await render_adm_banusers(uid, mid, context)
            return
        elif d == "adm_bwallet": await render_adm_bwallet(uid, mid, context)
        elif d == "adm_bwallet_add":
            context.user_data['state'] = "WAITING_BAN_WALLET"
            await reply_admin_cb("🚫 **Ban Wallet**\n\nPlease send the exact 10-digit wallet ID or UPI ID you wish to permanently block.", get_back_btn("adm_bwallet"))
        elif d.startswith("bwd_"):
            wid = d.split("_", 1)[1]
            db['banned_wallets'] = [x for x in db['banned_wallets'] if x != wid]
            write_db(db)
            await render_adm_bwallet(uid, mid, context)
            return
        elif d == "adm_addbal":
            context.user_data['state'] = "WAITING_ADDBAL_MULTI"
            await reply_admin_cb("➕ **Add User Balance**\n\nPlease send the exact User ID and the Amount to add.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_main")]]))
        elif d == "adm_rembal":
            context.user_data['state'] = "WAITING_REMBAL_MULTI"
            await reply_admin_cb("➖ **Remove User Balance**\n\nPlease send the exact User ID and the Amount to deduct.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_main")]]))
        elif d == "adm_talk":
            context.user_data['state'] = "WAITING_TALK_ID"
            await reply_admin_cb("💬 **Talk With User**\n\nPlease strictly enter the Telegram User ID you wish to message directly:", get_back_btn("adm_main"))
        elif d == "adm_broadcast":
            context.user_data['state'] = "WAITING_BCAST_MSG"
            await reply_admin_cb("📢 **Broadcast Message**\n\nPlease forward or send the text, photo, or media file you wish to broadcast to all registered bot users:", get_back_btn("adm_main"))
        elif d == "adm_rstbal":
            kb = [[InlineKeyboardButton("✅ Confirm Total Reset", callback_data="adm_rstbal_cnf")], [InlineKeyboardButton("❌ Cancel", callback_data="adm_main")]]
            await reply_admin_cb("⚠️ **Reset All User Balances**\n\nAre you absolutely sure you want to completely clear and reset the balance of ALL users to ₹0?", InlineKeyboardMarkup(kb))
        elif d == "adm_rstbal_cnf":
            for u in db['users']:
                if u['balance'] > 0:
                    bal = u['balance']
                    u['balance'] = 0
                    tx_id = gen_txid()
                    db['transactions'].append({"id": db['auto_inc']['transactions'], "tx_id": tx_id, "user_id": u['user_id'], "related_user": uid, "amount": -bal, "type": "➖ Balance Removed", "desc": "Global Balance Reset", "bal_before": bal, "bal_after": 0, "status": "✅ Successful", "created_at": str(datetime.now())[:19]})
                    db['auto_inc']['transactions'] += 1
            write_db(db)
            await q.answer(to_font("All user balances have been securely reset to 0!"), show_alert=True)
            await render_admin_panel(update, context)
            return
        elif d == "adm_findu":
            context.user_data['state'] = "WAITING_FINDU"
            await reply_admin_cb("🚹 **Find User Details**\n\nPlease enter the exact Telegram User ID to fetch their account statistics:", get_back_btn("adm_main"))
        elif d == "adm_vuser":
            context.user_data['state'] = "WAITING_VUSER"
            await reply_admin_cb("🕵️ **Verify User**\n\nPlease enter the exact Telegram User ID you wish to verify or unverify:", get_back_btn("adm_main"))
        elif d.startswith("utgl_"):
            _, action, target = d.split("_")
            target = int(target)
            u = next((x for x in db['users'] if x['user_id'] == target), None)
            if u:
                if action == "v": u['verified'] = 0 if u['verified'] else 1
                if action == "w": u['wallet_banned'] = 0 if u['wallet_banned'] else 1
                if action == "b": u['is_banned'] = 0 if u['is_banned'] else 1
                write_db(db)
            await reply_admin_cb(f"✅ **Update Complete**\n\nSettings for User `{target}` have been successfully updated.", get_back_btn("adm_main"))
        elif d == "adm_stats":
            tot_u = len(db['users'])
            ban_u = sum(1 for u in db['users'] if u.get('is_banned'))
            tot_b = sum(u['balance'] for u in db['users'])
            w_pd = sum(w['final_amount'] for w in db['withdrawals'] if w['status'] == '💰 Paid')
            w_pd_c = sum(1 for w in db['withdrawals'] if w['status'] == '💰 Paid')
            w_pn_c = sum(1 for w in db['withdrawals'] if w['status'] == '⏳ Pending')
            txt = f"📊 **BOT STATISTICS**\n\n👥 Total Users: {tot_u}\n🚫 Banned Users: {ban_u}\n💰 Total User Balances: ₹{tot_b:.2f}\n\n📤 Total Withdrawals Paid: ₹{w_pd} ({w_pd_c} Requests)\n⏳ Total Pending Requests: {w_pn_c}"
            await reply_admin_cb(txt, get_back_btn("adm_main"))
        elif d == "adm_channels": await render_channels_panel(uid, mid, context)
        elif d == "adm_ch_addchk":
            context.user_data['state'] = "WAITING_CHADD_CHK"
            await reply_admin_cb("➕ **Add Checked Channel**\n\n🔎 Please send the channel username.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_channels")]]))
        elif d == "adm_ch_adduchk":
            context.user_data['state'] = "WAITING_CHADD_UCHK"
            await reply_admin_cb("➕ **Add Unchecked Channel**\n\n🔎 Please send the channel username.", InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="adm_channels")]]))
        elif d.startswith("chv_"):
            channel = d[4:]
            ch_data = next((c for c in db['channels'] if c['channel_id'] == channel), None)
            if not ch_data: return await q.answer(to_font("Channel not found"), show_alert=True)
            txt = f"📢 **Channel Information**\n\n**Username:** `{channel}`\n**Type Status:** {'✅ Checked' if ch_data['type']=='checked' else '🔘 Unchecked'}"
            await reply_admin_cb(txt, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_channels")]]))
        elif d.startswith("chd_"):
            channel = d[4:]
            db['channels'] = [c for c in db['channels'] if c['channel_id'] != channel]
            write_db(db)
            await q.answer(to_font(f"Removed Channel: {channel}"), show_alert=False)
            await render_channels_panel(uid, mid, context)
            return
        elif d == "adm_gc_main": await render_adm_gc_main(uid, mid, context)
        elif d.startswith("gft_st_"):
            code = d.split("_")[2]
            c = next((x for x in db['gift_codes'] if x['code'] == code), None)
            if not c: return await q.answer(to_font("Not found"), show_alert=True)
            txt = f"🎁 **Gift Code Statistics**\n\n**Code Hash:** `{c['code']}`\n💰 **Bonus Provided:** ₹{c['amount']}\n👥 **Maximum Limit:** {c['usage_limit']}\n✅ **Total Claimed:** {c['used_count']}\n💵 **Min Balance Requirement:** ₹{c.get('req_balance', 0)}"
            await reply_admin_cb(txt, InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_gc_main")]]))
        elif d.startswith("gft_del_"):
            code = d.split("_")[2]
            c = next((x for x in db['gift_codes'] if x['code'] == code), None)
            if c:
                unclaimed = c['usage_limit'] - c['used_count']
                refund = unclaimed * c['amount']
                if refund > 0: record_tx(db, MAIN_ADMIN_ID, refund, '↩️ Gift Code Refund', f'Deleted code {code}')
                db['gift_codes'] = [x for x in db['gift_codes'] if x['code'] != code]
                write_db(db)
            await render_adm_gc_main(uid, mid, context)
            return
        elif d == "gft_add":
            context.user_data['state'] = "WAITING_GFTC_CREATE_ADM"
            await reply_admin_cb("🎁 **Gift Code Creation**\n\nPlease enter the code configuration strictly in this format: `Bonus-Limit--Balance`", get_back_btn("adm_gc_main"))
        elif d == "adm_bgc_main": await render_adm_bgc_main(uid, mid, context)
        elif d == "bgc_add":
            context.user_data['state'] = "WAITING_BGC_ADD"
            await reply_admin_cb("🛍️ **Add Buyable Gift Card**\n\nPlease enter details strictly in this format: `Platform-Code--Amount`", get_back_btn("adm_bgc_main"))
        elif d.startswith("bgcvw_"):
            cid = int(d.split("_")[1])
            c = next((x for x in db['buyable_gift_cards'] if x['id'] == cid), None)
            if not c: return await q.answer(to_font("Not found"), show_alert=True)
            txt = f"🛍️ **Gift Card Database Details**\n\n**Platform Source:** {c['platform']}\n**Code String:** `{c['code']}`\n**Face Value:** ₹{c['amount']}"
            await reply_admin_cb(txt, get_back_btn("adm_bgc_main"))
        elif d.startswith("bgcdel_"):
            cid = int(d.split("_")[1])
            db['buyable_gift_cards'] = [x for x in db['buyable_gift_cards'] if x['id'] != cid]
            write_db(db)
            await render_adm_bgc_main(uid, mid, context)
            return
        elif d == "admt_main": await render_admt_main(uid, mid, context)
        elif d.startswith("tsk_vw_"):
            tid = int(d.split("_")[2])
            t = next((x for x in db['tasks'] if x['id'] == tid), None)
            if not t: return await q.answer(to_font("Task not found"), show_alert=True)
            txt = f"🆔 **Task Reference ID:** TSK{t['id']}\n📄 **Task Title:** {t['title']}\n💰 **Reward Amount:** ₹{t['reward']}\n\n📝 **Detailed Instructions:**\n{t['description']}"
            await reply_admin_cb(txt, get_back_btn("admt_main"))
        elif d.startswith("tsk_del_"):
            tid = int(d.split("_")[2])
            db['tasks'] = [t for t in db['tasks'] if t['id'] != tid]
            write_db(db)
            await render_admt_main(uid, mid, context)
            return
        elif d == "tsk_add":
            context.user_data['state'] = "WAITING_TSK_TITLE"
            await reply_admin_cb("📝 **Task Creation: Step 1 (Title)**\n\nPlease enter a clear and concise title for the new task.", get_back_btn("admt_main"))
        elif d == "adm_addf_main": await render_addf_gateways_panel(uid, mid, context)
        elif d == "adf_add_btn":
            context.user_data['state'] = "WAITING_ADF_NAME"
            await reply_admin_cb("🔗 **Add Fund Gateway Configuration**\n\n✏️ Please enter the display name for the new Gateway", get_back_btn("adm_addf_main"))
        elif d.startswith("adf_vw_"):
            gname = d.split("_", 2)[2]
            gw = next((x for x in db['add_fund_gateways'] if x['name'] == gname), None)
            if gw:
                txt = f"🔗 **Gateway Configuration Info**\n\n🏷 **Gateway Name:** `{gw['name']}`\n👛 **Wallet ID Output:** `{gw['wallet_id']}`\n💰 **Limits:** Min ₹{gw['min_amt']} | Max ₹{gw['max_amt']}\n🧾 **Applied Tax:** {gw['tax']}%"
                await reply_admin_cb(txt, get_back_btn("adm_addf_main"))
        elif d.startswith("adf_del_"):
            gname = d.split("_", 2)[2]
            db['add_fund_gateways'] = [x for x in db['add_fund_gateways'] if x['name'] != gname]
            write_db(db)
            await render_addf_gateways_panel(uid, mid, context)
            return
        elif d.startswith("admaf_"):
            action = d.split("_")[1]
            req_id = int(d.split("_")[2])
            req = next((x for x in db['add_fund_requests'] if x['id'] == req_id), None)
            if not req or req['status'] != 'PENDING': return await q.edit_message_caption(to_font("❌ Request has already been processed."))
            cap = q.message.caption or ""
            if action == "app":
                context.user_data['state'] = f"WAITING_ADDF_APP_{req_id}"
                await q.edit_message_caption(to_font(cap + "\n\n✏️ **Action Required:** Enter the exact amount you wish to credit."), parse_mode='Markdown')
            elif action == "rej":
                context.user_data['state'] = f"WAITING_ADDF_REJ_{req_id}"
                await q.edit_message_caption(to_font(cap + "\n\n✏️ **Action Required:** Enter your specific reason for rejecting this add fund request."), parse_mode='Markdown')
        elif d.startswith("admsub_"):
            _, action, sub_id = d.split("_")
            sub_id = int(sub_id)
            sub = next((x for x in db['task_submissions'] if x['id'] == sub_id), None)
            if not sub or sub['status'] != 'PENDING': return await q.edit_message_caption(to_font("❌ Task submission has already been processed."))
            tsk = next((x for x in db['tasks'] if x['id'] == sub['task_id']), None)
            base_cap = f"🔔 **New Task Submission Alert**\n👤 **User ID:** {sub['user_id']}\n🆔 **Task Reference:** TSK{sub['task_id']}"
            if action == "app":
                sub['status'] = 'APPROVED'
                record_tx(db, sub['user_id'], tsk['reward'], '➕ Task Reward', f"Approved TSK{sub['task_id']}")
                write_db(db)
                try: await context.bot.send_message(sub['user_id'], f"🎉 **Task Approved!**\n\nYour submission for **{tsk['title']}** (TSK{sub['task_id']}) was successful.\n💰 **₹{tsk['reward']}** has been credited to your balance.", parse_mode='Markdown')
                except: pass
                await q.edit_message_caption(to_font(base_cap + "\n\n✅ **STATUS: APPROVED**"), parse_mode='Markdown')
            elif action == "rej":
                sub['status'] = 'REJECTED'
                write_db(db)
                try: await context.bot.send_message(sub['user_id'], f"❌ **Task Rejected**\n\nUnfortunately, your task submission for TSK{sub['task_id']} was rejected by the admin team.", parse_mode='Markdown')
                except: pass
                await q.edit_message_caption(to_font(base_cap + "\n\n❌ **STATUS: REJECTED**"), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Admin CB Err: {e}")
    finally:
        try: await q.answer()
        except: pass

async def handle_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    d = q.data
    mid = q.message.message_id
    db = read_db()

    try:
        if d == "usr_claim_join":
            all_joined = True
            for ch in db['channels']:
                ch_id = ch['channel_id']
                if ch['type'] == 'unchecked':
                    if not any(uc['user_id'] == uid and uc['channel_id'] == ch_id for uc in db['user_channels']):
                        db['user_channels'].append({"user_id": uid, "channel_id": ch_id})
                else:
                    try:
                        member = await context.bot.get_chat_member(chat_id=ch_id, user_id=uid)
                        if member.status not in ['creator', 'administrator', 'member', 'restricted']: all_joined = False
                    except: all_joined = False
            write_db(db)
            if all_joined:
                try: await q.message.delete()
                except: pass
                wmsg = get_set(db, 'welcome_message', "✨ Welcome To Tasks Payment Bot")
                await context.bot.send_message(uid, wmsg, reply_markup=get_main_keyboard())
            else: await q.answer("❌ You haven't joined all channels! Please join and click Claim.", show_alert=True)
            return
            
        if not await check_user(update, context): return
        
        if d.startswith("usr_apiwd_"):
            dom = d.split("_", 2)[2]
            gw = next((x for x in db['api_withdraw_gateways'] if x['domain'] == dom), None)
            if not gw: return await q.answer("Gateway error", show_alert=True)
            addr = context.user_data.get('wd_addr')
            amt = context.user_data.get('wd_amt')
            if not addr or not amt: return await q.edit_message_text("❌ Session expired. Try again.")
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            if u['balance'] < amt: return await q.edit_message_text("❌ Insufficient balance!")
            url = gw['url'].replace("{wallet}", str(addr)).replace("{number}", str(addr)).replace("{amount}", str(amt))
            try:
                res = await asyncio.to_thread(call_gateway_api, url)
                success = "success" in res.lower()
            except Exception as e:
                res = str(e)
                success = False
            if success:
                record_tx(db, uid, -amt, '🏧 API Withdrawal', f"To {dom}", related_user=None)
                write_db(db)
                await q.edit_message_text(f"💸 Your Withdrawal Paid Successfully !! 💸\n\n🎉 Please Check Your {dom} Account 🎉")
            else:
                await q.edit_message_text(f"🙇‍♂️ Withdraw Processing Failed !!\n\n⚠️ Failed Reason :- {res[:100]}\n\n🥳 Balance Returned Back To Your Account.")
            return
        elif d == "usr_acc_back":
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            t = f"🚀 Wallet Summary\n\n👤 User ID -> {uid}\n💸 Balance : ₹{u['balance']:.2f}\n🔐 Keeper Balance : ₹0.00\n\n👉 Hit '🪪 Balance Records' to open your Keeper vault"
            kb = [
                [InlineKeyboardButton("➕ Add Fund", callback_data="usr_addf")],
                [InlineKeyboardButton("🪪 Balance Records", callback_data="usr_tx_page_0"), InlineKeyboardButton("📄 Withdrawal History", callback_data="usr_wd_page_0")],
                [InlineKeyboardButton("📞 Support", callback_data="usr_support")]
            ]
            await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup(kb))
        elif d == "usr_support":
            st = get_set(db, 'support_text', 'Not configured')
            t = st if st.strip() else 'Not configured'
            kb = [[InlineKeyboardButton("⬅️ Go Back", callback_data="usr_acc_back")]]
            await q.edit_message_text(t, reply_markup=InlineKeyboardMarkup(kb))
        elif d.startswith("usr_tx_page_"):
            page = int(d.split("_")[3])
            limit = 5
            offset = page * limit
            txs = [t for t in db['transactions'] if t['user_id'] == uid]
            txs.sort(key=lambda x: x['id'], reverse=True)
            total = len(txs)
            page_txs = txs[offset:offset+limit]
            if not page_txs and page == 0: txt = "💳 **Transaction History**\n\nNo transactions found."
            else:
                txt = "💳 **Transaction History**\n\n"
                for t in page_txs:
                    icon = "🟢" if t['amount'] > 0 else "🔴"
                    amt = f"+₹{t['amount']}" if t['amount'] > 0 else f"-₹{abs(t['amount'])}"
                    txt += f"{icon} {amt}\n📌 {t['type']}: {t['desc']}\n🆔 `{t['tx_id']}`\n🕐 {t['created_at'][:16]}\n\n"
            kb = []
            nav = []
            if page > 0: nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"usr_tx_page_{page-1}"))
            nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"usr_tx_page_{page}"))
            if (offset + limit) < total: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"usr_tx_page_{page+1}"))
            if nav: kb.append(nav)
            kb.append([InlineKeyboardButton("⬅️ Go Back", callback_data="usr_acc_back")])
            await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        elif d.startswith("usr_wd_page_"):
            page = int(d.split("_")[3])
            limit = 5
            offset = page * limit
            wds = [w for w in db['withdrawals'] if w['user_id'] == uid]
            wds.sort(key=lambda x: x['id'], reverse=True)
            total = len(wds)
            page_wds = wds[offset:offset+limit]
            if not page_wds and page == 0: txt = "🏧 **Withdrawal History**\n\nNo withdrawals found."
            else:
                txt = "🏧 **Withdrawal History**\n\n"
                for w in page_wds: txt += f"#{w['id']}\n💰 Amount: ₹{w['amount']}\n💳 Gateway: {w['gateway']}\n📌 Status: {w['status']}\n🆔 TX: `{w['tx_id']}`\n🕐 {w['created_at'][:16]}\n\n"
            kb = []
            nav = []
            if page > 0: nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"usr_wd_page_{page-1}"))
            nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"usr_wd_page_{page}"))
            if (offset + limit) < total: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"usr_wd_page_{page+1}"))
            if nav: kb.append(nav)
            kb.append([InlineKeyboardButton("⬅️ Go Back", callback_data="usr_acc_back")])
            await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        elif d == "usr_gc_main": await render_usr_gc_main(uid, mid, context)
        elif d.startswith("ugft_st_"):
            code = d.split("_")[2]
            c = next((x for x in db['gift_codes'] if x['code'] == code), None)
            if not c: return await q.answer("Not found", show_alert=True)
            txt = f"🎁 **Gift Code Statics**\n\nCode: `{c['code']}`\n💰 Bonus: ₹{c['amount']}\n👥 Limit: {c['usage_limit']}\n✅ Claimed: {c['used_count']}\n💵 Min Balance Needed: ₹{c.get('req_balance', 0)}"
            await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="usr_gc_main")]]))
        elif d.startswith("ugft_del_"):
            code = d.split("_")[2]
            c = next((x for x in db['gift_codes'] if x['code'] == code and x['creator_id'] == uid), None)
            if c:
                unclaimed = c['usage_limit'] - c['used_count']
                refund = unclaimed * c['amount']
                if refund > 0: record_tx(db, uid, refund, '↩️ Gift Code Refund', f'Deleted code {code}')
                db['gift_codes'] = [x for x in db['gift_codes'] if x['code'] != code]
                write_db(db)
                await q.answer(f"✅ Code deleted. ₹{refund} refunded.", show_alert=True)
            await render_usr_gc_main(uid, mid, context)
            return
        elif d == "usr_gc_create":
            context.user_data['state'] = "WAITING_GC_CREATE_USR"
            await context.bot.send_message(uid, "🎁 **Gift Code Creation**\n\nPlease enter the details in this format: `Bonus-Limit--Balance`\n\nExample: `10-20--0` (₹10 Bonus, 20 Users, ₹0 Min Balance)", reply_markup=get_cancel_keyboard())
        elif d == "usr_gc_claim":
            context.user_data['state'] = "WAITING_GC_CLAIM"
            await context.bot.send_message(uid, "💸 Send Gift Code To Claim Reward!", reply_markup=get_cancel_keyboard())
        elif d == "usr_addf":
            gws = db['add_fund_gateways']
            if not gws: return await q.answer("❌ Add Fund disabled (No gateways configured).", show_alert=True)
            txt = "💳 **Manual Add Fund Option**\n\n💳 **Available Add Fund Gateways:**\n\n"
            for g in gws: txt += f"• {g['name']}\n  Wallet ID: `{g['wallet_id']}`\n  Min: ₹{g['min_amt']} | Max: ₹{g['max_amt']} | Tax: {g['tax']}%\n"
            txt += "\n📸 Please send your payment screenshot with caption including:\nUTR: 1234567890 ₹10\nOR\nWallet: Payzy Wallet ₹1"
            context.user_data['state'] = "WAITING_ADDF_SCREENSHOT"
            context.user_data['edit_msg_id'] = mid
            await q.edit_message_text(txt, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="usr_acc_back")]]))
        elif d.startswith("usr_bgc_p_"):
            plat = d.split("_", 3)[3]
            cards = set([c['amount'] for c in db['buyable_gift_cards'] if c['platform'] == plat and c['is_bought'] == 0])
            kb = []
            row = []
            for a in cards:
                row.append(InlineKeyboardButton(f"₹{a}", callback_data=f"usr_bgc_a_{plat}_{a}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="usr_bgc_back")])
            await q.edit_message_text(f"🎁 Platform: {plat}\nSelect Amount:", reply_markup=InlineKeyboardMarkup(kb))
        elif d.startswith("usr_bgc_a_"):
            parts = d.split("_")
            plat = parts[3]
            amt = float(parts[4])
            kb = [[InlineKeyboardButton("✅ Confirm Buy", callback_data=f"usr_bgc_c_{plat}_{amt}"), InlineKeyboardButton("❌ Cancel", callback_data="usr_bgc_back")]]
            await q.edit_message_text(f"🛍️ You are about to buy a {plat} gift card worth ₹{amt}.\n\nProceed?", reply_markup=InlineKeyboardMarkup(kb))
        elif d.startswith("usr_bgc_c_"):
            parts = d.split("_")
            plat = parts[3]
            amt = float(parts[4])
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            if u['balance'] < amt: return await q.edit_message_text("❌ Insufficient balance!")
            card = next((c for c in db['buyable_gift_cards'] if c['platform'] == plat and c['amount'] == amt and c['is_bought'] == 0), None)
            if not card: return await q.edit_message_text("❌ Sorry, this gift card just went out of stock.")
            
            card['is_bought'] = 1
            card['bought_by'] = uid
            card['bought_at'] = str(datetime.now())[:19]
            record_tx(db, uid, -amt, '🛒 Buy Gift Card', f"Bought {plat} ₹{amt}")
            write_db(db)
            await q.edit_message_text(f"✅ Withdrawal Successful!\n\n📱 Platform :- {plat}\n🎁 Your Gift Code: `{card['code']}`\n💰 Amount: {amt}\n💸 Deducted: {amt}\n\n🎉 You can redeem this gift code anytime!", parse_mode='Markdown')
        elif d == "usr_bgc_back":
            plats = set([c['platform'] for c in db['buyable_gift_cards'] if c['is_bought'] == 0])
            if not plats: return await q.edit_message_text("🚫 No gift card available right now.")
            kb = []
            row = []
            for p in plats:
                row.append(InlineKeyboardButton(p, callback_data=f"usr_bgc_p_{p}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            await q.edit_message_text("🎁 Select Platform:", reply_markup=InlineKeyboardMarkup(kb))
        elif d.startswith("usr_tsk_"):
            action = d.split("_")[2]
            if action == "back":
                tasks = [t for t in db['tasks'] if t['active'] == 1]
                if not tasks: return await q.edit_message_text("⚠️ No tasks available right now. Please check back later!")
                kb = [[InlineKeyboardButton(f"{t['title']} (₹{t['reward']})", callback_data=f"usr_tsk_view_{t['id']}")] for t in tasks]
                await q.edit_message_text("Available tasks:\n", reply_markup=InlineKeyboardMarkup(kb))
            elif action == "view":
                tid = int(d.split("_")[3])
                t = next((x for x in db['tasks'] if x['id'] == tid), None)
                if not t: return await q.answer("Task not found.")
                txt = f"🆔 Task ID: TSK{t['id']}\n📄 Title: {t['title']}\n💰 Reward: ₹{t['reward']}\n\n📝 Details: {t['description']}"
                kb = [[InlineKeyboardButton("📸 Submit Screenshot", callback_data=f"usr_tsk_sub_{t['id']}")], [InlineKeyboardButton("🔙 Back", callback_data="usr_tsk_back")]]
                await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
            elif action == "sub":
                tid = int(d.split("_")[3])
                has_submitted = any(s['task_id'] == tid and s['user_id'] == uid for s in db['task_submissions'])
                if has_submitted: return await q.answer("⚠️ You already submitted this task.", show_alert=True)
                t = next((x for x in db['tasks'] if x['id'] == tid), None)
                txt = f"📸 Submit Task Proof\n\nPlease send a photo as proof for your task completion.\n\n🆔 Task: TSK{t['id']}\n📄 {t['title']}\n💰 Reward: ₹{t['reward']}\n\nIf you change your mind, tap ❌ Cancel Submission below."
                kb = [[InlineKeyboardButton("❌ Cancel Submission", callback_data="usr_tsk_cnl")]]
                context.user_data['state'] = f"WAITING_TSK_PROOF_{t['id']}"
                context.user_data['edit_msg_id'] = mid
                await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
            elif action == "cnl":
                cancel_state(context)
                q.data = "usr_tsk_back"
                await handle_user_cb(update, context)
    except Exception as e:
        logger.error(f"User CB Err: {e}")
    finally:
        try: await q.answer()
        except: pass

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message
    txt = msg.text or msg.caption or ""

    if not txt and not (msg.photo or msg.video or msg.document or msg.audio or msg.animation or msg.voice or msg.sticker): return
    if not await check_user(update, context): return
        
    db = read_db()
    if txt == "❌ Cancel":
        cancel_state(context)
        return await msg.reply_text("Action cancelled.", reply_markup=get_main_keyboard())
        
    kb_data = get_set(db, 'keyboard')
    btn_map = {item['text']: item['id'] for item in kb_data}
    
    if txt in btn_map:
        action = btn_map[txt]
        if action == "tsk":
            cancel_state(context)
            tasks = [t for t in db['tasks'] if t['active'] == 1]
            if not tasks: return await msg.reply_text("⚠️ No tasks available right now. Please check back later!")
            kb = [[InlineKeyboardButton(f"{t['title']} (₹{t['reward']})", callback_data=f"usr_tsk_view_{t['id']}")] for t in tasks]
            return await msg.reply_text("Available tasks:\n", reply_markup=InlineKeyboardMarkup(kb))
        elif action == "acc":
            cancel_state(context)
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            t = f"🚀 Wallet Summary\n\n👤 User ID -> {uid}\n💸 Balance : ₹{u['balance']:.2f}\n🔐 Keeper Balance : ₹0.00\n\n👉 Hit '🪪 Balance Records' to open your Keeper vault"
            kb = [
                [InlineKeyboardButton("➕ Add Fund", callback_data="usr_addf")],
                [InlineKeyboardButton("🪪 Balance Records", callback_data="usr_tx_page_0"), InlineKeyboardButton("📄 Withdrawal History", callback_data="usr_wd_page_0")],
                [InlineKeyboardButton("📞 Support", callback_data="usr_support")]
            ]
            return await msg.reply_text(t, reply_markup=InlineKeyboardMarkup(kb))
        elif action == "wtd":
            cancel_state(context)
            wd_status = get_set(db, 'withdraw_status', 'Both On')
            if wd_status == 'Both Off': return await msg.reply_text(get_set(db, 'withdraw_off_text', '🚫 Withdraw is currently off'))
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            if u['balance'] < 1: return await msg.reply_text("⚠️ You Need Minimum ₹1 To Withdraw!")
            pchan = get_set(db, 'payout_channel')
            if not pchan or pchan == 'Not Set': return await msg.reply_text("⚠️ No payout channel added")
            gws_api = db['api_withdraw_gateways']
            g_upi = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
            upi_on = (g_upi and g_upi['enabled'] == 1) and wd_status in ['Both On', 'Upi Only']
            wal_on = wd_status in ['Both On', 'Wallet Only']
            if not upi_on and not wal_on: return await msg.reply_text("🚫 Withdraw is currently off")
            txt_msg = "👇🏻 Send Your UPI ID Or Wallet Number To Initiate Withdrawal:\n\n💳 Supported Wallet Platforms:\n"
            if wal_on:
                for ag in gws_api: txt_msg += f"{ag['domain']}\n"
            if upi_on: txt_msg += "UPI\n"
            context.user_data['state'] = "WAITING_WD_ADDRESS"
            return await msg.reply_text(txt_msg, reply_markup=get_cancel_keyboard())
        elif action == "ptu":
            context.user_data['state'] = "WAITING_PAY_USER"
            t = "🆔 Please enter the User ID(s) you want to pay:\n\n✅ You can send payments in two ways:\n\n💡 Multiple IDs with the same amount\n 👉 Example: `8522410574,7314163802 50`\n\n💡 Each ID with its own amount\n 👉 Example:\n`8522410574 50`\n`7314163802 100`"
            return await msg.reply_text(t, reply_markup=get_cancel_keyboard(), parse_mode='Markdown')
        elif action == "gfc":
            cancel_state(context)
            changed = False
            for c in list(db['gift_codes']):
                if c['used_count'] >= c['usage_limit']:
                    db['gift_codes'].remove(c)
                    changed = True
            if changed: write_db(db)
            codes = [c for c in db['gift_codes'] if c['is_active'] == 1 and c['creator_id'] == uid][:10]
            if not codes: t = "🔍 You don’t have any active Gift Codes.\n\nCreate your first one now!"
            else: t = "Here is your all gift codes\n\nCode. | Statics. | Delete"
            kb = []
            for c in codes:
                kb.append([
                    InlineKeyboardButton(f"{c['code']}", callback_data=f"ugft_st_{c['code']}"),
                    InlineKeyboardButton("📊", callback_data=f"ugft_st_{c['code']}"),
                    InlineKeyboardButton("❌", callback_data=f"ugft_del_{c['code']}")
                ])
            kb.append([InlineKeyboardButton("➕ Create gift code", callback_data="usr_gc_create")])
            kb.append([InlineKeyboardButton("🎁 Claim Gift Code", callback_data="usr_gc_claim")])
            return await msg.reply_text(t, reply_markup=InlineKeyboardMarkup(kb))
        elif action == "mgc":
            cancel_state(context)
            my_cards = [c for c in db['buyable_gift_cards'] if c['bought_by'] == uid]
            my_cards.sort(key=lambda x: x['bought_at'], reverse=True)
            if not my_cards: return await msg.reply_text("You haven't bought any gift cards yet.")
            t = f"🎉 Your Received Gift Codes\n\n📌 Total Codes: {len(my_cards)}\n\n"
            for i, c in enumerate(my_cards): t += f"{i+1}) `{c['code']}`\n 💰 Amount: {c['amount']}\n 🕒 Received: {c['bought_at']}\n\n"
            return await msg.reply_text(t, parse_mode='Markdown')
        elif action == "bgc":
            cancel_state(context)
            plats = set([c['platform'] for c in db['buyable_gift_cards'] if c['is_bought'] == 0])
            if not plats: return await msg.reply_text("🚫 No gift card available right now.")
            kb = []
            row = []
            for p in plats:
                row.append(InlineKeyboardButton(p, callback_data=f"usr_bgc_p_{p}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            return await msg.reply_text("🎁 Select Platform:", reply_markup=InlineKeyboardMarkup(kb))

    state = context.user_data.get('state')
    mid = context.user_data.get('edit_msg_id')
    if not state: return 

    def back_kbp(): return InlineKeyboardMarkup([[InlineKeyboardButton(to_font("🔙 Back"), callback_data="adm_main")]])

    async def reply_admin(text, markup=None):
        text = to_font(text)
        markup = font_markup(markup)
        try: await context.bot.delete_message(chat_id=uid, message_id=msg.message_id)
        except: pass
        try: await context.bot.edit_message_text(text, chat_id=uid, message_id=mid, reply_markup=markup, parse_mode='Markdown', disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e).lower():
                try: await context.bot.delete_message(chat_id=uid, message_id=mid)
                except: pass
                try:
                    res = await context.bot.send_message(chat_id=uid, text=text, reply_markup=markup, parse_mode='Markdown', disable_web_page_preview=True)
                    context.user_data['edit_msg_id'] = res.message_id
                except: pass

    try:
        if state == "WAITING_WD_ADDRESS":
            addr = txt.strip()
            if addr in db['banned_wallets']:
                if '@' in addr: return await msg.reply_text(f"🚫 {addr} this upi id is banned", reply_markup=get_cancel_keyboard())
                else: return await msg.reply_text(f"🚫 {addr} this wallet id is banned", reply_markup=get_cancel_keyboard())
                
            wd_status = get_set(db, 'withdraw_status', 'Both On')
            is_upi = '@' in addr
            
            if is_upi:
                if wd_status == 'Wallet Only': return await msg.reply_text("🚫 UPI withdraw is currently off", reply_markup=get_main_keyboard())
                g_upi = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
                if not g_upi or g_upi['enabled'] == 0: return await msg.reply_text("🚫 Upi withdraw is currently off", reply_markup=get_main_keyboard())
            elif len(addr) == 10 and addr.isdigit():
                if wd_status == 'Upi Only': return await msg.reply_text("🚫 Wallet withdraw is currently off", reply_markup=get_main_keyboard())
                if not db['api_withdraw_gateways']: return await msg.reply_text("⚠️ No API gateways active.", reply_markup=get_main_keyboard())
            else: return await msg.reply_text("❌ Invalid address. Send UPI ID or 10-digit wallet number.", reply_markup=get_cancel_keyboard())
                
            context.user_data['wd_addr'] = addr
            context.user_data['state'] = "WAITING_WD_AMOUNT"
            await msg.reply_text("👇🏻 Send The Amount To Withdraw", reply_markup=get_cancel_keyboard())
            
        elif state == "WAITING_WD_AMOUNT":
            try: amt = float(txt)
            except ValueError: return await msg.reply_text("❌ Invalid amount.")
            addr = context.user_data['wd_addr']
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            is_upi = '@' in addr
            if is_upi:
                g = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
                if amt < g['min_w'] or amt > g['max_w']: return await msg.reply_text(f"❌ Amount must be between ₹{g['min_w']} and ₹{g['max_w']}.")
                if u['balance'] < amt: return await msg.reply_text("❌ Insufficient balance.")
                tax_amt = (amt * g['tax']) / 100
                final_amt = amt - tax_amt
                record_tx(db, uid, -amt, '🏧 Withdrawal', f"Pending UPI WD", related_user=None)
                tx_id = gen_txid()
                wid = db['auto_inc']['withdrawals']
                db['withdrawals'].append({
                    "id": wid, "tx_id": tx_id, "user_id": uid, "username": update.effective_user.username,
                    "gateway": "UPI", "amount": amt, "tax": tax_amt, "final_amount": final_amt,
                    "payout_details": addr, "status": "⏳ Pending", "created_at": str(datetime.now())[:19], "updated_at": str(datetime.now())[:19]
                })
                db['auto_inc']['withdrawals'] += 1
                write_db(db)
                await msg.reply_text(f"🎉 Your Cashout Request Sent For Approval !\n\n🏧 Final Amount : ₹{final_amt}\n\n🔎 You Will Be Notified On Its Approval.\n\n🆔 Transaction ID : {tx_id}", reply_markup=get_main_keyboard())
                try:
                    pchan = get_set(db, 'payout_channel')
                    admin_txt = f"⚠️ New UPI Payout Request!\n\nUser : {uid}\nRequest Amount : ₹{amt}\nAmount After Tax ({g['tax']}) : ₹{final_amt}\nUPI ID : {mask_string(addr)}\nTransaction ID : {tx_id}"
                    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"ch_wd_app_{wid}"), InlineKeyboardButton("❌ Reject", callback_data=f"ch_wd_rej_{wid}")]]
                    admin_txt = to_font(admin_txt)
                    await context.bot.send_message(chat_id=pchan, text=admin_txt, reply_markup=font_markup(kb))
                except: pass
                cancel_state(context)
            else:
                context.user_data['wd_amt'] = amt
                gws_api = db['api_withdraw_gateways']
                kb = []
                row = []
                for ag in gws_api:
                    row.append(InlineKeyboardButton(ag['domain'], callback_data=f"usr_apiwd_{ag['domain']}"))
                    if len(row) == 2:
                        kb.append(row)
                        row = []
                if row: kb.append(row)
                await msg.reply_text("Choose gateway in which you want to withdraw", reply_markup=InlineKeyboardMarkup(kb))
                context.user_data['state'] = "WAITING_WD_GATEWAY_SELECTION"
                
        elif state == "WAITING_ADDF_SCREENSHOT" and msg.photo:
            cap = txt
            m = re.search(r'(?:UTR|Wallet):\s*(.+?)\s*₹\s*([\d\.]+)', cap, re.IGNORECASE)
            if not m: return await msg.reply_text("⚠️ Invalid format! Please include caption like:\nUTR: 1234567890 ₹10\nOR\nWallet: Payzy Wallet ₹1", reply_markup=get_cancel_keyboard())
            tx_id = m.group(1).strip()
            amt = float(m.group(2))
            gateway_name = "Manual"
            tax_pct = 0.0
            for g in db['add_fund_gateways']:
                if g['name'].lower() in cap.lower() or tx_id.lower() in g['name'].lower():
                    gateway_name = g['name']
                    tax_pct = g['tax']
                    break
            if cap.lower().startswith('wallet'): gateway_name = tx_id
            tax_amt = (amt * tax_pct) / 100
            final_amt = amt - tax_amt
            photo_id = msg.photo[-1].file_id
            req_id = db['auto_inc']['add_fund_requests']
            db['add_fund_requests'].append({
                "id": req_id, "user_id": uid, "gateway": gateway_name, "amount": amt,
                "tax_pct": tax_pct, "final_amount": final_amt, "tx_id": tx_id,
                "photo_id": photo_id, "status": "PENDING", "created_at": str(datetime.now())[:19]
            })
            db['auto_inc']['add_fund_requests'] += 1
            write_db(db)
            cancel_state(context)
            if tax_pct > 0: await msg.reply_text(f"✅ Fund Request Submitted Successfully!\n\n💰 Amount: ₹{amt}\n💸 Tax ({tax_pct}%): ₹{tax_amt}\n💵 Final Amount After Tax: ₹{final_amt}\n\n⏳ Please Wait For Admin Approval.", reply_markup=get_main_keyboard())
            else: await msg.reply_text(f"✅ Fund Request Submitted Successfully!\n\n⏳ Please Wait For Admin Approval.", reply_markup=get_main_keyboard())
            admin_txt = f"🧾 New Fund Request\n\n👤 User: {update.effective_user.first_name} (@{update.effective_user.username})\n🆔 User ID: {uid}\n💳 Payment Type: {gateway_name}\n🔢 Transaction ID: {tx_id}\n💰 Amount: ₹{amt}"
            akb = [[InlineKeyboardButton("✅ Approve", callback_data=f"admaf_app_{req_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"admaf_rej_{req_id}")]]
            admin_txt = to_font(admin_txt)
            try: await context.bot.send_photo(chat_id=MAIN_ADMIN_ID, photo=photo_id, caption=admin_txt, reply_markup=font_markup(akb))
            except: pass
            return
            
        elif state.startswith("WAITING_TSK_PROOF_") and msg.photo:
            task_id = int(state.split("_")[3])
            photo_id = msg.photo[-1].file_id
            has_submitted = any(s['task_id'] == task_id and s['user_id'] == uid for s in db['task_submissions'])
            if has_submitted: return await msg.reply_text("⚠️ You already submitted this task.", reply_markup=get_main_keyboard())
            sub_id = db['auto_inc']['task_submissions']
            db['task_submissions'].append({
                "id": sub_id, "task_id": task_id, "user_id": uid, 
                "photo_id": photo_id, "status": "PENDING", "created_at": str(datetime.now())[:19]
            })
            db['auto_inc']['task_submissions'] += 1
            write_db(db)
            await msg.reply_text("✅ Task Submitted Successfully!\n\n⏳ Please Wait For Admin Approval.", reply_markup=get_main_keyboard())
            cancel_state(context)
            admin_txt = f"🔔 New Task Submission!\nUser: {uid}\nTask ID: TSK{task_id}"
            akb = [[InlineKeyboardButton("✅ Approve", callback_data=f"admsub_app_{sub_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"admsub_rej_{sub_id}")]]
            admin_txt = to_font(admin_txt)
            try: await context.bot.send_photo(chat_id=MAIN_ADMIN_ID, photo=photo_id, caption=admin_txt, reply_markup=font_markup(akb))
            except: pass
            return
            
        elif state == "WAITING_PAY_USER":
            lines = txt.strip().split('\n')
            total_cost = 0
            payouts = []
            tax_pct = float(get_set(db, 'pay_to_user_tax', 0))
            try:
                for line in lines:
                    if not line.strip(): continue
                    parts = line.strip().split()
                    if len(parts) != 2: raise ValueError()
                    ids_str, amt_str = parts[0], parts[1]
                    amt = float(amt_str)
                    if amt <= 0: raise ValueError()
                    target_ids = ids_str.split(',')
                    for tid in target_ids:
                        tid_int = int(tid)
                        if tid_int == uid: return await msg.reply_text("❌ You cannot transfer to yourself.", reply_markup=get_cancel_keyboard())
                        tax_amt = (amt * tax_pct) / 100
                        payouts.append((tid_int, amt, tax_amt))
                        total_cost += (amt + tax_amt)
            except ValueError: return await msg.reply_text("❌ **Invalid format detected!**\n\nPlease use one of these precise formats:\n\n💡 **Same amount to multiple users:**\n 👉 `8522410574,7314163802 50`\n\n💡 **Different amounts per user:**\n 👉 `8522410574 50`\n`7314163802 100`", reply_markup=get_cancel_keyboard(), parse_mode='Markdown')
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            if u['balance'] < total_cost: return await msg.reply_text(f"⚠️ Insufficient balance! You need ₹{total_cost} but have ₹{u['balance']}.", reply_markup=get_cancel_keyboard())
            for tid, amt, tax_amt in payouts:
                tgt = next((x for x in db['users'] if x['user_id'] == tid), None)
                if not tgt: return await msg.reply_text(f"⚠️ User {tid} does not exist in the database.", reply_markup=get_cancel_keyboard())
            success_details = ""
            for tid, amt, tax_amt in payouts:
                total_deduction = amt + tax_amt
                record_tx(db, uid, -total_deduction, '💸 Payment Sent', f'Sent to {tid}', related_user=tid)
                record_tx(db, tid, amt, '💰 Payment Received', f'Received from {uid}', related_user=uid)
                tgt = next((x for x in db['users'] if x['user_id'] == tid), None)
                new_bal = tgt['balance'] if tgt else 0.0
                success_details += f"👤 {tid} : +₹{amt:.2f} → ₹{new_bal:.2f}\n"
                try: await context.bot.send_message(tid, f"💸 You received ₹{amt:.2f} from <a href='tg://user?id={uid}'>{uid}</a>", parse_mode='HTML')
                except: pass
            write_db(db)
            cancel_state(context)
            rem_bal = next((x['balance'] for x in db['users'] if x['user_id'] == uid), 0.0)
            summary = f"✅ Transfer Summary\n\n📤 Successful: {len(payouts)} User\n💸 Total Deducted: ₹{total_cost:.2f}\n💰 Remaining Balance: ₹{rem_bal:.2f}\n\n📊 Success Details:\n{success_details}"
            await msg.reply_text(summary, reply_markup=get_main_keyboard())
            
        elif state == "WAITING_GC_CREATE_USR" or state == "WAITING_GC_CREATE_ADM":
            m = re.match(r'^([\d\.]+)-(\d+)--([\d\.]+)$', txt.strip())
            if not m:
                if state == "WAITING_GC_CREATE_USR": return await msg.reply_text("⚠️ **Invalid Format**\n\nPlease use the exact structure: `Bonus-Limit--Balance`\n💡 Example: `10-20--0`", reply_markup=get_cancel_keyboard(), parse_mode='Markdown')
                else: return await reply_admin("⚠️ **Invalid Format**\n\nPlease use the exact structure: `Bonus-Limit--Balance`\n💡 Example: `10-20--0`", get_back_btn("adm_gc_main"))
            bonus, limit, req_bal = float(m.group(1)), int(m.group(2)), float(m.group(3))
            total_cost = bonus * limit
            if state == "WAITING_GC_CREATE_USR":
                u = next((x for x in db['users'] if x['user_id'] == uid), None)
                if u['balance'] < total_cost or u['balance'] < 1: return await msg.reply_text(f"⛔ You Need ₹{max(1.0, total_cost)} To Create This Gift Code.\n💰 Your Balance: ₹{u['balance']}", reply_markup=get_cancel_keyboard())
                u['balance'] -= total_cost
            code = str(uuid.uuid4()).split('-')[0].upper()
            record_tx(db, uid, -total_cost, '🎁 Gift Code Creation', f'Created code {code}')
            db['gift_codes'].append({"code": code, "amount": bonus, "usage_limit": limit, "used_count": 0, "is_active": 1, "creator_id": uid, "req_balance": req_bal})
            write_db(db)
            cancel_state(context)
            success_msg = f"✅ Gift Code created successfully!\n\n🎁 Code: `{code}`\n💰 Bonus: ₹{bonus}\n👥 Limit: {limit}\n💵 Min Balance Needed: ₹{req_bal}"
            if state == "WAITING_GC_CREATE_USR": await msg.reply_text(success_msg, parse_mode='Markdown', reply_markup=get_main_keyboard())
            else: await reply_admin(success_msg, get_back_btn("adm_gc_main"))
            
        elif state == "WAITING_GC_CLAIM":
            gc = next((x for x in db['gift_codes'] if x['code'] == txt.strip() and x['is_active'] == 1), None)
            if not gc or gc['used_count'] >= gc['usage_limit']: return await msg.reply_text("⛔ Invalid Gift Code. Please Check Carefully & Try Again!", reply_markup=get_cancel_keyboard())
            u = next((x for x in db['users'] if x['user_id'] == uid), None)
            if u['balance'] < gc.get('req_balance', 0): return await msg.reply_text(f"⛔ You need a minimum balance of ₹{gc['req_balance']} to claim this code.", reply_markup=get_cancel_keyboard())
            if any(x['code'] == txt.strip() and x['user_id'] == uid for x in db['gift_redemptions']): return await msg.reply_text("⛔ You already used this code.", reply_markup=get_cancel_keyboard())
            db['gift_redemptions'].append({"code": txt.strip(), "user_id": uid})
            gc['used_count'] += 1
            if gc['used_count'] >= gc['usage_limit']: db['gift_codes'] = [x for x in db['gift_codes'] if x['code'] != txt.strip()]
            record_tx(db, uid, gc['amount'], '🎁 Gift Code', f'Redeemed {txt.strip()}')
            write_db(db)
            cancel_state(context)
            await msg.reply_text(f"🎉 Redeemed successfully! ₹{gc['amount']} added.", reply_markup=get_main_keyboard())
            
        # Admin States
        elif is_admin(db, uid):
            if state in ["WAITING_THEME_BOTOFF", "WAITING_THEME_WDOFF", "WAITING_THEME_SUPPORT", "WAITING_THEME_WELCOME", "WAITING_PCHAN_NEW", "WAITING_GTAX", "WAITING_PUTAX"]:
                key_map = {
                    "WAITING_THEME_BOTOFF": "bot_off_text", "WAITING_THEME_WDOFF": "withdraw_off_text", 
                    "WAITING_THEME_SUPPORT": "support_text", "WAITING_THEME_WELCOME": "welcome_message",
                    "WAITING_PCHAN_NEW": "payout_channel", "WAITING_GTAX": "withdraw_tax", "WAITING_PUTAX": "pay_to_user_tax"
                }
                db['settings'][key_map[state]] = txt
                write_db(db)
                await reply_admin(f"✅ **Update Complete**\n\nValue successfully updated to:\n`{txt}`", back_kbp())
            elif state.startswith("WAITING_KB_EDIT_"):
                idx = int(state.split("_")[3])
                kb_data = get_set(db, 'keyboard')
                kb_data[idx]['text'] = txt
                db['settings']['keyboard'] = kb_data
                write_db(db)
                await reply_admin(f"✅ Button renamed to `{txt}`.", get_back_btn("adm_theme_kbd"))
            elif state == "WAITING_SETREF":
                try: amt = float(txt)
                except ValueError: return await reply_admin("❌ **Invalid Amount**\n\nPlease enter a valid numerical amount.", back_kbp())
                if amt < 0: return await reply_admin("❌ **Invalid Amount**\n\nPlease enter a valid numerical amount.", back_kbp())
                db['settings']['cfg_refer_amount'] = str(amt)
                write_db(db)
                await reply_admin(f"✅ **Referral Amount Updated**\n\n👥 New Referral Reward: **₹{amt}**", back_kbp())
            elif state == "WAITING_WD_LIMITS_BULK":
                for part in txt.split():
                    m = re.match(r'(.+?):([\d\.]+)-([\d\.]+)--([\d\.]+)', part)
                    if m:
                        name, min_w, max_w, tax = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))
                        if name.upper() == 'UPI':
                            g = next((x for x in db['gateways'] if x['name'] == 'UPI'), None)
                            if g: g.update({"min_w": min_w, "max_w": max_w, "tax": tax})
                        else:
                            g = next((x for x in db['api_withdraw_gateways'] if x['domain'].lower() == name.lower()), None)
                            if g: g.update({"min_w": min_w, "max_w": max_w, "tax": tax})
                write_db(db)
                await reply_admin("✅ **Limits Updated Successfully**", get_back_btn("admw_main"))
            elif state == "WAITING_ADDA":
                ids = [int(i.strip()) for i in txt.replace(',', ' ').split() if i.strip().isdigit()]
                for i in ids:
                    if i not in db['admins']: 
                        db['admins'].append(i)
                        try: await context.bot.send_message(i, "🫵 Congratulations,\n👼 You are now an admin. Use /adminpanel to access your bot panel.")
                        except: pass
                write_db(db)
                await reply_admin(f"✅ **Admins Granted**\n\nSuccessfully added {len(ids)} new administrators.", get_back_btn("adm_admins"))
            elif state == "WAITING_TALK_ID":
                try: context.user_data['talk_id'] = int(txt)
                except ValueError: return await reply_admin("❌ Please enter a valid numerical User ID.", back_kbp())
                context.user_data['state'] = "WAITING_TALK_MSG"
                await reply_admin(f"💬 **Direct Communication Channel Opened**\n\nTarget User: `{txt}`\n\nPlease type your message or send media now:", back_kbp())
            elif state == "WAITING_TALK_MSG":
                tgt = context.user_data['talk_id']
                try:
                    await msg.copy(chat_id=tgt)
                    await reply_admin(f"✅ **Message Dispatched Successfully**\n\nYour message was delivered to `{tgt}`.", back_kbp())
                except Exception as e: await reply_admin(f"❌ **Delivery Failed**\n\nError Output: `{e}`", back_kbp())
            elif state == "WAITING_BCAST_MSG":
                users = db['users']
                await reply_admin(f"📢 **Broadcast Initialized**\n\nBroadcasting your message to {len(users)} registered users. Please wait for the completion report...")
                succ = err = 0
                for u in users:
                    try:
                        await msg.copy(chat_id=u['user_id'])
                        succ += 1
                        await asyncio.sleep(0.04)
                    except: err += 1
                await reply_admin(f"📢 **BROADCAST COMPLETION REPORT**\n\n✅ Delivered: `{succ}`\n❌ Failed: `{err}`\n👥 Total Processed: `{len(users)}`", back_kbp())
            elif state in ["WAITING_ADDBAL_MULTI", "WAITING_REMBAL_MULTI"]:
                lines = txt.strip().split('\n')
                payouts = []
                try:
                    for line in lines:
                        if not line.strip(): continue
                        parts = line.strip().split()
                        if len(parts) != 2: raise ValueError()
                        ids_str, amt_str = parts[0], parts[1]
                        amt = float(amt_str)
                        if amt <= 0: raise ValueError()
                        for tid in ids_str.split(','):
                            if tid.strip().isdigit(): payouts.append((int(tid.strip()), amt))
                except ValueError: return await msg.reply_text("⚠️ **Format Validation Error**\n\nPlease carefully check the examples and try again.", reply_markup=get_cancel_keyboard() if not is_admin(db, uid) else None, parse_mode='Markdown')
                admin_reply = ""
                for tid, amt in payouts:
                    u = next((x for x in db['users'] if x['user_id'] == tid), None)
                    if not u: continue
                    if state == "WAITING_ADDBAL_MULTI":
                        record_tx(db, tid, amt, '➕ Balance Added', 'Admin balance increase', related_user=uid)
                        admin_reply += f"💴 Account Of {tid} Was Increased By {amt}\n💰 Final Balance = {u['balance']}\n\n"
                        try: await context.bot.send_message(tid, f"💰 Admin Gave You A Increase In Balance By {amt}")
                        except: pass
                    else:
                        if u['balance'] < amt: continue
                        record_tx(db, tid, -amt, '➖ Balance Removed', 'Admin balance decrease', related_user=uid)
                        admin_reply += f"💴 Account Of {tid} Was Decreased By {amt}\n💰 Final Balance = {u['balance']}\n\n"
                        try: await context.bot.send_message(tid, f"💰 Admin Gave You A Decrease In Balance By {amt}")
                        except: pass
                write_db(db)
                cancel_state(context)
                if admin_reply.strip(): await reply_admin(admin_reply.strip(), get_back_btn("adm_main"))
                else: await reply_admin("✅ **Operations Completed (No Changes Executed)**", get_back_btn("adm_main"))
            elif state == "WAITING_RSTBAL_ID":
                try: tgt = int(txt)
                except ValueError: return await reply_admin("❌ Please enter a valid numerical User ID.", back_kbp())
                u = next((x for x in db['users'] if x['user_id'] == tgt), None)
                if u and u['balance'] > 0:
                    record_tx(db, tgt, -u['balance'], '➖ Balance Removed', 'Admin Reset Balance', related_user=uid)
                    write_db(db)
                await reply_admin(f"✅ **Account Reset**\n\nBalance for user `{tgt}` has been wiped to ₹0.", back_kbp())
            elif state == "WAITING_FINDU":
                try: tgt = int(txt)
                except ValueError: return await reply_admin("❌ Please enter a valid numerical User ID.", back_kbp())
                u = next((x for x in db['users'] if x['user_id'] == tgt), None)
                if not u: return await reply_admin("❌ User not found in the database.", back_kbp())
                w_paid = sum(w['final_amount'] for w in db['withdrawals'] if w['user_id'] == tgt and w['status'] == '💰 Paid')
                total_ref = sum(1 for usr in db['users'] if usr.get('referred_by') == tgt)
                info = f"🚹 **User Profile Match Found**\n\n🆔 **User ID:** `{tgt}`\n💰 **Current Balance:** ₹{u['balance']}\n💸 **Total Withdrawn:** ₹{w_paid}\n👥 **Total Referrals:** {total_ref}\n✅ **Verified Referrals:** {total_ref}\n🛑 **Blocked Referrals (Fraud):** 0\n⏳ **Pending Referrals:** 0\n🎁 **Referrals Claimed:** {total_ref}"
                kb = [[InlineKeyboardButton("✅ Toggle Verify", callback_data=f"utgl_v_{tgt}"), InlineKeyboardButton("🚫 Toggle Wallet Ban", callback_data=f"utgl_w_{tgt}")], [InlineKeyboardButton("⛔ Toggle System Ban", callback_data=f"utgl_b_{tgt}")], [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]
                await reply_admin(info, InlineKeyboardMarkup(kb))
            elif state == "WAITING_VUSER" or state == "WAITING_BWALL":
                try: tgt = int(txt)
                except ValueError: return await reply_admin("❌ Please enter a valid numerical User ID.", back_kbp())
                u = next((x for x in db['users'] if x['user_id'] == tgt), None)
                if not u: return await reply_admin("❌ **User Profile Not Found**", back_kbp())
                kb = [[InlineKeyboardButton("✅ Toggle Verify", callback_data=f"utgl_v_{tgt}"), InlineKeyboardButton("🚫 Toggle Wallet Ban", callback_data=f"utgl_w_{tgt}")], [InlineKeyboardButton("🔙 Back", callback_data="adm_main")]]
                await reply_admin(f"⚙️ **Available Actions for User {tgt}:**", InlineKeyboardMarkup(kb))
            elif state == "WAITING_BAN_WALLET":
                wid = txt.strip()
                if wid not in db['banned_wallets']:
                    db['banned_wallets'].append(wid)
                    write_db(db)
                await reply_admin(f"🚫 **Ban Applied**\n\nWallet/UPI ID `{wid}` has been permanently banned.", get_back_btn("adm_bwallet"))
            elif state == "WAITING_BANU":
                ids = [int(i.strip()) for i in txt.replace(',', ' ').split() if i.strip().isdigit()]
                for i in ids:
                    usr = next((x for x in db['users'] if x['user_id'] == i), None)
                    if not usr: db['users'].append({"user_id": i, "username": "Unknown", "first_name": "User", "balance": 0.0, "joined_at": str(datetime.now())[:19], "verified": 0, "referred_by": None, "wallet_banned": 0, "is_banned": 1})
                    else: usr['is_banned'] = 1
                write_db(db)
                await reply_admin(f"✅ **Ban Execution Complete**\n\nSuccessfully restricted {len(ids)} user accounts.", get_back_btn("adm_banusers"))
            elif state.startswith("WAITING_GW_"):
                parts = state.split("_")
                act = parts[2]
                gw = parts[3]
                try: val = float(txt)
                except ValueError: return await msg.reply_text("❌ Invalid Input Type. Requires numerical amount.")
                col = "min_w" if act == "MIN" else "max_w" if act == "MAX" else "tax"
                g_dict = next((x for x in db['gateways'] if x['name'] == gw), None)
                if g_dict:
                    g_dict[col] = val
                    write_db(db)
                await reply_admin(f"✅ **Gateway Parameter Updated**\n\n[{gw}] {col.upper()} threshold was updated successfully to **{val}**.", get_back_btn(f"admw_main"))
            elif state.startswith("WAITING_WREJ_"):
                wid = int(state.split("_")[2])
                reason = txt
                w = next((x for x in db['withdrawals'] if x['id'] == wid), None)
                if w and w['status'] not in ['💰 Paid', '❌ Rejected', '🚫 Cancelled']:
                    w['status'] = '❌ Rejected'
                    w['admin_note'] = f"Reason: {reason}"
                    w['updated_at'] = str(datetime.now())[:19]
                    record_tx(db, w['user_id'], w['amount'], '↩️ Withdrawal Refund', f'Rejected WD #{wid}', related_user=uid)
                    tx = next((t for t in db['transactions'] if t['tx_id'] == w['tx_id']), None)
                    if tx: tx['status'] = '❌ Rejected'
                    write_db(db)
                    try: await context.bot.send_message(w['user_id'], f"❌ Your Withdrawal Request Of ₹{w['amount']} Has Been Rejected! The Amount Has Been Refunded To Your Balance.")
                    except: pass
                await reply_admin(f"✅ **Request Denied**\n\nWithdrawal #{wid} has been rejected and funds were refunded to the user.", get_back_btn("admw_reqs"))
            elif state == "WAITING_CHADD_CHK":
                n_ch = normalize_channel(txt)
                try:
                    m = await context.bot.get_chat_member(chat_id=n_ch, user_id=context.bot.id)
                    if m.status not in ['administrator', 'creator']: return await reply_admin("❌ **Permission Denied**\n\nThe bot currently lacks Admin rights in this channel. Please promote the bot to Admin and try again.", back_kbp())
                except Exception as e: return await reply_admin("❌ **Channel Verification Failed**\n\nEnsure the username is perfectly typed and the bot is actively placed as an Admin.", back_kbp())
                ch = next((x for x in db['channels'] if x['channel_id'] == n_ch), None)
                if ch: ch['type'] = 'checked'
                else: db['channels'].append({"channel_id": n_ch, "type": "checked"})
                write_db(db)
                await reply_admin(f"✅ **Checked Channel Successfully Added**\n\n📢 Registered Channel ID: `{n_ch}`", get_back_btn("adm_channels"))
            elif state == "WAITING_CHADD_UCHK":
                n_ch = normalize_channel(txt)
                if any(x['channel_id'] == n_ch for x in db['channels']): return await reply_admin("❌ **Duplication Error**\n\nThis exact channel is already registered.", back_kbp())
                db['channels'].append({"channel_id": n_ch, "type": "unchecked"})
                write_db(db)
                await reply_admin(f"✅ **Unchecked Channel Successfully Added**\n\n📢 Registered Channel ID: `{n_ch}`", get_back_btn("adm_channels"))
            elif state == "WAITING_ADF_NAME":
                context.user_data['adf_name'] = txt.strip()
                context.user_data['state'] = "WAITING_ADF_WALLET"
                await reply_admin("👛 **Receiver Wallet Definition**\n\n✏️ Please provide the exact Receiver Wallet ID mapping:", get_back_btn("adm_addf_main"))
            elif state == "WAITING_ADF_WALLET":
                context.user_data['adf_wallet'] = txt.strip()
                context.user_data['state'] = "WAITING_ADF_LIMITS"
                txt_prompt = "⚙️ **Gateway Parameters (Limits & Tax)**\n\n💰 Please enter the configuration strictly separated by hyphens:\n\n📌 **Format Protocol:**\n`Min Limit - Max Limit - Tax`\n\n💡 Example:\n`10 - 5000 - 2`"
                await reply_admin(txt_prompt, get_back_btn("adm_addf_main"))
            elif state == "WAITING_ADF_LIMITS":
                try:
                    parts = [p.strip() for p in txt.replace('%', '').split('-')]
                    if len(parts) != 3: raise ValueError()
                    min_amt, max_amt, tax = float(parts[0]), float(parts[1]), float(parts[2])
                except ValueError: return await msg.reply_text("⚠️ **Format Violation**\n\nUse strict structure: `Min - Max - Tax`\n💡 Example: `10 - 5000 - 2`", reply_markup=get_cancel_keyboard() if not is_admin(db, uid) else None, parse_mode='Markdown')
                gname = context.user_data.get('adf_name', 'Unknown')
                gwallet = context.user_data.get('adf_wallet', 'Unknown')
                gw = next((x for x in db['add_fund_gateways'] if x['name'] == gname), None)
                if gw: gw.update({"wallet_id": gwallet, "min_amt": min_amt, "max_amt": max_amt, "tax": tax})
                else: db['add_fund_gateways'].append({"name": gname, "wallet_id": gwallet, "min_amt": min_amt, "max_amt": max_amt, "tax": tax})
                write_db(db)
                success_txt = f"🎉 **Add Fund Gateway Deployed**\n\n🏷 **Name:** `{gname}`\n👛 **Mapping:** `{gwallet}`\n💰 **Range Limit:** ₹{min_amt} ~ ₹{max_amt}\n🧾 **Applied Deduction Tax:** {tax}%"
                await reply_admin(success_txt, get_back_btn("adm_addf_main"))
            elif state.startswith("WAITING_ADDF_APP_"):
                req_id = int(state.split("_")[3])
                try: admin_amt = float(txt)
                except ValueError: return await msg.reply_text("❌ Input validation failed. Amount must be a valid number.")
                req = next((x for x in db['add_fund_requests'] if x['id'] == req_id), None)
                if req and req['status'] == 'PENDING':
                    req['status'] = 'APPROVED'
                    record_tx(db, req['user_id'], admin_amt, '➕ Balance Added', f'Add Fund Approved ({req["tx_id"]})', related_user=uid)
                    write_db(db)
                    if req['tax_pct'] > 0: utxt = f"🎉 **Fund Request Approved!**\n\n💳 Gateway Used: `{req['gateway']}`\n🔢 Transaction Ref: `{req['tx_id']}`\n💰 Original Request: ₹{req['amount']:.2f}\n💸 Deduction Tax: ₹{(req['amount']*req['tax_pct']/100):.2f}\n💵 Projected Value: ₹{req['final_amount']:.2f}\n💡 **Final Admin Credit:** ₹{admin_amt:.2f}\n\n✅ Your wallet balance has been successfully refreshed."
                    else: utxt = f"🎉 **Fund Request Approved!**\n\n💳 Gateway Used: `{req['gateway']}`\n🔢 Transaction Ref: `{req['tx_id']}`\n💰 Original Request: ₹{req['amount']:.2f}\n💡 **Final Admin Credit:** ₹{admin_amt:.2f}\n\n✅ Your wallet balance has been successfully refreshed."
                    try: await context.bot.send_message(req['user_id'], utxt, parse_mode='Markdown')
                    except: pass
                    req_user = next((x for x in db['users'] if x['user_id'] == req['user_id']), None)
                    uname = req_user['first_name'] if req_user else "Unknown"
                    new_cap = f"✅ **Fund Action Finalized (Approved)**\n\n👤 Targeted User: {uname}\n🆔 Database ID: `{req['user_id']}`\n💳 Selected Source: {req['gateway']}\n🔢 Transaction Hash: {req['tx_id']}\n💰 Original Request Value: ₹{req['amount']}\n💡 Validated & Paid by Admin: ₹{admin_amt}"
                    try: await context.bot.edit_message_caption(chat_id=uid, message_id=mid, caption=to_font(new_cap), parse_mode='Markdown')
                    except Exception as e: print("Caption Edit Error:", e)
                cancel_state(context)
                await context.bot.send_message(uid, to_font(f"✅ **Success:** ₹{admin_amt} was added to the user balance."), reply_markup=get_back_btn("adm_main"))
            elif state.startswith("WAITING_ADDF_REJ_"):
                req_id = int(state.split("_")[3])
                reason = txt
                req = next((x for x in db['add_fund_requests'] if x['id'] == req_id), None)
                if req and req['status'] == 'PENDING':
                    req['status'] = 'REJECTED'
                    write_db(db)
                    try: await context.bot.send_message(req['user_id'], f"❌ **Fund Request Rejected**\n\n💳 Gateway Used: `{req['gateway']}`\n🔢 Transaction Ref: `{req['tx_id']}`\n💰 Request Value: ₹{req['amount']}\n\n**Decision Reason:** {reason}", parse_mode='Markdown')
                    except: pass
                    req_user = next((x for x in db['users'] if x['user_id'] == req['user_id']), None)
                    uname = req_user['first_name'] if req_user else "Unknown"
                    new_cap = f"❌ **Fund Action Finalized (Rejected)**\n\n👤 Targeted User: {uname}\n🆔 Database ID: `{req['user_id']}`\n💳 Selected Source: {req['gateway']}\n🔢 Transaction Hash: {req['tx_id']}\n💰 Original Request Value: ₹{req['amount']}\n📝 **Provided Reason:** {reason}"
                    try: await context.bot.edit_message_caption(chat_id=uid, message_id=mid, caption=to_font(new_cap), parse_mode='Markdown')
                    except Exception as e: print("Caption Edit Error:", e)
                cancel_state(context)
                await context.bot.send_message(uid, to_font(f"❌ **Request Denied Successfully.** The rejection reason was relayed to the user."), reply_markup=get_back_btn("adm_main"))
            elif state == "WAITING_AWAPI_URL":
                parsed = urllib.parse.urlparse(txt)
                domain = parsed.netloc or parsed.path.split('/')[0]
                name = domain.split('-')[0].capitalize() if '-' in domain else domain.split('.')[0].capitalize()
                link = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else f"https://{domain}"
                db['api_withdraw_gateways'].append({"domain": name, "url": txt, "link": link, "min_w": 1, "max_w": 100, "tax": 0})
                write_db(db)
                cancel_state(context)
                await reply_admin(f"✅ **Gateway Added Successfully!**\n\n**Gateway Name:** {name}\n**Link:** {link}", get_back_btn("admw_api_st"))
            elif state.startswith("WAITING_AWAPI_"):
                act = state.split("_")[2]
                dom = state.split("_", 3)[3]
                try: val = float(txt)
                except ValueError: return await reply_admin("❌ **Error:** Field demands purely numerical data.", back_kbp())
                col = "min_w" if act == "MIN" else "max_w" if act == "MAX" else "tax"
                g = next((x for x in db['api_withdraw_gateways'] if x['domain'] == dom), None)
                if g: 
                    g[col] = val
                    write_db(db)
                await reply_admin(f"✅ **Update Finalized**\n\nModified `{dom}` parameter `{col}` applied to new value **{val}**.", get_back_btn(f"awapi_vw_{dom}"))
            elif state == "WAITING_GC_CREATE_ADM":
                m = re.match(r'^([\d\.]+)-(\d+)--([\d\.]+)$', txt.strip())
                if not m: return await reply_admin("⚠️ **Invalid Format**\n\nPlease use the exact structure: `Bonus-Limit--Balance`\n💡 Example: `10-20--0`", get_back_btn("adm_gc_main"))
                bonus, limit, req_bal = float(m.group(1)), int(m.group(2)), float(m.group(3))
                total_cost = bonus * limit
                code = str(uuid.uuid4()).split('-')[0].upper()
                record_tx(db, uid, -total_cost, '🎁 Gift Code Creation', f'Created code {code}')
                db['gift_codes'].append({"code": code, "amount": bonus, "usage_limit": limit, "used_count": 0, "is_active": 1, "creator_id": uid, "req_balance": req_bal})
                write_db(db)
                cancel_state(context)
                await reply_admin(f"✅ Gift Code created successfully!\n\n🎁 Code: `{code}`\n💰 Bonus: ₹{bonus}\n👥 Limit: {limit}\n💵 Min Balance Needed: ₹{req_bal}", get_back_btn("adm_gc_main"))
            elif state == "WAITING_BGC_ADD":
                m = re.match(r'^(.+?)-(.+?)--([\d\.]+)$', txt.strip())
                if not m: return await reply_admin("⚠️ **Data Format Issue**\n\nTry following the pattern: `PlayStore-ABCD1234567--100`", get_back_btn("adm_bgc_main"))
                plat, code, amt = m.group(1).strip(), m.group(2).strip(), float(m.group(3))
                db['buyable_gift_cards'].append({"id": db['auto_inc']['buyable_gift_cards'], "platform": plat, "code": code, "amount": amt, "is_bought": 0, "bought_by": None, "bought_at": None})
                db['auto_inc']['buyable_gift_cards'] += 1
                write_db(db)
                cancel_state(context)
                cards = [c for c in db['buyable_gift_cards'] if c['is_bought'] == 0]
                kb = []
                for c in cards: kb.append([InlineKeyboardButton(to_font(f"{c['platform']} (₹{c['amount']})"), callback_data=f"bgcvw_{c['id']}"), InlineKeyboardButton(to_font("❌"), callback_data=f"bgcdel_{c['id']}")])
                kb.append([InlineKeyboardButton(to_font("➕ Add Redeem Code"), callback_data="bgc_add")])
                kb.append([InlineKeyboardButton(to_font("🔙 Back"), callback_data="adm_main")])
                await reply_admin("🛍️ **Here are the stored inventory cards:**\n", InlineKeyboardMarkup(kb))
            elif state.startswith("WAITING_TSK_"):
                if "TITLE" in state:
                    context.user_data['tt'] = txt; context.user_data['state'] = "WAITING_TSK_DESC"
                    await reply_admin("📝 **Task Creation: Step 2 (Description)**\n\nPlease provide detailed instructions and completion steps for this task.", get_back_btn("admt_main"))
                elif "DESC" in state:
                    context.user_data['td'] = txt; context.user_data['state'] = "WAITING_TSK_REW"
                    await reply_admin("💰 **Task Creation: Step 3 (Reward)**\n\nPlease enter the exact numerical reward amount (in ₹) users get for successfully clearing this task.", get_back_btn("admt_main"))
                elif "REW" in state:
                    task_id = db['auto_inc']['tasks']
                    title = context.user_data['tt']
                    desc = context.user_data['td']
                    rew = float(txt)
                    db['tasks'].append({"id": task_id, "title": title, "description": desc, "reward": rew, "active": 1})
                    db['auto_inc']['tasks'] += 1
                    write_db(db)
                    await reply_admin(f"✅ **Task Activation Complete!**\n\n🆔 **Task Assignment ID:** `TSK{task_id}`\n📄 **Header Title:** {title}\n💰 **Offered Reward:** ₹{rew}\n\n📝 **Instruction Block:**\n{desc}", get_back_btn("admt_main"))
                    cancel_state(context)
    except Exception as e:
        logger.error(f"Input Error: {e}")
        try: await msg.reply_text(to_font(f"❌ **System Error Triggered**\nOutput: `{str(e)}`"), reply_markup=get_cancel_keyboard() if not is_admin(db, uid) else None, parse_mode='Markdown')
        except: pass 

# ==========================================
# VERCEL FLASK WEBHOOK SERVER
# ==========================================

flask_app = Flask(__name__)
tg_app = None

def get_tg_app():
    global tg_app
    if not tg_app:
        read_db() # Firebase check
        tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
        tg_app.add_handler(CommandHandler("start", cmd_start))
        tg_app.add_handler(CommandHandler("adminpanel", cmd_admin))
        tg_app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^adm_|^admw_|^admg_|^admt_|^set_|^utgl_|^awd_|^ch_|^chv_|^chd_|^gft_|^tsk_|^adf_|^admsub_|^admaf_|^bgc_|^awapi_|^bwd_|^kb_"))
        tg_app.add_handler(CallbackQueryHandler(handle_user_cb, pattern="^usr_|^pay_|^ugft_|^ubgc_|^ch_wd_"))
        tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_input))
    return tg_app

def tg_request(method, params=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if params: url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res: return json.loads(res.read().decode('utf-8'))
    except Exception as e: return {"ok": False, "error": str(e)}

@flask_app.route('/api/bot_control', methods=['GET', 'POST'])
def control():
    if request.method == 'GET':
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        return jsonify({"bot_name": "Tasks Payment Bot", "is_running": bool(webhook_url), "webhook_url": webhook_url})
    if request.method == 'POST':
        data = request.json or {}
        action = data.get("action")
        domain = data.get("domain", "").rstrip('/')
        if action == "start": res = tg_request("setWebhook", {"url": f"{domain}/api/webhook"})
        elif action == "stop": res = tg_request("deleteWebhook")
        else: res = {"ok": False, "error": "Invalid action"}
        return jsonify(res)

@flask_app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        update_data = request.json
        telegram_app = get_tg_app()
        update = Update.de_json(update_data, telegram_app.bot)
        
        async def process_update():
            async with telegram_app: await telegram_app.process_update(update)
                
        asyncio.run(process_update())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"WEBHOOK ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 200

# Expose app for Vercel
app = flask_app
