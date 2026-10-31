const VERSION = "0.1.0.dev0";
const MAX_BYTES = 1048576;

export class PyYakiError extends Error {
  constructor(code, message, details) {
    super(message);
    this.name = "PyYakiError";
    this.code = code;
    this.details = details;
  }
}

let state = "connecting";
let socket;
let connection;
let capabilities = Object.freeze([]);
const pending = new Map();

function fail(code, message) {
  state = code === "DISCONNECTED" ? "disconnected" : "failed";
  capabilities = Object.freeze([]);
  for (const call of [...pending.values()]) call.reject(new PyYakiError(code, message));
}

function checkJson(value, ancestors = new Set(), depth = 0) {
  if (depth > 64) throw new PyYakiError("BAD_REQUEST", "JSON nesting exceeds 64");
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value) &&
      (!Number.isInteger(value) || Number.isSafeInteger(value))) return;
  if (typeof value !== "object" || ancestors.has(value) ||
      (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype)) {
    throw new PyYakiError("BAD_REQUEST", "Unsupported JSON value");
  }
  ancestors.add(value);
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== "string") throw new PyYakiError("BAD_REQUEST", "Invalid JSON key");
    if (Array.isArray(value) && key === "length") continue;
    checkJson(value[key], ancestors, depth + 1);
  }
  // JSON.stringify turns sparse arrays into nulls, so reject holes explicitly.
  if (Array.isArray(value) && Object.keys(value).length !== value.length) {
    throw new PyYakiError("BAD_REQUEST", "Sparse arrays are unsupported");
  }
  ancestors.delete(value);
}

function send(message) {
  checkJson(message);
  const text = JSON.stringify(message);
  if (new TextEncoder().encode(text).length > MAX_BYTES) {
    throw new PyYakiError("BAD_REQUEST", "Message exceeds 1 MiB");
  }
  socket.send(text);
}

function nativeSocket() {
  const mac = window.__PYYAKI_NATIVE__.mode === "macos";
  const bridge = mac ? window : window.chrome.webview;
  const eventName = mac ? "__pyyaki_message__" : "message";
  const transport = {
    readyState: WebSocket.OPEN,
    send(text) { window.ipc.postMessage(text); },
    close() {
      if (this.readyState === WebSocket.CLOSED) return;
      this.readyState = WebSocket.CLOSED;
      bridge.removeEventListener(eventName, receive);
      this.onclose?.();
    },
  };
  const receive = (event) => transport.onmessage?.({data: JSON.stringify(event.data)});
  bridge.addEventListener(eventName, receive);
  return transport;
}

function connect() {
  return new Promise((resolve, reject) => {
    let settled = false;
    const abort = new AbortController();
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) { fail(error.code, error.message); reject(error); }
    };
    const timer = setTimeout(() => {
      abort.abort();
      finish(new PyYakiError("TIMEOUT", "Connection timed out"));
      socket?.close();
    }, 10000);
    const native = window.__PYYAKI_NATIVE__;
    const mode = native ? native.mode : "web";
    const attach = (nonce) => {
      if (settled) return;
      if (native) {
        if (native.version !== VERSION || !["windows", "macos"].includes(native.mode) ||
            (native.mode === "windows" ? location.origin !== "https://pyyaki.localhost" :
              !location.href.startsWith("pyyaki://localhost/")) || window.top !== window ||
            (native.mode === "windows" && !window.chrome?.webview) ||
            typeof window.ipc?.postMessage !== "function") {
          finish(new PyYakiError("PROTOCOL_MISMATCH", "Invalid native bootstrap"));
          return;
        }
        socket = nativeSocket();
      } else {
        const url = new URL("/_pyyaki/ws", location.href);
        url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(url);
      }
      socket.onopen = () => send({ v: 1, type: "hello", client_version: VERSION,
        ...(native ? {} : { nonce }) });
      socket.onmessage = ({ data }) => {
        try {
          if (typeof data !== "string" || new TextEncoder().encode(data).length > MAX_BYTES) {
            throw new Error("Invalid message");
          }
          const message = JSON.parse(data);
          checkJson(message);
          if (message.v !== 1) throw new Error("Protocol mismatch");
          if (!settled && message.type === "welcome") {
            if (message.framework_version !== VERSION || message.mode !== mode ||
                !Array.isArray(message.capabilities)) throw new Error("Version mismatch");
            capabilities = Object.freeze([...message.capabilities]);
            state = "ready";
            finish();
            resolve({ mode: message.mode, capabilities, protocolVersion: message.v });
            return;
          }
          if (!settled || !["result", "error"].includes(message.type)) {
            throw new Error("Unexpected message");
          }
          const call = pending.get(message.id);
          if (!call) return; // Late response after cancellation or timeout.
          if (message.type === "result") call.resolve(message.result);
          else call.reject(new PyYakiError(
            message.error.code, message.error.message, message.error.details,
          ));
        } catch {
          finish(new PyYakiError("PROTOCOL_MISMATCH", "Invalid server message"));
          fail("PROTOCOL_MISMATCH", "Invalid server message");
          socket.close();
        }
      };
      socket.onerror = () => finish(new PyYakiError("DISCONNECTED", "Connection failed"));
      socket.onclose = () => {
        finish(new PyYakiError("DISCONNECTED", "Connection closed"));
        fail("DISCONNECTED", "Connection closed");
      };
      if (native) {
        // The native host starts Python only after this post-layout marker.
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (socket.readyState !== WebSocket.OPEN) return;
          send({ v: 1, type: "pyyaki_shell_ready" });
          socket.onopen();
        }));
      }
    };
    if (native) {
      attach();
    } else {
      fetch("/_pyyaki/session", { credentials: "same-origin", cache: "no-store", signal: abort.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error("Session initialization failed");
          attach((await response.json()).nonce);
        })
        .catch(() => finish(new PyYakiError("DISCONNECTED", "Session initialization failed")));
    }

  });
}

function ready({ timeoutMs = 10000 } = {}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 120000) {
    return Promise.reject(new PyYakiError("BAD_REQUEST", "Invalid ready timeout"));
  }
  if (state === "failed" || state === "disconnected") {
    return Promise.reject(new PyYakiError("DISCONNECTED", "Reload to start a new session"));
  }
  connection ??= connect();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new PyYakiError("TIMEOUT", "Ready timed out")), timeoutMs);
    connection.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}

async function invoke(name, params = {}, { timeoutMs = 30000, signal } = {}) {
  if (state !== "ready") {
    throw new PyYakiError(state === "connecting" ? "NOT_READY" : "DISCONNECTED", "Not connected");
  }
  if (typeof name !== "string" || !/^[a-z][a-z0-9_.-]{0,63}$/.test(name) ||
      !params || Object.getPrototypeOf(params) !== Object.prototype ||
      (signal !== undefined && !(signal instanceof AbortSignal)) ||
      !Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 120000) {
    throw new PyYakiError("BAD_REQUEST", "Invalid call options");
  }
  if (signal?.aborted) throw new PyYakiError("CANCELLED", "Command cancelled");
  if (pending.size >= 16) throw new PyYakiError("BUSY", "Too many concurrent commands");
  const id = typeof crypto.randomUUID === "function" ? crypto.randomUUID() :
    [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("");
  return new Promise((resolve, reject) => {
    const finish = (callback, value) => {
      if (!pending.delete(id)) return;
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      callback(value);
    };
    const stop = (code, message) => {
      finish(reject, new PyYakiError(code, message));
      if (socket.readyState === WebSocket.OPEN) send({ v: 1, type: "cancel", id });
    };
    const cancel = () => stop("CANCELLED", "Command cancelled");
    const timer = setTimeout(() => stop("TIMEOUT", "Command timed out"), timeoutMs);
    pending.set(id, {
      resolve: (value) => finish(resolve, value),
      reject: (error) => finish(reject, error),
    });
    signal?.addEventListener("abort", cancel, { once: true });
    try { send({ v: 1, type: "call", id, method: name, params, timeout_ms: timeoutMs }); }
    catch (error) { finish(reject, error); }
  });
}

export const pyyaki = Object.freeze({
  ready, invoke,
  get state() { return state; },
  get capabilities() { return capabilities; },
});
