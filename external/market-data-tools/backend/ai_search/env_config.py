"""加载项目根目录的本地环境配置。

所有脚本都通过这个模块加载 ``.env``，因此 Embedding 和聊天模型使用同一份
本地配置。``override=False`` 保证部署环境已经设置的系统环境变量优先级更高，
避免项目目录中的本地文件意外覆盖正式运行环境配置。
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


# 本文件位于 tools/backend/ai_search 下，向上三级才是可整体复制项目的根目录。
# 这样 tools/.env 会在本地运行、复制到其他 Agent 项目后都被正确加载。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_env() -> None:
    """加载项目根目录的 ``.env``；文件不存在时保持系统环境不变。"""

    load_dotenv(dotenv_path=ENV_FILE, override=False)
