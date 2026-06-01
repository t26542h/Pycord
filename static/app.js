const $ = (selector) => document.querySelector(selector);

const loginView = $("#loginView");
const appView = $("#appView");
const loginForm = $("#loginForm");
const nickInput = $("#nickInput");
const loginError = $("#loginError");
const serverList = $("#serverList");
const serverTitle = $("#serverTitle");
const connectionStatus = $("#connectionStatus");
const textChannels = $("#textChannels");
const voiceChannels = $("#voiceChannels");
const channelTitle = $("#channelTitle");
const messages = $("#messages");
const messageForm = $("#messageForm");
const messageInput = $("#messageInput");
const membersList = $("#membersList");
const mediaTiles = $("#mediaTiles");
const voiceTitle = $("#voiceTitle");
const myAvatar = $("#myAvatar");
const myNick = $("#myNick");
const myState = $("#myState");
const createServerBtn = $("#createServerBtn");
const createTextBtn = $("#createTextBtn");
const createVoiceBtn = $("#createVoiceBtn");
const micBtn = $("#micBtn");
const screenBtn = $("#screenBtn");
const leaveVoiceBtn = $("#leaveVoiceBtn");
const copyInviteBtn = $("#copyInviteBtn");

let socket;
let me = null;
let appState = { servers: [], users: [] };
let selectedServerId = null;
let selectedTextChannelId = null;
let currentVoiceChannelId = null;
let localAudioStream = null;
let localScreenStream = null;
let voicePeerIds = new Set();
const peers = new Map();

const iceServers = [{ urls: "stun:stun.l.google.com:19302" }];

// --- ИСПРАВЛЕННАЯ ФУНКЦИЯ ДЛЯ RENDER ---
function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.hostname;
  // Теперь мы всегда подключаемся к /ws на текущем хосте
  return `${protocol}//${host}/ws`;
}
// ----------------------------------------

function iconRefresh() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function send(type, payload = {}) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ type, ...payload }));
}

function initials(name) {
  return (name || "?").trim().slice(0, 2).toUpperCase();
}

function formatTime(value) {
  try {
    return new Intl.DateTimeFormat("ru", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "";
  }
}

function currentServer() {
  return appState.servers.find((server) => server.id === selectedServerId) || appState.servers[0] || null;
}

function currentTextChannel(server = currentServer()) {
  if (!server) return null;
  return (
    server.text_channels.find((channel) => channel.id === selectedTextChannelId) ||
    server.text_channels[0] ||
    null
  );
}

function usersForServer(serverId) {
  return appState.users.filter((user) => user.serverId === serverId);
}

function usersForVoice(channelId) {
  return appState.users.filter((user) => user.voiceChannelId === channelId);
}

function userById(id) {
  return appState.users.find((user) => user.id === id) || null;
}

function ensureSelection() {
  const server = currentServer();
  if (!server) return;
  selectedServerId = server.id;
  const text = currentTextChannel(server);
  if (text) selectedTextChannelId = text.id;
  const myUser = userById(me?.id);
  currentVoiceChannelId = myUser?.voiceChannelId || null;
}

function render() {
  ensureSelection();
  const server = currentServer();
  if (!server || !me) return;
  const text = currentTextChannel(server);
  const myUser = userById(me.id);

  serverTitle.textContent = server.name;
  channelTitle.textContent = text ? `# ${text.name}` : "#";
  myAvatar.textContent = initials(me.nick);
  myNick.textContent = me.nick;
  myState.textContent = currentVoiceChannelId ? "в голосе" : "в сети";

  serverList.innerHTML = "";
  for (const item of appState.servers) {
    const button = document.createElement("button");
    button.className = `server-btn${item.id === server.id ? " active" : ""}`;
    button.title = item.name;
    button.type = "button";
    button.textContent = initials(item.name);
    button.addEventListener("click", () => {
      selectedServerId = item.id;
      const targetText = item.text_channels[0];
      selectedTextChannelId = targetText?.id || null;
      closeAllPeers();
      stopLocalMedia();
      send("join_server", { serverId: item.id });
      render();
    });
    serverList.append(button);
  }

  renderTextChannels(server);
  renderVoiceChannels(server);
  renderMessages(server, text);
  renderMembers(server);
  renderVoicePanel(server, myUser);
  iconRefresh();
}

function renderTextChannels(server) {
  textChannels.innerHTML = "";
  for (const channel of server.text_channels) {
    const button = document.createElement("button");
    button.className = `channel-btn${channel.id === selectedTextChannelId ? " active" : ""}`;
    button.type = "button";
    button.innerHTML = `<i data-lucide="hash"></i><span></span>`;
    button.querySelector("span").textContent = channel.name;
    button.addEventListener("click", () => {
      selectedTextChannelId = channel.id;
      send("join_text", { serverId: server.id, channelId: channel.id });
      render();
    });
    textChannels.append(button);
  }
}

function renderVoiceChannels(server) {
  voiceChannels.innerHTML = "";
  for (const channel of server.voice_channels) {
    const count = usersForVoice(channel.id).length;
    const button = document.createElement("button");
    button.className = `channel-btn${channel.id === currentVoiceChannelId ? " active" : ""}`;
    button.type = "button";
    button.innerHTML = `<i data-lucide="volume-2"></i><span></span><span class="voice-count"></span>`;
    button.querySelector("span").textContent = channel.name;
    button.querySelector(".voice-count").textContent = count ? String(count) : "";
    button.addEventListener("click", () => joinVoice(server.id, channel.id));
    voiceChannels.append(button);
  }
}

function renderMessages(server, text) {
  const shouldStick = messages.scrollTop + messages.clientHeight >= messages.scrollHeight - 48;
  messages.innerHTML = "";
  const items = text ? server.messages[text.id] || [] : [];
  for (const item of items) {
    const row = document.createElement("article");
    row.className = "message";

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = initials(item.nick);

    const body = document.createElement("div");
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const nick = document.createElement("strong");
    nick.textContent = item.nick;
    const time = document.createElement("time");
    time.dateTime = item.at;
    time.textContent = formatTime(item.at);
    meta.append(nick, time);

    const textNode = document.createElement("div");
    textNode.className = "message-text";
    textNode.textContent = item.text;
    body.append(meta, textNode);
    row.append(avatar, body);
    messages.append(row);
  }
  if (shouldStick) {
    messages.scrollTop = messages.scrollHeight;
  }
}

function renderMembers(server) {
  membersList.innerHTML = "";
  for (const user of usersForServer(server.id)) {
    const row = document.createElement("div");
    row.className = "member";
    row.innerHTML = `
      <div class="avatar"></div>
      <strong></strong>
      <div class="badges">
        <i data-lucide="mic"></i>
        <i data-lucide="monitor-up"></i>
      </div>
    `;
    row.querySelector(".avatar").textContent = initials(user.nick);
    row.querySelector("strong").textContent = user.nick;
    const icons = row.querySelectorAll(".badges i");
    icons[0].classList.toggle("on", Boolean(user.media?.mic));
    icons[1].classList.toggle("on", Boolean(user.media?.screen));
    membersList.append(row);
  }
}

function renderVoicePanel(server, myUser) {
  const voice = server.voice_channels.find((channel) => channel.id === myUser?.voiceChannelId);
  voiceTitle.textContent = voice ? voice.name : "Не подключен";
  micBtn.classList.toggle("active", Boolean(localAudioStream));
  screenBtn.classList.toggle("active", Boolean(localScreenStream));
  micBtn.disabled = !voice;
  screenBtn.disabled = !voice;
  leaveVoiceBtn.disabled = !voice;
  renderMediaTiles();
}

function renderMediaTiles() {
  mediaTiles.innerHTML = "";
  let rendered = false;

  if (localScreenStream) {
    mediaTiles.append(createVideoTile(localScreenStream, `${me.nick} · экран`, true));
    rendered = true;
  }

  for (const [peerId, entry] of peers) {
    const user = userById(peerId);
    const hasVideo = entry.remoteStream.getVideoTracks().some((track) => track.readyState === "live");
    const hasAudio = entry.remoteStream.getAudioTracks().some((track) => track.readyState === "live");
    if (!hasVideo && !hasAudio) continue;
    mediaTiles.append(createVideoTile(entry.remoteStream, user ? user.nick : "Участник", false));
    rendered = true;
  }

  if (!rendered) {
    const empty = document.createElement("div");
    empty.className = "empty-media";
    empty.textContent = currentVoiceChannelId ? "Голосовой канал пуст" : "Выберите голосовой канал";
    mediaTiles.append(empty);
  }
}

function createVideoTile(stream, label, muted) {
  const tile = document.createElement("div");
  tile.className = "media-tile";

  const video = document.createElement("video");
  video.autoplay = true;
  video.playsInline = true;
  video.muted = muted;
  video.srcObject = stream;
  if (!stream.getVideoTracks().length) {
    video.style.display = "none";
  }

  const footer = document.createElement("div");
  footer.className = "media-label";
  const span = document.createElement("span");
  span.textContent = label;
  footer.append(span);

  tile.append(video, footer);
  return tile;
}

function setConnected(connected) {
  connectionStatus.textContent = connected ? "online" : "offline";
  connectionStatus.style.color = connected ? "var(--green)" : "var(--rose)";
}

function connect(nick) {
  loginError.textContent = "";
  socket = new WebSocket(wsUrl());

  socket.addEventListener("open", () => {
    setConnected(true);
    send("hello", { nick });
  });

  socket.addEventListener("message", async (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "welcome") {
      me = data.me;
      appState = data.state;
      selectedServerId = me.serverId;
      selectedTextChannelId = me.textChannelId;
      loginView.classList.add("hidden");
      appView.classList.remove("hidden");
      render();
    } else if (data.type === "state") {
      appState = data.state;
      render();
    } else if (data.type === "voice_peers") {
      await syncVoicePeers(data);
    } else if (data.type === "signal") {
      await handleSignal(data.from, data.data);
    } else if (data.type === "error") {
      loginError.textContent = data.message || "Ошибка";
    }
  });

  socket.addEventListener("close", () => {
    setConnected(false);
    stopLocalMedia();
    closeAllPeers();
  });

  socket.addEventListener("error", () => {
    loginError.textContent = "Не удалось подключиться к серверу.";
  });
}

function askName(title, fallback) {
  const value = window.prompt(title, fallback);
  if (value === null) return null;
  const name = value.trim();
  return name || null;
}

async function joinVoice(serverId, channelId) {
  currentVoiceChannelId = channelId;
  send("join_voice", { serverId, channelId });
  render();
}

function localTrackEntries() {
  const entries = [];
  if (localAudioStream) {
    for (const track of localAudioStream.getTracks()) {
      entries.push({ track, stream: localAudioStream });
    }
  }
  if (localScreenStream) {
    for (const track of localScreenStream.getTracks()) {
      entries.push({ track, stream: localScreenStream });
    }
  }
  return entries;
}

function createPeer(peerId) {
  const pc = new RTCPeerConnection({ iceServers });
  const entry = {
    pc,
    polite: me.id > peerId,
    makingOffer: false,
    ignoreOffer: false,
    isSettingRemoteAnswerPending: false,
    remoteStream: new MediaStream(),
  };
  peers.set(peerId, entry);

  for (const { track, stream } of localTrackEntries()) {
    pc.addTrack(track, stream);
  }

  pc.addEventListener("icecandidate", ({ candidate }) => {
    if (candidate) send("signal", { to: peerId, data: { candidate } });
  });

  pc.addEventListener("track", ({ track }) => {
    entry.remoteStream.addTrack(track);
    track.addEventListener("ended", renderMediaTiles);
    renderMediaTiles();
  });

  pc.addEventListener("connectionstatechange", () => {
    if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
      renderMediaTiles();
    }
  });

  pc.addEventListener("negotiationneeded", async () => {
    try {
      entry.makingOffer = true;
      await pc.setLocalDescription();
      send("signal", { to: peerId, data: { description: pc.localDescription } });
    } catch (error) {
      console.warn("Negotiation failed", error);
    } finally {
      entry.makingOffer = false;
    }
  });

  return entry;
}

function removePeer(peerId) {
  const entry = peers.get(peerId);
  if (!entry) return;
  entry.pc.close();
  peers.delete(peerId);
}

function closeAllPeers() {
  for (const peerId of [...peers.keys()]) {
    removePeer(peerId);
  }
  voicePeerIds = new Set();
  renderMediaTiles();
}

async function syncVoicePeers(data) {
  const myUser = userById(me?.id);
  currentVoiceChannelId = myUser?.voiceChannelId || data.channelId || null;

  if (!currentVoiceChannelId) {
    closeAllPeers();
    return;
  }

  const wanted = new Set((data.peers || []).map((peer) => peer.id));
  for (const peerId of [...peers.keys()]) {
    if (!wanted.has(peerId)) removePeer(peerId);
  }
  for (const peerId of wanted) {
    if (!peers.has(peerId)) createPeer(peerId);
  }
  voicePeerIds = wanted;
  render();
}

async function handleSignal(peerId, data) {
  let entry = peers.get(peerId);
  if (!entry) entry = createPeer(peerId);
  const pc = entry.pc;

  try {
    if (data.description) {
      const description = data.description;
      const readyForOffer =
        !entry.makingOffer && (pc.signalingState === "stable" || entry.isSettingRemoteAnswerPending);
      const offerCollision = description.type === "offer" && !readyForOffer;

      entry.ignoreOffer = !entry.polite && offerCollision;
      if (entry.ignoreOffer) return;

      entry.isSettingRemoteAnswerPending = description.type === "answer";
      await pc.setRemoteDescription(description);
      entry.isSettingRemoteAnswerPending = false;

      if (description.type === "offer") {
        await pc.setLocalDescription();
        send("signal", { to: peerId, data: { description: pc.localDescription } });
      }
    } else if (data.candidate) {
      try {
        await pc.addIceCandidate(data.candidate);
      } catch (error) {
        if (!entry.ignoreOffer) throw error;
      }
    }
  } catch (error) {
    console.warn("Signal failed", error);
  }
}

function refreshPeerTracks() {
  for (const entry of peers.values()) {
    for (const sender of entry.pc.getSenders()) {
      if (sender.track) {
        entry.pc.removeTrack(sender);
      }
    }
    for (const { track, stream } of localTrackEntries()) {
      entry.pc.addTrack(track, stream);
    }
  }
}

function publishMediaState() {
  send("media_state", {
    mic: Boolean(localAudioStream),
    screen: Boolean(localScreenStream),
  });
}

async function toggleMic() {
  if (!currentVoiceChannelId) return;
  if (localAudioStream) {
    localAudioStream.getTracks().forEach((track) => track.stop());
    localAudioStream = null;
  } else {
    localAudioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  }
  refreshPeerTracks();
  publishMediaState();
  render();
}

async function toggleScreen() {
  if (!currentVoiceChannelId) return;
  if (localScreenStream) {
    stopScreen();
  } else {
    localScreenStream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true,
    });
    for (const track of localScreenStream.getTracks()) {
      track.addEventListener("ended", () => {
        if (localScreenStream && !localScreenStream.getTracks().some((item) => item.readyState === "live")) {
          stopScreen();
        }
      });
    }
  }
  refreshPeerTracks();
  publishMediaState();
  render();
}

function stopScreen() {
  if (!localScreenStream) return;
  localScreenStream.getTracks().forEach((track) => track.stop());
  localScreenStream = null;
  refreshPeerTracks();
  publishMediaState();
  render();
}

function stopLocalMedia() {
  if (localAudioStream) {
    localAudioStream.getTracks().forEach((track) => track.stop());
    localAudioStream = null;
  }
  if (localScreenStream) {
    localScreenStream.getTracks().forEach((track) => track.stop());
    localScreenStream = null;
  }
  publishMediaState();
}

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const nick = nickInput.value.trim();
  if (!nick) return;
  connect(nick);
});

messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const server = currentServer();
  const text = currentTextChannel(server);
  const value = messageInput.value.trim();
  if (!server || !text || !value) return;
  send("chat", { serverId: server.id, channelId: text.id, text: value });
  messageInput.value = "";
});

createServerBtn.addEventListener("click", () => {
  const name = askName("Название сервера", "Новый сервер");
  if (name) send("create_server", { name });
});

createTextBtn.addEventListener("click", () => {
  const server = currentServer();
  if (!server) return;
  const name = askName("Название текстового канала", "chat");
  if (name) send("create_channel", { serverId: server.id, kind: "text", name });
});

createVoiceBtn.addEventListener("click", () => {
  const server = currentServer();
  if (!server) return;
  const name = askName("Название голосового канала", "voice");
  if (name) send("create_channel", { serverId: server.id, kind: "voice", name });
});

micBtn.addEventListener("click", async () => {
  try {
    await toggleMic();
  } catch (error) {
    window.alert("Браузер не дал доступ к микрофону.");
  }
});

screenBtn.addEventListener("click", async () => {
  try {
    await toggleScreen();
  } catch (error) {
    window.alert("Демонстрация экрана не запущена.");
  }
});

leaveVoiceBtn.addEventListener("click", () => {
  stopLocalMedia();
  closeAllPeers();
  currentVoiceChannelId = null;
  send("leave_voice");
  render();
});

copyInviteBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(window.location.href);
    copyInviteBtn.querySelector("span").textContent = "Скопировано";
    setTimeout(() => {
      copyInviteBtn.querySelector("span").textContent = "Адрес";
    }, 1200);
  } catch {
    window.prompt("Адрес", window.location.href);
  }
});

window.addEventListener("beforeunload", () => {
  stopLocalMedia();
  closeAllPeers();
});

iconRefresh();
