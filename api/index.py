import os
import json
import asyncio
import urllib.request
import urllib.parse
from flask import Flask, request, jsonify
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

app = Flask(__name__)

# Aapka purana Token
BOT_TOKEN = "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8"

tg_app = None

def get_tg_app():
    global tg_app
    if not tg_app:
        tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        def get_custom_keyboard():
            # 5 Buttons ka 2-1-2 Layout
            keyboard = [
                [KeyboardButton("Laugh"), KeyboardButton("Cool")],
                [KeyboardButton("Rocket")],
                [KeyboardButton("Fire"), KeyboardButton("Star")]
            ]
            return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        async def start_handler(update: Update, context):
            await update.message.reply_text("Ek emoji name select karein:", reply_markup=get_custom_keyboard())

        # /maker command handler
        async def maker_handler(update: Update, context):
            maker_text = """⚡️ Pʏᴛʜᴏɴ Mᴀᴋᴇʀ

━━━━━━━━━━━━━━━━━━━
👨‍💻 Mᴀᴅᴇ Bʏ : Sᴜɴɴʏ
🖥️ Hᴏsᴛᴇᴅ Oɴ : Pʀɪᴠᴀᴛᴇ Sᴇʀᴠᴇʀ
🐍 Rᴜɴɴɪɴɢ Oɴ : Pʏᴛʜᴏɴ 3
⚡️ Sᴛᴀᴛᴜs : Oɴʟɪɴᴇ 🟢
━━━━━━━━━━━━━━━━━━━"""
            await update.message.reply_text(maker_text)

        # Message handler for emojis
        async def message_handler(update: Update, context):
            text = update.message.text
            
            if text == 'Laugh':
                await update.message.reply_text("😂")
            elif text == 'Cool':
                await update.message.reply_text("😎")
            elif text == 'Rocket':
                await update.message.reply_text("🚀")
            elif text == 'Fire':
                await update.message.reply_text("🔥")
            elif text == 'Star':
                await update.message.reply_text("⭐")
            else:
                await update.message.reply_text("Kripya keyboard se koi option select karein.")

        tg_app.add_handler(CommandHandler("start", start_handler))
        tg_app.add_handler(CommandHandler("maker", maker_handler))
        tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
    return tg_app

# --- VERCEL FLASK WEBHOOK SERVER LOGIC ---

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

@app.route('/api/bot_control', methods=['GET', 'POST'])
def control():
    if request.method == 'GET':
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        return jsonify({
            "bot_name": "Emoji & Maker Bot",
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
        print(f"WEBHOOK ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 200
