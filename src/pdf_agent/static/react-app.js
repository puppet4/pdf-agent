import React, { useEffect, useRef, useState } from "https://esm.sh/react@18";
import { createRoot } from "https://esm.sh/react-dom@18/client";

const h = React.createElement;

const PROMPT_PRESETS = [
  "把这些 PDF 合并成一个干净的新文件，保持原顺序。",
  "对这个扫描件做 OCR，并保留原分页。",
  "提取第 3 到第 7 页，输出一个新 PDF。",
  "比较这两个版本，告诉我差异并生成结果文件。",
];

function parseDateValue(value) {
  if (!value && value !== 0) {
    return null;
  }
  if (typeof value === "number") {
    return new Date(value < 1_000_000_000_000 ? value * 1000 : value);
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatTime(value) {
  const parsed = parseDateValue(value);
  if (!parsed) {
    return "刚刚";
  }
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(bytes) {
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
}

function humanizeName(value) {
  if (!value) {
    return "处理中";
  }
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function truncateText(value, maxLength = 70) {
  if (!value) {
    return "";
  }
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function fileExtension(filename) {
  const parts = (filename || "").split(".");
  return parts.length > 1 ? parts.pop().toUpperCase() : "FILE";
}

function mapConversationMessages(messages) {
  return (messages || []).flatMap((message, index) => {
    if (message.type === "human") {
      return [{ id: `human-${index}`, kind: "user", content: message.content || "" }];
    }
    if (message.type === "ai") {
      return message.content ? [{ id: `assistant-${index}`, kind: "assistant", content: message.content }] : [];
    }
    if (message.type === "tool") {
      return [{
        id: `tool-${index}`,
        kind: "step",
        label: humanizeName(message.name),
        status: "DONE",
        content: message.content || "",
        progress: null,
        progressLabel: "",
        elapsedSeconds: null,
        downloads: [],
      }];
    }
    return [];
  });
}

function parseSseBlock(block) {
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
}

async function consumeSse(response, handlers) {
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
}

function App() {
  const [files, setFiles] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [messages, setMessages] = useState([]);
  const [resultFiles, setResultFiles] = useState([]);
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const [statusText, setStatusText] = useState("上传 PDF，然后直接描述你想要的结果。");
  const [surfaceError, setSurfaceError] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isDragging, setIsDragging] = useState(false);

  const uploadInputRef = useRef(null);
  const messageEndRef = useRef(null);

  useEffect(() => {
    refreshSurface().finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadFiles().catch(() => {});
      loadConversations().catch(() => {});
      if (currentConversationId) {
        loadConversationFiles(currentConversationId).catch(() => {});
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [currentConversationId]);

  useEffect(() => {
    if (currentConversationId) {
      loadConversationFiles(currentConversationId).catch(() => {});
    } else {
      setResultFiles([]);
    }
  }, [currentConversationId]);

  useEffect(() => {
    if (messageEndRef.current) {
      messageEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages]);

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(path, {
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
  }

  async function refreshSurface() {
    setSurfaceError("");
    const results = await Promise.allSettled([loadFiles(), loadConversations()]);
    const failures = results
      .filter((item) => item.status === "rejected")
      .map((item) => item.reason?.message)
      .filter(Boolean);
    if (failures.length > 0) {
      setSurfaceError(failures[0]);
      setStatusText(failures[0]);
    }
  }

  async function loadFiles() {
    const data = await api("/api/files?page=1&limit=100", { headers: {} });
    const nextFiles = data.files || [];
    setFiles(nextFiles);
    setSelectedFileIds((current) => current.filter((fileId) => nextFiles.some((file) => file.id === fileId)));
  }

  async function loadConversations() {
    const data = await api("/api/agent/threads?page=1&limit=20", { headers: {} }).catch(() => ({ threads: [] }));
    setConversations(data.threads || []);
  }

  async function loadConversationFiles(conversationId) {
    if (!conversationId) {
      setResultFiles([]);
      return;
    }
    const data = await api(`/api/agent/threads/${conversationId}/files`, { headers: {} }).catch(() => ({ files: [] }));
    setResultFiles(data.files || []);
  }

  async function openConversation(conversationId) {
    const [conversation, filesData] = await Promise.all([
      api(`/api/agent/threads/${conversationId}`, { headers: {} }),
      api(`/api/agent/threads/${conversationId}/files`, { headers: {} }).catch(() => ({ files: [] })),
    ]);
    setCurrentConversationId(conversationId);
    setMessages(mapConversationMessages(conversation.messages || []));
    setResultFiles(filesData.files || []);
    setStatusText("已切换到历史会话。");
  }

  function startNewConversation() {
    setCurrentConversationId("");
    setMessages([]);
    setResultFiles([]);
    setDraftMessage("");
    setStatusText("新的会话已就绪。");
  }

  async function deleteConversation(conversationId) {
    await api(`/api/agent/threads/${conversationId}`, { method: "DELETE" });
    if (currentConversationId === conversationId) {
      startNewConversation();
    }
    await loadConversations();
    setStatusText("历史会话已删除。");
  }

  async function uploadFiles(fileList) {
    if (!fileList.length) {
      return;
    }
    const createdIds = [];
    setSurfaceError("");
    setStatusText(`正在上传 ${fileList.length} 个文件...`);

    for (const file of fileList) {
      const body = new FormData();
      body.append("file", file);
      const payload = await api("/api/files", { method: "POST", body });
      if (payload?.id) {
        createdIds.push(String(payload.id));
      }
    }

    await loadFiles();
    if (createdIds.length > 0) {
      setSelectedFileIds((current) => Array.from(new Set([...createdIds, ...current])));
    }
    setStatusText(`已上传 ${fileList.length} 个文件。`);
  }

  async function deleteFile(fileId) {
    await api(`/api/files/${fileId}`, { method: "DELETE" });
    await loadFiles();
    setSelectedFileIds((current) => current.filter((item) => item !== fileId));
    setStatusText("文件已删除。");
  }

  function toggleFileSelection(fileId) {
    setSelectedFileIds((current) =>
      current.includes(fileId) ? current.filter((item) => item !== fileId) : [...current, fileId]
    );
  }

  function pushSystemMessage(content) {
    setMessages((current) => [...current, { id: `system-${Date.now()}-${Math.random()}`, kind: "system", content }]);
  }

  function upsertStepMessage(stepName, patch) {
    setMessages((current) => {
      const next = [...current];
      let index = -1;
      for (let i = next.length - 1; i >= 0; i -= 1) {
        if (next[i].kind === "step" && next[i].stepName === stepName && next[i].status === "RUNNING") {
          index = i;
          break;
        }
      }

      if (index === -1) {
        next.push({
          id: `step-${stepName}-${Date.now()}-${Math.random()}`,
          kind: "step",
          stepName,
          label: humanizeName(stepName),
          status: patch.status || "RUNNING",
          content: patch.content || "",
          progress: patch.progress ?? null,
          progressLabel: patch.progressLabel || "",
          elapsedSeconds: patch.elapsedSeconds ?? null,
          downloads: patch.downloads || [],
        });
      } else {
        next[index] = { ...next[index], ...patch };
      }
      return next;
    });
  }

  async function sendChatMessage() {
    const message = draftMessage.trim();
    if (!message || isSending) {
      return;
    }
    if (!currentConversationId && selectedFileIds.length === 0) {
      setStatusText("先上传并选中至少一个文件。");
      return;
    }

    setDraftMessage("");
    setIsSending(true);
    setSurfaceError("");
    setStatusText("正在处理你的 PDF 请求...");
    setMessages((current) => [...current, { id: `user-${Date.now()}`, kind: "user", content: message }]);

    let assistantMessageId = null;
    let nextConversationId = currentConversationId || null;

    const appendAssistantToken = (content) => {
      setMessages((current) => {
        const next = [...current];
        const index = assistantMessageId ? next.findIndex((item) => item.id === assistantMessageId) : -1;
        if (index === -1) {
          assistantMessageId = `assistant-${Date.now()}-${Math.random()}`;
          next.push({ id: assistantMessageId, kind: "assistant", content });
        } else {
          next[index] = { ...next[index], content: `${next[index].content}${content}` };
        }
        return next;
      });
    };

    try {
      const response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: currentConversationId || undefined,
          message,
          file_ids: selectedFileIds,
        }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }

      await consumeSse(response, {
        thread(data) {
          if (data.thread_id) {
            nextConversationId = data.thread_id;
            setCurrentConversationId(data.thread_id);
          }
        },
        token(data) {
          if (data.content) {
            appendAssistantToken(data.content);
          }
        },
        tool_start(data) {
          upsertStepMessage(data.tool || "processing_step", {
            status: "RUNNING",
            content: "",
            progress: 0,
            progressLabel: "",
            elapsedSeconds: null,
            downloads: [],
          });
        },
        tool_progress(data) {
          upsertStepMessage(data.tool || "processing_step", {
            status: "RUNNING",
            progress: Number.isFinite(data.percent) ? data.percent : 0,
            progressLabel: data.message || "",
            elapsedSeconds: data.elapsed_seconds ?? null,
          });
        },
        tool_end(data) {
          upsertStepMessage(data.tool || "processing_step", {
            status: "DONE",
            content: data.output || "",
            progress: 100,
            progressLabel: "",
            elapsedSeconds: data.elapsed_seconds ?? null,
            downloads: data.files || [],
          });
        },
        error(data) {
          const messageText = data.message || "会话流处理失败";
          pushSystemMessage(messageText);
          setStatusText(messageText);
          setSurfaceError(messageText);
        },
        done() {
          setStatusText("处理完成，可以继续追问或下载结果。");
        },
      });

      if (nextConversationId) {
        await loadConversationFiles(nextConversationId);
      }
      await loadConversations();
    } catch (error) {
      pushSystemMessage(error.message);
      setStatusText(error.message);
      setSurfaceError(error.message);
    } finally {
      setIsSending(false);
    }
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    uploadFiles(Array.from(event.dataTransfer.files || [])).catch((error) => {
      setSurfaceError(error.message);
      setStatusText(error.message);
    });
  }

  const selectedFiles = selectedFileIds
    .map((fileId) => files.find((item) => item.id === fileId))
    .filter(Boolean);

  return h(
    "div",
    { className: "app-shell" },
    h("div", { className: "ambient ambient-a" }),
    h("div", { className: "ambient ambient-b" }),
    h(
      "header",
      { className: "topbar" },
      h(
        "div",
        { className: "brand" },
        h("div", { className: "eyebrow" }, "Conversation-First PDF Editing"),
        h("h1", null, "上传文件，直接说结果"),
        h(
          "p",
          null,
          "不要先选工具，不要排工作流。把 PDF 放进来，然后像交代同事一样描述你最终想拿到什么文件。"
        )
      ),
      h(
        "div",
        { className: "status-cluster" },
        h(StatusPill, { label: "已上传", value: String(files.length) }),
        h(StatusPill, { label: "已选中", value: String(selectedFileIds.length) }),
        h(StatusPill, { label: "已产出", value: String(resultFiles.length) }),
        h(
          "button",
          { className: "ghost-btn", onClick: startNewConversation },
          "新建会话"
        )
      )
    ),
    h(
      "main",
      { className: "workspace" },
      h(
        "section",
        { className: "chat-panel" },
        h(
          "div",
          { className: "chat-hero" },
          h("div", { className: "hero-kicker" }, currentConversationId ? "当前会话" : "开始处理"),
          h("div", { className: "hero-title-row" }, h("h2", null, currentConversationId ? "继续修改这组文件" : "告诉我你要的最终 PDF")),
          h(
            "p",
            null,
            currentConversationId
              ? `当前会话 ID：${truncateText(currentConversationId, 24)}`
              : "上传文件后，直接描述目标结果，例如“合并并加页码”“先 OCR 再压缩”“提取第 5 页到第 9 页”。"
          ),
          h(
            "div",
            { className: "preset-row" },
            PROMPT_PRESETS.map((prompt) =>
              h(
                "button",
                {
                  key: prompt,
                  className: "preset-chip",
                  onClick: () => setDraftMessage(prompt),
                },
                prompt
              )
            )
          )
        ),
        surfaceError &&
          h(
            "div",
            { className: "surface-error" },
            h("strong", null, "当前服务不可用"),
            h("span", null, surfaceError)
          ),
        h(
          "div",
          { className: "selection-strip" },
          selectedFiles.length === 0
            ? h("div", { className: "strip-empty" }, "还没有选中文件。上传后点一下文件卡片即可加入当前会话。")
            : selectedFiles.map((file) =>
                h(
                  "button",
                  {
                    key: file.id,
                    className: "selection-chip",
                    onClick: () => toggleFileSelection(file.id),
                    title: "移出当前会话",
                  },
                  h("span", { className: "selection-chip-name" }, file.orig_name),
                  h("span", { className: "selection-chip-meta" }, formatBytes(file.size_bytes)),
                  h("span", { className: "selection-chip-close" }, "×")
                )
              )
        ),
        h(
          "div",
          { className: "chat-stage" },
          messages.length === 0
            ? h(
                "div",
                { className: "empty-stage" },
                h("div", { className: "empty-kicker" }, "三步完成"),
                h("h3", null, "上传，描述，拿结果"),
                h(
                  "div",
                  { className: "onboarding-grid" },
                  h(OnboardingCard, {
                    index: "01",
                    title: "上传源文件",
                    copy: "支持一次上传多个 PDF。默认会自动加入当前上下文。",
                  }),
                  h(OnboardingCard, {
                    index: "02",
                    title: "直接说目标",
                    copy: "按结果描述，不按工具描述。比如“去空白页并压缩”。",
                  }),
                  h(OnboardingCard, {
                    index: "03",
                    title: "下载输出",
                    copy: "会话完成后，结果文件会出现在右侧结果区。",
                  })
                )
              )
            : h(
                "div",
                { className: "message-list" },
                messages.map((message) => h(MessageCard, { key: message.id, message })),
                h("div", { ref: messageEndRef })
              )
        ),
        h(
          "div",
          { className: "composer-shell" },
          h(
            "div",
            { className: "composer-header" },
            h("span", { className: "composer-label" }, "当前状态"),
            h("span", { className: "composer-status" }, statusText)
          ),
          h(
            "div",
            { className: "composer-row" },
            h(
              "button",
              {
                className: "upload-trigger",
                onClick: () => uploadInputRef.current && uploadInputRef.current.click(),
                title: "上传文件",
              },
              "上传文件"
            ),
            h("input", {
              ref: uploadInputRef,
              type: "file",
              multiple: true,
              accept: ".pdf,image/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx",
              style: { display: "none" },
              onChange: (event) => {
                uploadFiles(Array.from(event.target.files || [])).catch((error) => {
                  setSurfaceError(error.message);
                  setStatusText(error.message);
                });
                event.target.value = "";
              },
            }),
            h("textarea", {
              value: draftMessage,
              onChange: (event) => setDraftMessage(event.target.value),
              placeholder: "例如：把选中的两个 PDF 合并，删除空白页，再给每页加右下角页码。",
              rows: 4,
              onKeyDown: (event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendChatMessage();
                }
              },
            }),
            h(
              "button",
              {
                className: "send-btn",
                disabled: isSending || !draftMessage.trim(),
                onClick: sendChatMessage,
              },
              isSending ? "处理中..." : "发送"
            )
          )
        )
      ),
      h(
        "aside",
        { className: "dock" },
        h(
          "section",
          {
            className: `dock-card dropzone-card${isDragging ? " dragging" : ""}`,
            onDragOver: (event) => {
              event.preventDefault();
              setIsDragging(true);
            },
            onDragLeave: () => setIsDragging(false),
            onDrop: handleDrop,
          },
          h("div", { className: "dock-kicker" }, "文件入口"),
          h("h3", null, "把 PDF 拖到这里"),
          h("p", null, "点击下方文件卡片可加入或移出当前会话。"),
          h(
            "button",
            {
              className: "dropzone-btn",
              onClick: () => uploadInputRef.current && uploadInputRef.current.click(),
            },
            "选择文件"
          )
        ),
        h(
          "section",
          { className: "dock-card file-library" },
          h(
            "div",
            { className: "dock-head" },
            h("div", { className: "dock-kicker" }, "已上传文件"),
            h("span", { className: "dock-meta" }, isLoading ? "加载中..." : `${files.length} 个文件`)
          ),
          files.length === 0
            ? h("div", { className: "dock-empty" }, "还没有文件。")
            : h(
                "div",
                { className: "file-stack" },
                files.map((file) =>
                  h(FileCard, {
                    key: file.id,
                    file,
                    selected: selectedFileIds.includes(file.id),
                    onToggle: () => toggleFileSelection(file.id),
                    onDelete: () => deleteFile(file.id).catch((error) => {
                      setSurfaceError(error.message);
                      setStatusText(error.message);
                    }),
                  })
                )
              )
        ),
        h(
          "section",
          { className: "dock-card conversation-card" },
          h(
            "div",
            { className: "dock-head" },
            h("div", { className: "dock-kicker" }, "历史会话"),
            h("span", { className: "dock-meta" }, `${conversations.length} 条`)
          ),
          conversations.length === 0
            ? h("div", { className: "dock-empty" }, "还没有历史会话。")
            : h(
                "div",
                { className: "conversation-stack" },
                conversations.map((conversation) =>
                  h(
                    "div",
                    {
                      key: conversation.thread_id,
                      className: `conversation-item${currentConversationId === conversation.thread_id ? " active" : ""}`,
                    },
                    h(
                      "button",
                      {
                        className: "conversation-main",
                        onClick: () => openConversation(conversation.thread_id).catch((error) => {
                          setSurfaceError(error.message);
                          setStatusText(error.message);
                        }),
                      },
                      h("strong", null, truncateText(conversation.thread_id, 18)),
                      h("span", null, `${conversation.step_count || 0} 步 · ${formatTime(conversation.updated_at)}`)
                    ),
                    h(
                      "button",
                      {
                        className: "conversation-remove",
                        title: "删除会话",
                        onClick: () => deleteConversation(conversation.thread_id).catch((error) => {
                          setSurfaceError(error.message);
                          setStatusText(error.message);
                        }),
                      },
                      "×"
                    )
                  )
                )
              )
        ),
        h(
          "section",
          { className: "dock-card result-card" },
          h(
            "div",
            { className: "dock-head" },
            h("div", { className: "dock-kicker" }, "输出结果"),
            h("span", { className: "dock-meta" }, `${resultFiles.length} 个`)
          ),
          currentConversationId && resultFiles.length === 0 && h("div", { className: "dock-empty" }, "当前会话还没有产出文件。"),
          !currentConversationId && h("div", { className: "dock-empty" }, "发送一次请求后，这里会出现可下载的结果。"),
          resultFiles.length > 0 &&
            h(
              "div",
              { className: "result-stack" },
              resultFiles.map((file) =>
                h(
                  "a",
                  {
                    key: `${file.download_url}-${file.filename}`,
                    className: "result-item",
                    href: file.download_url,
                    download: true,
                  },
                  h(
                    "div",
                    { className: "result-copy" },
                    h("strong", null, file.path || file.filename),
                    h("span", null, formatBytes(file.size_bytes))
                  ),
                  h("span", { className: "result-arrow" }, "下载")
                )
              )
            )
        )
      )
    )
  );
}

function StatusPill({ label, value }) {
  return h(
    "div",
    { className: "status-pill" },
    h("span", null, label),
    h("strong", null, value)
  );
}

function OnboardingCard({ index, title, copy }) {
  return h(
    "div",
    { className: "onboarding-card" },
    h("span", { className: "onboarding-index" }, index),
    h("strong", null, title),
    h("p", null, copy)
  );
}

function FileCard({ file, selected, onToggle, onDelete }) {
  return h(
    "div",
    { className: `file-card${selected ? " selected" : ""}` },
    h(
      "button",
      { className: "file-main", onClick: onToggle },
      file.thumbnail_url
        ? h("img", {
            className: "file-thumb",
            src: file.thumbnail_url,
            alt: file.orig_name,
          })
        : h("div", { className: "file-thumb fallback" }, fileExtension(file.orig_name)),
      h(
        "div",
        { className: "file-copy" },
        h("strong", null, truncateText(file.orig_name, 28)),
        h("span", null, `${formatBytes(file.size_bytes)}${file.page_count ? ` · ${file.page_count} 页` : ""}`)
      )
    ),
    h(
      "div",
      { className: "file-actions" },
      h(
        "a",
        { href: file.download_url, className: "file-link", download: true },
        "原件"
      ),
      h(
        "button",
        {
          className: "file-delete",
          onClick: onDelete,
          title: "删除文件",
        },
        "删除"
      )
    )
  );
}

function MessageCard({ message }) {
  if (message.kind === "step") {
    return h(
      "div",
      { className: `message-card step${message.status === "RUNNING" ? " running" : ""}` },
      h(
        "div",
        { className: "message-head" },
        h("span", { className: "message-role" }, "处理中"),
        h("strong", null, message.label || "处理中")
      ),
      message.progressLabel && h("p", { className: "message-meta" }, message.progressLabel),
      Number.isFinite(message.progress) && message.status === "RUNNING" && h("p", { className: "message-meta" }, `${message.progress}%`),
      message.elapsedSeconds != null && h("p", { className: "message-meta" }, `${message.elapsedSeconds}s`),
      message.content && h("div", { className: "message-body" }, message.content),
      message.downloads && message.downloads.length > 0 &&
        h(
          "div",
          { className: "inline-downloads" },
          message.downloads.map((file) =>
            h(
              "a",
              { key: file, href: file, download: true, className: "inline-download" },
              file.split("/").pop() || "下载文件"
            )
          )
        )
    );
  }

  const className =
    message.kind === "user"
      ? "message-card user"
      : message.kind === "assistant"
        ? "message-card assistant"
        : "message-card system";
  const roleLabel = message.kind === "user" ? "你" : message.kind === "assistant" ? "助手" : "系统";

  return h(
    "div",
    { className },
    h(
      "div",
      { className: "message-head" },
      h("span", { className: "message-role" }, roleLabel)
    ),
    h("div", { className: "message-body" }, message.content)
  );
}

createRoot(document.getElementById("root")).render(h(App));
