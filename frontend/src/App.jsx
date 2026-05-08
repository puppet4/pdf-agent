import React, { useState, useEffect, useRef } from 'react';
import { FileService, ConversationService, consumeSse } from './services/api';
import { API_BASE_URL } from './services/config';
import { MessageCard } from './components/MessageCard.jsx';
import { truncateText, formatBytes, formatTime, mapConversationMessages, normalizeConversationTitle, fileExtension } from './utils';

// Icons
const SendIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>;
const AttachIcon = () => <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>;
const PlusIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>;
const TrashIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>;
const DownloadIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>;
const EyeIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z"></path><circle cx="12" cy="12" r="3"></circle></svg>;
const CloseIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>;
const CopyIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>;
const EditIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"></path></svg>;
const RetryIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="1 4 1 10 7 10"></polyline><polyline points="23 20 23 14 17 14"></polyline><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10"></path><path d="M3.51 15A9 9 0 0 0 18.36 18.36L23 14"></path></svg>;
const StopIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5"></rect></svg>;

const PROMPT_PRESETS = [
  "把选中的 PDF 合并成一个文件",
  "提取第一页到第三页",
  "给文件添加 OCR 文字层",
  "压缩文件体积"
];

const FILE_ONLY_PROMPT = "请先查看我这次选中的文件，并准备按我的下一步要求处理。";
const SURFACE_ERROR_TIMEOUT_MS = 5000;

function App() {
  const [files, setFiles] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [messages, setMessages] = useState([]);
  const [selectedFileIds, setSelectedFileIds] = useState([]);
  const [selectedArtifactPaths, setSelectedArtifactPaths] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const [surfaceError, setSurfaceError] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState("");
  const [editingOriginalText, setEditingOriginalText] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState("");
  const [previewFile, setPreviewFile] = useState(null);

  const uploadInputRef = useRef(null);
  const messageEndRef = useRef(null);
  const textareaRef = useRef(null);
  const streamingAssistantIdRef = useRef(null);
  const streamingStepIdRef = useRef(null);
  const pendingArtifactUrlsRef = useRef([]);
  const abortControllerRef = useRef(null);

  async function refreshConversationSurface(conversationId, options = {}) {
    if (!conversationId) {
      setMessages([]);
      setArtifacts([]);
      return;
    }
    const { activate = false } = options;
    const [conversation, nextArtifacts] = await Promise.all([
      ConversationService.getConversation(conversationId),
      ConversationService.getArtifacts(conversationId),
    ]);
    if (activate) {
      setCurrentConversationId(conversationId);
    }
    streamingStepIdRef.current = null;
    setMessages(mapConversationMessages(conversation.messages || []));
    setArtifacts(nextArtifacts);
  }

  useEffect(() => {
    const run = async () => {
      setSurfaceError("");
      const results = await Promise.allSettled([loadFiles(), loadConversations()]);
      const failures = results
        .filter((item) => item.status === "rejected")
        .map((item) => item.reason?.message)
        .filter(Boolean);
      if (failures.length > 0) {
        setSurfaceError(failures[0]);
      }
    };
    run();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadFiles().catch(() => {});
      loadConversations().catch(() => {});
      if (currentConversationId) {
        loadArtifacts(currentConversationId).catch(() => {});
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [currentConversationId]);

  useEffect(() => {
    if (messageEndRef.current) {
      messageEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [draftMessage]);

  useEffect(() => {
    if (!copiedMessageId) {
      return undefined;
    }
    const timer = window.setTimeout(() => setCopiedMessageId(""), 1800);
    return () => window.clearTimeout(timer);
  }, [copiedMessageId]);

  useEffect(() => {
    if (!surfaceError) {
      return undefined;
    }
    const timer = window.setTimeout(() => setSurfaceError(""), SURFACE_ERROR_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [surfaceError]);

  useEffect(() => {
    if (!previewFile) {
      return undefined;
    }
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        setPreviewFile(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [previewFile]);

  useEffect(() => () => {
    abortControllerRef.current?.abort();
  }, []);

  const loadFiles = async () => {
    const nextFiles = await FileService.getFiles();
    setFiles(nextFiles);
  };

  const loadConversations = async () => {
    const nextConversations = await ConversationService.getConversations();
    setConversations(nextConversations);
  };

  const loadArtifacts = async (conversationId) => {
    if (!conversationId) {
      setArtifacts([]);
      return;
    }
    const nextArtifacts = await ConversationService.getArtifacts(conversationId);
    setArtifacts(nextArtifacts);
  };

  const openConversation = async (conversationId) => {
    try {
      await refreshConversationSurface(conversationId, { activate: true });
      setSelectedArtifactPaths([]);
      setSelectedFileIds([]);
      setSurfaceError("");
    } catch (e) {
      if (e?.message === "Conversation not found") {
        setConversations((current) => current.filter((item) => item.id !== conversationId));
        if (currentConversationId === conversationId) {
          setCurrentConversationId("");
          setMessages([]);
          setArtifacts([]);
        }
      }
      setSurfaceError(e.message);
    }
  };

  const startNewConversation = async () => {
    try {
      const conversation = await ConversationService.createConversation();
      setCurrentConversationId(conversation.id);
      setArtifacts([]);
      setMessages([]);
      setDraftMessage("");
      setSelectedArtifactPaths([]);
      setSelectedFileIds([]);
      setSurfaceError("");
      await loadConversations();
    } catch (error) {
      setSurfaceError(error.message);
    }
  };

  const resetComposerOnly = () => {
    setMessages([]);
    setArtifacts([]);
    setDraftMessage("");
    setSelectedArtifactPaths([]);
    setSelectedFileIds([]);
    setEditingMessageId("");
    setEditingOriginalText("");
    setSurfaceError("");
  };

  const deleteConversation = async (e, conversationId) => {
    e.stopPropagation();
    try {
      await ConversationService.deleteConversation(conversationId);
      if (currentConversationId === conversationId) {
        setCurrentConversationId("");
        resetComposerOnly();
      }
      await loadConversations();
    } catch (error) {
      setSurfaceError(error.message);
    }
  };

  const uploadFiles = async (fileList) => {
    if (!fileList.length) return;
    setSurfaceError("");
    try {
      const createdIds = await FileService.uploadFiles(fileList);
      const nextFiles = await FileService.getFiles();
      setFiles(nextFiles);
      if (createdIds.length > 0) {
        setSelectedFileIds((current) => Array.from(new Set([...createdIds, ...current])));
      }
    } catch (error) {
      setSurfaceError(error.message);
    }
  };

  const toggleFileSelection = (fileId) => {
    setSelectedFileIds((current) =>
      current.includes(fileId) ? current.filter((item) => item !== fileId) : [...current, fileId]
    );
  };

  const toggleArtifactSelection = (artifactPath) => {
    setSelectedArtifactPaths((current) =>
      current.includes(artifactPath) ? current.filter((item) => item !== artifactPath) : [...current, artifactPath]
    );
  };

  const upsertAssistantDownloads = (downloads) => {
    const nextDownloads = Array.from(new Set((downloads || []).filter(Boolean)));
    if (nextDownloads.length === 0) {
      return;
    }

    setMessages((current) => {
      const next = [...current];
      const assistantMessageId = streamingAssistantIdRef.current;
      const index = assistantMessageId ? next.findIndex((item) => item.id === assistantMessageId) : -1;
      if (index === -1) {
        return current;
      }
      next[index] = {
        ...next[index],
        downloads: Array.from(new Set([...(next[index].downloads || []), ...nextDownloads])),
      };
      return next;
    });
  };

  const upsertStepMessage = (patch) => {
    const stepId = streamingStepIdRef.current || `step-${Date.now()}-${Math.random()}`;
    streamingStepIdRef.current = stepId;
    setMessages((current) => {
      const next = [...current];
      const index = next.findIndex((item) => item.id === stepId);
      const base = index === -1 ? {
        id: stepId,
        kind: "step",
        status: "RUNNING",
        label: "处理中",
        progress: 0,
        progressLabel: "",
        elapsedSeconds: null,
        content: "",
        warning: "",
        downloads: [],
      } : next[index];
      const merged = {
        ...base,
        ...patch,
      };
      if (index === -1) {
        next.push(merged);
      } else {
        next[index] = merged;
      }
      return next;
    });
  };

  const finalizeStepMessage = (patch = {}) => {
    const stepId = streamingStepIdRef.current;
    if (!stepId) {
      return;
    }
    setMessages((current) => current.map((item) => (
      item.id === stepId
        ? {
            ...item,
            status: item.status === "ERROR" ? item.status : "DONE",
            progress: item.progress ?? 100,
            ...patch,
          }
        : item
    )));
    streamingStepIdRef.current = null;
  };

  const attachDownloadsToCurrentStep = (downloads) => {
    const stepId = streamingStepIdRef.current;
    if (!stepId) {
      return;
    }
    const nextDownloads = Array.from(new Set((downloads || []).filter(Boolean)));
    if (nextDownloads.length === 0) {
      return;
    }
    setMessages((current) => current.map((item) => (
      item.id === stepId
        ? {
            ...item,
            downloads: Array.from(new Set([...(item.downloads || []), ...nextDownloads])),
          }
        : item
    )));
  };

  const beginEditMessage = (message) => {
    setDraftMessage(message.content || "");
    setEditingMessageId(message.id);
    setEditingOriginalText(message.content || "");
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const cancelEditing = () => {
    setEditingMessageId("");
    setEditingOriginalText("");
  };

  const getMessageCopyText = (message) => {
    if (!message) {
      return "";
    }
    return message.content || "";
  };

  const copyMessage = async (message) => {
    const content = getMessageCopyText(message);
    if (!content) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessageId(message.id);
    } catch {
      setSurfaceError("复制失败，请检查浏览器权限。");
    }
  };

  const findRetrySource = (index) => {
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      if (messages[cursor]?.kind === "user" && messages[cursor]?.content?.trim()) {
        return messages[cursor];
      }
    }
    return null;
  };

  const retryFromMessage = (index) => {
    const source = findRetrySource(index);
    if (!source) {
      setSurfaceError("没有找到可重试的上一条用户消息。");
      return;
    }
    setEditingMessageId("");
    setEditingOriginalText("");
    sendChatMessage(source.content, { attachments: source.attachments });
  };

  const resolveAssetUrl = (value) => {
    if (!value) {
      return "";
    }
    if (/^https?:\/\//i.test(value)) {
      return value;
    }
    if (value.startsWith('/')) {
      return `${API_BASE_URL}${value}`;
    }
    return "";
  };

  const inferPreviewKind = (name, mimeType = "", url = "") => {
    const lowered = `${name || ""} ${url || ""}`.toLowerCase();
    if (mimeType.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp|bmp|svg)$/.test(lowered)) {
      return 'image';
    }
    if (mimeType === 'application/pdf' || lowered.includes('.pdf')) {
      return 'pdf';
    }
    return 'other';
  };

  const openPreview = (item) => {
    const previewUrl = resolveAssetUrl(item.previewUrl || item.downloadUrl || item.path || "");
    const downloadUrl = resolveAssetUrl(item.downloadUrl || item.path || "");
    setPreviewFile({
      name: item.name,
      kind: inferPreviewKind(item.name, item.mimeType || "", previewUrl || downloadUrl),
      previewUrl: previewUrl || downloadUrl,
      downloadUrl: downloadUrl || previewUrl,
    });
  };

  const withInlinePreview = (value) => {
    if (!value) {
      return "";
    }
    const separator = value.includes('?') ? '&' : '?';
    return `${value}${separator}inline=1`;
  };

  const extractArtifactPath = (attachment) => {
    if (!attachment || attachment.source !== "artifact") {
      return "";
    }
    if (typeof attachment.artifactPath === "string" && attachment.artifactPath) {
      return attachment.artifactPath;
    }
    const candidate = attachment.path || attachment.downloadUrl || "";
    if (typeof candidate !== "string" || !candidate) {
      return "";
    }
    const matchedArtifact = artifacts.find((artifact) => artifact.download_url === candidate);
    if (matchedArtifact?.path) {
      return matchedArtifact.path;
    }
    const marker = "/artifacts/";
    const markerIndex = candidate.indexOf(marker);
    if (markerIndex === -1) {
      return "";
    }
    const rawPath = candidate.slice(markerIndex + marker.length).split("?")[0];
    try {
      return decodeURIComponent(rawPath);
    } catch {
      return rawPath;
    }
  };

  const resolveAttachmentSelection = (attachments = []) => {
    const fileIds = [];
    const artifactPaths = [];

    attachments.forEach((attachment) => {
      if (!attachment || typeof attachment !== "object") {
        return;
      }
      if (attachment.source === "artifact") {
        const artifactPath = extractArtifactPath(attachment);
        if (artifactPath) {
          artifactPaths.push(artifactPath);
        }
        return;
      }
      const fileId = attachment.fileId || attachment.file_id || "";
      if (typeof fileId === "string" && fileId) {
        fileIds.push(fileId);
      }
    });

    return {
      fileIds: Array.from(new Set(fileIds)),
      artifactPaths: Array.from(new Set(artifactPaths)),
    };
  };

  const stopStreamingResponse = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsThinking(false);
    setIsSending(false);
  };

  const sendChatMessage = async (overrideMessage = null, options = {}) => {
    const messageSource = typeof overrideMessage === "string" ? overrideMessage : draftMessage;
    const retriedAttachments = Array.isArray(options.attachments) ? options.attachments : [];
    const fallbackSelection = resolveAttachmentSelection(retriedAttachments);
    const fileIdsForSend = fallbackSelection.fileIds.length > 0 ? fallbackSelection.fileIds : [...selectedFileIds];
    const artifactPathsForSend = fallbackSelection.artifactPaths.length > 0 ? fallbackSelection.artifactPaths : [...selectedArtifactPaths];
    const hasSelectedInputs = fileIdsForSend.length > 0 || artifactPathsForSend.length > 0;
    const trimmedMessage = messageSource.trim();
    const message = trimmedMessage || (hasSelectedInputs ? FILE_ONLY_PROMPT : "");
    if (!message || isSending) return;

    setDraftMessage("");
    setIsSending(true);
    setIsThinking(true);
    setSurfaceError("");
    const isEditedResend = Boolean(editingMessageId);
    const attachmentsForSend = retriedAttachments.length > 0 ? retriedAttachments : buildSelectedInputAttachments();
    setMessages((current) => [...current, {
      id: `user-${Date.now()}`,
      kind: "user",
      content: message,
      editedFromId: isEditedResend ? editingMessageId : "",
      edited: isEditedResend && editingOriginalText !== message,
      attachments: attachmentsForSend,
    }]);
    setEditingMessageId("");
    setEditingOriginalText("");

    streamingAssistantIdRef.current = null;
    streamingStepIdRef.current = null;
    pendingArtifactUrlsRef.current = [];
    abortControllerRef.current?.abort();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    let nextConversationId = currentConversationId || null;

    const appendAssistantToken = (content) => {
      if (!content) {
        return;
      }

      setMessages((current) => {
        const next = [...current];
        const assistantMessageId = streamingAssistantIdRef.current;
        const index = assistantMessageId ? next.findIndex((item) => item.id === assistantMessageId) : -1;
        if (index === -1) {
          if (!content.trim()) {
            return current;
          }
          const nextId = `assistant-${Date.now()}-${Math.random()}`;
          streamingAssistantIdRef.current = nextId;
          next.push({
            id: nextId,
            kind: "assistant",
            content,
            downloads: [...pendingArtifactUrlsRef.current],
          });
        } else {
          next[index] = {
            ...next[index],
            content: `${next[index].content}${content}`,
            downloads: Array.from(new Set([...(next[index].downloads || []), ...pendingArtifactUrlsRef.current])),
          };
        }
        return next;
      });
    };

    try {
      if (!nextConversationId) {
        const created = await ConversationService.createConversation();
        nextConversationId = created.id;
        setCurrentConversationId(created.id);
        await loadConversations();
      }

	      const response = await ConversationService.sendMessage(nextConversationId, {
	        message,
	        file_ids: fileIdsForSend,
          artifact_paths: artifactPathsForSend,
	      }, {
          signal: abortController.signal,
        });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }

      await consumeSse(response, {
        conversation(data) {
          if (data.conversation_id) {
            nextConversationId = data.conversation_id;
            setCurrentConversationId(data.conversation_id);
          }
        },
        tool_start(data) {
          setIsThinking(false);
          if (streamingStepIdRef.current) {
            finalizeStepMessage({ status: "DONE", progress: 100 });
          }
          upsertStepMessage({
            status: "RUNNING",
            label: data.label || data.name || "处理中",
            progress: 0,
            progressLabel: "开始执行",
            content: "",
            warning: "",
            downloads: [],
            elapsedSeconds: null,
          });
        },
        progress(data) {
          setIsThinking(false);
          upsertStepMessage({
            status: "RUNNING",
            label: data.label || data.name || "处理中",
            progress: Number.isFinite(data.percent) ? data.percent : undefined,
            progressLabel: data.message || "处理中",
          });
        },
        heartbeat(data) {
          setIsThinking(false);
          upsertStepMessage({
            status: "RUNNING",
            label: data.label || data.name || "处理中",
            progressLabel: "处理中",
          });
        },
        token(data) {
          if (data.content) {
            setIsThinking(false);
            appendAssistantToken(data.content);
          }
        },
        artifact(data) {
          if (!Array.isArray(data.files) || data.files.length === 0) {
            return;
          }
          pendingArtifactUrlsRef.current = Array.from(new Set([
            ...pendingArtifactUrlsRef.current,
            ...data.files.filter(Boolean),
          ]));
          attachDownloadsToCurrentStep(data.files);
          upsertAssistantDownloads(pendingArtifactUrlsRef.current);
        },
        tool_end(data) {
          const details = [data.warning, data.message].filter(Boolean).join("\n");
          finalizeStepMessage({
            status: data.warning ? "WARNING" : "DONE",
            label: data.label || data.name || "处理中",
            progress: 100,
            progressLabel: data.warning ? "已完成，但需要注意" : "已完成",
            elapsedSeconds: Number.isFinite(data.elapsed_seconds) ? data.elapsed_seconds : null,
            warning: data.warning || "",
            content: details,
          });
        },
        done() {
          if (streamingStepIdRef.current) {
            finalizeStepMessage({ status: "DONE", progress: 100, progressLabel: "已完成" });
          }
          if (streamingAssistantIdRef.current) {
            upsertAssistantDownloads(pendingArtifactUrlsRef.current);
            return;
          }
          if (pendingArtifactUrlsRef.current.length === 0) {
            return;
          }
          const fallbackId = `assistant-${Date.now()}-${Math.random()}`;
          streamingAssistantIdRef.current = fallbackId;
          setMessages((current) => [...current, {
            id: fallbackId,
            kind: "assistant",
            content: "已处理完成。",
            downloads: [...pendingArtifactUrlsRef.current],
          }]);
        },
        error(data) {
          setIsThinking(false);
          finalizeStepMessage({
            status: "ERROR",
            progressLabel: "执行失败",
            content: data.message || "处理失败。",
          });
          setSurfaceError(data.message || "处理失败。");
        }
      });

      await loadConversations();
      await refreshConversationSurface(nextConversationId, { activate: true });
    } catch (error) {
	      if (error?.name === "AbortError") {
          return;
        }
	      setIsThinking(false);
	      setSurfaceError(error.message);
	    } finally {
      if (abortControllerRef.current === abortController) {
        abortControllerRef.current = null;
      }
      setIsThinking(false);
      streamingAssistantIdRef.current = null;
      streamingStepIdRef.current = null;
      pendingArtifactUrlsRef.current = [];
      setIsSending(false);
    }
  };

  const selectedFiles = selectedFileIds
    .map((fileId) => files.find((item) => item.id === fileId))
    .filter(Boolean);
  const selectedArtifacts = selectedArtifactPaths
    .map((artifactPath) => artifacts.find((item) => item.path === artifactPath))
    .filter(Boolean);
  const artifactLookup = new Map(artifacts.map((artifact) => [artifact.download_url, artifact]));
  const turnArtifactGroups = (() => {
    const groups = [];
    let currentGroup = null;
    const matchedArtifactUrls = new Set();

    messages.forEach((message, index) => {
      if (message.kind === "user") {
        if (currentGroup) {
          currentGroup.endIndex = Math.max(currentGroup.endIndex, index - 1);
        }
        currentGroup = {
          id: message.id,
          label: `第 ${groups.length + 1} 轮`,
          prompt: truncateText((message.content || "").trim() || "仅发送文件", 36),
          artifacts: [],
          endIndex: index,
        };
        groups.push(currentGroup);
        return;
      }

      if (!currentGroup) {
        return;
      }

      currentGroup.endIndex = index;

      if (!Array.isArray(message.downloads)) {
        return;
      }

      message.downloads.forEach((downloadUrl) => {
        if (!downloadUrl || matchedArtifactUrls.has(downloadUrl)) {
          return;
        }
        const artifact = artifactLookup.get(downloadUrl);
        if (!artifact) {
          return;
        }
        matchedArtifactUrls.add(downloadUrl);
        currentGroup.artifacts.push(artifact);
      });
    });

    if (currentGroup) {
      currentGroup.endIndex = Math.max(currentGroup.endIndex, messages.length - 1);
    }

    const unmatchedArtifacts = artifacts.filter((artifact) => !matchedArtifactUrls.has(artifact.download_url));
    if (unmatchedArtifacts.length > 0) {
      groups.push({
        id: "unmatched-artifacts",
        label: "其他结果",
        prompt: "未能关联到具体轮次的历史结果",
        artifacts: unmatchedArtifacts,
        endIndex: Math.max(messages.length - 1, 0),
      });
    }

    return groups.filter((group) => group.artifacts.length > 0).reverse();
  })();
  const artifactGroupsByEndIndex = turnArtifactGroups.reduce((acc, group) => {
    const key = String(group.endIndex);
    if (!acc[key]) {
      acc[key] = [];
    }
    acc[key].push(group);
    return acc;
  }, {});
  const buildSelectedInputAttachments = () => [
    ...selectedFiles.map((file) => ({
      name: file.orig_name,
      source: "upload",
      fileId: file.id,
      mimeType: file.mime_type || "",
      downloadUrl: file.download_url,
      previewUrl: withInlinePreview(file.download_url),
      thumbnailUrl: file.thumbnail_url,
    })),
    ...selectedArtifacts.map((artifact) => ({
      name: artifact.filename || artifact.path,
      source: "artifact",
      artifactPath: artifact.path,
      path: artifact.download_url,
      mimeType: artifact.filename?.toLowerCase().endsWith('.pdf') ? 'application/pdf' : '',
      downloadUrl: artifact.download_url,
      previewUrl: withInlinePreview(artifact.download_url),
    })),
  ];
  const describeAttachment = (attachment) => (
    attachment.source === 'artifact' ? '来自结果文件' : '本次附带'
  );
  const renderArtifactGroup = (group) => (
    <div key={group.id} className="artifact-rail message-turn-artifacts">
      <div className="artifact-rail-list">
        {group.artifacts.map((artifact) => (
          <div
            key={artifact.download_url}
            className={`artifact-row ${selectedArtifactPaths.includes(artifact.path) ? 'active' : ''}`}
          >
            <button
              type="button"
              className="artifact-row-main artifact-preview-trigger"
              onClick={() => openPreview({
                name: artifact.filename || artifact.path,
                mimeType: (artifact.filename || artifact.path || '').toLowerCase().endsWith('.pdf') ? 'application/pdf' : '',
                previewUrl: withInlinePreview(artifact.download_url),
                downloadUrl: artifact.download_url,
              })}
            >
              <div className="artifact-row-badge">{fileExtension(artifact.filename || artifact.path)}</div>
              <div className="artifact-copy">
                <strong>{truncateText(artifact.filename || artifact.path, 46)}</strong>
                <span>{formatBytes(artifact.size_bytes) || '结果文件'} · 点击预览</span>
              </div>
            </button>
            <div className="artifact-row-actions">
              <a
                href={resolveAssetUrl(artifact.download_url)}
                download
                className="artifact-action-btn"
                title="下载文件"
              >
                <DownloadIcon />
                <span>下载</span>
              </a>
              <button
                className={`artifact-action-btn ${selectedArtifactPaths.includes(artifact.path) ? 'primary' : ''}`}
                onClick={() => toggleArtifactSelection(artifact.path)}
              >
                {selectedArtifactPaths.includes(artifact.path) ? '已用作输入' : '用作输入'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="app-container">
      
      {/* Conversation Sidebar */}
      <aside className="conversation-sidebar">
        <button className="new-chat-btn" onClick={startNewConversation}>
          <span>新建会话</span>
          <PlusIcon />
        </button>
        
        <div className="sidebar-title">会话列表</div>
        <div className="conversation-list">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${currentConversationId === conv.id ? 'active' : ''}`}
              onClick={() => openConversation(conv.id)}
            >
              <div className="conversation-item-main">
                <div className="conversation-title">{truncateText(normalizeConversationTitle(conv.title) || conv.id, 28)}</div>
                <div className="conversation-date">{formatTime(conv.updated_at)}</div>
              </div>
              <button className="delete-conversation" onClick={(e) => deleteConversation(e, conv.id)} title="删除会话">
                <TrashIcon />
              </button>
            </div>
          ))}
          {conversations.length === 0 && <div style={{ fontSize: 13, color: 'var(--text-muted)', padding: 10 }}>还没有会话</div>}
        </div>
      </aside>

      {/* MAIN CHAT AREA */}
      <main className="chat-main">
        <header className="chat-header">PDF 智能助手</header>
        
        {surfaceError && (
          <div className="surface-error">
            <span>{surfaceError}</span>
          </div>
        )}

        <div className="chat-messages">
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>今天想怎么处理你的 PDF？</h2>
              <p>先用下方回形针上传文件，然后直接告诉我你的需求。例如：</p>
              <div className="preset-grid">
                {PROMPT_PRESETS.map((preset, idx) => (
                  <button key={idx} className="preset-card" onClick={() => setDraftMessage(preset)}>
                    {preset}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg, index) => (
              <React.Fragment key={msg.id}>
                {msg.kind === 'step' ? (
                  <div className="message-wrapper assistant step">
                    <div className="message-content">
                      <div className="avatar assistant">AI</div>
                      <div className="message-column">
                        <MessageCard message={msg} />
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className={`message-wrapper ${msg.kind}`}>
                    <div className="message-content">
                      {msg.kind !== 'user' && (
                        <div className={`avatar ${msg.kind}`}>
                          AI
                        </div>
                      )}
                      <div className="message-column">
                        {Array.isArray(msg.attachments) && msg.attachments.length > 0 && (
                          <div className="message-files-shell">
                            <div className="message-file-strip">
                              {msg.attachments.map((attachment, attachmentIndex) => (
                                <button
                                  type="button"
                                  key={`${msg.id}-attachment-${attachmentIndex}`}
                                  className="message-file-card compact"
                                  onClick={() => openPreview({
                                    name: attachment.name,
                                    mimeType: attachment.mimeType || attachment.type || "",
                                    previewUrl: attachment.previewUrl || withInlinePreview(attachment.path),
                                    downloadUrl: attachment.downloadUrl || attachment.path || (attachment.fileId ? `/api/files/${attachment.fileId}/download` : ""),
                                  })}
                                >
                                  <div className="message-file-badge">{fileExtension(attachment.name)}</div>
                                  <div className="message-file-meta">
                                    <strong>{truncateText(attachment.name, 28)}</strong>
                                    <span>{describeAttachment(attachment)}</span>
                                  </div>
                                  <span className="message-file-open">
                                    <EyeIcon />
                                    <span>预览</span>
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        <div className="message-body">
                          <div>{msg.content}</div>
                        </div>
                        <div className="message-actions">
                          <button className="message-action-btn" onClick={() => copyMessage(msg)}>
                            <CopyIcon />
                            <span>{copiedMessageId === msg.id ? "已复制" : "复制"}</span>
                          </button>
                          {msg.kind === "user" ? (
                            <button className="message-action-btn" onClick={() => beginEditMessage(msg)}>
                              <EditIcon />
                              <span>编辑</span>
                            </button>
                          ) : (
                            <button className="message-action-btn" onClick={() => retryFromMessage(index)}>
                              <RetryIcon />
                              <span>重试</span>
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
                {(artifactGroupsByEndIndex[String(index)] || []).map(renderArtifactGroup)}
              </React.Fragment>
            ))
          )}
          {isThinking && (
            <div className="message-wrapper assistant thinking">
              <div className="message-content">
                <div className="avatar assistant">AI</div>
                <div className="message-body">
                  <div className="thinking-bubble">
                    <span className="thinking-label">正在思考</span>
                    <span className="thinking-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}
          <div ref={messageEndRef} />
        </div>

        {/* INPUT AREA */}
        <div className="chat-input-wrapper">
          <div className="chat-input-container">
            
            {/* Selected files chips above input */}
            {(selectedFiles.length > 0 || selectedArtifacts.length > 0) && (
              <div className="attached-files-row">
                {selectedFiles.map(f => (
                  <div key={f.id} className="attached-file-chip">
                    <span
                      className="file-name-chip"
                      data-tooltip={f.orig_name}
                      title={f.orig_name}
                    >
                      <span className="file-name-text">{truncateText(f.orig_name, 20)}</span>
                    </span>
                    <button className="remove-file-btn" onClick={() => toggleFileSelection(f.id)}><CloseIcon /></button>
                  </div>
                ))}
                {selectedArtifacts.map((artifact) => (
                  <div
                    key={artifact.path}
                    className="attached-file-chip artifact-chip"
                  >
                    <span
                      className="file-name-chip"
                      data-tooltip={artifact.filename || artifact.path}
                      title={artifact.filename || artifact.path}
                    >
                      <span className="file-name-text">{truncateText(artifact.filename || artifact.path, 20)}</span>
                    </span>
                    <button className="remove-file-btn" onClick={() => toggleArtifactSelection(artifact.path)}><CloseIcon /></button>
                  </div>
                ))}
              </div>
            )}

	            <div className="input-box">
              <button 
                className="attach-trigger" 
                onClick={() => uploadInputRef.current && uploadInputRef.current.click()}
                title="上传 PDF 文件"
              >
                <AttachIcon />
              </button>
              <input
                ref={uploadInputRef}
                type="file"
                multiple
                accept=".pdf,image/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx"
                style={{ display: "none" }}
                onChange={(e) => {
                  uploadFiles(Array.from(e.target.files || []));
                  e.target.value = "";
                }}
              />
	              <textarea
                ref={textareaRef}
                className="chat-textarea"
                value={draftMessage}
                onChange={(e) => setDraftMessage(e.target.value)}
                placeholder="输入消息，告诉我你想怎么处理文件..."
                rows={1}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendChatMessage();
                  }
                }}
	              />
	              {isSending ? (
                <button
                  className="send-btn stop"
                  type="button"
                  onClick={stopStreamingResponse}
                  title="停止生成"
                >
                  <StopIcon />
                </button>
              ) : (
                <button 
                  className="send-btn" 
                  disabled={!draftMessage.trim() && selectedFiles.length === 0 && selectedArtifacts.length === 0}
                  onClick={() => sendChatMessage()}
                >
                  <SendIcon />
                </button>
              )}
            </div>
              <div className="input-hint">
                已选中的原始文件和结果文件都会保留在当前会话中，直到手动移除。处理结果如需继续使用，请在“结果文件”里点“用作输入”。
              </div>
              {editingMessageId && (
                <div className="edit-banner">
                  <span>正在编辑之前的用户消息，发送后会按新一轮消息提交。</span>
                  <button className="edit-banner-btn" onClick={cancelEditing}>取消编辑</button>
                </div>
              )}
	            <div className="disclaimer">
	              PDF 智能助手会使用本地工具处理文件，尽量保证安全与隐私。
	            </div>
          </div>
        </div>
      </main>
      {previewFile && (
        <div className="preview-modal-backdrop" onClick={() => setPreviewFile(null)}>
          <div className="preview-modal" onClick={(event) => event.stopPropagation()}>
            <div className="preview-modal-header">
              <div className="preview-modal-meta">
                <strong>{previewFile.name}</strong>
                <span>{previewFile.kind === 'pdf' ? 'PDF 预览' : previewFile.kind === 'image' ? '图片预览' : '文件预览'}</span>
              </div>
              <div className="preview-modal-actions">
                <a href={previewFile.downloadUrl} download className="preview-modal-btn">
                  <DownloadIcon />
                  <span>下载</span>
                </a>
                <button className="preview-modal-btn" onClick={() => setPreviewFile(null)}>
                  <CloseIcon />
                  <span>关闭</span>
                </button>
              </div>
            </div>
            <div className="preview-modal-body">
              {previewFile.kind === 'pdf' && (
                <iframe
                  title={previewFile.name}
                  src={previewFile.previewUrl}
                  className="preview-frame"
                />
              )}
              {previewFile.kind === 'image' && (
                <img src={previewFile.previewUrl} alt={previewFile.name} className="preview-image" />
              )}
              {previewFile.kind === 'other' && (
                <div className="preview-empty">
                  <div className="preview-empty-badge">{fileExtension(previewFile.name)}</div>
                  <strong>当前格式暂不支持直接预览</strong>
                  <span>可以先下载后在本地打开，或继续将它作为输入文件处理。</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
