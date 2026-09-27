import os
import json
import asyncio
import time
import urllib.request
import urllib.parse
import requests
from flask import Flask, request, jsonify
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = "8416519129:AAGTP3rS27N0f8H0WcU6fFdg4xja9_MTfAs"
MAIN_ADMIN_ID = 8522410574
FIREBASE_URL = "https://task-pay-f7f88-default-rtdb.europe-west1.firebasedatabase.app/maker_data.json"
FIREBASE_STATE_URL = "https://task-pay-f7f88-default-rtdb.europe-west1.firebasedatabase.app/admin_state.json"

tg_app = None

def get_tg_app():
    global tg_app
    if not tg_app:
        tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        def get_emoji_keyboard():
            keyboard = [
                [KeyboardButton("Laugh"), KeyboardButton("Cool")],
                [KeyboardButton("Rocket")],
                [KeyboardButton("Fire"), KeyboardButton("Star")]
            ]
            return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        # 1. /start Command
        async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            name = user.username if user.username else user.first_name
            await update.message.reply_text(f"Welcome {name}", reply_markup=get_emoji_keyboard())

        # 2. /add Command (Admin Only)
        async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_user.id != MAIN_ADMIN_ID:
                return await update.message.reply_text("❌ You are not authorized.")
            
            try:
                requests.put(FIREBASE_STATE_URL, json={"status": "WAITING"}, timeout=5)
                await update.message.reply_text("Send your message")
            except Exception:
                await update.message.reply_text("Database connection error.")

        # 3. /maker Command (Forward Saved Message)
        async def cmd_maker(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                res = requests.get(FIREBASE_URL, timeout=5)
                data = res.json()
                
                if data and 'msg_id' in data and 'chat_id' in data:
                    await context.bot.forward_message(
                        chat_id=update.effective_user.id,
                        from_chat_id=data['chat_id'],
                        message_id=data['msg_id']
                    )
                else:
                    await update.message.reply_text("No maker message has been set yet.")
            except Exception:
                await update.message.reply_text("Error loading maker message.")

        # 4. /ping & /pink Command (True Low-Latency Calculation)
        async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
            t1 = time.time()
            await context.bot.get_me()
            t2 = time.time()
            
            latency = int((t2 - t1) * 1000)
            
            if latency < 180:
                speed = "Excellent 🚀"
            elif latency < 350:
                speed = "Good ⚡"
            elif latency < 700:
                speed = "Normal 🟢"
            else:
                speed = "Poor 🐌"

            ping_text = (
                f"🏓 PING COMPLETED\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📶 Latency: {latency}ms\n"
                f"🌐 Server Speed: {speed}\n"
                f"🤖 Bot Server: Online 🟢\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(ping_text)

        # 5. Handle Text, Media, and Emoji Inputs
        async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            
            # Check if Admin is setting a maker message
            if uid == MAIN_ADMIN_ID:
                try:
                    state_res = requests.get(FIREBASE_STATE_URL, timeout=5).json()
                    if state_res and state_res.get("status") == "WAITING":
                        msg_id = update.message.message_id
                        save_data = {"chat_id": uid, "msg_id": msg_id}
                        
                        requests.put(FIREBASE_URL, json=save_data, timeout=5)
                        requests.put(FIREBASE_STATE_URL, json={"status": "DONE"}, timeout=5)
                        await update.message.reply_text("✅ Message saved! Users will now receive this exact forwarded message when they type /maker.")
                        return
                except Exception:
                    pass

            txt = update.message.text
            if not txt:
                return

            emoji_map = {
                "Laugh": "😂",
                "Cool": "😎",
                "Rocket": "🚀",
                "Fire": "🔥",
                "Star": "⭐"
            }
            if txt in emoji_map:
                await update.message.reply_text(emoji_map[txt])

        tg_app.add_handler(CommandHandler("start", cmd_start))
        tg_app.add_handler(CommandHandler("add", cmd_add))
        tg_app.add_handler(CommandHandler("maker", cmd_maker))
        tg_app.add_handler(CommandHandler("ping", cmd_ping))
        tg_app.add_handler(CommandHandler("pink", cmd_ping))
        tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
        
    return tg_app

# --- VERCEL FLASK WEBHOOK ROUTING ---
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

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({"status": "bot_is_awake"})

@app.route('/api/bot_control', methods=['GET', 'POST'])
def control():
    if request.method == 'GET':
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        return jsonify({
            "bot_name": "New Maker Bot",
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
        
        async def process_update():
            async with telegram_app:
                await telegram_app.process_update(update)
                
        asyncio.run(process_update())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200

app = app
