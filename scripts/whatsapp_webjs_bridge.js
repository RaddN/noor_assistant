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
  last_incoming_type: '',
  last_incoming_chat_id: '',
  last_incoming_chat_label: '',
  last_ignored_reason: '',
  last_request_action: '',
  last_response_message: '',
  last_unread_scan_at: '',
  last_unread_count: 0,
  last_call_at: '',
  last_call_chat_id: '',
  last_call_origin: '',
  last_call_poll_at: '',
  last_call_poll_count: 0,
  last_call_ui_at: '',
  last_call_ui_label: '',
  last_call_ui_type: '',
  last_call_snapshot: {},
  active_request_id: '',
  active_request_age_ms: 0,
};
const seenCallEvents = new Set();
const seenIncomingEvents = new Set();
const seenUnreadEvents = new Set();
let callPollInFlight = false;
let callUiPollInFlight = false;
let unreadPollInFlight = false;
let activeUiCallKey = '';
let activeUiCallStartedAt = 0;

function serializedId(value) {
  if (!value) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value._serialized === 'string') return value._serialized;
  if (value.id) return serializedId(value.id);
  return String(value);
}

function normalizeDirectChatId(rawValue, allowBareNumber = false) {
  const value = serializedId(rawValue).trim();
  if (!value || value.endsWith('@g.us') || value === 'status@broadcast' || value.endsWith('@broadcast')) return '';
  if (value.endsWith('@c.us') || value.endsWith('@lid')) return value;
  if (value.endsWith('@s.whatsapp.net')) {
    const number = value.slice(0, -'@s.whatsapp.net'.length);
    return number ? `${number}@c.us` : '';
  }
  const digits = digitsOnly(value);
  return allowBareNumber && digits.length >= 8 && /^[+\d\s().-]+$/.test(value) ? `${digits}@c.us` : '';
}

function directChatId(value, options = {}) {
  const allowBareNumber = Boolean(options.allowBareNumber);
  const candidates = [];
  if (typeof value === 'string' || typeof value === 'number') {
    candidates.push(value);
  } else if (value && typeof value === 'object') {
    candidates.push(
      value._serialized,
      value.peerJid,
      value.from,
      value.chatId,
      value.remote,
      value.participant,
      value.id && value.id._serialized,
      value.id && value.id.remote,
      value.id && value.id.participant,
    );
    if (value.user && value.server) candidates.push(`${value.user}@${value.server}`);
  }
  for (const candidate of candidates) {
    const chatId = normalizeDirectChatId(candidate, allowBareNumber);
    if (chatId) return chatId;
  }
  return '';
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

function messageTimestamp(message) {
  const value = Number(message && (message.timestamp || message.t || message.__x_t || message.__x_timestamp || 0));
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function messageHash(message) {
  const rawMessageId = serializedId(message.id);
  const chatId = directChatId(message.from) || directChatId(message.to) || serializedId(message.from) || serializedId(message.to) || 'unknown-chat';
  const body = String(message.body || '');
  let stableMessageId = '';
  if (rawMessageId) {
    const parts = rawMessageId.split('_').filter(Boolean);
    const suffix = parts.length > 1 ? parts[parts.length - 1] : rawMessageId;
    stableMessageId = `${chatId}:${suffix || rawMessageId}`;
  } else {
    stableMessageId = `${chatId}:${messageTimestamp(message) || 'no-time'}:${body}`;
  }
  return crypto.createHash('sha256').update(`whatsapp-message\n${stableMessageId}\n${body}`).digest('hex');
}

function callSnapshot(call, origin, chatId) {
  return {
    origin,
    id: serializedId(call && call.id),
    from: serializedId(call && call.from),
    peerJid: serializedId(call && call.peerJid),
    chatId,
    timestamp: Number((call && (call.timestamp || call.offerTime)) || 0),
    isVideo: Boolean(call && call.isVideo),
    isGroup: Boolean(call && call.isGroup),
    outgoing: Boolean(call && (call.outgoing || call.fromMe)),
  };
}

function callEventKey(call, chatId) {
  const stableId = serializedId(call && call.id) || String((call && (call.timestamp || call.offerTime)) || Math.floor(Date.now() / 10000));
  return crypto.createHash('sha256').update(`call\n${chatId}\n${stableId}\n${Boolean(call && call.isVideo) ? 'video' : 'voice'}`).digest('hex');
}

function rememberCallEvent(eventKey) {
  if (!eventKey) return false;
  if (seenCallEvents.has(eventKey)) return false;
  seenCallEvents.add(eventKey);
  if (seenCallEvents.size > 200) {
    const first = seenCallEvents.values().next().value;
    seenCallEvents.delete(first);
  }
  return true;
}

function rememberUnreadEvent(eventKey) {
  if (!eventKey) return false;
  if (seenUnreadEvents.has(eventKey)) return false;
  seenUnreadEvents.add(eventKey);
  if (seenUnreadEvents.size > 500) {
    const first = seenUnreadEvents.values().next().value;
    seenUnreadEvents.delete(first);
  }
  return true;
}

function rememberIncomingEvent(eventKey) {
  if (!eventKey) return false;
  if (seenIncomingEvents.has(eventKey)) return false;
  seenIncomingEvents.add(eventKey);
  if (seenIncomingEvents.size > 1000) {
    const first = seenIncomingEvents.values().next().value;
    seenIncomingEvents.delete(first);
  }
  return true;
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
  const eventType = String(fallback.eventType || 'message');
  if (eventType === 'call') diagnostics.call_events += 1;
  else diagnostics.message_events += 1;
  if (eventType !== 'call' && message.fromMe) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'from_me';
    writeStatus();
    return;
  }
  const rawChatId = String(fallback.chatId || serializedId(message.from) || '');
  const chatId = directChatId(fallback.chatId) || directChatId(message.from) || rawChatId;
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
  if (!rememberIncomingEvent(eventId)) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'duplicate_event';
    writeStatus();
    return;
  }
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
  diagnostics.last_incoming_type = eventType;
  diagnostics.last_incoming_chat_id = chatId;
  diagnostics.last_incoming_chat_label = String(fallback.chatLabel || 'Direct contact');
  diagnostics.last_ignored_reason = '';
  writeStatus();
}

client.on('message', writeIncomingEvent);
client.on('message_create', writeIncomingEvent);
async function chatLabelForDirectChat(chatId, fallbackLabel = 'Direct contact') {
  if (!chatId) return fallbackLabel;
  try {
    const contact = await promiseTimeout(client.getContactById(chatId), 4000, 'WhatsApp call contact lookup');
    return String((contact && (contact.pushname || contact.name || contact.number)) || fallbackLabel || chatId);
  } catch (error) {
    return String(fallbackLabel || chatId);
  }
}

async function writeCallEvent(call, origin = 'event') {
  const chatId = directChatId(call, { allowBareNumber: true });
  diagnostics.last_call_at = new Date().toISOString();
  diagnostics.last_call_chat_id = chatId;
  diagnostics.last_call_origin = origin;
  diagnostics.last_call_snapshot = callSnapshot(call, origin, chatId);
  if (call && (call.outgoing || call.fromMe)) {
    diagnostics.call_events += 1;
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = 'outgoing_call';
    writeStatus();
    return;
  }
  const eventKey = callEventKey(call, chatId);
  if (!rememberCallEvent(eventKey)) {
    writeStatus();
    return;
  }
  const providedLabel = String(call && (call.chatLabel || call.label) || '').trim();
  const chatLabel = providedLabel || await chatLabelForDirectChat(chatId);
  writeIncomingEvent(
    {
      id: { _serialized: `call-${eventKey}` },
      from: chatId,
      fromMe: false,
      body: 'Incoming WhatsApp call',
      timestamp: Number((call && (call.timestamp || call.offerTime)) || Date.now()),
    },
    { eventType: 'call', eventSubtype: 'incoming', chatId, chatLabel, body: 'Incoming WhatsApp call', isGroup: Boolean(call && call.isGroup) },
  );
}

client.on('call', (call) => {
  writeCallEvent(call, 'event').catch((error) => {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = `call_event_failed:${String(error.message || error).slice(0, 80)}`;
    writeStatus();
  });
});

async function pollActiveCalls() {
  if (!connected || callPollInFlight || !client.pupPage) return;
  callPollInFlight = true;
  try {
    const calls = await client.pupPage.evaluate(() => {
      try {
        const collection = window.require && window.require('WAWebCallCollection');
        const mapKey = collection && Object.keys(collection).find((key) => collection[key] instanceof Map);
        const callMap = mapKey ? collection[mapKey] : null;
        if (!callMap || typeof callMap.values !== 'function') return [];
        return Array.from(callMap.values()).slice(-8).map((value) => ({
          id: value && value.id,
          peerJid: value && (value.peerJid || value.from),
          from: value && (value.from || value.peerJid),
          offerTime: value && (value.offerTime || value.timestamp),
          isVideo: Boolean(value && value.isVideo),
          isGroup: Boolean(value && value.isGroup),
          outgoing: Boolean(value && value.outgoing),
          canHandleLocally: Boolean(value && value.canHandleLocally),
          webClientShouldHandle: Boolean(value && value.webClientShouldHandle),
        }));
      } catch (error) {
        return { error: String(error && (error.message || error)).slice(0, 120) };
      }
    });
    diagnostics.last_call_poll_at = new Date().toISOString();
    if (!Array.isArray(calls)) {
      diagnostics.last_ignored_reason = `call_poll_read_failed:${String(calls && calls.error || '').slice(0, 80)}`;
      writeStatus();
      return;
    }
    diagnostics.last_call_poll_count = calls.length;
    for (const call of calls) await writeCallEvent(call, 'poll');
  } catch (error) {
    diagnostics.last_ignored_reason = `call_poll_failed:${String(error.message || error).slice(0, 80)}`;
    writeStatus();
  } finally {
    callPollInFlight = false;
  }
}

async function incomingCallUiPayload() {
  if (!client.pupPage) return null;
  const title = await client.pupPage.title().catch(() => '');
  const dom = await client.pupPage.evaluate(() => {
    function clean(value) {
      return String(value || '').replace(/\s+/g, ' ').trim();
    }
    const applications = Array.from(document.querySelectorAll('[role="application"]'));
    for (const app of applications) {
      const text = String(app.innerText || app.textContent || '');
      const buttons = Array.from(app.querySelectorAll('button,[role="button"]')).map((button) => clean(button.getAttribute('aria-label') || button.innerText || button.textContent));
      const hasDecline = buttons.some((value) => /^decline$/i.test(value));
      const hasAccept = buttons.some((value) => /^accept$/i.test(value));
      if (!hasDecline || !hasAccept || !/\bcall\b/i.test(text)) continue;
      const lines = text.split(/\r?\n/).map(clean).filter(Boolean);
      const caller = lines.find((line) => !/^(voice call|video call|decline|accept|mute microphone)$/i.test(line)) || '';
      const callType = lines.some((line) => /^video call$/i.test(line)) ? 'video' : 'voice';
      return { caller, callType, text: clean(text).slice(0, 240) };
    }
    return null;
  }).catch(() => null);
  const match = String(title || '').match(/^Incoming\s+(voice|video)\s+call\s+from\s+(.+)$/i);
  const caller = String((match && match[2]) || (dom && dom.caller) || '').trim();
  if (!caller) return null;
  return {
    caller,
    callType: String((match && match[1]) || (dom && dom.callType) || 'voice').toLowerCase(),
    title,
    text: String((dom && dom.text) || '').slice(0, 240),
  };
}

async function directChatIdForChatLabel(label) {
  const target = normalizedText(label);
  if (!target || !client.pupPage) return '';
  return client.pupPage.evaluate((expected) => {
    function clean(value) {
      return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
    }
    function serialized(id) {
      if (!id) return '';
      if (typeof id === 'string') return id;
      if (typeof id._serialized === 'string') return id._serialized;
      if (id.user && id.server) return `${id.user}@${id.server}`;
      return String(id || '');
    }
    try {
      const collection = window.require && window.require('WAWebChatCollection');
      const models = collection && collection.ChatCollection && collection.ChatCollection._models;
      const chats = Array.from(models || []);
      const exact = chats.find((chat) => {
        if (!chat || chat.isGroup) return false;
        const names = [
          chat.name,
          chat.formattedTitle,
          chat.pushname,
          chat.displayName,
          chat.title,
          chat.contact && chat.contact.name,
          chat.contact && chat.contact.pushname,
          chat.contact && chat.contact.shortName,
          chat.contact && chat.contact.number,
        ].map(clean).filter(Boolean);
        return names.includes(expected);
      });
      return serialized(exact && exact.id);
    } catch (error) {
      return '';
    }
  }, target).catch(() => '');
}

async function pollIncomingCallUi() {
  if (!connected || callUiPollInFlight || !client.pupPage) return;
  callUiPollInFlight = true;
  try {
    const payload = await incomingCallUiPayload();
    if (!payload) {
      activeUiCallKey = '';
      activeUiCallStartedAt = 0;
      return;
    }
    diagnostics.last_call_ui_at = new Date().toISOString();
    diagnostics.last_call_ui_label = payload.caller;
    diagnostics.last_call_ui_type = payload.callType;
    const uiKey = `${payload.callType}:${normalizedText(payload.caller)}`;
    if (uiKey !== activeUiCallKey) {
      activeUiCallKey = uiKey;
      activeUiCallStartedAt = Date.now();
    }
    let chatId = await directChatIdForChatLabel(payload.caller);
    if (!chatId) {
      chatId = await resolveDirectChatId({ chat_label: payload.caller, expected_chat: payload.caller });
    }
    if (!chatId) {
      diagnostics.ignored_events += 1;
      diagnostics.last_ignored_reason = `call_ui_chat_not_resolved:${payload.caller.slice(0, 60)}`;
      writeStatus();
      return;
    }
    await writeCallEvent(
      {
        id: `ui-${activeUiCallStartedAt}`,
        peerJid: chatId,
        from: chatId,
        timestamp: activeUiCallStartedAt,
        isVideo: payload.callType === 'video',
        isGroup: false,
        outgoing: false,
        chatLabel: payload.caller,
      },
      'ui',
    );
  } catch (error) {
    diagnostics.last_ignored_reason = `call_ui_poll_failed:${String(error.message || error).slice(0, 80)}`;
    writeStatus();
  } finally {
    callUiPollInFlight = false;
  }
}

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
      id: serializedId(message.id),
      text: String(message.body || '').slice(0, 4000),
      timestamp: messageTimestamp(message),
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
          timestamp: message.timestamp || 0,
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

async function internalUnreadPayload() {
  if (!client.pupPage) return null;
  return client.pupPage.evaluate(() => {
    function clean(value) {
      return String(value || '').replace(/\s+/g, ' ').trim();
    }
    function serialized(id) {
      if (!id) return '';
      if (typeof id === 'string') return id;
      if (typeof id._serialized === 'string') return id._serialized;
      if (id.user && id.server) return `${id.user}@${id.server}`;
      return String(id || '');
    }
    function msgBody(message) {
      return String(message && (message.body || message.caption || message.__x_body || message.__x_caption || '') || '').trim();
    }
    function msgId(message) {
      return serialized(message && (message.id || message.__x_id));
    }
    function msgTimestamp(message) {
      const value = Number(message && (message.timestamp || message.t || message.__x_t || message.__x_timestamp || 0));
      return Number.isFinite(value) && value > 0 ? value : 0;
    }
    function msgType(message) {
      return String(message && (message.type || message.__x_type || '') || '').toLowerCase();
    }
    function msgFromMe(message) {
      return Boolean(message && (message.fromMe || message.__x_fromMe));
    }
    function chatTitle(chat) {
      return clean(chat && (
        chat.name ||
        chat.formattedTitle ||
        chat.pushname ||
        chat.displayName ||
        chat.title ||
        (chat.contact && (chat.contact.name || chat.contact.pushname || chat.contact.shortName || chat.contact.number))
      ));
    }
    try {
      const collection = window.require && window.require('WAWebChatCollection');
      const models = collection && collection.ChatCollection && collection.ChatCollection._models;
      const chats = Array.from(models || []).filter((chat) => {
        const chatId = serialized(chat && chat.id);
        const unread = Number((chat && (chat.unreadCount || chat.__x_unreadCount)) || 0);
        return chat && unread > 0 && !chat.isGroup && !chat.__x_isGroup && !chat.isReadOnly && !chat.__x_isReadOnly && (chatId.endsWith('@c.us') || chatId.endsWith('@lid'));
      });
      chats.sort((left, right) => Number((right && (right.t || right.__x_t)) || 0) - Number((left && (left.t || left.__x_t)) || 0));
      for (const chat of chats) {
        const chatId = serialized(chat.id);
        const unreadCount = Math.max(1, Math.min(Number(chat.unreadCount || chat.__x_unreadCount || 1), 10));
        const rawMessages = chat.msgs || chat.__x_msgs;
        const messages = Array.isArray(rawMessages)
          ? rawMessages
          : Array.isArray(rawMessages && rawMessages._models)
            ? rawMessages._models
            : Array.isArray(rawMessages && rawMessages.models)
              ? rawMessages.models
              : [];
        const incoming = messages
          .slice(-unreadCount)
          .map((message) => ({
            id: msgId(message),
            text: msgBody(message).slice(0, 4000),
            type: msgType(message),
            timestamp: msgTimestamp(message),
            from_me: msgFromMe(message),
          }))
          .filter((message) => !message.from_me && message.id && !message.id.startsWith('true_') && message.text && (!message.type || message.type === 'chat'));
        if (incoming.length) {
          return {
            has_unread: true,
            chat: chatTitle(chat) || 'Direct contact',
            chat_id: chatId,
            is_group: false,
            incoming_messages: incoming,
          };
        }
      }
      return { has_unread: false };
    } catch (error) {
      return { has_unread: false, error: String(error && (error.message || error)).slice(0, 120) };
    }
  }).catch((error) => ({ has_unread: false, error: String(error.message || error).slice(0, 120) }));
}

async function writeInternalUnreadEvent() {
  if (!connected || unreadPollInFlight || !client.pupPage) return;
  unreadPollInFlight = true;
  try {
    const payload = await internalUnreadPayload();
    diagnostics.last_unread_scan_at = new Date().toISOString();
    if (!payload || !payload.has_unread) {
      diagnostics.last_unread_count = 0;
      if (payload && payload.error) diagnostics.last_ignored_reason = `internal_unread_failed:${payload.error}`;
      writeStatus();
      return;
    }
    const messages = Array.isArray(payload.incoming_messages) ? payload.incoming_messages : [];
    diagnostics.last_unread_count = messages.length;
    const latest = messages[messages.length - 1];
    if (!latest || !latest.id || !latest.text || !rememberUnreadEvent(`${payload.chat_id}:${latest.id}`)) {
      writeStatus();
      return;
    }
    writeIncomingEvent(
      {
        id: { _serialized: latest.id },
        from: String(payload.chat_id || ''),
        fromMe: false,
        body: latest.text,
        timestamp: Number(latest.timestamp || 0),
      },
      { chatId: String(payload.chat_id || ''), chatLabel: String(payload.chat || 'Direct contact'), isGroup: false },
    );
  } catch (error) {
    diagnostics.ignored_events += 1;
    diagnostics.last_ignored_reason = `internal_unread_event_failed:${String(error.message || error).slice(0, 100)}`;
    writeStatus();
  } finally {
    unreadPollInFlight = false;
  }
}

async function nextUnreadPayload() {
  let chats = [];
  try {
    chats = await promiseTimeout(client.getChats(), 10000, 'Unread WhatsApp chat scan');
  } catch (error) {
    const internal = await internalUnreadPayload();
    if (internal && internal.has_unread) {
      const incoming = (internal.incoming_messages || []).map((message) => ({
        hash: messageHash({ id: { _serialized: message.id }, from: internal.chat_id, body: message.text, timestamp: Number(message.timestamp || 0) }),
        text: message.text,
      }));
      diagnostics.last_unread_scan_at = new Date().toISOString();
      diagnostics.last_unread_count = incoming.length;
      return {
        ok: true,
        message: incoming.length ? 'Unread WhatsApp direct chat found.' : 'Unread chat had no readable incoming text.',
        data: { ...internal, incoming_messages: incoming },
        error: '',
      };
    }
    if (internal && !internal.error) {
      diagnostics.last_unread_scan_at = new Date().toISOString();
      diagnostics.last_unread_count = 0;
      return { ok: true, message: 'No unread WhatsApp direct chats found.', data: { has_unread: false }, error: '' };
    }
    throw error;
  }
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
    hash: messageHash({ id: { _serialized: message.id }, from: serializedId(chat.id), body: message.text, timestamp: Number(message.timestamp || 0) }),
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
        await client.sendMessage(chatId, reply, { waitUntilMsgSent: true, sendSeen: true });
        try {
          await client.sendSeen(chatId);
        } catch (error) {
          diagnostics.last_ignored_reason = `send_seen_failed:${String(error.message || error).slice(0, 80)}`;
        }
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

setInterval(() => {
  pollActiveCalls();
  pollIncomingCallUi();
  writeInternalUnreadEvent();
}, 1000);

client.initialize();
