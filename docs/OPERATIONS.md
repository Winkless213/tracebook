# 独立部署与恢复

范围：单人 Tracebook，单容器/单 worker，SQLite WAL。默认回环端口8083、候选18083，与既有 another app 的目录、数据及8080服务完全隔离。脚本不会自动操作其他应用；`--protect-container`用于部署前后比对其ID、镜像、PID及启动时间。

## 发布状态机

干净提交及tracked文件哈希 → 受控解包到 `/opt/devrag/releases/<commit>` → 独立配置 → 清单校验与tar内容流构建 → 候选容器认证/静态文件/导入检索 → 在线备份 → 切换Tracebook → 镜像与进程身份/健康/API → 真实桌面与手机浏览器。

`package-manifest.json`包含revision和逐文件SHA256；部署拒绝错误目录、错误提交格式、文件哈希不一致。配置只读取 `/opt/devrag/config.env`（0600），运行数据只挂载 `/opt/devrag/data`。容器UID10001、2 CPU、2GiB内存、全部capability移除。前端静态文件在镜像构建阶段完成，运行时不覆盖产物。

```sh
python3 /opt/devrag/releases/<commit>/scripts/deploy.py /opt/devrag/releases/<commit> --protect-container other-app-backend --protect-container other-app-frontend --protect-container other-app-db --protect-container other-app-proxy
```

候选通过后停止并重命名旧Tracebook容器，新容器失败则恢复旧容器并验证原健康身份；旧容器、候选、release与备份保留。此版本仅追加表结构；未来不兼容schema migration必须增加数据库整体回滚步骤，不能依赖容器回滚。部署报告写到release内 `deployment.json`，`current`链接只在健康检查通过后更新。浏览器验收仍单独执行，脚本完成不能冒充UI已验收。

## 使用与诊断

```sh
ssh -N -L 3415:127.0.0.1:8083 root@your-host
docker logs --tail 80 tracebook
docker inspect tracebook
curl http://127.0.0.1:8083/health
```

本机访问 `http://127.0.0.1:3415`，使用独立访问密码。Health校验SQLite可读及release，不代表模型在线。模型401/403不继续备用请求，其他连接/超时/结构错误最多主备各一次；默认单次70秒网络读取超时。供应商usage未返回的请求标为未知，未计算货币成本。

## 备份和恢复

宿主机的 `scripts/backup.py` 使用Python标准库，无需安装应用依赖：

```sh
python3 /opt/devrag/current/scripts/backup.py /opt/devrag/data/knowledge.sqlite /opt/devrag/backups/manual-<timestamp>.sqlite
python3 /opt/devrag/current/scripts/backup.py /opt/devrag/backups/manual-<timestamp>.sqlite /opt/devrag/restore-<timestamp>/knowledge.sqlite
chown -R 10001:10001 /opt/devrag/restore-<timestamp>
```

先以同一镜像、独立18084回环端口和恢复目录启动验证容器，核对项目、历史问答和原文哈希。成功后才在明确维护时切换Tracebook数据挂载，原运行目录保留。不递归删除数据，不把新备份写到已有路径。

`docker restart tracebook`可进行正常重启验证；强制中断演练仅对独立演练容器执行。处理中问题恢复一次，仍固定原快照；第二次中断会failed。记录每次上游调用，未回传的usage标为interrupted/unknown，用户重试可能重复计费。备份并不取消上游已发出的请求。

## 发布门槛

- Git和包身份一致，独立配置与数据路径明确。
- 候选API、认证、导入检索通过，正式镜像/进程release一致。
- 旧RAG容器身份与health前后相同。
- 问答、原文引用、历史、取消/失败恢复在API与真实UI验收。
- 在线备份恢复、故障续跑在独立容器演练，并保留证据。
- 桌面/手机截图进入 `output/frontend-qa/`，验收结论单列已测和未测。
