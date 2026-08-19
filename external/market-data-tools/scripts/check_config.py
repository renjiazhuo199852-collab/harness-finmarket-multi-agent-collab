"""检查 tools 的配置、数据库连接和核心目录。

该脚本只输出配置是否存在和数据库对象数量，不输出密码、Embedding Key 或聊天
模型 Key，适合复制到新 Agent 项目后作为第一步自检。
"""

from __future__ import annotations

import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from backend.ai_search.config import configuration_status, database_connection_kwargs  # noqa: E402


def main() -> int:
    """执行配置和数据库健康检查。"""

    status = configuration_status()
    print("配置状态：")
    for name, value in status.items():
        print(f"  {name}: {value}")

    if not status["database_configured"]:
        print("错误：未配置 AI_SEARCH_DB_PASSWORD")
        return 1

    try:
        import psycopg2

        with psycopg2.connect(**database_connection_kwargs()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_schema, count(*)
                    FROM information_schema.tables
                    WHERE table_schema IN ('source', 'ai_search')
                    GROUP BY table_schema
                    ORDER BY table_schema
                    """
                )
                print("数据库表数量：")
                for schema, count in cursor.fetchall():
                    print(f"  {schema}: {count}")
        print("数据库连接：ok")
        return 0
    except Exception as exc:  # noqa: BLE001 - 自检需要把根因显示给操作者
        print(f"数据库连接：error ({exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
