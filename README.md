# Tracebook · 开发者项目知识助手

将 GitHub 项目代码、文档与开发会话整理成有出处的 Markdown，供 Codex / Claude 按任务读取。默认网页只显示项目覆盖和可用知识；旧问答与诊断保留在 `?view=diagnostics`。数据与服务独立，不影响既有 another app。

## 项目知识主路径

GitHub 登录与默认分支检出 → [Repomix 1.18.1](https://github.com/yamadashy/repomix) 整理源码和检查秘密 → 现有强模型分批归纳 → `INDEX.md` / `CODEBASE.md` / `MODULES.md` → 开发助手按仓库读取。

复用已核验的 [deja-vu v0.21.7](https://github.com/vshulcz/deja-vu) 解析与脱敏会话。仓库地址、历史目录关联、会话主题相关性是不同证据，不能只凭工作目录把本机迁移或其他项目讨论当作项目开发经验。`SESSION-INDEX.md` 和 `history/*.md` 是来源索引与脱敏记录，不表示全部经过模型提炼。

```powershell
npm --prefix scripts/knowledge ci --ignore-scripts --no-audit --no-fund
python -m scripts.sync_github_knowledge --owner <GitHub账号> --state <独立同步目录> --output <知识目录> --proxy http://127.0.0.1:7897
```

Git 凭据由本机 Credential Manager 提供，模型沿用 `DISTILL_*` 环境变量。`--watch` 在每轮结束后每5分钟重新核对GitHub；同一输入复用缓存。默认分支浅检出不含全部Git历史或LFS大文件；本地副本存在改动或身份不符时停止，绝不覆盖开发工作树。

代码归纳按 [HTTPX 官方建议](https://www.python-httpx.org/advanced/transports/)复用 [Tenacity 9.2.1](https://github.com/jd/tenacity)：连接中断、读超时、流未完整结束、临时上游错误以及 HTTP 429/524 等按10–120秒退避自动重试，直到成功或取消；已成功的批次直接读取缓存。每次请求重新读取完整响应，半截内容不拼入下一次结果。重试次数、错误及已报告用量保存在生成缓存；中断请求没有返回用量时不能视为零费用。

在知识输出根目录创建 `STOP` 可停止等待和后续请求，当前请求在收到下一个流事件或网络超时后结束；移除 `STOP` 后原命令可续跑。鉴权、参数或来源引用错误不盲目重试，修正后用 `--retry-failed` 续跑同步器记录的失败提交。

```powershell
python -m scripts.export_project_history --catalog <同步目录>/github-checkouts.json --deja <已核验deja.exe> --state <独立会话状态目录> --output <知识目录> --inventory-only
```

去掉 `--inventory-only` 后导出已关联会话；可用 `--project <仓库名>` 选定项目。原始记录只读；超过现有采集器大小门槛或来源校验失败的文件保留为失败，不能算已接入。代码知识的来源校验只保证引用路径存在，不保证每个模型判断正确，改代码前仍须打开当前源码并运行相关测试。

API `GET /api/developer-knowledge` 返回真实发布目录；已完成项目的 `CODEBASE.md` 和 `MODULES.md` 可在认证后下载。目录由 `DEVELOPER_KNOWLEDGE_DIR` 指定，默认 `DATA_DIR/developer-knowledge`。不发布原始私有源码、会话全文或模型生成缓存。

## 保留的诊断能力

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

开发经验可来自已有交接、故障文档或明确选定的开发会话。通用规则仍维护在原有 AGENTS.md/CLAUDE.md；提炼结果只作为候选经验，不自动改写规则。

### 自动跟踪代码与文档

复制 `examples/repository-connector.json`，填写明确的本地仓库或GitHub URL、固定分支、同一组代码和文档目录，再运行：

```powershell
python scripts/sync_repositories.py path/to/repository-connector.json
python scripts/sync_repositories.py path/to/repository-connector.json --watch
```

本地默认每30秒检查HEAD；GitHub默认每5分钟核对分支提交，有变化才抓取到连接器自己的裸仓库并导入。新增、修改和删除随完整快照原子生效，历史引用保留。读取与同步不调用模型。远端失败保留上次成功快照并在下一轮重试，不能把旧内容当作已追上远端。

两类来源都只同步已提交的允许文本，代码与文档绑定同一个commit。未提交和untracked不入库；连接器不commit、pull、checkout或push开发仓库。当前本地分支若与配置不符则停止该来源，避免串项目。GitHub使用现有Git认证；URL不得嵌入token，HTTPS代理可显式配置本机 `proxy`（例如 `http://127.0.0.1:7897`）。不在日志输出凭据。

同一项目不同分支可在页面切换；同一个项目分支只能配置一个来源，避免本地与远端交替覆盖。会话的project_id仍需绑定到正确的项目分支，尚不自动推断历史聊天对应哪个提交。没有GitHub远端的本地仓库也能同步，不会为此创建或推送远端仓库。

每次输出包含已核对时间、同步时间、commit和snapshot；状态与游标写入独立state_dir。使用同一配置可继续，服务端恢复到旧备份后用 `--force` 重新核对导入。后台运行和开机启动需由使用者启动，此脚本不安装计划任务。单快照仍受200文件、2MB范围约束，超出时缩小明确的模块范围，不静默截断。

## 接入 Codex / Claude 会话

本机连接器只读配置中列出的 JSONL 文件，使用 [deja-vu v0.21.7](https://github.com/vshulcz/deja-vu) 的 Windows 可执行文件在隔离目录索引和脱敏，再同步到认证 API。固定二进制 SHA256 为 `85db2a1b094b4b9a7afead17ade22c9b0ef746955e3314efe2e5e730f29c8418`。连接器不安装 hooks 或自启动，不扫描其他会话，不改写原始日志。

复制 `examples/history-connector.json`，填入服务地址、已核验的 deja.exe、独立状态目录、会话文件及会话原生 ID。已有代码项目使用 API `/api/projects` 返回的 `project_id`；没有 Git 的项目使用 `project_name` 创建独立会话归档，界面明确显示“无 Git 快照”。必须按实际工作目录核实归属，不能根据标题猜项目。

```powershell
python scripts/sync_history.py path/to/history-connector.json
python scripts/sync_history.py path/to/history-connector.json --watch
```

运行时交互输入工作台密码，或由本机环境提供 `TRACEBOOK_PASSWORD`。`--watch` 每30秒检查一次，Ctrl+C停止；下次使用同一配置续传。网络失败保留游标，重复同步不重复入库；只处理写完的行，原文件截断或改写时停止。文件丢失时保留服务端记录，但不能继续同步；留存副本须在标题明确标明。当前仅支持128MB以内的 JSONL，不处理压缩归档。

原文按用户、助手、工具输出区分，保留时间、来源文件、行号、分段和原始行哈希。Codex 的工具文本块与压缩摘要在隔离副本中适配，摘要仍标为助手。标准密钥检查是辅助措施，不能保证识别所有私人内容；同步前应核对选定文件。脱敏副本与服务器数据同属私人资料。

默认 `auto_distill=false`，同步本身不调用模型。在“会话知识”查看原文、分页、任务与依据；点击“提炼候选经验”处理最近最多20段、60000字符。启用 `auto_distill` 后，每次发现新增记录最多排入一批，不自动清空积压历史。其余记录仍可原文检索，待提炼数量会显示。

提炼独立使用 `DISTILL_*` 配置，默认 `gpt-6-astra`、`max` 推理。问答、核验与提炼均不主动设置输出token上限，使用模型服务默认值；服务自身仍可能截断输出，流不完整、引用或角色不符则不采纳。失败可以续跑；取消为终态，再提炼会创建新任务。取消和重启无法撤回已发出的供应商请求，未报告用量单独标明。

会话和候选经验只参与所属项目最新版本的检索，历史代码快照仍保持原范围。回答引用会话时可回查脱敏记录；历史记录和 AI 候选不证明当前线上状态。问答还经过独立语义核验，拒绝原因保存在执行记录；核验仍可能出错，不代替人工判断。

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
