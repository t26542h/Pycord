from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from aiohttp import web
import aiohttp

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
PORT = int(os.environ.get("PORT", 10000))

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

def clean_name(value: Any, fallback: str, limit: int = 36) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return " ".join(text.split())[:limit]

def discover_lan_hosts() -> list[str]:
    hosts: set[str] = set()
    try:
        hostname = socket.gethostname()
        for item in socket.gethostbyname_ex(hostname)[2]:
            if not item.startswith("127."):
                hosts.add(item)
    except OSError:
        pass
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
    id: str
    nick: str
    websocket: Any
    server_id: str
    text_channel_id: str
    voice_channel_id: str | None = None
    media: dict[str, bool] = field(default_factory=lambda: {"mic": False, "screen": False})

state_lock = asyncio.Lock()
clients: dict[str, Client] = {}
servers: dict[str, dict[str, Any]] = {}

def seed_state() -> None:
    if servers:
        return
    server = make_server("Дом")
    lounge = voice_channel("Разговор")
    stream = voice_channel("Стрим")
    server["voice_channels"] = [lounge, stream]
    general_id = server["text_channels"][0]["id"]
    server["text_channels"].append(text_channel("ideas"))
    server["messages"][general_id].append(
        {
            "id": make_id("msg"),
            "userId": "system",
            "nick": "System",
            "text": "Добро пожаловать. Напишите ник и заходите без регистрации.",
            "at": utc_now(),
        }
    )
    servers[server["id"]] = server

def first_text_channel(server: dict[str, Any]) -> str:
    return server["text_channels"][0]["id"]

def public_user(client: Client) -> dict[str, Any]:
    return {
        "id": client.id,
        "nick": client.nick,
        "serverId": client.server_id,
        "textChannelId": client.text_channel_id,
        "voiceChannelId": client.voice_channel_id,
        "media": dict(client.media),
    }

def public_state() -> dict[str, Any]:
    safe_servers: list[dict[str, Any]] = []
    for server in servers.values():
        safe_servers.append(
            {
                "id": server["id"],
                "name": server["name"],
                "text_channels": list(server["text_channels"]),
                "voice_channels": list(server["voice_channels"]),
                "messages": {
                    channel_id: messages[-100:]
                    for channel_id, messages in server["messages"].items()
                },
            }
        )
    return {"servers": safe_servers, "users": [public_user(client) for client in clients.values()]}

async def send_json(websocket: Any, payload: dict[str, Any]) -> None:
    await websocket.send_str(json.dumps(payload, ensure_ascii=False))

async def broadcast(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False)
    for client in clients.values():
        await client.websocket.send_str(body)

async def broadcast_state() -> None:
    async with state_lock:
        payload = {"type": "state", "state": public_state()}
    await broadcast(payload)

async def broadcast_voice_peers() -> None:
    async with state_lock:
        for client in clients.values():
            peers = [
                public_user(peer)
                for peer in clients.values()
                if peer.id != client.id
                and peer.server_id == client.server_id
                and peer.voice_channel_id == client.voice_channel_id
            ]
            await send_json(client.websocket, {
                "type": "voice_peers",
                "serverId": client.server_id,
                "channelId": client.voice_channel_id,
                "peers": peers,
            })

def get_client_by_ws(websocket: Any) -> Client | None:
    for client in clients.values():
        if client.websocket is websocket:
            return client
    return None

# ТУТ ВСТАВЬ ВСЕ СВОИ ФУНКЦИИ (handle_hello, handle_create_server И Т.Д.)
# Я ОСТАВИЛ ТЕБЕ МЕСТО, ЧТОБЫ ТЫ САМ ВСТАВИЛ ИХ ИЗ ОРИГИНАЛА
# (dispatch, websocket_handler) -> ЗАМЕНЯЕМ НА ws_route НИЖЕ

async def ws_route(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await dispatch(ws, msg.data)
    finally:
        async with state_lock:
            client = get_client_by_ws(ws)
            if client: clients.pop(client.id, None)
        await broadcast_state()
        await broadcast_voice_peers()
    return ws

async def static_route(request):
    path = request.path
    if path == "/config.js":
        return web.Response(text="window.PYCORD_CONFIG = { 'wsHost': window.location.hostname, 'wsPort': '' };", content_type="application/javascript")
    if path == "/": path = "/index.html"
    file_path = STATIC_DIR / path.lstrip("/")
    return web.FileResponse(file_path) if file_path.exists() else web.Response(status=404)

async def init_app():
    seed_state()
    app = web.Application()
    app.add_routes([web.get('/ws', ws_route), web.get('/{tail:.*}', static_route)])
    return app

if __name__ == "__main__":
    web.run_app(init_app(), host='0.0.0.0', port=PORT)
