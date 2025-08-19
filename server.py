import asyncio
import logging
from fastapi import FastAPI
# importa sua função assíncrona main() do bot
from ig_to_discord import main as monitor_main

log = logging.getLogger("uvicorn")
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok", "msg": "insta→discord monitor running"}

@app.get("/healthz")
def health():
    return {"ok": True}

@app.on_event("startup")
async def start_background_monitor():
    # sobe o loop do bot em background
    asyncio.create_task(monitor_main())
    log.info("Background monitor task started.")
