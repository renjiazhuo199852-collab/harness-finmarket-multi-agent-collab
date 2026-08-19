"""查询金融工具与宏观指标之间的正式关系。

关系表是数据库中的业务事实，不是查询理解模型的输出。模型最多只能说明用户
想查“与主体相关的指标”，具体的 ``instrument_id``、``metric_id``、供应商和
关系角色都必须从 ``source.instrument_metric_link`` 读取，并再次核对 active
工具和实际宏观观测数据。
"""

from __future__ import annotations

from datetime import date
from typing import Any


def resolve_instrument_metric_links(
    cursor: Any,
    instrument_id: str,
    *,
    provider: str | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """读取当前有效的宏观关系，并过滤没有实际观测数据的关系。

    ``macro_observations.instrument_id`` 允许为空，因此这里严格使用
    ``metric_id + source`` 作为观测数据关联键。日期边界采用与
    ``instrument_identifier`` 相同的 ``effective_date <= d < expire_date`` 语义。
    """

    if not instrument_id:
        raise ValueError("宏观关系查询缺少 instrument_id")

    query_date = as_of_date or date.today()
    parameters: list[Any] = [instrument_id, query_date, query_date]
    provider_clause = ""
    if provider:
        provider_clause = " AND link.provider = %s"
        parameters.append(provider)

    relation_query = (
        """
        SELECT link.instrument_id,
               link.metric_id,
               link.relationship_role,
               link.provider,
               link.status,
               link.effective_date,
               link.expire_date,
               EXISTS (
                   SELECT 1
                   FROM source.macro_observations AS observation
                   WHERE observation.metric_id = link.metric_id
                     AND observation.source = link.provider
               ) AS has_observations
        FROM source.instrument_metric_link AS link
        JOIN source.instrument_master AS instrument
          ON instrument.instrument_id = link.instrument_id
        WHERE link.instrument_id = %s
          AND instrument.status = 'active'
           AND (link.effective_date IS NULL OR link.effective_date <= %s)
           AND (link.expire_date IS NULL OR %s < link.expire_date)
        """
        + provider_clause
        + """
         ORDER BY link.relationship_role, link.metric_id, link.provider
        """
    )
    cursor.execute(relation_query, tuple(parameters))
    rows = cursor.fetchall()

    active_links: list[dict[str, Any]] = []
    provider_mismatch = False
    inactive_count = 0
    missing_metric_count = 0
    for row in rows:
        link = {
            "instrument_id": row[0],
            "metric_id": row[1],
            "relationship_role": row[2],
            "provider": row[3],
            "status": row[4],
            "effective_date": row[5].isoformat() if row[5] else None,
            "expire_date": row[6].isoformat() if row[6] else None,
            "has_observations": bool(row[7]),
        }
        if str(link["status"]).lower() != "active":
            inactive_count += 1
            continue
        if not link["has_observations"]:
            missing_metric_count += 1
            continue
        active_links.append(link)

    if provider:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM source.instrument_metric_link AS link
                JOIN source.instrument_master AS instrument
                  ON instrument.instrument_id = link.instrument_id
                WHERE link.instrument_id = %s
                  AND instrument.status = 'active'
                  AND link.provider <> %s
            )
            """,
            (instrument_id, provider),
        )
        provider_mismatch = bool(cursor.fetchone()[0])

    if active_links:
        status = "resolved"
        reason = "已从正式关系表解析有效宏观指标"
    elif provider_mismatch:
        status = "provider_mismatch"
        reason = "请求供应商与 EURUSD 的宏观关系供应商不一致"
    elif inactive_count:
        status = "inactive"
        reason = "宏观关系存在，但当前不在有效期或已 inactive"
    elif missing_metric_count:
        status = "metric_not_found"
        reason = "宏观关系指向的 metric_id 没有实际观测数据"
    else:
        status = "not_found"
        reason = "没有找到当前金融工具的有效宏观指标关系"

    return {
        "status": status,
        "instrument_id": instrument_id,
        "provider_requested": provider,
        "as_of_date": query_date.isoformat(),
        "links": active_links,
        "link_count": len(active_links),
        "inactive_count": inactive_count,
        "missing_metric_count": missing_metric_count,
        "reason": reason,
    }
