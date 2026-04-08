import React from 'react';
import { API_BASE_URL } from '../services/config';

const resolveAssetUrl = (value) => {
  if (!value) {
    return "";
  }
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  return `${API_BASE_URL}${value}`;
};

export const MessageCard = ({ message }) => {
  if (message.kind === "step") {
    return (
      <div className={`message-card step${message.status === "RUNNING" ? " running" : ""}`}>
        <div className="message-head">
          <span className="message-role">处理中</span>
          <strong>{message.label || "处理中"}</strong>
        </div>
        {message.progressLabel && <p className="message-meta">{message.progressLabel}</p>}
        {Number.isFinite(message.progress) && message.status === "RUNNING" && (
          <p className="message-meta">{`${message.progress}%`}</p>
        )}
        {message.elapsedSeconds != null && <p className="message-meta">{`${message.elapsedSeconds}s`}</p>}
        {message.warning && <div className="message-warning">{message.warning}</div>}
        {message.content && <div className="message-body">{message.content}</div>}
        {message.downloads && message.downloads.length > 0 && (
          <div className="inline-downloads">
            {message.downloads.map((file) => (
              <a key={file} href={resolveAssetUrl(file)} download className="inline-download-btn">
                {file.split("/").pop() || "DOWNLOAD"}
              </a>
            ))}
          </div>
        )}
      </div>
    );
  }

  const className =
    message.kind === "user"
      ? "message-card user"
      : message.kind === "assistant"
      ? "message-card assistant"
      : "message-card system";
  const roleLabel = message.kind === "user" ? "我" : message.kind === "assistant" ? "AI" : "系统";

  return (
    <div className={className}>
      <div className="message-head">
        <span className="message-role">{roleLabel}</span>
      </div>
      <div className="message-body">{message.content}</div>
    </div>
  );
};
