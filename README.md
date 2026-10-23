# Tracebook

把项目代码、文档和开发会话整理成带出处的知识库，供 AI 编码助手和人查阅；在此基础上提供技术客服 Agent，用项目知识回答问题和排查故障。单人项目：FastAPI + SQLite 后端，React 前端，LangGraph 编排客服流程。

![检索与证据面板（导入 Tracebook 自身代码后的检索结果）](docs/images/search.png)

## 功能

**项目知识库**
- 从本地 Git 或 GitHub 导入已提交的代码和文档，按项目、分支、提交建立快照，更新原子生效，旧回答固定在原快照；检索用 jieba 分词 + BM25 + 符号匹配，最多 6 个片段、24,000 字符证据预算，问到"代码和文档是否一致"时沿文档里出现的符号补查实现。
- 每条结论必须引用真实片段并逐字匹配原文，校验不过不展示，证据不足时明确说不足；开发会话（Codex / Claude Code 的 JSONL）脱敏归档后可提炼候选经验。

**技术客服 Agent**（`devrag/support/`）
- 两种模式：日常答疑和工单排查。排查用 LangGraph `StateGraph`：缺日志或版本时 `interrupt` 暂停追问，补充后恢复；`SqliteSaver` 持久化检查点，服务重启后可继续。
- 模型通过 function calling 自主决定是否联网搜索：第一轮 `tool_choice=auto`，搜索后第二轮 `tool_choice=none` 综合作答；日志先脱敏（私钥、Authorization、Cookie、令牌、JWT、邮箱等）再发给模型，主备模型有限重试，答疑轻量、排查用更高推理档位，每次尝试的模型、耗时和用量都有记录。

## 环境要求

Python 3.13+、Node 24+。用 Docker 部署时宿主机不需要这两个运行时。

## 快速开始

1. 创建虚拟环境、安装 Python 依赖并构建前端（页面由后端从 `frontend/dist` 提供，未构建时首页返回 503）：
   ```powershell
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt
   npm --prefix frontend ci
   npm --prefix frontend run build
   ```
2. 设置必需环境变量：`$env:APP_PASSWORD = '<至少 16 位的访问密码>'`、`$env:SESSION_SECRET = '<至少 32 位的随机字符串>'`。长度不足会启动报错；模型变量可以不填，不配置也能导入、浏览和检索。
3. 启动服务，打开 `http://127.0.0.1:8021` 登录：
   ```powershell
   .venv/Scripts/python -m uvicorn devrag.app:create_app --factory --host 127.0.0.1 --port 8021 --workers 1
   ```

首次运行在当前目录创建 `data/`（`DATA_DIR` 可改）：`knowledge.sqlite` 主库、`worker.lock`、`developer-knowledge/` 和客服库 `support/`。示例配置见 `.env.example`；客服模块另读 `SUPPORT_*` 变量，密钥只从进程环境读取，不自动加载 `.env`。联网搜索可选自建 SearXNG（`searxng/compose.yaml`），设置 `SUPPORT_SEARXNG_URL`。

## Docker 部署

`compose.yaml` 只启动服务 `app`：镜像 `tracebook:$APP_RELEASE`，容器端口 `8000` 映射到 `127.0.0.1:8083`，数据卷 `tracebook_data` 挂到 `/data`；`APP_RELEASE` 未设置时 compose 直接报错，端口可用 `TRACEBOOK_PORT` 覆盖。`.env` 由 `env_file` 注入容器（路径可用 `TRACEBOOK_ENV_FILE` 覆盖），发布、备份和恢复见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

```powershell
Copy-Item .env.example .env   # 填入真实密钥和模型配置
$env:APP_RELEASE = '<已验证的 Git 提交>'
docker compose up -d --build
curl.exe http://127.0.0.1:8083/health
```

## 目录结构

```text
devrag/      FastAPI 应用：检索、存储、问答与客服 Agent（devrag/support/）
frontend/    React 前端，构建产物 frontend/dist
scripts/     导入、导出、评测、打包部署与知识发布脚本
docs/        OPERATIONS.md、PROJECT-GUIDE.md、images/
tests/ evaluation/ examples/ searxng/ 与顶层 Dockerfile、compose.yaml、API-CONTRACT.md、.env.example
```

## 导入自己的项目

`python scripts/inspect_repo.py C:/path/to/repo --output output/inventory.md` 生成接入建议，`python scripts/export_repo.py C:/path/to/repo --project my-project --include README.md --include src --include docs --output output/my-project.json` 生成快照 JSON，在页面"导入项目快照"上传。只读取指定提交，未提交和未跟踪文件不会进入；单份快照上限 200 个文本文件、2MB。需要持续同步时用 `scripts/sync_repositories.py`，配置示例在 `examples/`。

## 测试与评测

```powershell
python -m pytest -q
python -m ruff check devrag tests scripts
npm --prefix frontend test
python scripts/evaluate.py output/my-project.json --output output/retrieval.json
python scripts/evaluate_boundaries.py --output output/edges.json
```

`evaluation/questions.json` 是针对 Tracebook 自身代码的示例题，用来演示题集格式；评测自己的项目需要另写题集，`--answers` 和边界评测会调用已配置的模型。`pytest` 和 `ruff` 不在 `requirements.txt` 里，需要另行安装。

## 已知局限

- 检索没有向量召回和重排，也没有调用图。
- 逐字引用校验只能证明引用存在，不能证明结论正确。
- 单用户、单 worker，不支持多人权限。
- 客服模块目前只接受代码里指定的模型网关和两种已验证的模型（见 `devrag/support/model.py`）。

## 文档索引

- [docs/OPERATIONS.md](docs/OPERATIONS.md)：发布状态机、备份恢复、诊断与验收门槛。
- [API-CONTRACT.md](API-CONTRACT.md)：API v1 端点与请求、响应形状。
- [.env.example](.env.example)：环境变量示例。

## 许可证

MIT，见 [LICENSE](LICENSE)。
