import os
import json
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8416519129:AAHfVrOHd8V8FUMSCQC3w1NbMKA5sv0qSU8")[span_30](start_span)[span_30](end_span)

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
        # Webhook status check
        info = tg_request("getWebhookInfo")
        webhook_url = info.get("result", {}).get("url", "")
        is_running = bool(webhook_url)

        res_data = {
            "bot_name": "Tasks Payment Bot",[span_31](start_span)[span_31](end_span)
            "admin_id": 8522410574,[span_32](start_span)[span_32](end_span)
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
        body = json.loads(self.rfile.read(content_length).decode('utf-8'))
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
