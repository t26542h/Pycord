from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import websockets
except ImportError as exc:
    raise SystemExit(
        "The 'websockets' package is required. Run: python -m pip install -r requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

# --- ИСПРАВЛЕННЫЕ НАСТРОЙКИ ДЛЯ RENDER ---
# Берем порт из переменной среды Render, по умолчанию 8000
PORT = int(os.environ.get("PORT", 8000))
# Слушаем все интерфейсы для работы в облаке
HOST = "0.0.0.0"

MAX_MESSAGES_PER_CHANNEL = 100

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
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        if not address.startswith("127."):
            hosts.add(address)
    except OSError:
        pass
    finally:
        if probe is not None:
            try: probe.close()
            except Exception: pass
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
    if servers: return
    server = make_server("Дом")
    lounge = voice_channel("Разговор")
    stream = voice_channel("Стрим")
    server["voice_channels"] = [lounge, stream]
    general_id = server["text_channels"][0]["id"]
    server["text_channels"].append(text_channel("ideas"))
    server["messages"][general_id].append({
        "id": make_id("msg"), "userId": "system", "nick": "System", 
        "text": "Добро пожаловать. Напишите ник и заходите без регистрации.", "at": utc_now(),
    })
    servers[server["id"]] = server

def first_text_channel(server: dict[str, Any]) -> str: return server["text_channels"][0]["id"]
def public_user(client: Client) -> dict[str, Any]: return {"id": client.id, "nick": client.nick, "serverId": client.server_id, "textChannelId": client.text_channel_id, "voiceChannelId": client.voice_channel_id, "media": dict(client.media)}
def public_state() -> dict[str, Any]:
    safe_servers = []
    for server in servers.values():
        safe_servers.append({
            "id": server["id"], "name": server["name"], 
            "text_channels": list(server["text_channels"]), "voice_channels": list(server["voice_channels"]),
            "messages": {cid: messages[-MAX_MESSAGES_PER_CHANNEL:] for cid, messages in server["messages"].items()},
        })
    return {"servers": safe_servers, "users": [public_user(client) for client in clients.values()]}

async def send_json(websocket: Any, payload: dict[str, Any]) -> None: await websocket.send(json.dumps(payload, ensure_ascii=False))
async def broadcast(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False)
    recipients = [client.websocket for client in clients.values()]
    if not recipients: return
    await asyncio.gather(*(recipient.send(body) for recipient in recipients), return_exceptions=True)

async def broadcast_state() -> None:
    async with state_lock: payload = {"type": "state", "state": public_state()}
    await broadcast(payload)

async def broadcast_voice_peers() -> None:
    async with state_lock:
        snapshots = []
        for client in clients.values():
            peers = [public_user(peer) for peer in clients.values() if peer.id != client.id and peer.server_id == client.server_id and peer.voice_channel_id and peer.voice_channel_id == client.voice_channel_id]
            snapshots.append((client.websocket, {"type": "voice_peers", "serverId": client.server_id, "channelId": client.voice_channel_id, "peers": peers}))
    await asyncio.gather(*(send_json(websocket, payload) for websocket, payload in snapshots), return_exceptions=True)

def get_client_by_ws(websocket: Any) -> Client | None:
    return next((c for c in clients.values() if c.websocket is websocket), None)

async def handle_hello(websocket: Any, data: dict[str, Any]) -> Client:
    nick = clean_name(data.get("nick"), "Guest", 24)
    async with state_lock:
        seed_state()
        default_server = next(iter(servers.values()))
        client = Client(id=make_id("usr"), nick=nick, websocket=websocket, server_id=default_server["id"], text_channel_id=first_text_channel(default_server))
        clients[client.id] = client
        welcome = {"type": "welcome", "me": public_user(client), "state": public_state()}
    await send_json(websocket, welcome)
    await broadcast_state()
    return client

async def handle_create_server(client: Client, data: dict[str, Any]) -> None:
    async with state_lock:
        server = make_server(data.get("name", "Server"))
        servers[server["id"]] = server
        client.server_id = server["id"]
        client.text_channel_id = first_text_channel(server)
    await broadcast_state()
    await broadcast_voice_peers()

async def handle_join_server(client: Client, data: dict[str, Any]) -> None:
    server_id = str(data.get("serverId", ""))
    async with state_lock:
        if server_id in servers:
            client.server_id = server_id
            client.text_channel_id = first_text_channel(servers[server_id])
    await broadcast_state()
    await broadcast_voice_peers()

async def handle_create_channel(client: Client, data: dict[str, Any]) -> None:
    server_id = str(data.get("serverId", client.server_id))
    kind = str(data.get("kind", "text"))
    async with state_lock:
        if server_id in servers:
            if kind == "voice": servers[server_id]["voice_channels"].append(voice_channel(data.get("name", "voice")))
            else:
                c = text_channel(data.get("name", "chat"))
                servers[server_id]["text_channels"].append(c)
                servers[server_id]["messages"][c["id"]] = []
    await broadcast_state()

async def handle_join_text(client: Client, data: dict[str, Any]) -> None:
    server_id, channel_id = str(data.get("serverId", client.server_id)), str(data.get("channelId", ""))
    async with state_lock:
        if server_id in servers and channel_id in servers[server_id]["messages"]:
            client.server_id, client.text_channel_id = server_id, channel_id
    await broadcast_state()

async def handle_chat(client: Client, data: dict[str, Any]) -> None:
    server_id, channel_id, text = str(data.get("serverId", client.server_id)), str(data.get("channelId", client.text_channel_id)), str(data.get("text", ""))[:2000]
    async with state_lock:
        if server_id in servers and channel_id in servers[server_id]["messages"]:
            servers[server_id]["messages"][channel_id].append({"id": make_id("msg"), "userId": client.id, "nick": client.nick, "text": text, "at": utc_now()})
            del servers[server_id]["messages"][channel_id][:-MAX_MESSAGES_PER_CHANNEL]
    await broadcast_state()

async def handle_join_voice(client: Client, data: dict[str, Any]) -> None:
    server_id, channel_id = str(data.get("serverId", client.server_id)), str(data.get("channelId", ""))
    async with state_lock:
        if server_id in servers and any(c["id"] == channel_id for c in servers[server_id]["voice_channels"]):
            client.server_id, client.voice_channel_id = server_id, channel_id
    await broadcast_state()
    await broadcast_voice_peers()

async def handle_leave_voice(client: Client) -> None:
    async with state_lock: client.voice_channel_id, client.media = None, {"mic": False, "screen": False}
    await broadcast_state()
    await broadcast_voice_peers()

async def handle_media_state(client: Client, data: dict[str, Any]) -> None:
    async with state_lock: client.media = {"mic": bool(data.get("mic")), "screen": bool(data.get("screen"))}
    await broadcast_state()

async def handle_signal(client: Client, data: dict[str, Any]) -> None:
    target_id, s_data = str(data.get("to", "")), data.get("data")
    if target_id and isinstance(s_data, dict):
        async with state_lock:
            target = clients.get(target_id)
            if target and target.server_id == client.server_id and target.voice_channel_id == client.voice_channel_id:
                await send_json(target.websocket, {"type": "signal", "from": client.id, "data": s_data})

async def dispatch(websocket: Any, raw: str) -> None:
    try: data = json.loads(raw)
    except: return
    client = get_client_by_ws(websocket)
    if data.get("type") == "hello" and not client: await handle_hello(websocket, data)
    elif client:
        handlers = {
            "create_server": handle_create_server, "join_server": handle_join_server,
            "create_channel": handle_create_channel, "join_text": handle_join_text,
            "chat": handle_chat, "join_voice": handle_join_voice,
            "media_state": handle_media_state, "signal": handle_signal,
            "leave_voice": lambda c, d: handle_leave_voice(c)
        }
        h = handlers.get(data.get("type"))
        if h: await h(client, data)

async def websocket_handler(websocket: Any) -> None:
    try:
        async for raw in websocket: await dispatch(websocket, raw)
    except Exception: pass # Игнорируем ошибки от пингов Render
    finally:
        async with state_lock:
            c = get_client_by_ws(websocket)
            if c:
                clients.pop(c.id, None)
                await broadcast_state()
                await broadcast_voice_peers()

class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None: super().__init__(*args, directory=str(STATIC_DIR), **kwargs)
    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/config.js":
            body = f"window.PYCORD_CONFIG = {{ \"wsHost\": window.location.hostname, \"wsPort\": {PORT} }};".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

def start_http_server() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((HOST, PORT), AppHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd

async def run_websocket_server() -> None:
    async with websockets.serve(websocket_handler, HOST, PORT, max_size=2_000_000):
        await asyncio.Future()

def main() -> None:
    seed_state()
    start_http_server()
    print(f"Сервер запущен на {HOST}:{PORT}")
    try: asyncio.run(run_websocket_server())
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
