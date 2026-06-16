"""
选课逻辑 —— 直接 HTTP API，无 CDP 依赖。
流程：登录 → PartDisplay 匹配课程 → 窗口开启时 QuickSelect 一键选课
"""

import asyncio, sys, time, traceback
from datetime import datetime

from course_bot.config import Config
from course_bot.client import Client, ApiError, LoginError


# ================================================================
# 倒计时显示
# ================================================================

def _format_countdown(seconds: float) -> str:
    if seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _countdown_line(target_dt: datetime) -> str:
    remaining = (target_dt - datetime.now()).total_seconds()
    if remaining <= 0:
        return "\r  时间已到！                                             "
    return (f"\r  距选课窗口 {_format_countdown(remaining)}  |  "
            f"目标 {target_dt.strftime('%m-%d %H:%M:%S')}")


# ================================================================
# 选课机器人
# ================================================================

class CourseBot:
    """选课机器人 —— 纯 HTTP 请求，无需浏览器"""

    def __init__(self, config: Config):
        self.config = config
        self.client = Client(config)
        self.found_courses: list = []  # 匹配到的课程列表 [{jxbbh, jxb_id, ...}]

    # ============================================================
    # 阶段一：登录 + 匹配课程
    # ============================================================

    def phase_login_and_find(self):
        """登录并查找所有目标课程"""
        print("\n" + "=" * 50)
        print("阶段一：登录并匹配目标课程")
        print("=" * 50)

        # 1. 登录
        self.client.login()

        # 2. 获取选课页面参数和 tab 信息
        params = self.client.fetch_select_page()
        tabs = params.get("_tabs", {})

        # 3. 逐门课程查询
        for course_cfg in self.config.target_courses:
            jxbbh = course_cfg["jxbbh"]
            kklxdm = course_cfg.get("kklxdm", "06")

            # 检查是否已匹配
            if any(c["jxbbh"] == jxbbh for c in self.found_courses):
                print(f"  [{jxbbh}] 已匹配，跳过")
                continue

            print(f"  [{jxbbh}] kklxdm={kklxdm} ", end="", flush=True)

            # 查找 tab 信息
            tab_info = tabs.get(kklxdm)
            if not tab_info:
                # 回退到第一个 tab
                tab_info = list(tabs.values())[0] if tabs else {
                    "kklxdm": kklxdm,
                    "xkkz_id": params.get("firstXkkzId", ""),
                    "njdm_id": params.get("firstNjdmId", params.get("njdm_id", "")),
                    "zyh_id": params.get("firstZyhId", params.get("zyh_id", "")),
                    "xkkz_xh": params.get("firstXkkzXh", ""),
                }

            try:
                courses = self.client.query_courses(tab_info, params)
                found = self.client.find_course(courses, jxbbh)
                if found:
                    found["jxbbh"] = jxbbh
                    self.found_courses.append(found)
                    print(f"OK ({found['kcmc']})")
                else:
                    print(f"FAIL — 在 {len(courses)} 门课中未找到")
            except Exception as e:
                print(f"FAIL — {e}")
                traceback.print_exc()

            time.sleep(self.config.api_delay)

        print(f"\n结果: {len(self.found_courses)}/"
              f"{len(self.config.target_courses)} 门已匹配")

    # ============================================================
    # 阶段二：等待 + 一键选课
    # ============================================================

    async def phase_wait_and_select(self):
        """实时倒计时等待，到点一键选课"""
        print("\n" + "=" * 50)
        print("阶段二：等待窗口并一键选课")
        print("=" * 50)

        if not self.found_courses:
            print("未匹配到任何课程，无法选课")
            return

        # 优先使用服务器返回的选课开始时间
        params = self.client._page_params or {}
        server_open = params.get("xkkssj", "")
        if server_open:
            try:
                open_dt = datetime.strptime(server_open, "%Y-%m-%d %H:%M:%S")
                print(f"选课窗口(服务器): {server_open}")
            except ValueError:
                open_dt = self.config.window_open_dt
        else:
            open_dt = self.config.window_open_dt
            print(f"选课窗口(配置): {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        server_now = params.get("server_now", "")
        if server_now:
            print(f"服务器时间: {server_now}")

        print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"\n待选课程:")
        for c in self.found_courses:
            print(f"  {c['jxbbh']} — {c['kcmc']} (jxb_id={c['jxb_id'][:20]}...)")

        now = datetime.now()
        if now >= open_dt:
            print("\n窗口已到，立即选课！")
        else:
            wait_sec = (open_dt - now).total_seconds()
            print(f"\n还需等待: {_format_countdown(wait_sec)}")
            print()
            print("实时倒计时（按 Ctrl+C 可安全退出）:")
            print("-" * 45)

            try:
                while True:
                    now = datetime.now()
                    remaining = (open_dt - now).total_seconds()
                    if remaining <= 0:
                        break
                    sys.stdout.write(_countdown_line(open_dt))
                    sys.stdout.flush()
                    await asyncio.sleep(0.5)
                print()
            except KeyboardInterrupt:
                print("\n\n用户中断，退出等待。")
                return

        print("\n[!] 窗口已到，开始一键选课...")
        await self._select_with_retry()

    async def _select_with_retry(self):
        """带重试的一键选课，时间未到则持续轮询（最多 5 分钟）"""
        jxb_ids = ",".join(c["jxb_id"] for c in self.found_courses)

        retry_count = 0
        max_retries = self.config.max_retries
        max_time_retries = 300  # 时间未到时最多重试 300 次（约 5 分钟）

        while True:
            retry_count += 1
            try:
                print(f"\n  选课尝试 {retry_count}")
                result = await asyncio.to_thread(
                    self.client.quick_select, jxb_ids
                )
                self._handle_result(result)
                return  # 成功
            except ApiError as e:
                msg = str(e)
                if ("时间" in msg or "不可选课" in msg) and retry_count < max_time_retries:
                    if retry_count % 10 == 1:
                        print(f"  时间未到，持续轮询中... (已尝试 {retry_count} 次)")
                    await asyncio.sleep(1)
                    continue
                print(f"  异常: {e}")
                if retry_count < max_retries:
                    print(f"  {self.config.retry_delay}s 后重试...")
                    await asyncio.sleep(self.config.retry_delay)
                    if not self.client.check_session():
                        print("  Session 过期，重新登录...")
                        try:
                            self.client.login()
                            self.client.fetch_select_page()
                        except Exception as le:
                            print(f"  重新登录失败: {le}")
                else:
                    break
            except Exception as e:
                print(f"  未知错误: {e}")
                traceback.print_exc()
                if retry_count < max_retries:
                    await asyncio.sleep(self.config.retry_delay)

        print("\n[!] 选课失败，请手动登录教务系统操作")

    def _handle_result(self, result: dict):
        """处理选课 API 返回结果"""
        if isinstance(result, dict):
            flag = result.get("flag")
            msg = result.get("msg", "")

            if flag == "1":
                print(f"  [成功] {msg}")
            elif "不可选课" in msg or "时间" in msg:
                raise ApiError(f"时间校验失败: {msg}")
            elif "异常" in msg:
                raise ApiError(f"服务器异常: {msg}")
            else:
                print(f"  服务器返回: flag={flag}, msg={msg}")
        else:
            print(f"  原始响应: {result}")

    # ============================================================
    # 主流程
    # ============================================================

    async def run(self, do_find: bool = True):
        """完整流程：登录匹配 → 等待 → 一键选课"""
        try:
            if do_find:
                for retry in range(self.config.max_retries):
                    try:
                        self.phase_login_and_find()
                        if self.found_courses:
                            break
                    except (ApiError, LoginError) as e:
                        print(f"阶段一出错: {e}")
                        if retry < self.config.max_retries - 1:
                            print(f"  {self.config.retry_delay}s 后重试...")
                            time.sleep(self.config.retry_delay)
            else:
                # 跳过查找，假设手动指定了 jxb_id
                self.client.login()
                self.client.fetch_select_page()
                print("\n[跳过查找，使用预设课程]")

            if not self.found_courses:
                print("\n[!] 未找到任何目标课程，无法继续选课")
                return

            await self.phase_wait_and_select()
        finally:
            self.client.close()
            self._print_summary()

    def _print_summary(self):
        print(f"\n{'=' * 50}")
        print("结果")
        print(f"{'=' * 50}")
        for c in self.config.target_courses:
            found = any(f["jxbbh"] == c["jxbbh"] for f in self.found_courses)
            status = "已匹配" if found else "未匹配"
            print(f"  [{status}] {c['jxbbh']}")
