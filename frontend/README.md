# Frontend

这是 `pdf-agent` 的独立前端工程，负责单用户、conversation-first 的 Web UI。

当前产品表层固定为三件事：

- 上传文件
- 发起/切换会话
- 在对话里直接处理 PDF，并下载当前会话产物

## 开发

```bash
npm install
npm run dev
```

默认通过 `frontend/src/services/config.js` 中的 API 基地址访问后端。
如果后端启用了 API key 鉴权，可通过 `VITE_API_KEY` 注入：

```bash
VITE_API_KEY=your-api-key npm run dev
```

也可以在浏览器控制台设置：

```js
localStorage.setItem('pdf_agent_api_key', 'your-api-key')
```

## 构建

```bash
npm run build
```

生产部署时，将 `dist/` 发布到 nginx 的 `/var/www/pdf-agent`，并让 nginx 反代 `/api/`、`/healthz` 和 `/metrics` 到后端服务。

## 约束

- 不在前端重新暴露旧的平台化操作概念
- 前端只调用 `files / conversations / messages / artifacts` 这组表层 API
- LangChain / LangGraph 只存在于后端实现层，前端不感知
