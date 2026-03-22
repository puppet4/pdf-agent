import { API_BASE_URL, getApiKey } from './config';

const withApiKey = (headers) => {
  const next = new Headers(headers || {});
  const apiKey = getApiKey();
  if (apiKey && !next.has("X-API-Key")) {
    next.set("X-API-Key", apiKey);
  }
  return next;
};

export const api = async (path, options = {}) => {
  const headers = withApiKey(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return null;
  }
  return response.json();
};

export const FileService = {
  uploadFiles: async (fileList) => {
    const createdIds = [];
    for (const file of fileList) {
      const body = new FormData();
      body.append("file", file);
      const payload = await api("/api/files", { method: "POST", body });
      if (payload?.id) {
        createdIds.push(String(payload.id));
      } else if (payload?.file_id) { // In case the backend returns file_id
        createdIds.push(String(payload.file_id));
      }
    }
    return createdIds;
  },
  getFiles: async () => {
    const data = await api("/api/files?page=1&limit=100");
    // Handle both cases: a dict with 'files' or just a top-level list
    return data.files || (Array.isArray(data) ? data : []);
  },
  deleteFile: async (fileId) => {
    return api(`/api/files/${fileId}`, { method: "DELETE" });
  }
};

export const ConversationService = {
  createConversation: async () => {
    return api("/api/conversations", { method: "POST" });
  },
  getConversations: async () => {
    const data = await api("/api/conversations?page=1&limit=20").catch(() => ({ conversations: [] }));
    return data.conversations || (Array.isArray(data) ? data : []);
  },
  getConversation: async (conversationId) => {
    return api(`/api/conversations/${conversationId}`);
  },
  getArtifacts: async (conversationId) => {
    const data = await api(`/api/conversations/${conversationId}/artifacts`).catch(() => ({ artifacts: [] }));
    return data.artifacts || (Array.isArray(data) ? data : []);
  },
  deleteConversation: async (conversationId) => {
    return api(`/api/conversations/${conversationId}`, { method: "DELETE" });
  },
  sendMessage: async (conversationId, payload, options = {}) => {
    const headers = withApiKey({ "Content-Type": "application/json" });
    return fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  },
};

export const parseSseBlock = (block) => {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  const payload = dataLines.join("\n");
  if (!payload) {
    return { event, data: {} };
  }
  try {
    return { event, data: JSON.parse(payload) };
  } catch {
    return { event, data: { content: payload } };
  }
};

export const consumeSse = async (response, handlers) => {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary).trim();
      buffer = buffer.slice(boundary + 2);
      if (block) {
        const parsed = parseSseBlock(block);
        if (handlers[parsed.event]) {
          handlers[parsed.event](parsed.data);
        }
      }
      boundary = buffer.indexOf("\n\n");
    }

    if (done) {
      break;
    }
  }

  if (buffer.trim()) {
    const parsed = parseSseBlock(buffer.trim());
    if (handlers[parsed.event]) {
      handlers[parsed.event](parsed.data);
    }
  }
};
