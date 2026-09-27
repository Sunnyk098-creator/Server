import os
import sys
import asyncio
import subprocess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

# System variables
active_bots = {}
log_history = []
active_connections = set()

async def broadcast_log(bot_name, line):
    msg = f"[{bot_name}] {line}"
    log_history.append(msg)
    # Memory limit for logs
    if len(log_history) > 1000:
        log_history.pop(0)
    for conn in list(active_connections):
        try:
            await conn.send_text(msg)
        except Exception:
            active_connections.discard(conn)

async def stream_output(bot_name, pipe):
    while True:
        line = await pipe.readline()
        if not line:
            break
        decoded_line = line.decode('utf-8', errors='ignore')
        await broadcast_log(bot_name, decoded_line)

@app.get("/api/bots")
async def get_bots():
    bots = []
    # Auto-detect all Python files in the directory
    for file in os.listdir("."):
        if file.endswith(".py") and file != "server.py":
            is_running = file in active_bots and active_bots[file].returncode is None
            bots.append({
                "name": file,
                "running": is_running,
                "pid": active_bots[file].pid if is_running else "None"
            })
    return {"bots": bots}

class BotAction(BaseModel):
    bot_name: str

@app.post("/api/start")
async def start_bot(action: BotAction):
    bot_name = action.bot_name
    if not os.path.exists(bot_name):
        return {"error": "File not found"}
    if bot_name in active_bots and active_bots[bot_name].returncode is None:
        return {"status": "Already running"}
    
    # Run the bot in an unbuffered subprocess
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", bot_name,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    active_bots[bot_name] = proc
    asyncio.create_task(stream_output(bot_name, proc.stdout))
    await broadcast_log("SYSTEM", f"Process Started: {bot_name} (PID: {proc.pid})\n")
    return {"status": "Started"}

@app.post("/api/stop")
async def stop_bot(action: BotAction):
    bot_name = action.bot_name
    if bot_name in active_bots:
        proc = active_bots[bot_name]
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
            await broadcast_log("SYSTEM", f"Process Stopped: {bot_name}\n")
            return {"status": "Stopped"}
    return {"error": "Not running"}

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        for log in log_history:
            await websocket.send_text(log)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.discard(websocket)

# Frontend routing
os.makedirs("public", exist_ok=True)
app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/")
async def root():
    return FileResponse("public/index.html")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("server.py:app", host="0.0.0.0", port=port, reload=False)
