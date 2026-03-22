export const truncateText = (value, maxLength = 70) => {
  if (!value) {
    return "";
  }
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
};

export const normalizeConversationTitle = (value) => {
  if (!value) {
    return "";
  }
  return value === "New Conversation" ? "新会话" : value;
};

export const formatBytes = (bytes) => {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
};

export const parseDateValue = (value) => {
  if (!value && value !== 0) {
    return null;
  }
  if (typeof value === "number") {
    return new Date(value < 1_000_000_000_000 ? value * 1000 : value);
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

export const formatTime = (value) => {
  const parsed = parseDateValue(value);
  if (!parsed) {
    return "刚刚";
  }
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
};

export const fileExtension = (filename) => {
  const parts = (filename || "").split(".");
  return parts.length > 1 ? parts.pop().toUpperCase() : "文件";
};

const coalesceAssistantMessages = (messages) => {
  const merged = [];

  for (const message of messages) {
    if (message.kind === "assistant" && !message.content.trim()) {
      if (Array.isArray(message.downloads) && message.downloads.length > 0) {
        message.content = "已处理完成。";
      } else {
        continue;
      }
    }

    const last = merged[merged.length - 1];
    if (message.kind === "assistant" && last?.kind === "assistant") {
      last.content = `${last.content}${message.content}`;
      if (Array.isArray(message.downloads) && message.downloads.length > 0) {
        last.downloads = Array.from(new Set([...(last.downloads || []), ...message.downloads]));
      }
      continue;
    }

    merged.push({ ...message });
  }

  return merged;
};

export const mapConversationMessages = (messages) => {
  const mapped = (messages || []).flatMap((message, index) => {
    if (message.type === "human") {
      return [{
        id: `human-${index}`,
        kind: "user",
        content: message.content || "",
        attachments: Array.isArray(message.attachments) ? message.attachments : [],
      }];
    }
    if (message.type === "ai") {
      if (!message.content && !Array.isArray(message.files)) {
        return [];
      }
      return [{
        id: `assistant-${index}`,
        kind: "assistant",
        content: message.content || "",
        downloads: Array.isArray(message.files)
          ? message.files
          : (Array.isArray(message.downloads) ? message.downloads : []),
      }];
    }
    return [];
  });

  return coalesceAssistantMessages(mapped);
};
