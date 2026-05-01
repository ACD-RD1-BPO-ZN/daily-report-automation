# -*- coding: utf-8 -*-
"""
generate_report.py — 遊戲產業日報的薄入口 (Thin Entry Point)

此腳本不再包含硬編碼的 RSS 來源或 Prompt，
所有頻道特定邏輯已遷移至 channels/gamedev/channel_config.json。
核心引擎位於 core/report_engine.py。

用法：
    python generate_report.py                     # 預設遊戲頻道
    python generate_report.py channels/movie      # 指定其他頻道
"""

import sys
import asyncio
from core.report_engine import generate_report


def main():
    # 支援命令列指定頻道目錄，預設為 gamedev
    if len(sys.argv) > 1:
        channel_dir = sys.argv[1]
    else:
        channel_dir = "channels/gamedev"

    asyncio.run(generate_report(channel_dir))


if __name__ == "__main__":
    main()
