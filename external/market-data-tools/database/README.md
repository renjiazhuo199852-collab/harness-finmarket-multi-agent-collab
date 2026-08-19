# 数据库快照说明

`full_database.sql` 是服务器基础数据库的备份快照，包含：

- `source` Schema 的九张原有正式业务表和新增的
  `instrument_metric_link` 关系表；
- `ai_search` Schema 的三张检索文档表和全部 Embedding；
- `halfvec(2048)` 向量列和三套 HNSW `halfvec_cosine_ops` 索引；
- 当前向量模型为 SiliconFlow `Qwen/Qwen3-Embedding-8B`，请求维度为 `2048`；
- 索引、约束、序列以及 `pg_trgm`、`vector` 扩展依赖。

当前服务器目录还包含：

- `source.dataset_catalog` 中的 `INSTRUMENT_MASTER` 目录记录；
- `source.dataset_field_catalog` 中的 5 个标准化返回字段；
- `ai_search.dataset_search_documents` 中对应的第 8 条数据集检索文档。

本次目录增量对应的可重复迁移文件是
`database/003_register_instrument_master.sql`，当前服务器已经执行完成；日常启动不需要
再次执行该迁移文件。快照文件只用于灾备，不保证自动包含后续每一次目录增量。

本次 EURUSD 相关宏观指标增量对应
`sql/004_create_instrument_metric_link.sql`，当前服务器也已经执行完成，登记
欧元区 5 条和美国 11 条 `METRIC` 关系。该迁移只新增关系表和关系数据，不修改原有九张
source 业务表；日常启动不需要再次执行。

## 日常使用

服务器数据库已经完成初始化和验收。正常运行 tools 时：

1. 不要执行数据库恢复；
2. 不要重新创建 Schema 或检索表；
3. 不要执行 003、004 关系和目录迁移；
4. 不要重复生成 Embedding；
5. 先建立 SSH 隧道，再让后端连接本机 `127.0.0.1:15433`。

```powershell
ssh -p 22 -L 15433:127.0.0.1:5433 root@101.35.55.7
```

`15433` 是本机转发端口，服务器 PostgreSQL 实际监听 `127.0.0.1:5433`。

## 灾备恢复

只有在迁移到另一台空数据库或管理员明确批准灾备恢复时，才使用快照。恢复目标必须
支持 PostgreSQL 16、`pg_trgm` 和 pgvector `0.8.x`，并且支持 `halfvec(2048)` 与 HNSW。
恢复基础快照后，再按 `database/003_register_instrument_master.sql`、
`sql/004_create_instrument_metric_link.sql` 的顺序执行增量迁移。不要把基础快照直接恢复到
已经配置好的 `icbc_shared`，避免覆盖服务器数据。

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

新增或修改数据集目录后，只需要重新生成数据集检索文档和对应向量；不需要恢复整个
数据库，也不需要重新生成 `instrument_search_documents` 的 188 条金融工具向量。

本次 `INSTRUMENT_MASTER` 目录登记已经完成，服务器不需要再次执行迁移脚本。

只有切换 Embedding 模型、模型维度或需要重新生成向量时，才执行：

```powershell
python .\scripts\rebuild_embeddings.py
```

脚本会先生成全部向量并校验为 2048 维，然后在一个事务中更新三张 AI 检索表、重建
三套 HNSW 索引并执行 `ANALYZE`。日常启动 tools 不需要运行该脚本，也不要重复恢复
`full_database.sql` 到已经配置好的服务器数据库。
