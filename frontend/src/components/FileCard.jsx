import React from 'react';
import { API_BASE_URL } from '../services/config';
import { truncateText, formatBytes, fileExtension } from '../utils';

const resolveAssetUrl = (value) => {
  if (!value) {
    return "";
  }
  if (/^https?:\/\//i.test(value)) {
    return value;
  }
  return `${API_BASE_URL}${value}`;
};

export const FileCard = ({ file, selected, onToggle, onDelete }) => {
  return (
    <div className={`file-card${selected ? " selected" : ""}`}>
      <button className="file-main" onClick={onToggle}>
        {file.thumbnail_url ? (
          <img className="file-thumb" src={resolveAssetUrl(file.thumbnail_url)} alt={file.orig_name} />
        ) : (
          <div className="file-thumb fallback">{fileExtension(file.orig_name)}</div>
        )}
        <div className="file-copy">
          <strong>{truncateText(file.orig_name, 28)}</strong>
          <span>{`${formatBytes(file.size_bytes)}${file.page_count ? ` · ${file.page_count} 页` : ""}`}</span>
        </div>
      </button>
      <div className="file-actions">
        <a href={resolveAssetUrl(file.download_url)} className="file-link" download>
          原件
        </a>
        <button
          className="file-delete"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title="删除文件"
        >
          删除
        </button>
      </div>
    </div>
  );
};
