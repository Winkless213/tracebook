# Tracebook 项目导航

用途：为开发助手生成可追溯的项目 Markdown。主入口和运行命令见 [README](../README.md#项目知识主路径)：`sync_github_knowledge.py` 组合 GitHub/Git、Repomix 与 `build_knowledge.py`；`export_project_history.py` 复用 deja-vu 生成会话来源索引和脱敏 Markdown。默认网页只读发布结果，自主修改项目代码不在此服务范围内。

下面保留既有诊断接口的设计与边界，不作为新知识主路径的完成证明。

## 从仓库到可查询资料

1. `scripts/inspect_repo.py` 读取指定 Git 提交的文件清单，按说明、代码、配置、测试给出接入建议。先围绕一个真实问题选资料。
2. `scripts/export_repo.py` 从 Git 对象读取允许的文本，排除未提交内容、链接、敏感路径及可识别的密钥。文件清单与排除原因可检查，扫描不保证识别所有秘密。
3. `devrag/models.py` 复核路径、大小和哈希；`Store.ingest` 在 SQLite 事务中写入快照并切换当前版本。相同内容不重复建快照。
4. `devrag/indexing.py` 提取 Python 函数/类方法、Markdown 章节及行范围。其他语言保留路径与行片段，尚无 TypeScript 调用图。
5. 项目资料导航展示已导入文件、章节和函数，以及未纳入范围的文件数量。它是来源目录，尚不具备自动确认完整业务流程的能力。

已有 README、交接、设计或故障说明可直接纳入，无需另写一份 RAG 文档。未记录的历史决策不能从代码反推。AGENTS.md 与 CLAUDE.md 继续作为开发工具原有规则载体；按需检索其中内容不会改变助手的执行规约。

## 查询路径与取舍

`POST /api/search` → `Retriever.search` → 选中快照的切片 → 中文分词、BM25 与符号匹配 → 最多六个默认片段、24,000 字符证据预算。

明确要求对照“代码”和“文档”时，额外沿命中文档里出现的类/函数符号查找对应实现，并保留双方证据；只有标题没有正文的Markdown片段不占证据槽。这是一跳符号关联，尚不等于完整依赖或调用图。未召回某个文件不代表完整快照或仓库没有该文件。

`POST /api/questions` → 固定 snapshot_id 并持久化问题 → 单 worker 检索 → `LLM.answer` 主模型/备用模型有限尝试 → 结构与连续原文校验 → 保存回答及用量。`validate_answer` 验证引用的存在和逐字匹配，不能单独证明结论语义正确。

首版使用 SQLite、单 worker 和单人登录，以减少运行组件；最多同时排队五题。模型调用可能在取消或进程中断后继续计费。重启只自动恢复一次，再中断则转失败，由用户决定重试。相关行为分别位于 `devrag/store.py`、`devrag/worker.py`、`devrag/llm.py`，对应测试在 `tests/test_answers.py`。

## 版本与运行事实

快照表示导入内容对应的提交标记，不证明线上正在运行该版本；手写 JSON 的提交真实性也无法由服务器单独验证。问答固定原快照，导入更新后显示历史提示；删除的文件退出新快照检索，旧回答仍可查看旧原文。`tests/test_knowledge.py` 覆盖更新、删除、分支隔离与历史保留。

部署身份、健康、真实 API/UI、恢复演练与效果评测以独立验收记录为准，不从本导航推断已经上线或已经通过所有检查。
