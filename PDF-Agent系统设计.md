# PDF Agent（对标 Stirling-PDF）PRD + 系统设计书（单机/内网工具版）

> **目标**：实现与 Stirling-PDF 同等级别的 PDF 工具集合（功能矩阵可对齐），并以自然语言对话作为唯一主交互；旧的手动工具、独立工作流、执行管理 HTTP 入口不再保留为产品接口。
>
> **范围声明**：不考虑多租户、登录、RBAC、配额等；专注 **PDF 处理技术** 与 **工程落地**。
>
> **交付导向**：本文件既是 PRD，也是系统设计书，面向“能实施”的产品级方案：包含模块边界、工具插件化、参数规范、接口、数据模型、对话编排、部署、测试策略与里程碑。

---

## 目录
- [1. 产品需求文档（PRD）](#1-产品需求文档prd)
  - [1.1 产品概述](#11-产品概述)
  - [1.2 目标用户与典型场景](#12-目标用户与典型场景)
  - [1.3 产品目标与成功指标](#13-产品目标与成功指标)
  - [1.4 需求范围与分期](#14-需求范围与分期)
  - [1.5 功能清单（对标 Stirling-PDF 的全量能力域）](#15-功能清单对标-stirling-pdf-的全量能力域)
  - [1.6 通用交互与页面](#16-通用交互与页面)
  - [1.7 非功能需求](#17-非功能需求)
  - [1.8 验收标准](#18-验收标准)
- [2. 系统设计书（System Design）](#2-系统设计书system-design)
  - [2.1 架构概览](#21-架构概览)
  - [2.2 核心设计原则](#22-核心设计原则)
  - [2.3 关键模块设计](#23-关键模块设计)
  - [2.4 工具插件化规范（Manifest + Runtime）](#24-工具插件化规范manifest--runtime)
  - [2.5 对话执行流（Conversation Runtime）](#25-对话执行流conversation-runtime)
  - [2.6 文件存储与清理策略](#26-文件存储与清理策略)
  - [2.7 数据库设计（建议 PostgreSQL）](#27-数据库设计建议-postgresql)
  - [2.8 API 设计（REST + SSE）](#28-api-设计rest--sse)
  - [2.9 前端设计（Conversation-First）](#29-前端设计conversation-first)
  - [2.10 依赖引擎选型（实现全量 PDF 能力）](#210-依赖引擎选型实现全量-pdf-能力)
  - [2.11 安全与可靠性](#211-安全与可靠性)
  - [2.12 可观测性与运维](#212-可观测性与运维)
  - [2.13 部署方案（Docker Compose）](#213-部署方案docker-compose)
  - [2.14 测试策略（当前以 Smoke 为主）](#214-测试策略当前以-smoke-为主)
  - [2.15 收口规则（禁止继续膨胀）](#215-收口规则禁止继续膨胀)
  - [2.16 里程碑与工作量拆解](#216-里程碑与工作量拆解建议)
- [3. 附录](#3-附录)
  - [3.1 Page Range 语法规范](#31-page-range-语法规范)
  - [3.2 错误码建议](#32-错误码建议)
  - [3.3 输出命名规范](#33-输出命名规范)

---

# 1. 产品需求文档（PRD）

## 1.1 产品概述
**产品名称**：PDF Agent（对标 Stirling-PDF）  
**产品形态**：Chat-first Web 应用（自托管），以“上传文件 + 对话处理 PDF”为核心路径。  
**核心价值**：
- 覆盖 PDF 全量处理需求（对话入口驱动内部工具执行）
- 用户只需要“上传文件 + 用自然语言说明要做什么”
- 本地/内网运行，文件不出域
- 工具执行过程可观察、可追踪、可复现（便于排障与回放）

## 1.2 目标用户与典型场景
**目标用户**：
- 个人/NAS 自建用户
- 内网团队（行政/法务/财务/研发）共享工具，无需账号体系

**典型场景**：
- 扫描件 OCR → 压缩 → 加水印 → 批量输出
- 合同拆分指定页 → 旋转纠正 → 添加页码/页眉页脚
- 图片/Office 批量转 PDF
- 处理前清理元数据、脱敏涂黑、加密后对外发送
- PDF/A 归档转换

## 1.3 产品目标与成功指标
**产品目标**：
1. 功能矩阵与 Stirling-PDF 对齐（按菜单项对齐）
2. 工具输出稳定，不破坏文件可打开性
3. 批处理能力可用（目录级、任务队列级）

**成功指标（建议）**：
- 工具覆盖率：≥ 95%（最终 100%）
- 任务成功率：≥ 99%（排除输入损坏文件）
- OCR 平均处理吞吐：按硬件可测（比如 10 页/分钟基准）
- 失败可定位率：≥ 95%（错误明确、日志可读）

## 1.4 需求范围与分期
### 1.4.1 本期（产品级基础 + 全量工具框架）
- 对话主链路：上传文件、创建会话、发送消息、流式返回结果、下载产物
- 工具框架：后端插件注册 + LangChain tool adapter
- 文件系统：上传/下载/会话产物、清理策略
- 核心引擎集成：qpdf/pikepdf、poppler、ocrmypdf、ghostscript、libreoffice（可按需）

### 1.4.2 后续增强（不涉及权限多租户）
- 复杂可视化：页缩略图、拖拽重排、对比可视化
- 目标体积压缩策略（多轮自动）
- 更强 OCR 预处理（去噪、二值化、版面分析）

## 1.5 功能清单（对标 Stirling-PDF 的全量能力域）
> 每个工具都要支持：输入校验、参数校验、输出规范、日志与错误码。

### A. 页面与文档结构（Core PDF Ops）
- 合并（merge）
  - 顺序合并
  - 交错合并（interleave）
  - 插入合并（在指定位置插入另一个 PDF）
- 拆分（split）
  - 按页范围（1-3,5）
  - 每页一个
  - 按固定页数分块（每 N 页一个）
  - 按书签拆分（如可实现）
- 提取页 / 删除页（extract/delete pages）
- 旋转页（rotate，90/180/270，支持 page range）
- 重排页（reorder，输入页序列）
- 插入空白页（add blank pages）
- 裁剪（crop，按边距或坐标）
- 调整页面尺寸/缩放（resize/scale）
- 页眉页脚/页码（header/footer + page numbers）
- N-up（多页拼一页）
- 小册子（booklet / imposition）
- 修复/清理（repair / optimize structure）

### B. 转换（Convert）
- PDF → 图片（png/jpg/webp 可选）
- 图片 → PDF（多图合成、排序、页面尺寸）
- PDF → 文本（pdftotext）
- PDF → HTML（可选，保真有限）
- PDF → DOCX / PPTX（可选，保真有限）
- Office → PDF（libreoffice headless）
- PDF/A：转换与校验（可选引擎）

### C. OCR 与增强（OCR & Enhancement）
- OCR（输出可搜索 PDF）
- OCR 语言选择、多语言组合
- 强制 OCR / 跳过已有文本
- deskew / rotate-pages
- 选择页 OCR
- 输出文本导出（txt/json）

### D. 压缩与优化（Optimize）
- 压缩等级（low/medium/high）
- 目标体积（可选：多轮策略）
- 图像重采样、颜色降级
- 线性化（fast web view）
- 清理未使用对象、去元数据

### E. 安全（Security）
- 加密（设置用户密码/所有者密码）
- 解密（提供密码）
- 权限限制（打印/复制/修改）
- 数字签名（P12）
- 验签
- Redact（涂黑脱敏：按文本/区域）
- 添加签章外观（stamp/signature appearance）

### F. 水印与标注（Watermark & Markup）
- 文本水印
- 图片水印
- 盖章（stamp）
- （可选）注释叠加/导出（难度较高，可后置）

### G. 表单与元数据（Forms & Metadata）
- 查看/编辑元数据
- 移除元数据
- 提取表单字段（导出 JSON）
- 填写表单字段（按字段名写值）
- 表单扁平化（flatten）
- 书签导入/导出/编辑（可选）

### H. 内容提取（Extract）
- 提取文本（按页）
- 提取图片
- 提取附件（embedded files，若引擎支持）
- 拆成单页 PDF（与 split 类似但命名规则不同）

### I. 比对与分析（Compare & Analyze）
- 文本差异（基于文本提取）
- 像素差异（渲染后比对）
- 统计：页数/尺寸/是否扫描件/是否加密/是否有文本层

### J. 批处理与多步处理（Batch & Multi-step）
- 对多个文件执行同一工具（batch）
- 多步骤串联处理，由对话驱动内部工具链完成
- 并发控制：重任务与轻任务分队列

## 1.6 通用交互与页面
- 主页面采用 Conversation-First 交互
  - 左侧：会话列表
  - 主区：聊天区，用户直接描述要对 PDF 做什么
  - 下方：上传按钮、已选文件、消息输入框
  - 结果区：当前会话产物列表与下载入口
- 不再提供独立“工具台”“工作流页”“任务中心”作为产品主入口
- 多步处理直接在当前对话里完成，不要求用户先理解底层工具或执行计划

## 1.7 非功能需求
- 稳定性：任务隔离；单任务失败不影响整体
- 性能：支持并发（可配置），OCR/转换为主要瓶颈
- 资源限制：
  - 最大上传大小（默认 200MB，可配）
  - 最大页数（默认 2000 页，可配）
  - 外部命令超时（默认 30 分钟，可配）
- 兼容性：优先 Docker 部署；macOS/Linux 可开发运行
- 可维护性：工具新增无需修改前端（manifest 驱动）
- 数据生命周期：保留天数、磁盘上限、自动清理

## 1.8 验收标准
- 输出文件可打开、结构正确、页数符合预期
- OCR 输出可搜索（抽样验证）
- 压缩输出体积降低且可打开
- 失败任务可定位到 tool/step，含错误码与日志摘要
- 批处理可运行，长耗时处理不会把 API 服务拖死

---

# 2. 系统设计书（System Design）

## 2.1 架构概览
**方案A**：React + FastAPI + LangChain + LangGraph + PostgreSQL + Local Storage

组件：
- **Frontend**：会话列表、输入文件选择、对话区、结果下载
- **API Server**：上传、会话接口、Agent 对话流、产物下载
- **LangChain Planner**：将自然语言请求转成结构化 plan
- **LangGraph Runtime**：负责 Agent 对话、多步工具调用与状态推进
- **Tool Adapter / Command Runner**：把 LangChain tool 调用落到受控工具执行链与外部命令封装
- **PostgreSQL**：文件元数据、LangGraph checkpoint、审计
- **Storage**：本地磁盘（uploads/conversations/tmp）

## 2.2 核心设计原则
1. **Conversation-First**：用户面对的是会话，不是工具台、工作流面板或任务中心
2. **执行确定性**：LLM 通过 LangChain/LangGraph 驱动工具调用，实际执行仍由受控工具层完成
3. **多引擎并存**：用正确的引擎覆盖正确能力（qpdf/poppler/ocr/gs/libreoffice）
4. **外部命令安全**：不拼 shell；限制超时；固定工作目录
5. **统一编排**：对话、状态、工具调用统一复用 LangChain/LangGraph 语义，不维护第二套自实现规划器
6. **可追溯**：会话消息、工具执行事件、输出产物应可定位与回放

### 2.2.1 当前阶段的最终目标架构
为避免继续架构膨胀，当前阶段的目标架构固定为四层：

- **API / Orchestration**
  - 负责上传、对话、会话结果查询与下载
  - 不承载 PDF 处理实现细节
- **Conversation Runtime**
  - 由 LangGraph 管理会话状态、多步调用、流式事件输出
  - 是唯一允许编排多 step 执行的地方
- **Tool Plugins**
  - 每个工具只做输入校验、参数归一化、执行、产物输出
  - 不自行实现对话状态、事件流、数据库写入
- **Storage**
  - 只负责文件和最小必要元数据持久化
  - 不扩展出新的业务编排抽象

当前阶段不再引入新的重型任务模型、事件总线、独立编排 DSL 或额外微服务。

## 2.3 关键模块设计
### 2.3.1 Tool Registry（工具注册中心）
- 启动时加载所有工具插件
- 提供运行时查找 tool：`registry.get(tool_name)`

### 2.3.2 Conversation Service（会话服务）
- 创建/读取/删除会话
- 读取 LangGraph 持久化消息
- 关联上传文件与当前会话目录
- 聚合当前会话产物列表

### 2.3.3 LangGraph Runtime（编排器）
- 接收用户消息与附加文件
- 通过 LangChain `StructuredTool` adapter 统一执行 step
- 在同一会话内逐步推进：
  - 理解用户意图
  - 调用合适工具
  - 产出流式 token / tool_start / tool_progress / tool_end 事件
- 失败处理：
  - 返回结构化错误事件
  - 保留可排查的日志与产物信息

### 2.3.4 模块边界约束
- `api/*`：只做请求编解码、调用 service、返回响应；不要沉积工具执行细节
- `agent/*`：只负责 LangChain/LangGraph 的聊天与 tool adapter；不要长出第二套并行编排体系
- `tools/_builtins/*`：只关心工具本身；不要直接写会话状态、不要直接操作 SSE、不要直接写数据库
- `external_commands.py`：是唯一的外部命令执行入口；不要再在活跃运行路径里散落新的 `subprocess.run(...)`
- `db/models.py`：当前只保留 `FileRecord` 作为核心持久化对象；不为“也许以后有用”预埋复杂实体

## 2.4 工具插件化规范（Manifest + Runtime）
### 2.4.1 Manifest 规范（建议）
字段：
- `name`：唯一 key
- `category`：分类
- `inputs`：min/max、类型（pdf/image/office/mixed）
- `outputs`：类型（pdf/zip/images/text/json）
- `params[]`：表单字段定义（type、默认值、范围、必填、提示）
- `engine`：实现引擎（qpdf/poppler/ocrmypdf/gs/libreoffice/pypdf…）
- `async_hint`：是否建议走异步（OCR/convert/compare）

### 2.4.2 Runtime 规范
- `validate(params)-> normalized_params`
- `run(inputs, params, workdir, reporter)-> ToolResult`

**ToolResult**：
- `output_files`
- `meta`（页数、大小、耗时）
- `log`（摘要）

## 2.5 对话执行流（Conversation Runtime）
### 2.5.0 Agent 规划与执行
- `Agent chat`：由 LangGraph StateGraph 驱动，对话中按需调用 LangChain tools
- 不再保留 `Plan preview / confirm` 或独立工作流 HTTP 入口
- 多步 PDF 处理直接在对话中完成，并把产物写入当前会话目录

### 2.5.1 Plan Schema（建议）
```json
{
  "version": "1.0",
  "steps": [
    {
      "tool": "ocr",
      "inputs": [{"type":"file","file_id":"..."}],
      "params": {"lang":"chi_sim+eng","deskew":true}
    },
    {
      "tool": "compress",
      "inputs": [{"type":"prev"}],
      "params": {"level":"medium"}
    }
  ],
  "output": {"format":"pdf"}
}
```

### 2.5.2 进度模型

- step 级：`(current_step/total_steps)*100`
- tool 内部：reporter 可上报粗粒度进度（OCR/convert）

## 2.6 文件存储与清理策略

目录：

- `data/uploads/{file_id}/source.pdf`
- `data/conversations/{conversation_id}/step_{n}/...`（当前实现中的会话产物目录）

说明：

- 当前实现层仅在 LangGraph 边界继续使用 `thread_id` 作为兼容命名
- 这是内部实现细节；产品与 API 表层统一使用 `conversation`

清理：

- 定时清理过期会话目录（保留 N 天）
- 清理孤儿 uploads（无活跃会话引用）
- 限制总容量（超限按 LRU 清理，或拒绝新任务）

## 2.7 数据库设计（建议 PostgreSQL）

### 2.7.1 表结构（建议）

**files**

- id (uuid)
- orig_name
- mime_type
- size_bytes
- sha256
- page_count (nullable)
- storage_path
- created_at

当前阶段不单独维护独立的“执行记录表”。

- 会话消息与状态由 LangGraph checkpointer 持久化
- 文件元数据由 `files` 表维护
- 若后续产品形态真的变成任务平台，再重新引入显式执行记录模型

索引：

- files(sha256)（可选去重）

## 2.8 API 设计（REST + SSE）

- `POST /api/files`：上传输入文件
- `GET /api/files`：文件列表
- `GET /api/files/{id}/download`：下载原始上传文件
- `DELETE /api/files/{id}`：删除上传文件
- `POST /api/conversations`：创建空会话
- `GET /api/conversations`：会话列表
- `GET /api/conversations/{conversation_id}`：会话详情与消息历史
- `DELETE /api/conversations/{conversation_id}`：删除会话
- `GET /api/conversations/{conversation_id}/artifacts`：当前会话产物列表
- `GET /api/conversations/{conversation_id}/artifacts/{artifact_path}`：下载当前会话产物
- `POST /api/conversations/{conversation_id}/messages`：发送消息并以 SSE 流式返回 token / tool_start / tool_progress / tool_end / done

## 2.9 前端设计（Conversation-First）

- 左侧：会话列表，仅呈现对话历史
- 主区：聊天区，用户通过自然语言描述 PDF 目标结果
- 下方：输入文件选择 + 当前输入文件 chips + 发送入口
- 结果区：当前会话结果下载
- 默认 UI 不直接暴露手动工具页、独立工作流页、执行管理入口
- 服务 HTTP 表层只保留“上传 + 对话 + 结果下载”主链路
- 工具与执行能力保留在内部运行时，不再提供独立手动 HTTP 入口
- 少量专用页面（后期，如确有必要）：
  - 页重排拖拽
  - PDF 对比可视化（像素差异）

## 2.10 依赖引擎选型（实现全量 PDF 能力）

为覆盖全量 PDF 能力，建议引擎组合：

- **pikepdf/qpdf**：合并/拆分/修复/线性化/加解密/对象级优化
- **poppler**：渲染、导出图片、导出文本
- **ocrmypdf + tesseract**：OCR（可搜索）
- **ghostscript**：压缩/重采样
- **libreoffice**：Office 转 PDF
- 可选：**mupdf/pdfium**：更强渲染与像素级对比

## 2.11 安全与可靠性

- 上传校验：扩展名 + magic header + MIME
- 外部命令执行：
  - 统一通过共享命令执行封装运行外部命令
  - 固定 workdir，禁止用户输入路径
  - 输出路径白名单
- 超时与取消：长任务可 kill 子进程
- 失败隔离：step 失败只影响当前会话内的本次处理
- 资源限制：并发、最大文件大小、最大页数、磁盘水位

## 2.12 可观测性与运维

- 结构化日志：包含 request_id、conversation_id、step/tool、status、error_code
- 指标：
  - 对话请求数量、失败率、平均耗时
  - 工具调用数量、失败率、平均耗时
- 健康检查：
  - `/healthz`（API）
  - agent 初始化状态

## 2.13 部署方案（Docker Compose）

服务：

- postgres
- api
- frontend（可选，也可静态部署）

镜像内置依赖：

- qpdf
- poppler-utils
- ghostscript
- ocrmypdf + tesseract + 语言包（chi_sim、eng）
- libreoffice（体积大，可做可选镜像）

## 2.14 测试策略（当前以 Smoke 为主）

- 当前开发阶段保留少量 smoke tests，覆盖核心 API 面、对话主链路、代表性工具与前端入口
- 对重度工具能力不做大规模细粒度回归，避免测试维护成本反向拖慢开发
- 关键验证方式：
  - 应用能启动，核心路由存在
  - 上传 / 会话 / 结果下载主链路可创建并推进任务
  - 代表性工具可执行并产出合法结果
- 后续若进入稳定期，再补充分层集成测试与更完整的 E2E

## 2.15 收口规则（禁止继续膨胀）

- 不新增 `Job/Step/Artifact` 一类重型持久化模型，除非产品形态明确变为多租户任务平台
- 不再实现第二套 planner / orchestrator；对话主链路统一复用 LangChain/LangGraph
- 不在工具内部重复实现超时、进度、数据库写入，统一走共享工具执行链路
- 不为局部需求拆微服务；当前阶段坚持单体 API 的简单部署形态
- 测试保持 smoke 为主，只补关键回归，不恢复大而全的实现耦合测试
- 新功能进入前先判断应落在哪一层；如果跨层泄漏，先改边界再写功能

## 2.16 里程碑与工作量拆解（建议）

**阶段0：框架可运行**

- 文件上传/下载
- 对话式 PDF 处理主链路（最小）
- LangChain/LangGraph 编排主链路

**阶段1：高频核心工具**

- merge/split/rotate/extract/delete/reorder
- pdf→images、images→pdf
- compress（gs）
- ocr（ocrmypdf）

**阶段2：安全/元数据/表单**

- encrypt/decrypt/permissions
- metadata read/write/strip
- forms fill/extract/flatten
- watermark/stamp

**阶段3：高级排版与对比**

- N-up、booklet、page size normalize
- compare（text + pixel）
- batch 多步处理体验完善

------

# 3. 附录

## 3.1 Page Range 语法规范

- `all`
- `1-3,5,7-9`
- 可选：`odd`/`even`
- 可选：`last`、`last-3-last`

解析规则：

- 1-based 输入，内部转 0-based
- 越界报错：`INVALID_PAGE_RANGE`

## 3.2 错误码建议

- `INVALID_INPUT_FILE`
- `UNSUPPORTED_FORMAT`
- `INVALID_PARAMS`
- `ENGINE_NOT_INSTALLED`
- `ENGINE_EXEC_TIMEOUT`
- `ENGINE_EXEC_FAILED`
- `OUTPUT_GENERATION_FAILED`
- `CONVERSATION_RUN_CANCELED`

## 3.3 输出命名规范

- 单输出：`{conversation_id}_{tool_or_pipeline}.pdf`
- 多输出：zip 内部 `{origName}_{tool}_{index}.pdf`
- 图片输出：`page_{pageNo}.png`
