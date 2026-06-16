#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
华农教务系统自动选课工具 v5.0 — 图形界面版
纯 HTTP 后端，基于 Scau_Course_bot
流程: 登录 → 匹配课程 → 倒计时 → quick_select（一键选课）
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading, queue, time, sys, os, re, json, asyncio, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from course_bot.config import Config
from course_bot.client import Client, ApiError, LoginError
from course_bot.course import CourseBot, _format_countdown

# Tkinter 根窗口引用（用于线程安全的 UI 更新）
_root_ref = None


def ui_call(fn):
    """在主线程执行 UI 更新"""
    if _root_ref:
        _root_ref.after(0, fn)


# ============================================================
# 后端适配层
# ============================================================

class GrabberBackend:
    """封装 course_bot 的操作，提供线程安全的接口"""

    def __init__(self, log_queue: queue.Queue):
        self.lq = log_queue
        self.config = Config()
        self.client: Client | None = None
        self.bot: CourseBot | None = None
        self._running = False
        self._server_time_offset: float = 0.0  # 服务器-客户端时间差

    def _log(self, msg: str):
        self.lq.put(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def stop(self):
        self._running = False

    # ---- 登录 ----
    def do_login(self, sid: str, pwd: str):
        """同步登录，返回 (ok, error_msg)"""
        self.config.student_id = sid
        self.config.password = pwd
        self.client = Client(self.config)
        try:
            self.client.login()
            self._log("登录成功")
            # 读取服务器时间
            self._sync_server_time()
            return True, ""
        except LoginError as e:
            self._log(f"登录失败: {e}")
            return False, str(e)
        except Exception as e:
            self._log(f"登录异常: {e}")
            return False, str(e)

    def _sync_server_time(self):
        """从 HTTP 响应头同步服务器时间"""
        try:
            resp = self.client.get("/jwglxt/xtgl/login_slogin.html")
            date_str = resp.headers.get("Date", "")
            if date_str:
                server_dt = datetime.strptime(date_str,
                    "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                local_dt = datetime.now(timezone.utc)
                self._server_time_offset = (server_dt - local_dt).total_seconds()
                self._log(f"服务器时间偏移: {self._server_time_offset:+.1f}s")
        except Exception:
            self._server_time_offset = 0.0

    def get_server_time(self) -> datetime:
        return datetime.now() + timedelta(seconds=self._server_time_offset)

    # ---- 课程扫描 ----
    def scan_courses(self) -> list:
        """扫描所有可选课程，返回课程列表"""
        if not self.client or not self.client._logged_in:
            raise RuntimeError("未登录")

        params = self.client.fetch_select_page()
        tabs = params.get("_tabs", {})
        all_courses = []
        seen = set()

        for kklxdm, tab_info in tabs.items():
            try:
                courses = self.client.query_courses(tab_info, params)
                self._log(f"Tab {kklxdm}: {len(courses)} 门")
                for c in courses:
                    key = c.get("jxbmc", "")
                    if key and key not in seen:
                        seen.add(key)
                        all_courses.append(c)
            except Exception as e:
                self._log(f"Tab {kklxdm} 查询失败: {e}")
            time.sleep(0.3)

        self._log(f"扫描完成: {len(all_courses)} 门")
        return all_courses

    # ---- 匹配课程 ----
    def match_courses(self, jxbh_list: list, all_courses: list) -> list:
        """从课程列表中匹配目标课程，返回 [{jxbbh, jxb_id, kcmc, ...}]"""
        found = []
        for jxbbh in jxbh_list:
            for c in all_courses:
                if c.get("jxbmc") == jxbbh:
                    found.append({
                        "jxbbh": jxbbh,
                        "jxb_id": c["jxb_id"],
                        "kch_id": c.get("kch_id", ""),
                        "kcmc": c.get("kcmc", ""),
                        "jxbzls": c.get("jxbzls", "1"),
                        "jxbrs": c.get("jxbrs", "?"),
                        "jxbrl": c.get("jxbrl", "?"),
                    })
                    break
            else:
                self._log(f"未找到: {jxbbh}")
        self._log(f"匹配: {len(found)}/{len(jxbh_list)} 门")
        return found

    # ---- 选课 ----
    def do_quick_select(self, found_courses: list) -> dict:
        """一键选课"""
        if not found_courses:
            return {"flag": "0", "msg": "无课程"}
        jxb_ids = ",".join(c["jxb_id"] for c in found_courses)
        return self.client.quick_select(jxb_ids)

    def do_cart_fallback(self, found_courses: list) -> str:
        """回退：加购物车"""
        results = []
        params = self.client._page_params or {}
        for c in found_courses:
            try:
                r = self.client.post(
                    "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html",
                    data={
                        "jxb_ids": c["jxb_id"],
                        "kch_id": c.get("kch_id", ""),
                        "kcmc": c.get("kcmc", ""),
                        "rwlx": params.get("rwlx", "3"),
                        "rlkz": params.get("rlkz", "0"),
                        "rlzlkz": params.get("rlzlkz", "1"),
                        "cdrlkz": params.get("cdrlkz", "0"),
                        "xxkbj": "0", "qz": "0", "cxbj": "0",
                        "xkkz_id": params.get("firstXkkzId", ""),
                        "njdm_id": params.get("njdm_id", ""),
                        "zyh_id": params.get("zyh_id", ""),
                        "kklxdm": params.get("firstKklxdm", "06"),
                        "xklc": params.get("xklc", "1"),
                        "xkxnm": params.get("xkxnm", "2026"),
                        "xkxqm": params.get("xkxqm", "3"),
                    })
                results.append(str(r.json() if r.ok else r.text))
            except Exception as e:
                results.append(str(e))
        return "; ".join(results)

    # ---- 主流程 ----
    def run_grab(self, found_courses: list, target_time_str: str):
        """后台线程：等待 → 选课"""
        try:
            asyncio.run(self._async_grab(found_courses, target_time_str))
        except Exception as e:
            self._log(f"错误: {e}")
            traceback.print_exc()

    async def _async_grab(self, found_courses: list, target_time_str: str):
        self._running = True
        target_dt = datetime.strptime(target_time_str, "%Y-%m-%d %H:%M:%S")

        # 服务器时间修正
        server_now = self.get_server_time()
        adjusted_target = target_dt - timedelta(seconds=self._server_time_offset)
        self._log(f"目标: {target_dt.strftime('%H:%M:%S')}, "
                  f"服务器偏移: {self._server_time_offset:+.1f}s")

        # 预检
        self._log("预检: 验证 API...")
        try:
            self.client.get(f"/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html?"
                           f"gnmkdm={self.config.gnmkdm}&layout=default")
            self._log("预检通过: Session 有效")
        except Exception as e:
            self._log(f"预检异常: {e}, 尝试重登...")
            try:
                self.client.login()
                self.client.fetch_select_page()
                self._log("重登成功")
            except Exception as le:
                self._log(f"重登失败: {le}, 继续尝试")

        # 倒计时
        self._log(f"开始倒计时, 剩余={(adjusted_target - datetime.now()).total_seconds():.0f}s")
        lead_seconds = 5
        while self._running:
            remaining = (adjusted_target - datetime.now()).total_seconds()
            if remaining <= 0.1:
                self._log(f"倒计时结束 (remaining={remaining:.3f}s)")
                break
            if remaining <= lead_seconds:
                if int(remaining * 10) % 10 == 0:
                    self._log(f"高频: {remaining:.1f}s")
                await asyncio.sleep(0.05)
            else:
                if int(remaining) % 10 == 0:
                    self.lq.put(f"COUNTDOWN|{remaining:.0f}")
                await asyncio.sleep(2)

        if not self._running:
            self._log("用户停止")
            return

        # 直接一键选课（quick_select 自带时间校验，时间不到自动重试）
        self._log("一键选课 (quick_select)...")

        for attempt in range(self.config.max_retries * 20):
            if not self._running:
                self._log("用户停止")
                return
            try:
                result = self.do_quick_select(found_courses)
                if isinstance(result, dict):
                    flag = str(result.get("flag", ""))
                    msg = str(result.get("msg", ""))
                    if flag == "1":
                        self._log(f"[OK] 选课成功: {msg}")
                        self._log("请到教务系统确认选课结果")
                        self._running = False
                        return
                    elif "不可选课" in msg or "时间" in msg:
                        if attempt == 0:
                            self._log(f"  时间未到，轮询等待...")
                        elif attempt % 10 == 0:
                            self._log(f"  等待中... (第{attempt+1}次)")
                    else:
                        self._log(f"  返回: flag={flag}, msg={msg}")
                else:
                    self._log(f"  返回: {str(result)[:200]}")
            except Exception as e:
                if attempt % 10 == 0:
                    self._log(f"  异常: {e}")

            await asyncio.sleep(0.05)

        self._log("选课超时，请手动登录确认")
        self._running = False


# ============================================================
# GUI
# ============================================================

class App:
    def __init__(self):
        global _root_ref
        self.root = tk.Tk()
        _root_ref = self.root
        self.root.title("华农教务系统自动选课工具 v5.0 (纯HTTP)")
        self.root.geometry("1050x750")
        self.root.minsize(900, 600)

        self.lq = queue.Queue()
        self.backend = GrabberBackend(self.lq)
        self.bg_thread = None
        self.monitoring = False

        # 数据
        self.all_courses: list = []       # API 扫描到的所有课程
        self.target_courses: list = []    # 目标课程 (List[str] of jxbh)
        self.matched_courses: list = []   # 匹配到的完整信息
        self._logged_in = False
        self._cached_target = datetime(2099, 1, 1)
        self.show_countdown = tk.BooleanVar(value=True)

        self._build()
        self._poll_log()
        self._cd_tick()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ========== UI ==========

    def _build(self):
        m = ttk.Frame(self.root, padding="8")
        m.pack(fill=tk.BOTH, expand=True)

        # ---- 登录 ----
        lf = ttk.LabelFrame(m, text="登录", padding="5")
        lf.pack(fill=tk.X, pady=(0, 5))
        r0 = ttk.Frame(lf)
        r0.pack(fill=tk.X)
        ttk.Label(r0, text="学号:").pack(side=tk.LEFT, padx=(0, 3))
        self.sid_var = tk.StringVar()
        ttk.Entry(r0, textvariable=self.sid_var, width=16).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(r0, text="密码:").pack(side=tk.LEFT, padx=(0, 3))
        self.pwd_var = tk.StringVar()
        ttk.Entry(r0, textvariable=self.pwd_var, width=16, show="*").pack(side=tk.LEFT, padx=(0, 10))
        self.login_btn = ttk.Button(r0, text="登录", command=self._login, width=6)
        self.login_btn.pack(side=tk.LEFT, padx=5)
        self._status_var = tk.StringVar(value="未登录")
        ttk.Label(r0, textvariable=self._status_var, foreground="gray").pack(side=tk.LEFT, padx=(10, 0))
        self._srv_var = tk.StringVar(value="服务器时间: --")
        ttk.Label(r0, textvariable=self._srv_var, foreground="blue").pack(side=tk.RIGHT)

        # ---- 课程 ----
        cf = ttk.LabelFrame(m, text="课程", padding="5")
        cf.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # 左侧：课程列表 + 搜索
        left = ttk.Frame(cf)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 扫描 + 搜索栏
        tr = ttk.Frame(left)
        tr.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(tr, text="扫描课程", command=self._scan).pack(side=tk.LEFT, padx=2)
        ttk.Label(tr, text="  搜索:").pack(side=tk.LEFT, padx=(10, 2))
        self._sv2 = tk.StringVar()
        ttk.Entry(tr, textvariable=self._sv2, width=20).pack(side=tk.LEFT)
        self._sv2.trace_add("write", lambda *_: self._filter())

        # 课程表格
        tf = ttk.Frame(left)
        tf.pack(fill=tk.BOTH, expand=True)
        cols = ("sel", "jxbh", "teacher", "time", "cap", "name")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings",
                                 selectmode="extended", height=10)
        for c, w in [("sel", 30), ("jxbh", 200), ("teacher", 75),
                     ("time", 120), ("cap", 55), ("name", 160)]:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="center" if c == "sel" else "w")
        sc = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sc.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._toggle)
        self.tree.tag_configure("u", background="white")
        self.tree.tag_configure("s", background="#90EE90")

        # 右侧：目标课程 + 手动添加
        right = ttk.Frame(cf, width=280)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)

        # 手动添加
        af = ttk.LabelFrame(right, text="手动添加课程", padding="5")
        af.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(af, text="教学班编号:").pack(anchor=tk.W)
        ar = ttk.Frame(af)
        ar.pack(fill=tk.X, pady=(3, 0))
        self._add_var = tk.StringVar()
        ttk.Entry(ar, textvariable=self._add_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        ttk.Button(ar, text="添加", width=4, command=self._add_course).pack(side=tk.LEFT)

        # 目标课程列表
        tf2 = ttk.LabelFrame(right, text="目标课程", padding="5")
        tf2.pack(fill=tk.BOTH, expand=True)
        self._target_list = tk.Listbox(tf2, height=8, font=("Consolas", 10))
        ts = ttk.Scrollbar(tf2, orient="vertical", command=self._target_list.yview)
        self._target_list.configure(yscrollcommand=ts.set)
        self._target_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(right, text="删除选中", command=self._del_course).pack(anchor=tk.E, pady=(3, 0))
        ttk.Button(right, text="清空全部", command=self._clear_courses).pack(anchor=tk.E, pady=(2, 0))

        # 从表格导入
        ttk.Button(right, text="<< 导入勾选课程", command=self._import_selected).pack(
            anchor=tk.W, pady=(10, 0))

        # ---- 选课控制 ----
        ctrl = ttk.LabelFrame(m, text="选课控制", padding="5")
        ctrl.pack(fill=tk.X, pady=(0, 5))
        r2 = ttk.Frame(ctrl)
        r2.pack(fill=tk.X)
        ttk.Label(r2, text="时间:").pack(side=tk.LEFT, padx=(0, 3))
        self._dv = tk.StringVar(value="2026-06-18")
        self._tv = tk.StringVar(value="12:30:00")
        self._dv.trace_add("write", lambda *_: self._refresh_tgt())
        self._tv.trace_add("write", lambda *_: self._refresh_tgt())
        ttk.Entry(r2, textvariable=self._dv, width=11).pack(side=tk.LEFT, padx=2)
        ttk.Entry(r2, textvariable=self._tv, width=9).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(r2, text="倒计时", variable=self.show_countdown).pack(side=tk.LEFT, padx=(15, 3))
        self._cd_var = tk.StringVar(value="--:--:--")
        ttk.Label(r2, textvariable=self._cd_var, font=("Consolas", 13, "bold"),
                  foreground="blue").pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(r2, text="模式: quick_select 一键选课").pack(side=tk.LEFT, padx=(10, 0))
        self._sbtn = ttk.Button(r2, text="开始监控", command=self._start, width=9)
        self._sbtn.pack(side=tk.RIGHT, padx=3)
        self._tbtn = ttk.Button(r2, text="停止", command=self._stop, width=5, state=tk.DISABLED)
        self._tbtn.pack(side=tk.RIGHT, padx=3)

        # ---- 日志 ----
        logf = ttk.LabelFrame(m, text="日志", padding="5")
        logf.pack(fill=tk.BOTH, expand=True)
        self._log_widget = tk.Text(logf, wrap=tk.WORD, state=tk.DISABLED,
                                   font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        ls = ttk.Scrollbar(logf, orient="vertical", command=self._log_widget.yview)
        self._log_widget.configure(yscrollcommand=ls.set)
        self._log_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

        # 底部栏
        bottom = ttk.Frame(m)
        bottom.pack(fill=tk.X)
        self._st_bar = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self._st_bar, relief=tk.SUNKEN, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(bottom, text="created by yuanarcsin、ToMo  ", relief=tk.SUNKEN).pack(side=tk.RIGHT)

    # ========== 日志 & 倒计时 ==========

    def _poll_log(self):
        try:
            while True:
                msg = self.lq.get_nowait()
                if isinstance(msg, str):
                    if msg.startswith("COUNTDOWN|"):
                        r = float(msg.split("|")[1])
                        h, m, s = int(r // 3600), int((r % 3600) // 60), int(r % 60)
                        self._cd_var.set(f"{h:02d}:{m:02d}:{s:02d}")
                    elif msg.startswith("SCAN_RESULT|"):
                        self._populate_table(json.loads(msg.split("|", 1)[1]))
                    else:
                        self._w(msg)
        except queue.Empty:
            pass
        self._sbtn.configure(state=tk.DISABLED if self.monitoring else tk.NORMAL)
        self._tbtn.configure(state=tk.NORMAL if self.monitoring else tk.DISABLED)
        self.root.after(200, self._poll_log)

    def _cd_tick(self):
        if not self.show_countdown.get():
            self._cd_var.set("--:--:--")
        else:
            r = (self._cached_target - datetime.now()).total_seconds()
            if r < 0:
                self._cd_var.set("已到点!")
            else:
                h, m, s = int(r // 3600), int((r % 3600) // 60), int(r % 60)
                self._cd_var.set(f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(500, self._cd_tick)

    def _refresh_tgt(self):
        try:
            self._cached_target = datetime.strptime(
                f"{self._dv.get()} {self._tv.get()}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            self._cached_target = datetime(2099, 1, 1)

    def _w(self, s):
        self._log_widget.configure(state=tk.NORMAL)
        self._log_widget.insert(tk.END, s + "\n")
        self._log_widget.see(tk.END)
        self._log_widget.configure(state=tk.DISABLED)

    # ========== 登录 ==========

    def _login(self):
        sid = self.sid_var.get().strip()
        pwd = self.pwd_var.get().strip()
        if not sid or not pwd:
            messagebox.showwarning("提示", "请输入学号和密码")
            return
        self.login_btn.configure(state=tk.DISABLED, text="登录中...")
        self._status_var.set("登录中...")

        def do():
            ok, err = self.backend.do_login(sid, pwd)
            if ok:
                self._logged_in = True
                srv = self.backend.get_server_time().strftime('%Y-%m-%d %H:%M:%S')
                ui_call(lambda: self._status_var.set("已登录"))
                ui_call(lambda: self._srv_var.set(f"服务器: {srv}"))
                ui_call(lambda: self.login_btn.configure(text="登录", state=tk.NORMAL))
            else:
                ui_call(lambda: self._status_var.set(f"失败: {err[:30]}"))
                ui_call(lambda: self.login_btn.configure(text="登录", state=tk.NORMAL))
                ui_call(lambda: messagebox.showerror("登录失败", err))

        threading.Thread(target=do, daemon=True).start()

    # ========== 课程扫描 ==========

    def _scan(self):
        if not self._logged_in:
            messagebox.showwarning("提示", "请先登录")
            return
        self._st_bar.set("正在扫描课程...")

        def do():
            try:
                courses = self.backend.scan_courses()
                self.lq.put(f"SCAN_RESULT|{json.dumps(courses, ensure_ascii=False)}")
            except Exception as e:
                self.lq.put(f"扫描失败: {e}")

        threading.Thread(target=do, daemon=True).start()

    def _populate_table(self, courses: list):
        self.all_courses = courses
        self.tree.delete(*self.tree.get_children())
        if courses:
            self._w(f"[DEBUG] API字段: {list(courses[0].keys())}")
        for c in courses:
            jxbh = c.get("jxbmc", "?")
            # PartDisplay API 不返回教师/时间/容量，只有课程名和学分
            name = (c.get("kcmc") or "")[:80]
            credit = c.get("xf", "?")
            self.tree.insert("", tk.END, values=(
                "", jxbh, f"学分{credit}", "", "", name.strip()), tags=("u",))
        self._st_bar.set(f"扫描完成: {len(courses)} 门课程")

    def _toggle(self, e):
        it = self.tree.focus()
        if not it: return
        v = self.tree.item(it, "values")
        self.tree.item(it, values=(("", "✓")[v[0] == ""],) + v[1:],
                       tags=(("u", "s")[v[0] == ""]))

    def _filter(self):
        kw = self._sv2.get().lower()
        for it in self.tree.get_children():
            v = self.tree.item(it, "values")
            if kw == "" or any(kw in str(x).lower() for x in v[1:]):
                try: self.tree.reattach(it, "", 0)
                except: pass
            else: self.tree.detach(it)

    # ========== 目标课程管理 ==========

    def _add_course(self):
        jxbh = self._add_var.get().strip()
        if not jxbh:
            return
        if jxbh in self.target_courses:
            messagebox.showinfo("提示", "该课程已在列表中")
            return
        self.target_courses.append(jxbh)
        self._target_list.insert(tk.END, jxbh)
        self._add_var.set("")
        self._w(f"手动添加: {jxbh}")

    def _del_course(self):
        sel = self._target_list.curselection()
        if not sel: return
        idx = sel[0]
        removed = self.target_courses.pop(idx)
        self._target_list.delete(idx)
        self._w(f"已移除: {removed}")

    def _clear_courses(self):
        self.target_courses.clear()
        self._target_list.delete(0, tk.END)

    def _import_selected(self):
        added = 0
        for it in self.tree.get_children():
            v = self.tree.item(it, "values")
            if v[0] == "✓" and v[1] not in self.target_courses:
                self.target_courses.append(v[1])
                self._target_list.insert(tk.END, v[1])
                added += 1
        self._w(f"导入 {added} 门课程")

    # ========== 监控 ==========

    def _start(self):
        if not self._logged_in:
            messagebox.showwarning("提示", "请先登录")
            return
        if not self.target_courses:
            messagebox.showwarning("提示", "请先添加目标课程")
            return
        tt = f"{self._dv.get()} {self._tv.get()}"
        try:
            datetime.strptime(tt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            messagebox.showerror("错误", "时间格式不正确")
            return

        # 匹配课程
        self._st_bar.set("正在匹配课程...")
        self.matched_courses = self.backend.match_courses(
            self.target_courses, self.all_courses)
        if not self.matched_courses:
            # 如果还没扫描，尝试现在扫描
            self._w("课程列表为空，正在扫描...")
            try:
                self.all_courses = self.backend.scan_courses()
                self._populate_table(self.all_courses)
                self.matched_courses = self.backend.match_courses(
                    self.target_courses, self.all_courses)
            except Exception as e:
                messagebox.showerror("错误", f"扫描失败: {e}")
                return
        if not self.matched_courses:
            messagebox.showwarning("提示", "未匹配到任何目标课程，请确认教学班编号正确")
            return

        self.monitoring = True
        self._st_bar.set(f"监控中 — {tt}")
        self._w(f"开始监控 {len(self.matched_courses)} 门课程: "
                f"{', '.join(c['jxbbh'] for c in self.matched_courses)}")
        self._w(f"目标时间: {tt}")

        self.bg_thread = threading.Thread(
            target=self.backend.run_grab,
            args=(self.matched_courses, tt),
            daemon=True)
        self.bg_thread.start()

    def _stop(self):
        self.backend.stop()
        self.monitoring = False
        self._st_bar.set("已停止")
        self._w("已停止")

    def _on_close(self):
        if self.monitoring:
            if not messagebox.askyesno("确认", "监控中，确定退出？"):
                return
            self.backend.stop()
        if self.backend.client:
            try:
                self.backend.client.close()
            except:
                pass
        self.root.destroy()


if __name__ == "__main__":
    app = App()
    app.root.mainloop()
