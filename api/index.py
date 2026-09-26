import os
import json
import asyncio
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8")
MAIN_ADMIN_ID = 8522410574

# Vercel (Serverless) me memory bachane ke liye Bot ko lazy-load karenge
tg_app = None

def get_tg_app():
    global tg_app
    if not tg_app:
        tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        def get_main_keyboard():
            keyboard = [
                ["👤 My Account", "🎟️ My Gift card"],
                ["🎁 Gift Code", "🎉 Pay to User"],
                ["📋 Task section", "🚀 Withdraw"],
                ["🛒 Buy Gift Card"]
            ]
            return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        async def start_handler(update: Update, context):
            await update.message.reply_text("✨ Welcome To Tasks Payment Bot", reply_markup=get_main_keyboard())

        async def admin_handler(update: Update, context):
            if update.effective_user.id != MAIN_ADMIN_ID:
                await update.message.reply_text("❌ Access Denied!")
                return
            await update.message.reply_text("🔍 Welcome To Admin Panel\n\nMain Owner: 8522410574\nBot Status: Active")

        async def message_handler(update: Update, context):
            text = update.message.text
            uid = update.effective_user.id
            if text == "👤 My Account":
                kb = [
                    [InlineKeyboardButton("➕ Add Fund", callback_data="usr_addf")],
                    [InlineKeyboardButton("🪪 Balance Records", callback_data="usr_tx_page_0")],
                    [InlineKeyboardButton("📞 Support", callback_data="usr_support")]
                ]
                await update.message.reply_text(f"🚀 Wallet Summary\n\n👤 User ID -> {uid}\n💸 Balance : ₹0.00", reply_markup=InlineKeyboardMarkup(kb))
            elif text == "📋 Task section":
                await update.message.reply_text("⚠️ No tasks available right now.")
            else:
                await update.message.reply_text("Please use the keyboard menu.")

        tg_app.add_handler(CommandHandler("start", start_handler))
        tg_app.add_handler(CommandHandler("adminpanel", admin_handler))
        tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    return tg_app

def tg_request(method, params=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- FLASK ROUTES FOR VERCEL ---

@app.route('/api/bot_control', methods=['GET', 'POST'])
def control():
    if request.method == 'GET':
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        return jsonify({
            "bot_name": "Tasks Payment Bot",
            "is_running": bool(webhook_url),
            "webhook_url": webhook_url
        })
    
    if request.method == 'POST':
        data = request.json or {}
        action = data.get("action")
        domain = data.get("domain", "").rstrip('/')
        
        if action == "start":
            res = tg_request("setWebhook", {"url": f"{domain}/api/webhook"})
        elif action == "stop":
            res = tg_request("deleteWebhook")
        else:
            res = {"ok": False, "error": "Invalid action"}
        return jsonify(res)

@app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        update_data = request.json
        telegram_app = get_tg_app()
        update = Update.de_json(update_data, telegram_app.bot)
        
        # Async run for serverless
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(telegram_app.process_update(update))
        loop.close()
        
        return jsonify({"status": "ok"})
    except Exception as e:
        # Error aane par ab Vercel HTML nahi, balki JSON error dega
        return jsonify({"status": "error", "message": str(e)}), 500

# Vercel requires the app to be named 'app'
