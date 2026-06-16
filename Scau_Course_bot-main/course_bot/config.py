"""
配置模块 —— 修改这里即可复用于不同学期。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Config:
    """选课配置"""

    # ====== 账号 ======
    student_id: str = ""
    password: str = ""

    # ====== 教务系统 ======
    # 校园内网地址
    # base_url: str = "http://10.42.100.1"
    # 外网地址（当前使用）
    base_url: str = "https://jwzf.scau.edu.cn"

    # 选课 gnkmdm 参数（正方教务系统固定值）
    gnmkdm: str = "N253512"

    # ====== 目标课程 ======
    # jxbbh = 教学班编号（从选课页面可见的课程编号）
    # kklxdm = 课程类型代码（体育=06, 大学英语=09, 专业课=01, 通识=05 等）
    target_courses: list = field(default_factory=lambda: [
        {"jxbbh": "202620271-610023-001-乒乓球02",
         "kklxdm": "06"},
        {"jxbbh": "202620271-604792-005",
         "kklxdm": "09"},
    ])

    # ====== 选课时间窗口（服务器时间可能有偏差，建议提前几秒） ======
    window_open: str = "2026-06-18 12:29:55"
    window_close: str = "2026-06-22 23:59:59"

    # ====== 重试与超时 ======
    max_retries: int = 5
    retry_delay: float = 2.0
    request_timeout: float = 10.0
    api_delay: float = 0.5       # API 调用间隔

    # ====== 检查间隔 ======
    poll_before: int = 300
    poll_interval: float = 1.0

    @property
    def window_open_dt(self) -> datetime:
        return datetime.strptime(self.window_open, "%Y-%m-%d %H:%M:%S")
