# Tracebook · 开发者项目知识助手

输入 Git 项目快照，检索代码与工程文档，回答带文件、行号、提交版本和原文摘录的问题。第一版单人使用；数据与模型配置均独立，不影响外贸 RAG。

## 本轮范围和完成门槛

- 导入本机 Git 已提交文本，经允许目录、文件类型、密钥检查后打包；服务不接受任意服务器路径、不自动克隆或执行代码。
- 快照按项目与分支隔离。更新原子完成；历史问答固定原快照，当前搜索只用选中版本。旧问答显示版本变化提示。
- Python AST、Markdown 标题和行范围切分；BM25、符号检索及中文分词。先比较精确词项基线，检索缺口有证据再增加向量能力。
- 回答每条结论引用真实片段及连续原文；结构和引用检查通过后显示。证据不足明确说明；代码与文档冲突需保留双方引用。
- 持久问答、后台队列、有限主备尝试、故障/重启续跑、幂等提交；最多一次自动重启恢复，反复失败转人工续跑。
- 私人资料须认证，部署默认只监听服务器回环，经 SSH 隧道访问。回答调用配置的外部模型，仅发送当前问题与检索片段。
- 冻结题集包含真实工程问题、库外问题、冲突、删除与提示注入反例；记录检索召回、有效引用、答案判读、延迟和供应商用量。单独记录现有 Agent 搜索对照，不用词项基线冒充完整 Agent。
- Docker 镜像/进程身份、备份恢复、重启续跑、桌面/手机真实浏览器均完成后才报交付。

## 复用与边界

迁移 another app 的来源组织、内容指纹刷新、证据预算和主备模型经验；没有复制其采购、法规 CRUD、旧向量分支或遗留完整工程。原法规 26 题不作为新项目的效果证明。编排/审查平台停止扩建；后续流程补强优先现有工具和社区插件。

## 本地运行

Python 3.13+、Node 24+。先创建独立虚拟环境并安装 `requirements.txt`；开发校验另安装 pytest 和 ruff。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
$env:APP_PASSWORD = '<随机访问密码，至少16字符>'
$env:SESSION_SECRET = '<随机签名密钥，至少32字符>'
$env:LLM_BASE_URL = 'https://模型供应商/v1'
$env:LLM_API_KEY = '<在本机配置，不要提交>'
$env:LLM_PRIMARY_MODEL = '<主模型>'
$env:LLM_FALLBACK_MODEL = '<备用模型>'
.venv/Scripts/python -m uvicorn devrag.app:create_app --factory --host 127.0.0.1 --port 8021 --workers 1
```

访问 `http://127.0.0.1:8021`。模型没有配置时可导入、浏览与检索。一个数据库只能运行一个 worker；锁冲突时启动失败，避免重复计费。开发前端可用 `npm --prefix frontend run dev`，默认5175代理到8021。

## 接入自己的真实项目

不要求把全部代码先改写成文档。原始代码、现有说明与项目记录保持各自来源；项目资料导航按原文提取文件、标题与 Python 符号。[项目导航](docs/PROJECT-GUIDE.md)说明数据流与实现边界。

```powershell
python scripts/inspect_repo.py C:/path/to/repo --output output/project-inventory.md
python scripts/export_repo.py C:/path/to/repo --project my-project --include README.md --include app/payments --include tests/payments --include docs --output output/my-project.json
```

先看接入清单，围绕一个真实问题选代码、对应测试和说明。检查导出 JSON 的文件列表和排除项，再从页面“导入项目快照”上传。单份快照最多200个文本文件、2MB正文；200以上先缩小模块范围。只读取指定提交，dirty及untracked不会进入；密钥检查只是辅助，无法保证识别所有秘密。

后续用相同 `--project`、分支和 `--include` 重新导出，原子更新新增/修改/删除；旧回答保留原快照。默认 `--ref HEAD`；显式分支或提交用 `--ref`。服务器验证内容哈希，无法独立验证手写 JSON 的 Git 提交真实性。

开发经验沿用已有交接或故障文档，保留有查询价值的原因和验证依据；通用规则继续维护在原有 AGENTS.md/CLAUDE.md。无需另写 RAG 总结、同步完整聊天或监控开发进程。

## 验证与评测

```powershell
python -m pytest -q
python -m ruff check devrag tests scripts
npm --prefix frontend test
python scripts/evaluate.py output/my-project.json --output output/retrieval-v1.json
python scripts/evaluate.py output/my-project.json --answers --output output/answers-v1.json
python scripts/evaluate_boundaries.py --output output/edges-v1.json
```

`evaluation/questions.json` 当前针对 sample-project 的真实工程资料，其他项目需另设题集，不能直接套用其分数。`--answers`及边界评测会调用已配置模型。真实评测和合成边界分开记录，逐题保留原文、结果、延迟与供应商用量；输出已有文件则拒绝覆盖。最终证据位于 `output/evaluation/` 与 `output/frontend-qa/`（含私有源码片段，不默认进入Git）。

检索的 term-overlap 仅是词项基线；没有证明优于完整 Codex/Claude Code 工作流。逐字引用校验不等于事实正确，做工程改动前仍应核对原文。当前没有向量检索、重排器、Git调用图、工具执行、跨项目混合检索或多人权限。

## Docker 与数据恢复

复制 `.env.example` 为本地 `.env`，填入独立配置并限制读取权限。密码和模型Key只放环境文件，数据挂载到独立卷。

```powershell
$env:APP_RELEASE = git rev-parse HEAD
docker compose -p tracebook up -d --build
```

默认只监听 `127.0.0.1:8083`。VPS使用SSH隧道访问，公网发布需另外配置HTTPS及访问边界。宿主机发布使用 `scripts/deploy.py`，只接受已核验的Git文件清单，通过独立候选容器后切换，失败恢复旧容器；具体见 [运行与恢复](docs/OPERATIONS.md)。

备份使用SQLite在线backup接口，不能直接复制正在写入的主数据库文件而忽略WAL。恢复到全新目录验证，禁止覆盖运行库。停用、取消和重启无法撤销已经发往模型的请求；界面单独显示未知用量，不能把未知当0费用。
