# 数据库快照说明

`full_database.sql` 是从服务器当前 `icbc_shared` 数据库导出的备份快照，包含：

- `source` Schema 的九张正式业务表和全部数据；
- `ai_search` Schema 的三张检索文档表和全部 Embedding；
- `halfvec(2048)` 向量列和三套 HNSW `halfvec_cosine_ops` 索引；
- 当前向量模型为 SiliconFlow `Qwen/Qwen3-Embedding-8B`，请求维度为 `2048`；
- 索引、约束、序列以及 `pg_trgm`、`vector` 扩展依赖。

## 日常使用

服务器数据库已经完成初始化和验收。正常运行 tools 时：

1. 不要执行数据库恢复；
2. 不要重新创建 Schema 或检索表；
3. 不要重复生成 Embedding；
4. 先建立 SSH 隧道，再让后端连接本机 `127.0.0.1:15433`。

```powershell
ssh -p 22 -L 15433:127.0.0.1:5433 root@101.35.55.7
```

`15433` 是本机转发端口，服务器 PostgreSQL 实际监听 `127.0.0.1:5433`。

## 灾备恢复

只有在迁移到另一台空数据库或管理员明确批准灾备恢复时，才使用快照。恢复目标必须
支持 PostgreSQL 16、`pg_trgm` 和 pgvector `0.8.x`，并且支持 `halfvec(2048)` 与 HNSW。

恢复脚本默认拒绝操作正式数据库 `icbc_shared`：

```powershell
cd D:\python\projects\ICBC-trading\tools
.\scripts\restore_database.ps1
```

如确需恢复，必须先确认目标数据库，再显式传入保护开关：

```powershell
.\scripts\restore_database.ps1 -AllowExistingServerDatabase
```

恢复完成后运行：

```powershell
python .\scripts\check_config.py
```

不要把服务器 SSH 密码写入项目文件；数据库密码和模型 API Key 只应保存在本机
`.env` 或部署环境变量中。

## Embedding 模型切换

只有切换 Embedding 模型、模型维度或需要重新生成向量时，才执行：

```powershell
python .\scripts\rebuild_embeddings.py
```

脚本会先生成全部向量并校验为 2048 维，然后在一个事务中更新三张 AI 检索表、重建
三套 HNSW 索引并执行 `ANALYZE`。日常启动 tools 不需要运行该脚本，也不要重复恢复
`full_database.sql` 到已经配置好的服务器数据库。
