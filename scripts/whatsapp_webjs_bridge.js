const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { Client, LocalAuth } = require('whatsapp-web.js');

function parseArgs() {
  const values = {};
  for (let index = 2; index < process.argv.length; index += 2) values[process.argv[index]] = process.argv[index + 1];
  return values;
}

const args = parseArgs();
const stateDir = path.resolve(args['--state-dir']);
const authDir = path.resolve(args['--auth-dir']);
const statusPath = path.join(stateDir, 'status.json');
const requestPath = path.join(stateDir, 'request.json');
const responsePath = path.join(stateDir, 'response.json');
const incomingDir = path.join(stateDir, 'incoming');
const chromePath = process.env.NOOR_CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
fs.mkdirSync(stateDir, { recursive: true });
fs.mkdirSync(incomingDir, { recursive: true });

let connected = false;
let loginVisible = false;
let authenticated = false;
let connectionState = 'INITIALIZING';
let lastRequestId = '';
let requestInFlight = false;
let activeRequestId = '';
let activeRequestStartedAt = 0;
let diagnostics = {
  message_events: 0,
  call_events: 0,
  incoming_events: 0,
  ignored_events: 0,
  last_incoming_at: '',
  last_incoming_hash: '',
  last_ignored_reason: '',
  last_request_action: '',
  last_response_message: '',
  last_unread_scan_at: '',
  last_unread_count: 0,
  active_request_id: '',
  active_request_age_ms: 0,
};

function serializedId(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value._serialized === 'string') return value._serialized;
  if (value.id) return serializedId(value.id);
  return String(value);
}

function directChatId(value) {
  const chatId = serializedId(value);
  return (chatId.endsWith('@c.us') || chatId.endsWith('@lid')) ? chatId : '';
}

function digitsOnly(value) {
  return String(value || '').replace(/\D+/g, '');
}

function normalizedText(value) {
  return String(value || '').trim().toLowerCase();
}

function chatTitle(chat) {
  return String(chat && (chat.name || chat.formattedTitle || chat.pushname || '') || '').trim();
}

function messageHash(message) {
  const messageId = serializedId(message.id) || `${serializedId(message.from)}-${message.timestamp || Date.now()}`;
  return crypto.createHash('sha256').update(`${messageId}\n${message.body || ''}`).digest('hex');
}

function writeJson(filePath, value) {
  const encoded = JSON.stringify(value);
  // Windows can lock a file briefly while the PySide bridge reads it. Status and
  // response files are short-lived IPC state, so in-place writes avoid a bridge crash.
  if (filePath === statusPath || filePath === responsePath) {
    fs.writeFileSync(filePath, encoded);
    return;
  }
  const temporary = `${filePath}.tmp`;
  fs.writeFileSync(temporary, encoded);
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      fs.renameSync(temporary, filePath);
      return;
    } catch (error) {
      if (attempt === 9) {
        fs.writeFileSync(filePath, encoded);
        return;
      }
    }
  }
}

function writeStatus() {
  diagnostics.active_request_id = activeRequestId;
  diagnostics.active_request_age_ms = activeRequestStartedAt ? Date.now() - activeRequestStartedAt : 0;
  writeJson(statusPath, {
    connected,
    authenticated,
    login_visible: loginVisible,
    connection_state: connectionState,
    backend: 'whatsapp-web.js',
    process_id: process.pid,
    updated_at: Date.now() / 1000,
    diagnostics,
  });
}

const client = new Client({
  authStrategy: new LocalAuth({ clientId: 'noor', dataPath: authDir }),
  puppeteer: { headless: false, executablePath: chromePath, args: ['--no-sandbox', '--disable-setuid-sandbox'] },
});

client.on('qr', () => { loginVisible = true; authenticated = false; });
client.on('authenticated', () => { authenticated = true; loginVisible = false; });
client.on('ready', () => { connected = true; authenticated = true; loginVisible = false; });
client.on('auth_failure', () => { connected = false; authenticated = false; });
client.on('disconnected', () => { connected = false; authenticated = false; });
client.on('change_state', (state) => {
  connectionState = String(state || 'UNKNOWN');
  if (connectionState === 'CONNECTED') connected = true;
});
function writeIncomingEvent(message, fallback = {}) {
  diagnostics.message_events += 1;
  const eventType = String(fallback.eventType || 'message');
  if (eventType === 'call') diagnostics.call_events += 1;
  if (eventType !== 'call' && message.fromMe) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'from_me';
    writeStatus();
    return;
  }
  const chatId = String(fallback.chatId || serializedId(message.from) || '');
  if (!chatId || chatId.endsWith('@g.us') || fallback.isGroup) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = chatId ? 'group_chat' : 'missing_chat_id';
    writeStatus();
    return;
  }
  const body = String(fallback.body || message.body || '');
  if (eventType !== 'call' && !body.trim()) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'empty_body';
    writeStatus();
    return;
  }
  const eventId = messageHash(message);
  const eventPath = path.join(incomingDir, `${eventId}.json`);
  if (fs.existsSync(eventPath)) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'duplicate_event_file';
    writeStatus();
    return;
  }
  writeJson(eventPath, {
    event_id: eventId,
    message_id: serializedId(message.id),
    event_type: eventType,
    event_subtype: String(fallback.eventSubtype || ''),
    chat_id: chatId,
    chat_label: String(fallback.chatLabel || 'Direct contact'),
    body: body.slice(0, 4000),
    received_at: new Date().toISOString(),
  });
  diagnostics.incoming_events += 1;
  diagnostics.last_incoming_at = new Date().toISOString();
  diagnostics.last_incoming_hash = eventId;
  diagnostics.last_ignored_reason = '';
  writeStatus();
}

client.on('message', writeIncomingEvent);
client.on('message_create', writeIncomingEvent);
client.on('call', (call) => {
  const chatId = directChatId(call && call.from);
  writeIncomingEvent(
    {
      id: { _serialized: `call-${chatId}-${call && (call.id || call.timestamp) || Date.now()}` },
      from: chatId,
      fromMe: false,
      body: 'Incoming WhatsApp call',
      timestamp: Date.now(),
    },
    { eventType: 'call', eventSubtype: 'incoming', chatId, chatLabel: 'Direct contact', body: 'Incoming WhatsApp call' },
  );
});

async function promiseTimeout(promise, milliseconds, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out.`)), milliseconds);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

function readableIncomingMessages(messages) {
  return (messages || [])
    .filter((message) => message && !message.fromMe && String(message.body || '').trim())
    .map((message) => ({
      id: serializedId(message.id) || `${serializedId(message.from)}-${message.timestamp || Date.now()}`,
      text: String(message.body || '').slice(0, 4000),
    }));
}

async function messagesForUnreadChat(chat) {
  const unreadCount = Math.max(1, Math.min(Number(chat.unreadCount || 1), 10));
  let messages = [];
  try {
    if (typeof chat.fetchMessages === 'function') {
      messages = await promiseTimeout(chat.fetchMessages({ limit: unreadCount, fromMe: false }), 10000, 'Unread message load');
    }
  } catch (error) {
    diagnostics.last_ignored_reason = `fetch_messages_failed:${String(error.message || error).slice(0, 80)}`;
  }
  if (!messages.length && chat.lastMessage) messages = [chat.lastMessage];
  return readableIncomingMessages(messages).slice(-unreadCount);
}

async function writeUnreadChatEvent(chat) {
  try {
    if (!connected || !chat || chat.isGroup) return;
    const chatId = serializedId(chat.id);
    if (!chatId || (!chatId.endsWith('@c.us') && !chatId.endsWith('@lid'))) return;
    const messages = await messagesForUnreadChat(chat);
    for (const message of messages) {
      writeIncomingEvent(
        {
          id: { _serialized: message.id },
          from: chatId,
          fromMe: false,
          body: message.text,
          timestamp: Date.now(),
        },
        { chatId, chatLabel: chat.name || chat.formattedTitle || 'Direct contact', isGroup: false },
      );
    }
  } catch (error) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = `unread_event_failed:${String(error.message || error).slice(0, 100)}`;
    writeStatus();
  }
}

client.on('unread_count', (chat) => {
  writeUnreadChatEvent(chat);
});

async function nextUnreadPayload() {
  const chats = await promiseTimeout(client.getChats(), 10000, 'Unread WhatsApp chat scan');
  const chat = chats.find((item) => {
    const chatId = serializedId(item.id);
    return !item.isGroup && !item.isReadOnly && Number(item.unreadCount || 0) > 0 && (chatId.endsWith('@c.us') || chatId.endsWith('@lid'));
  });
  if (!chat) {
    diagnostics.last_unread_scan_at = new Date().toISOString();
    diagnostics.last_unread_count = 0;
    return { ok: true, message: 'No unread WhatsApp direct chats found.', data: { has_unread: false }, error: '' };
  }
  const messages = await messagesForUnreadChat(chat);
  const incoming = messages.map((message) => ({
    hash: crypto.createHash('sha256').update(`${message.id}\n${message.text}`).digest('hex'),
    text: message.text,
  }));
  diagnostics.last_unread_scan_at = new Date().toISOString();
  diagnostics.last_unread_count = incoming.length;
  return {
    ok: true,
    message: incoming.length ? 'Unread WhatsApp direct chat found.' : 'Unread chat had no readable incoming text.',
    data: {
      has_unread: incoming.length > 0,
      chat: String(chat.name || chat.formattedTitle || 'Direct contact'),
      chat_id: serializedId(chat.id),
      is_group: Boolean(chat.isGroup),
      incoming_messages: incoming,
    },
    error: '',
  };
}

async function resolveDirectChatId(request) {
  const requestedId = directChatId(request.chat_id);
  if (requestedId) return requestedId;

  const target = String(request.contact || request.chat_label || request.expected_chat || '').trim();
  if (!target) return '';
  const directTarget = directChatId(target);
  if (directTarget) return directTarget;

  const targetDigits = digitsOnly(target);
  if (targetDigits.length >= 8 && typeof client.getNumberId === 'function') {
    try {
      const numberId = await promiseTimeout(client.getNumberId(targetDigits), 10000, 'WhatsApp number lookup');
      const resolved = directChatId(numberId);
      if (resolved) return resolved;
    } catch (error) {
      diagnostics.last_ignored_reason = `number_lookup_failed:${String(error.message || error).slice(0, 80)}`;
    }
  }

  const targetName = normalizedText(target);
  const chats = await promiseTimeout(client.getChats(), 10000, 'WhatsApp chat lookup');
  const directChats = chats.filter((chat) => {
    const chatId = directChatId(chat.id);
    return chatId && !chat.isGroup && !chat.isReadOnly;
  });
  const exact = directChats.find((chat) => {
    const chatId = directChatId(chat.id);
    const title = normalizedText(chatTitle(chat));
    return chatId === target || title === targetName || digitsOnly(chatId) === targetDigits;
  });
  if (exact) return directChatId(exact.id);
  if (targetName.length >= 3) {
    const partial = directChats.find((chat) => normalizedText(chatTitle(chat)).includes(targetName));
    if (partial) return directChatId(partial.id);
  }
  return '';
}

async function withTimeout(promise, milliseconds, label) {
  try {
    return await promiseTimeout(promise, milliseconds, label);
  } catch (error) {
    const detail = String(error.message || error || '');
    const timedOut = detail.includes('timed out');
    return {
      ok: false,
      message: timedOut ? `${label} timed out.` : `${label} failed.`,
      data: {},
      error: timedOut ? 'timeout' : detail.slice(0, 300),
    };
  }
}

function finishRequest(request, payload) {
  if (activeRequestId !== request.id) return;
  payload.id = request.id;
  diagnostics.last_response_message = String(payload.message || '');
  writeJson(responsePath, payload);
  activeRequestId = '';
  activeRequestStartedAt = 0;
  requestInFlight = false;
  writeStatus();
}

function failStuckRequest() {
  if (!requestInFlight || !activeRequestId || Date.now() - activeRequestStartedAt <= 30000) return false;
  const payload = {
    id: activeRequestId,
    ok: false,
    message: 'WhatsApp bridge request timed out.',
    data: {},
    error: 'timeout',
  };
  diagnostics.last_response_message = payload.message;
  diagnostics.last_ignored_reason = `request_watchdog:${diagnostics.last_request_action}`;
  writeJson(responsePath, payload);
  activeRequestId = '';
  activeRequestStartedAt = 0;
  requestInFlight = false;
  writeStatus();
  return true;
}

function handleRequest(request) {
  if (!request.id || request.id === lastRequestId) return;
  if (requestInFlight) {
    failStuckRequest();
    return;
  }
  lastRequestId = request.id;
  requestInFlight = true;
  activeRequestId = request.id;
  activeRequestStartedAt = Date.now();
  diagnostics.last_request_action = String(request.action || '');
  writeStatus();
  (async () => {
    if (request.action === 'next-unread') {
      if (!connected) return { ok: false, message: 'WhatsApp bridge is not connected yet.', data: { connected: false }, error: '' };
      return await withTimeout(nextUnreadPayload(), 25000, 'Unread WhatsApp scan');
    }
    if (request.action === 'send-reply') {
      if (!connected) return { ok: false, message: 'WhatsApp bridge is not connected yet.', data: { connected: false }, error: '' };
      return await withTimeout((async () => {
        const reply = String(request.reply || '').trim();
        const chatId = await resolveDirectChatId(request);
        if (!chatId || !reply) {
          return { ok: false, message: 'Reply request failed chat verification.', data: {}, error: '' };
        }
        await client.sendMessage(chatId, reply, { waitUntilMsgSent: true });
        return { ok: true, message: 'Reply sent.', data: { chat: String(request.chat_label || 'Direct contact') }, error: '' };
      })(), 25000, 'WhatsApp reply send');
    }
    if (request.action === 'status') {
      return { ok: connected, message: 'WhatsApp bridge status.', data: { connected }, error: '' };
    }
    return { ok: false, message: 'Unsupported WhatsApp bridge action.', data: {}, error: String(request.action || '') };
  })()
    .then((payload) => finishRequest(request, payload))
    .catch((error) => finishRequest(request, { ok: false, message: 'WhatsApp browser action failed without sending a message.', data: {}, error: String(error.message || error).slice(0, 300) }));
}

setInterval(async () => {
  if (authenticated && !connected) {
    try {
      const reportedState = await client.getState();
      if (reportedState) {
        connectionState = String(reportedState);
        if (connectionState === 'CONNECTED') connected = true;
      }
    } catch (error) {
      connectionState = 'AUTHENTICATED';
    }
  }
  writeStatus();
  try {
    const request = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
    handleRequest(request);
  } catch (error) {
    failStuckRequest();
    // A request can be replaced while it is read. The next bridge tick retries it.
  }
}, 500);

client.initialize();
