import os
import json
import asyncio
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

# Admin message save karne ke liye temporary state
admin_state = {}
tg_app = None

def get_tg_app():
    global tg_app
    if not tg_app:
        tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # 5 Button wala Keyboard
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
            # Agar username hai toh wo lega, warna first name
            name = user.username if user.username else user.first_name
            await update.message.reply_text(f"Welcome {name}", reply_markup=get_emoji_keyboard())

        # 2. /add Command (Only for Admin)
        async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            if uid != MAIN_ADMIN_ID:
                return await update.message.reply_text("❌ You are not authorized to use this command.")
            
            # Admin ko wait state me daal diya
            admin_state[uid] = "WAITING"
            await update.message.reply_text("Send your message")

        # 3. /maker Command (Forward the saved message)
        async def cmd_maker(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            try:
                # Firebase se message ki details nikalna
                res = requests.get(FIREBASE_URL, timeout=5)
                data = res.json()
                
                if data and 'msg_id' in data and 'chat_id' in data:
                    # Message FORWARD karna (As exactly requested)
                    await context.bot.forward_message(
                        chat_id=uid,
                        from_chat_id=data['chat_id'],
                        message_id=data['msg_id']
                    )
                else:
                    await update.message.reply_text("No maker message has been set yet.")
            except Exception as e:
                await update.message.reply_text("Error loading maker message.")

        # 4. Handle all texts, media, and emojis
        async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
            uid = update.effective_user.id
            
            # Agar admin /add ke baad message bhej raha hai
            if admin_state.get(uid) == "WAITING":
                msg_id = update.message.message_id
                
                # Firebase me chat_id aur message_id save kar rahe hain taaki forward ho sake
                save_data = {"chat_id": uid, "msg_id": msg_id}
                try:
                    requests.put(FIREBASE_URL, json=save_data, timeout=5)
                    admin_state[uid] = None  # State clear kar di
                    await update.message.reply_text("✅ Message saved! Users will now receive this exact forwarded message when they type /maker.")
                except Exception as e:
                    await update.message.reply_text("❌ Failed to save message to database.")
                return

            # EMOJI SYSTEM LOGIC
            txt = update.message.text
            if not txt:
                return

            if txt == "Laugh":
                await update.message.reply_text("😂")
            elif txt == "Cool":
                await update.message.reply_text("😎")
            elif txt == "Rocket":
                await update.message.reply_text("🚀")
            elif txt == "Fire":
                await update.message.reply_text("🔥")
            elif txt == "Star":
                await update.message.reply_text("⭐")

        # Handlers Add Karna
        tg_app.add_handler(CommandHandler("start", cmd_start))
        tg_app.add_handler(CommandHandler("add", cmd_add))
        tg_app.add_handler(CommandHandler("maker", cmd_maker))
        # Ye handler text, photo, video har cheez pakdega taaki admin media bhi forward set kar sake
        tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
        
    return tg_app

# ==========================================
# VERCEL WEBHOOK SYSTEM
# ==========================================
def tg_request(method, params=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if params: url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res: return json.loads(res.read().decode('utf-8'))
    except Exception as e: return {"ok": False, "error": str(e)}

@app.route('/api/bot_control', methods=['GET', 'POST'])
def control():
    if request.method == 'GET':
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        return jsonify({"bot_name": "New Maker Bot", "is_running": bool(webhook_url), "webhook_url": webhook_url})
    if request.method == 'POST':
        data = request.json or {}
        action = data.get("action")
        domain = data.get("domain", "").rstrip('/')
        if action == "start": res = tg_request("setWebhook", {"url": f"{domain}/api/webhook"})
        elif action == "stop": res = tg_request("deleteWebhook")
        else: res = {"ok": False, "error": "Invalid action"}
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
