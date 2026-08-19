"""重新构建三张 AI 检索文档表。

该入口以模块方式调用现有构建脚本，确保 tools 目录被复制到其他项目后仍能独立
运行，不引用原始 ICBC-trading-ai_search 路径。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """按顺序重建金融工具、数据集和新闻检索文档。"""

    modules = (
        "backend.ai_search.build_search_documents",
        "backend.ai_search.build_dataset_search_documents",
        "backend.ai_search.build_news_search_documents",
    )
    for module in modules:
        print(f"正在运行 {module} ...")
        completed = subprocess.run(
            [sys.executable, "-m", module],
            cwd=TOOLS_ROOT,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    print("检索文档重建完成；Embedding 如需更新，请单独运行向量生成脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
