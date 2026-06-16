"""
教务系统自动选课 —— 直接 HTTP 请求，无需浏览器。

用法:
    python course_bot/main.py              从项目根目录直接运行
    python -m course_bot.main              作为模块运行
    python course_bot/main.py --window "2026-06-18 12:29:55"
"""

import argparse, asyncio, sys, traceback
from pathlib import Path

# 确保项目根目录在 sys.path 中（兼容直接运行和模块运行）
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from course_bot.config import Config
from course_bot.course import CourseBot


async def main():
    parser = argparse.ArgumentParser(description="华农教务系统自动选课")
    parser.add_argument("--window", type=str,
                        help="选课窗口时间，如 '2026-06-18 12:29:55'")
    args = parser.parse_args()

    config = Config()
    if args.window:
        config.window_open = args.window

    bot = CourseBot(config)
    try:
        await bot.run(do_find=True)
    except KeyboardInterrupt:
        print("\n退出")
    except Exception as e:
        print(f"\n[错误] {e}")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
