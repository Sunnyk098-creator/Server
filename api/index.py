import os
import json
import asyncio
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8")
MAIN_ADMIN_ID = 8522410574

app = ApplicationBuilder().token(BOT_TOKEN).build()

def get_main_keyboard():
    keyboard = [
        ["👤 My Account", "🎟️ My Gift card"],
        ["🎁 Gift Code", "🎉 Pay to User"],
        ["📋 Task section", "🚀 Withdraw"],
        ["🛒 Buy Gift Card"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start_handler(update: Update, context):
    welcome_text = "✨ Welcome To Tasks Payment Bot"
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

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
            [InlineKeyboardButton("🪪 Balance Records", callback_data="usr_tx_page_0"), InlineKeyboardButton("📄 Withdrawal History", callback_data="usr_wd_page_0")],
            [InlineKeyboardButton("📞 Support", callback_data="usr_support")]
        ]
        await update.message.reply_text(f"🚀 Wallet Summary\n\n👤 User ID -> {uid}\n💸 Balance : ₹0.00\n🔐 Keeper Balance : ₹0.00", reply_markup=InlineKeyboardMarkup(kb))
    elif text == "📋 Task section":
        await update.message.reply_text("⚠️ No tasks available right now. Please check back later!")
    elif text == "🚀 Withdraw":
        await update.message.reply_text("👇🏻 Send Your UPI ID Or Wallet Number To Initiate Withdrawal:")
    elif text == "🎉 Pay to User":
        await update.message.reply_text("🆔 Please enter the User ID(s) you want to pay:\n👉 Example: `8522410574 50`", parse_mode="Markdown")
    elif text == "🎁 Gift Code":
        await update.message.reply_text("🔍 You don’t have any active Gift Codes.\n\nCreate your first one now!")
    elif text == "🛒 Buy Gift Card":
        await update.message.reply_text("🚫 No gift card available right now.")

app.add_handler(CommandHandler("start", start_handler))
app.add_handler(CommandHandler("adminpanel", admin_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

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

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # /api/bot_control path saathi
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        is_running = bool(webhook_url)

        res_data = {
            "bot_name": "Tasks Payment Bot",
            "admin_id": MAIN_ADMIN_ID,
            "is_running": is_running,
            "webhook_url": webhook_url
        }
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(res_data).encode('utf-8'))

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        # Webhook kiva Control Action olkha
        if "/api/webhook" in self.path or self.path == "/api/":
            try:
                update_data = json.loads(post_data.decode('utf-8'))
                update = Update.de_json(update_data, app.bot)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(app.process_update(update))
                loop.close()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
        else:
            # Bot control (Start / Stop Webhook)
            try:
                body = json.loads(post_data.decode('utf-8'))
                action = body.get("action")
                domain = body.get("domain", "").rstrip('/')

                if action == "start":
                    webhook_url = f"{domain}/api/webhook"
                    res = tg_request("setWebhook", {"url": webhook_url})
                elif action == "stop":
                    res = tg_request("deleteWebhook")
                else:
                    res = {"ok": False, "error": "Invalid action"}

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
