# API v1

中文单人知识库，所有正文按文本渲染，禁止 HTML 注入。所有 POST 加 `X-Requested-With: Tracebook`，JSON；错误 `{detail: string}` 或 FastAPI 校验数组。认证 Cookie HttpOnly。GET /api/info、/api/session、POST /api/login 无需登录，其余 /api 需登录。

- GET /api/info → `{name:"Tracebook",release,provider_configured,primary_model,fallback_model}`
- GET /api/session → `{authenticated:boolean}`
- POST /api/login `{password}` → `{authenticated:true}`；POST /api/logout `{}`。
- GET /api/projects → `[{id,name,branch,active_snapshot,commit,file_count,chunk_count,imported_at}]`
- GET /api/projects/{id}/snapshots → `[{id,project_id,commit,branch,created_at,file_count,chunk_count,active:boolean,changes:{added,modified,removed}}]`
- GET /api/projects/{id}/knowledge-map?snapshot_id=... → `{snapshot,scope:null|{selected_roots:string[],tracked_file_count,out_of_scope_count},counts:{document,code,test,config},files:[{path,kind,sha256,line_count,anchors:[{label,start_line,end_line}]}],gaps:string[]}`。导航为从原文提取的结构信息；不是模型总结或项目完整架构。点击文件用原有 sources API 读取。
- POST /api/import 接收 UTF-8 JSON 快照（文件上传由前端读文本后 JSON 发送），形状 `{schema:1,project,branch,commit,files:[{path,content,sha256}],exported_at,excluded:[{path,reason}]}`；最多200文件、总文本2MB，请求3MB。返回 `{project,snapshot,unchanged,excluded_count}`。上传区注明资料会在提问时将相关片段发往已配置模型；导入不会调用模型。
  可选 `scope:{selected_roots:string[],tracked_file_count,out_of_scope_count}` 由新版导出器产生，说明已选范围及仓库中未纳入的文件数量；旧快照缺省为null，不能猜测覆盖率。
- GET /api/samples → `[{id,title,description,question}]`。
- POST /api/search `{project_id,question,snapshot_id?:string,top_k:6}` → `{snapshot,sources:[source],query,method:"bm25-symbol",warnings:[string]}`。
- POST /api/questions `{project_id,question,snapshot_id?:string}` 加 `Idempotency-Key:<uuid>` → question；立即返回保存状态，前端每1.5秒轮询直到终态。问题8–2000字符。
- GET /api/questions?project_id=... → question summary 数组，最近100条。
- GET /api/questions/{id} → 完整 question。
- POST /api/questions/{id}/retry `{}` → question，只有 failed 可重试；POST /cancel `{}` → question。
- GET /api/questions/{id}/export → Markdown 下载。
- GET /api/sources/{snapshot_id}?path=<URL编码路径> → `{path,content,sha256,snapshot_id,commit,kind}`。原文按行显示，可突出命中 start_line/end_line，不执行代码。

source `{id:"S1",path,start_line,end_line,content,kind:"code"|"document"|"config"|"test",symbol,commit,sha256,score}`，每次检索的S编号只在当前问答有效。

question summary `{id,project_id,question,status,created_at,updated_at,snapshot_id,commit,error,stale}`。
完整 question 加 `{sources:[source],result:null|result,usage:{input_tokens,output_tokens,total_tokens,unreported_calls},attempts:[{model,status,latency_ms,input_tokens,output_tokens,error}],events:[{id,kind,message,created_at}],latency_ms,method,recovery_count}`。
status queued/retrieving/generating/completed/failed/cancelled。
result `{status:"answered"|"insufficient"|"conflict",summary,claims:[{text,evidence:[{source_id,quote}]}],gaps:[string]}`；不足不伪装成功解决，conflict 显示冲突提示。summary 是摘要，依据位于claims；quote必须是对应source的连续原文。

产品布局建议：左侧项目与分支/版本、问答历史；主体顶部当前快照与资料数量，中部提问和回答，旁边/下方证据面板。点击引用可查看片段与原文件行号；支持先检索不生成。样本只填问题，用户点击后才运行。首屏有导入入口、空态说明和样本。登录后刷新恢复历史，提问/导入/网络错误不清空用户输入。手机390px无横向溢出。
