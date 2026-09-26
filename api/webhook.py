import os
import json
import asyncio
from http.server import BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8")[span_0](start_span)[span_0](end_span)
MAIN_ADMIN_ID = 8522410574[span_1](start_span)[span_1](end_span)

# Telegram bot initialization
app = ApplicationBuilder().token(BOT_TOKEN).build()

def get_main_keyboard():
    keyboard = [
        ["👤 My Account", "🎟️ My Gift card"],[span_2](start_span)[span_2](end_span)
        ["🎁 Gift Code", "🎉 Pay to User"],[span_3](start_span)[span_3](end_span)
        ["📋 Task section", "🚀 Withdraw"],[span_4](start_span)[span_4](end_span)
        ["🛒 Buy Gift Card"][span_5](start_span)[span_5](end_span)
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)[span_6](start_span)[span_6](end_span)

async def start_handler(update: Update, context):
    welcome_text = "✨ Welcome To Tasks Payment Bot[span_7](start_span)"[span_7](end_span)
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())[span_8](start_span)[span_8](end_span)

async def admin_handler(update: Update, context):
    if update.effective_user.id != MAIN_ADMIN_ID:[span_9](start_span)[span_9](end_span)
        await update.message.reply_text("❌ Access Denied!")[span_10](start_span)[span_10](end_span)
        return
    await update.message.reply_text("🔍 Welcome To Admin Panel\n\nMain Owner: 8522410574\nBot Status: Active")[span_11](start_span)[span_11](end_span)

async def message_handler(update: Update, context):
    text = update.message.text
    uid = update.effective_user.id

    if text == "👤 My Account":[span_12](start_span)[span_12](end_span)
        kb = [
            [InlineKeyboardButton("➕ Add Fund", callback_data="usr_addf")],[span_13](start_span)[span_13](end_span)
            [InlineKeyboardButton("🪪 Balance Records", callback_data="usr_tx_page_0"), InlineKeyboardButton("📄 Withdrawal History", callback_data="usr_wd_page_0")],[span_14](start_span)[span_14](end_span)
            [InlineKeyboardButton("📞 Support", callback_data="usr_support")][span_15](start_span)[span_15](end_span)
        ]
        await update.message.reply_text(f"🚀 Wallet Summary\n\n👤 User ID -> {uid}\n💸 Balance : ₹0.00\n🔐 Keeper Balance : ₹0.00", reply_markup=InlineKeyboardMarkup(kb))[span_16](start_span)[span_16](end_span)

    elif text == "📋 Task section":[span_17](start_span)[span_17](end_span)
        await update.message.reply_text("⚠️ No tasks available right now. Please check back later!")[span_18](start_span)[span_18](end_span)

    elif text == "🚀 Withdraw":[span_19](start_span)[span_19](end_span)
        await update.message.reply_text("👇🏻 Send Your UPI ID Or Wallet Number To Initiate Withdrawal:")[span_20](start_span)[span_20](end_span)

    elif text == "🎉 Pay to User":[span_21](start_span)[span_21](end_span)
        await update.message.reply_text("🆔 Please enter the User ID(s) you want to pay:\n👉 Example: `8522410574 50`", parse_mode="Markdown")[span_22](start_span)[span_22](end_span)

    elif text == "🎁 Gift Code":[span_23](start_span)[span_23](end_span)
        await update.message.reply_text("🔍 You don’t have any active Gift Codes.\n\nCreate your first one now!")[span_24](start_span)[span_24](end_span)

    elif text == "🛒 Buy Gift Card":[span_25](start_span)[span_25](end_span)
        await update.message.reply_text("🚫 No gift card available right now.")[span_26](start_span)[span_26](end_span)

# Handlers register
app.add_handler(CommandHandler("start", start_handler))[span_27](start_span)[span_27](end_span)
app.add_handler(CommandHandler("adminpanel", admin_handler))[span_28](start_span)[span_28](end_span)
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))[span_29](start_span)[span_29](end_span)

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

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
