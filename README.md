# 织火 xiaohuo · AI 小说创作平台

把长篇小说当成一个结构化工程:**设定库 + AI 生成 + 章节审核 + 阅读器 + 多平台发布**(发布中心在 M3)。

完整设计方案见 [小说创作平台设计方案.md](./小说创作平台设计方案.md)。

## 当前实现(M1-M4 全量功能)

| 模块 | 说明 |
|---|---|
| 书架与标签 | 多书并行、题材/风格/状态/自定义标签、字数目标进度、**发布平台标记与各平台进度**、协作书标记 |
| 设定库 | 世界观条目、人物卡、**关系图谱(SVG 可视化)**、**规则页(生成时强制注入)**、**伏笔台账(埋设/回收/超期)** |
| 大纲 · 章节 | 卷-章两级、章纲/伏笔任务、**AI 生成章纲并一键套用** |
| AI 生成引擎 | 多供应商自配、按用途路由(大纲/正文/润色/复盘/摘要/Embedding)、流式输出、主模型失败自动切备用 |
| 上下文组装 | 规则页 + 人物卡 + 前情摘要(滚动更新)+ 上章结尾 + 章纲 + 伏笔提醒 + **知识库 RAG 片段** + **挂载技能** |
| 章节状态机 | AI 初稿 → 草稿 → 待审核 → 审核通过 / 驳回;版本历史与恢复 |
| 审核台 | AI 稿 vs 当前稿 **词级 diff**、驳回必填意见、审核记录审计 |
| 阅读器 | 仅聚合"审核通过"章节、纸张/护眼/夜间主题、字号调节、阅读进度、**TTS 朗读** |
| 个人中心 | 资料/改密、**AI 供应商配置(测试连通/同步模型)**、用途路由、**Token 用量看板(每日条形图)** |
| **首页自由对话** | DeepSeek 风格首页:点 Logo 直接开聊,多会话管理(自动起标题/切换/删除)、流式输出;模型走「自由对话」用途路由,未配置时自动回落到大纲/正文模型 |
| **知识库(M2)** | 上传 TXT/MD/PDF/DOCX/EPUB → 解析分块 → Embedding 向量化 → 检索测试页 → 挂载到书,生成时自动检索注入(带相关度与来源) |
| **技能工坊(M2)** | 技能包 CRUD、6 个预置技能一键导入、**SKILL.md 导入**、温度覆盖、按书按阶段挂载 |
| **发布中心(M3)** | 建/编书选平台、章节×平台发布任务、定时发布(到期自动转人工待办)、**复制文本 / 打开平台后台(Playwright 持久登录)**、标记已发布/失败/重试、全平台完成自动置"已发布"、平台账号状态管理 |
| **章节复盘(M3)** | AI 复盘报告:钩子强度/伏笔/节奏/人物一致性/矛盾检测,按章留档 |
| **团队协作(M4)** | 按书邀请协作者(editor/reviewer 角色),协作者书架可见共享书、可参与写作与审核;删书与邀请仅限所有者 |
| **导出(M4)** | 整书导出 TXT / EPUB(默认审核通过章节) |

> 说明:发布自动化为 **L1 半自动**(自动打开平台后台+复制文本,人工点发布),番茄等平台的 L2 全自动涉及账号风控,需自担风险后启用;知识库向量采用可移植 JSON 存储,数据量大时可平滑迁移 pgvector;MCP 插件为预留架构。

## 快速开始(本地开发,无需 Docker)

### 1. 启动后端(默认用 SQLite,零配置)

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8011
```

打开 http://localhost:3000,或本机局域网地址如 http://192.168.12.113:3000(API 会自动跟随当前主机名连 `:8011`)。

生产环境建议 PostgreSQL:复制 `.env.example` 为 `.env` 并修改 `DATABASE_URL` 与 `SECRET_KEY`。

### 2. 启动创作台前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:3000,注册账号即可。

### 3. 启动独立阅读器(端口 3001)

```bash
cd reader
npm install
npm run dev
```

阅读器与创作台分离:创作台点「阅读器」会打开 `http://主机:3001/{小说ID}`。旧链接 `/reader/...` 会自动跳转。

### 4. 配置 AI(必须)

进 **个人中心 → AI 供应商配置**:
1. 添加供应商(任意 OpenAI 兼容接口:GitCC / DeepSeek / 通义 / Kimi / Ollama…),填 `base_url` 和 `api_key`;
2. 点「测试」验证连通,点「同步模型」拉取模型列表;
3. 在「用途路由」里为 大纲 / 正文 / 润色 / 复盘 / 摘要 各选一个模型(可设备用模型);
4. 正文生成默认走"章节正文"路由。

## Docker Compose 一键部署

```bash
# 先准备环境变量
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
export POSTGRES_PASSWORD=$(python -c "import secrets;print(secrets.token_hex(16))")

docker compose up -d --build
```

访问 http://localhost:3000。生产部署务必:强随机 `SECRET_KEY`、HTTPS、修改数据库默认密码。

## 验证

```bash
cd backend
python scripts/smoke_test.py
```

覆盖 注册登录 → 建书(平台标记) → 设定库/伏笔 → 卷章 → 提交/驳回/通过审核 → 发布任务(定时/到期/标记成功/重试) → 阅读器 → 知识库(上传/分块/挂载) → 技能(预置/SKILL.md 导入/挂载) → 导出 TXT/EPUB → 团队协作权限 全流程(83 项断言)。

## 项目结构

```
xiaohuo/
├── backend/               # FastAPI + SQLAlchemy(同步)
│   ├── app/
│   │   ├── ai/            # 网关(多供应商路由+流式+Embedding)+ 上下文组装 + 检索 + 预置技能
│   │   ├── routers/       # auth/profile/providers/novels/settings/chapters/review/reader/
│   │   │                  # publish(发布中心)/kb(知识库)/skills/insights(伏笔+复盘)/export/collab/usage
│   │   ├── models.py      # 全部数据模型
│   │   └── main.py
│   └── scripts/smoke_test.py
├── frontend/              # Next.js 14(App Router)+ Tailwind + Tiptap
│   └── src/
│       ├── app/           # 书架 / 工作台 / 设定库(含伏笔)/ 大纲 / 编辑器(含复盘)/
│       │                  # 审核台 / 发布中心 / 知识库 / 技能工坊 / 阅读器(TTS)/ 个人中心
│       ├── components/    # ui / TopBar / DiffView
│       └── lib/           # api(SSE 流式)/ auth / types
└── docker-compose.yml
```

## 里程碑与后续

- **M1-M4 已全部实现**(见上表)
- 待办 / 可选增强:pgvector 索引迁移、番茄 L2 全自动(需自担风控风险)、起点/七猫等适配器选择器实测、MCP 插件、EPUB 封面图
