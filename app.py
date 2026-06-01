from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from aiohttp import web
import aiohttp

# --- НАСТРОЙКИ ---
PORT = int(os.environ.get("PORT", 10000))
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_MESSAGES_PER_CHANNEL = 100

# --- ТВОИ ОРИГИНАЛЬНЫЕ ФУНКЦИИ ---
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

def clean_name(value: Any, fallback: str, limit: int = 36) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())[:limit] if text else fallback

def discover_lan_hosts() -> list[str]:
    hosts = set()
    try:
        hostname = socket.gethostname()
        for item in socket.gethostbyname_ex(hostname)[2]:
            if not item.startswith("127."): hosts.add(item)
    except OSError: pass
    return sorted(hosts)

def text_channel(name: str) -> dict[str, str]:
    return {"id": make_id("txt"), "name": clean_name(name, "chat")}

def voice_channel(name: str) -> dict[str, str]:
    return {"id": make_id("vox"), "name": clean_name(name, "voice")}

def make_server(name: str) -> dict[str, Any]:
    general_text = text_channel("general")
    voice = voice_channel("lobby")
    return {
        "id": make_id("srv"),
        "name": clean_name(name, "Server"),
        "text_channels": [general_text],
        "voice_channels": [voice],
        "messages": {general_text["id"]: []},
    }

@dataclass
class Client:
    id: str; nick: str; websocket: Any; server_id: str; text_channel_id: str
    voice_channel_id: str | None = None
    media: dict[str, bool] = field(default_factory=lambda: {"mic": False, "screen": False})

state_lock = asyncio.Lock()
clients: dict[str, Client] = {}
servers: dict[str, dict[str, Any]] = {}

def seed_state() -> None:
    if servers: return
    server = make_server("Дом")
    lounge = voice_channel("Разговор")
    stream = voice_channel("Стрим")
    server["voice_channels"] = [lounge, stream]
    general_id = server["text_channels"][0]["id"]
    server["text_channels"].append(text_channel("ideas"))
    server["messages"][general_id].append({
        "id": make_id("msg"), "userId": "system", "nick": "System",
        "text": "Добро пожаловать. Напишите ник и заходите без регистрации.", "at": utc_now()
    })
    servers[server["id"]] = server

def first_text_channel(server: dict[str, Any]) -> str: return server["text_channels"][0]["id"]

def public_user(client: Client) -> dict[str, Any]:
    return {"id": client.id, "nick": client.nick, "serverId": client.server_id, "textChannelId": client.text_channel_id, "voiceChannelId": client.voice_channel_id, "media": dict(client.media)}

def public_state() -> dict[str, Any]:
    safe_servers = []
    for s in servers.values():
        safe_servers.append({
            "id": s["id"], "name": s["name"], "text_channels": list(s["text_channels"]),
            "voice_channels": list(s["voice_channels"]),
            "messages": {cid: msgs[-MAX_MESSAGES_PER_CHANNEL:] for cid, msgs in s["messages"].items()}
        })
    return {"servers": safe_servers, "users": [public_user(c) for c in clients.values()]}

async def send_json(ws: Any, p: dict[str, Any]) -> None: await ws.send_str(json.dumps(p, ensure_ascii=False))
async def broadcast(p: dict[str, Any]) -> None:
    body = json.dumps(p, ensure_ascii=False)
    for c in clients.values(): await c.websocket.send_str(body)

async def broadcast_state() -> None:
    async with state_lock: p = {"type": "state", "state": public_state()}
    await broadcast(p)

async def broadcast_voice_peers() -> None:
    async with state_lock:
        for c in clients.values():
            peers = [public_user(p) for p in clients.values() if p.id != c.id and p.server_id == c.server_id and p.voice_channel_id == c.voice_channel_id]
            await send_json(c.websocket, {"type": "voice_peers", "serverId": c.server_id, "channelId": c.voice_channel_id, "peers": peers})

def get_client_by_ws(ws: Any) -> Client | None: return next((c for c in clients.values() if c.websocket is ws), None)

# --- ТВОИ ОБРАБОТЧИКИ ---
async def handle_hello(ws, d):
    nick = clean_name(d.get("nick"), "Guest", 24)
    async with state_lock:
        seed_state(); srv = next(iter(servers.values()))
        c = Client(make_id("usr"), nick, ws, srv["id"], first_text_channel(srv))
        clients[c.id] = c
    await send_json(ws, {"type": "welcome", "me": public_user(c), "state": public_state()})
    await broadcast_state()

async def handle_create_server(c, d):
    srv = make_server(d.get("name", "Server"))
    async with state_lock: servers[srv["id"]] = srv
    c.server_id = srv["id"]; c.text_channel_id = first_text_channel(srv)
    await broadcast_state(); await broadcast_voice_peers()

async def handle_join_server(c, d):
    sid = str(d.get("serverId", "")); srv = servers.get(sid)
    if srv: c.server_id = sid; c.text_channel_id = first_text_channel(srv)
    await broadcast_state(); await broadcast_voice_peers()

async def handle_create_channel(c, d):
    srv = servers.get(d.get("serverId", c.server_id))
    if srv:
        if d.get("kind") == "voice": srv["voice_channels"].append(voice_channel(d.get("name", "voice")))
        else: ch = text_channel(d.get("name", "chat")); srv["text_channels"].append(ch); srv["messages"][ch["id"]] = []
    await broadcast_state()

async def handle_join_text(c, d):
    c.server_id = d.get("serverId", c.server_id); c.text_channel_id = d.get("channelId", c.text_channel_id)
    await broadcast_state()

async def handle_chat(c, d):
    srv = servers.get(d.get("serverId", c.server_id)); chid = d.get("channelId", c.text_channel_id)
    if srv and chid in srv["messages"]:
        srv["messages"][chid].append({"id": make_id("msg"), "userId": c.id, "nick": c.nick, "text": str(d.get("text", ""))[:2000], "at": utc_now()})
    await broadcast_state()

async def handle_join_voice(c, d):
    c.voice_channel_id = d.get("channelId"); await broadcast_state(); await broadcast_voice_peers()

async def handle_leave_voice(c):
    c.voice_channel_id = None; await broadcast_state(); await broadcast_voice_peers()

async def handle_media_state(c, d):
    c.media = {"mic": bool(d.get("mic")), "screen": bool(d.get("screen"))}
    await broadcast_state()

async def handle_signal(c, d):
    t = clients.get(d.get("to"))
    if t and t.server_id == c.server_id and t.voice_channel_id == c.voice_channel_id:
        await send_json(t.websocket, {"type": "signal", "from": c.id, "data": d.get("data")})

async def dispatch(ws, raw):
    try: d = json.loads(raw)
    except: return
    c = get_client_by_ws(ws)
    if d.get("type") == "hello": await handle_hello(ws, d)
    elif c:
        handlers = {"create_server": handle_create_server, "join_server": handle_join_server, "create_channel": handle_create_channel, "join_text": handle_join_text, "chat": handle_chat, "join_voice": handle_join_voice, "media_state": handle_media_state, "signal": handle_signal}
        if d.get("type") == "leave_voice": await handle_leave_voice(c)
        elif d.get("type") in handlers: await handlers[d["type"]](c, d)

# --- ЗАПУСК ---
async def ws_route(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT: await dispatch(ws, msg.data)
    finally:
        async with state_lock:
            c = get_client_by_ws(ws)
            if c: clients.pop(c.id, None)
        await broadcast_state(); await broadcast_voice_peers()
    return ws

async def static_route(request):
    path = request.path
    if path == "/config.js": return web.Response(text="window.PYCORD_CONFIG = { 'wsHost': window.location.hostname, 'wsPort': '' };", content_type="application/javascript")
    if path == "/": path = "/index.html"
    f = STATIC_DIR / path.lstrip("/")
    return web.FileResponse(f) if f.exists() else web.Response(status=404)

async def init_app():
    seed_state()
    app = web.Application()
    app.add_routes([web.get('/ws', ws_route), web.get('/{tail:.*}', static_route)])
    return app

if __name__ == "__main__": web.run_app(init_app(), host='0.0.0.0', port=PORT)
