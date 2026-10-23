# Simulink 文档驱动建模助手

前后端分离的文档驱动建模方案生成器。系统将 DOCX、PDF 和 Markdown 需求文档转换为可追溯的 Simulink 建模方案，输出模块清单、参数表、连线表、结构图和 Markdown/JSON/CSV 方案包。

首版不生成 `.slx`，不启动 MATLAB，不运行仿真。所有自动抽取内容都保留证据和待确认项。

## 技术栈

- 前端：React + TypeScript + Vite
- 后端：FastAPI + SQLAlchemy + SQLite（可切换 PostgreSQL）
- 文档解析：MinerU 适配器；未安装 MinerU 时使用本地 Markdown/DOCX/PDF 文本回退
- 需求理解：OpenAI 兼容 Chat Completions 接口，可关闭并使用本地规则回退
- 异步任务：默认 FastAPI BackgroundTasks；生产环境可选 Celery + Redis

## 快速启动

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

后端地址：<http://127.0.0.1:8000>，接口文档：<http://127.0.0.1:8000/docs>。

### 前端

```powershell
cd frontend
npm install
npm run dev
```

前端地址：<http://127.0.0.1:5173>。Vite 会将 `/api` 代理到后端 8000 端口。

## MinerU 配置

默认会尝试调用 `mineru-kit`，失败后回退到本地解析；如果必须使用 MinerU，将 `.env` 中的 `MINERU_REQUIRED=true`。请先按 MinerU 官方安装方式准备模型和命令，再通过 `MINERU_COMMAND`、`MINERU_PDF_TIER` 配置解析档位。

DOCX、PDF 解析会把解析器版本、警告、来源块和置信度保存到方案数据中。Markdown 由应用内解析器处理。

## LLM 配置

项目默认使用 MiniMax OpenAI 兼容接口，默认模型为 `MiniMax-M3.1-Flash`。本地密钥写入 `backend/.env`，不要提交该文件。前端侧栏的“模型服务”区域可点击“从上游获取”刷新模型列表并切换模型；模型选择保存在本地 `storage/llm_model.txt`。

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_API_KEY=your-key
LLM_MODEL=MiniMax-M3.1-Flash
```

上游模型列表由后端通过 `GET {LLM_BASE_URL}/models` 获取。选择模型使用 `PUT /api/v1/llm/model`，请求体为 `{"model":"模型ID"}`；后端会校验该 ID 存在于上游列表中。

LLM 负责从论文证据中生成候选数学模型和仿真方案，不直接生成最终图形；服务端会验证 JSON Schema、证据 ID、参数状态和连接关系。未配置 LLM 时，系统仅使用通用证据抽取回退，不套用固定领域或电机参数模板，并明确标注需要人工复核。

## 生产任务队列

默认 `TASK_BACKEND=inline` 适合本地 MVP。启用 Celery 时：

```powershell
pip install -r requirements-worker.txt
$env:TASK_BACKEND = "celery"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
celery -A app.worker.celery_app worker --loglevel=INFO
```

API 进程仍需单独启动。任务状态保存在数据库，队列消息可重复投递。

## 测试与质量检查

```powershell
cd backend
pytest
python -m compileall -q app

cd ..\frontend
npm run build
```

代码先在 `codex/mvp-foundation` 分支开发，合并前应通过测试、构建和人工代码评审。当前 MVP 未接入真实 MinerU 模型或远程 LLM，相关质量需在真实样本文档集上进行 PoC 评测。
