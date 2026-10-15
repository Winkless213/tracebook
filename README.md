# Tracebook

把项目代码、文档和开发会话整理成带出处的知识库，供 AI 编码助手和人查阅；在此基础上提供一个技术客服 Agent，用项目知识回答问题、排查故障。

单人项目：FastAPI + SQLite 后端，React 前端，LangGraph 编排客服流程。

## 功能

**项目知识库**
- 从本地 Git 或 GitHub 导入已提交的代码和文档，按项目、分支、提交建立快照，更新原子生效，旧回答固定在原快照。
- 检索：jieba 分词 + BM25 + 符号匹配，最多 6 个片段、24,000 字符证据预算；问到"代码和文档是否一致"时沿文档里出现的符号补查实现。
- 回答的每条结论必须引用真实片段并逐字匹配原文，校验不过不展示；证据不足时明确说不足。
- 开发会话（Codex / Claude Code 的 JSONL）经脱敏后归档，可提炼为候选经验。

**技术客服 Agent**（`devrag/support/`）
- 两种模式：日常答疑和工单排查。
- 排查流程用 LangGraph `StateGraph` 实现：缺日志或版本时 `interrupt` 暂停追问，用户补充后恢复；`SqliteSaver` 持久化检查点，服务重启后可继续。
- 模型通过 function calling 自主决定是否联网搜索：第一轮 `tool_choice=auto`，执行搜索后第二轮 `tool_choice=none` 综合作答。
- 用户日志发给模型之前先脱敏（私钥、Authorization、Cookie、令牌、JWT、邮箱等）。
- 主备模型和有限重试：答疑轻量（短超时、少重试），排查用更高推理档位；每次尝试的模型、耗时、用量都有记录。

## 本地运行

需要 Python 3.13+、Node 24+。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build

$env:APP_PASSWORD = '<至少 16 位的访问密码>'
$env:SESSION_SECRET = '<至少 32 位的随机字符串>'
$env:LLM_BASE_URL = 'https://<模型服务>/v1'
$env:LLM_API_KEY = '<只在本机配置>'
$env:LLM_PRIMARY_MODEL = '<主模型>'
$env:LLM_FALLBACK_MODEL = '<备用模型>'
.venv/Scripts/python -m uvicorn devrag.app:create_app --factory --host 127.0.0.1 --port 8021 --workers 1
```

打开 `http://127.0.0.1:8021`。不配置模型也能导入、浏览和检索。全部配置项见 `.env.example`；客服模块读取 `SUPPORT_*` 变量，密钥只从环境变量读取。

联网搜索可选用自建 SearXNG：`searxng/compose.yaml`，并设置 `SUPPORT_SEARXNG_URL`。

## 导入自己的项目

```powershell
python scripts/inspect_repo.py C:/path/to/repo --output output/inventory.md
python scripts/export_repo.py C:/path/to/repo --project my-project --include README.md --include src --include docs --output output/my-project.json
```

在页面"导入项目快照"上传生成的 JSON。只读取指定提交，未提交和未跟踪文件不会进入；单份快照上限 200 个文本文件、2MB。需要持续同步时用 `scripts/sync_repositories.py`，配置示例在 `examples/`。

## 测试与评测

```powershell
python -m pytest -q
python -m ruff check devrag tests scripts
npm --prefix frontend test
python scripts/evaluate.py output/my-project.json --output output/retrieval.json
python scripts/evaluate_boundaries.py --output output/edges.json
```

`evaluation/questions.json` 是针对 Tracebook 自身代码的示例题，用来演示题集格式。评测你自己的项目时需要按项目另写题集。`--answers` 和边界评测会调用已配置的模型。

## 局限

- 检索没有向量召回和重排，也没有调用图。
- 逐字引用校验只能证明引用存在，不能证明结论正确。
- 单用户、单 worker，不支持多人权限。
- 客服模块目前只接受代码里指定的模型网关和两种已验证的模型（见 `devrag/support/model.py`）。

## 部署

`Dockerfile` 和 `compose.yaml` 默认只监听 `127.0.0.1:8083`，数据挂载到独立卷。发布、备份和恢复见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。
