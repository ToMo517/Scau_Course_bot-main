"""
通信层 —— 基于 requests.Session 的直接 HTTP 请求，无 CDP 依赖。
"""

import re, time
from io import StringIO

import requests
from bs4 import BeautifulSoup

from course_bot.config import Config
from .PyRsa import RsaKey, Base64


class ApiError(Exception):
    """API 调用异常"""
    pass


class LoginError(Exception):
    """登录异常"""
    pass


# ================================================================
# HTTP 客户端
# ================================================================

class Client:
    """教务系统 HTTP 客户端，基于 requests.Session 保持登录态"""

    # 正方教务系统固定端点
    LOGIN_PAGE      = "/jwglxt/xtgl/login_slogin.html"
    PUBLIC_KEY      = "/jwglxt/xtgl/login_getPublicKey.html"
    INDEX_PAGE      = "/jwglxt/xtgl/index_initMenu.html"

    # 选课相关端点
    XK_INDEX        = "/jwglxt/xsxk/zzxkyzbjk_cxZzxkYzbIndex.html"
    XK_DISPLAY      = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbDisplay.html"
    XK_PART_DISPLAY = "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html"
    XK_CHECK_CART   = "/jwglxt/xsxk/zzxkyzb_cxCheckZyZzxkYzbInCart.html"
    XK_ADD_CART     = "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html"
    XK_QUERY_CART   = "/jwglxt/xsxk/zzxkyzb_cxWdgwcZzxkYzb.html"
    XK_QUICKLY      = "/jwglxt/xsxk/zzxkyzb_xkZzxkyzbQuickly.html"
    XK_SUBMIT       = "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html"

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        self._csrf: str | None = None
        self._page_params: dict | None = None
        self._logged_in: bool = False

    # ============================================================
    # 登录
    # ============================================================

    def login(self) -> bool:
        """登录教务系统"""
        base = self.config.base_url
        print(f"[登录] 连接 {base} ...")

        # Step 1: 获取登录页，提取 CSRF token
        url = f"{base}{self.LOGIN_PAGE}"
        resp = self.session.get(url, timeout=self.config.request_timeout)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        csrf_input = soup.select_one("#csrftoken")
        if not csrf_input:
            raise LoginError("未找到 CSRF token，检查登录页是否正常")
        self._csrf = csrf_input.get("value", "")
        print(f"[登录] CSRF: {self._csrf[:20]}...")

        # Step 2: 获取 RSA 公钥
        t = int(time.time() * 1000)
        key_url = f"{base}{self.PUBLIC_KEY}?time={t}&_={t}"
        key_resp = self.session.get(key_url, timeout=self.config.request_timeout)
        key_data = key_resp.json()
        modulus = key_data.get("modulus", "")
        exponent = key_data.get("exponent", "")
        if not modulus or not exponent:
            raise LoginError(f"获取公钥失败: {key_data}")

        # Step 3: RSA 加密密码
        b64 = Base64()
        rsa = RsaKey()
        rsa.set_public(b64.b64tohex(modulus), b64.b64tohex(exponent))
        encrypted = rsa.rsa_encrypt(self.config.password)
        mm = b64.hex2b64(encrypted)

        # Step 4: 提交登录
        login_url = f"{base}{self.LOGIN_PAGE}?time={t}"
        login_resp = self.session.post(
            login_url,
            data={
                "csrftoken": self._csrf,
                "yhm": self.config.student_id,
                "mm": mm,
                "language": "zh_CN",
            },
            timeout=self.config.request_timeout,
            allow_redirects=True,
        )
        login_resp.encoding = "utf-8"

        if self._check_logged_in(login_resp.text):
            self._logged_in = True
            print("[登录] 成功")
            return True

        err_soup = BeautifulSoup(login_resp.text, "html.parser")
        tips = err_soup.select_one("#tips")
        err_msg = tips.text.strip() if tips else "未知错误"
        raise LoginError(f"登录失败: {err_msg}")

    def _check_logged_in(self, html: str) -> bool:
        if f'value="{self.config.student_id}"' in html:
            return True
        if 'id="tips"' not in html:
            return True
        return False

    def check_session(self) -> bool:
        """检查 session 是否有效"""
        try:
            url = f"{self.config.base_url}{self.INDEX_PAGE}"
            resp = self.session.get(url, timeout=self.config.request_timeout)
            return "login" not in resp.url.lower()
        except Exception:
            return False

    # ============================================================
    # 页面获取与参数提取
    # ============================================================

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{path}"

    def get(self, path: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.config.request_timeout)
        return self.session.get(self._url(path), **kwargs)

    def post(self, path: str, data=None, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.config.request_timeout)
        return self.session.post(self._url(path), data=data, **kwargs)

    def fetch_select_page(self) -> dict:
        """获取选课主页面，提取隐藏参数和 tab 信息"""
        url = (f"{self.XK_INDEX}?"
               f"gnmkdm={self.config.gnmkdm}&layout=default&"
               f"su={self.config.student_id}")
        resp = self.get(url)
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        def gv(name: str) -> str:
            el = soup.find(attrs={"id": name}) or soup.find(attrs={"name": name})
            return el.get("value", "") if el else ""

        # 服务器时间
        server_now = gv("currentsj")  # 服务器当前时间
        xkkssj = gv("xkkssj")         # 选课开始时间
        xkjssj = gv("xkjssj")         # 选课结束时间

        # 基础页面参数
        params = {
            "xkkssj":  xkkssj,
            "xkjssj":  xkjssj,
            "server_now": server_now,
            "xkxnm":   gv("xkxnm") or "2026",
            "xkxqm":   gv("xkxqm") or "3",
            "xklc":    gv("xklc") or "1",
            "rwlx":    gv("rwlx") or "3",
            "rlkz":    gv("rlkz") or "0",
            "rlzlkz":  gv("rlzlkz") or "1",
            "cdrlkz":  gv("cdrlkz") or "0",
            "xszxzt":  gv("xszxzt") or "1",
            "xqh_id":  gv("xqh_id") or "3",
            "jg_id":   gv("jg_id_1") or "14",
            "njdm_id": gv("njdm_id") or "2025",
            "zyh_id":  gv("zyh_id") or "",
            "zyfx_id": gv("zyfx_id") or "wfx",
            "bh_id":   gv("bh_id") or "",
            "xh_id":   gv("xh_id") or "",
            "bklx_id": gv("bklx_id") or "",
            "kzkcgs":  gv("kzkcgs") or "0",
            "sfkkjyxdxnxq": gv("sfkkjyxdxnxq") or "",
        }

        # 默认 tab (第一个 tab)
        params["firstKklxdm"] = gv("firstKklxdm") or "06"
        params["firstXkkzId"] = gv("firstXkkzId") or ""
        params["firstXkkzXh"] = gv("firstXkkzXh") or ""
        params["firstNjdmId"] = gv("firstNjdmId") or params["njdm_id"]
        params["firstZyhId"]  = gv("firstZyhId") or params["zyh_id"]

        # 提取所有 tab 信息 (nav-tabs 中的 tab)
        tabs = {}
        for tab_el in soup.select("a[id^='tab_kklx_']"):
            tab_id = tab_el.get("id", "")
            # tab 的 onclick 包含 queryCourse 调用
            onclick = tab_el.get("onclick", "")
            m = re.search(r"queryCourse\(this,'(\w+)','([^']+)','([^']+)','([^']+)','([^']+)'\)", onclick)
            if m:
                tabs[m.group(1)] = {
                    "kklxdm":  m.group(1),
                    "xkkz_id": m.group(2),
                    "njdm_id": m.group(3),
                    "zyh_id":  m.group(4),
                    "xkkz_xh": m.group(5),
                }

        # 如果没找到 tabs，用 first 信息构造
        if not tabs:
            tabs[params["firstKklxdm"]] = {
                "kklxdm":  params["firstKklxdm"],
                "xkkz_id": params["firstXkkzId"],
                "njdm_id": params["firstNjdmId"],
                "zyh_id":  params["firstZyhId"],
                "xkkz_xh": params["firstXkkzXh"],
            }

        params["_tabs"] = tabs
        self._page_params = params

        # 获取 Display 页面的选课时间窗口
        first_tab = list(tabs.values())[0] if tabs else {
            "kklxdm": "06",
            "xkkz_id": params.get("firstXkkzId", ""),
            "xkkz_xh": params.get("firstXkkzXh", ""),
            "njdm_id": params.get("firstNjdmId", params.get("njdm_id", "")),
            "zyh_id": params.get("firstZyhId", params.get("zyh_id", "")),
        }
        self._fetch_display_time(first_tab)

        print(f"[页面] 学年={params['xkxnm']} 学期={params['xkxqm']} "
              f"轮次={params['xklc']}")
        print(f"[页面] 课程类型 tabs: {list(tabs.keys())}")
        print(f"[页面] 选课窗口: {params.get('xkkssj', '未知')} ~ "
              f"{params.get('xkjssj', '未知')}")
        return params

    def _fetch_display_time(self, tab_info: dict):
        """从 Display 页面提取选课时间窗口"""
        try:
            data = {
                "xkkz_id":  tab_info.get("xkkz_id", ""),
                "kklxdm":   tab_info.get("kklxdm", "06"),
                "njdm_id":  tab_info.get("njdm_id", ""),
                "zyh_id":   tab_info.get("zyh_id", ""),
                "xszxzt":   self._page_params.get("xszxzt", "1"),
                "kspage":   "0",
                "jspage":   "0",
            }
            url = f"{self.XK_DISPLAY}?gnmkdm={self.config.gnmkdm}"
            resp = self.post(url, data=data)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            for fid in ["xkkssj", "xkjssj", "hdmc"]:
                el = soup.find(attrs={"id": fid}) or soup.find(attrs={"name": fid})
                if el and el.get("value"):
                    self._page_params[fid] = el.get("value")
        except Exception:
            pass

    # ============================================================
    # 课程查询
    # ============================================================

    def query_courses(self, tab_info: dict, page_params: dict | None = None) -> list:
        """按课程类型查询可选课程，返回课程列表"""
        pp = dict(page_params or self._page_params or {})
        data = {
            "xkkz_id":  tab_info.get("xkkz_id", pp.get("firstXkkzId", "")),
            "kklxdm":   tab_info.get("kklxdm", "06"),
            "njdm_id":  tab_info.get("njdm_id", pp.get("njdm_id", "")),
            "zyh_id":   tab_info.get("zyh_id", pp.get("zyh_id", "")),
            "xkxnm":    pp.get("xkxnm", ""),
            "xkxqm":    pp.get("xkxqm", ""),
            "xklc":     pp.get("xklc", "1"),
            "rwlx":     pp.get("rwlx", "3"),
            "xszxzt":   pp.get("xszxzt", "1"),
            "xkkz_xh":  tab_info.get("xkkz_xh", ""),
            "xxkbj":    "0",
            "qz":       "0",
            "cxbj":     "0",
            "kspage":   "0",
            "jspage":   "500",
        }
        url = f"{self.XK_PART_DISPLAY}?gnmkdm={self.config.gnmkdm}"
        resp = self.post(url, data=data)
        resp.encoding = "utf-8"
        try:
            result = resp.json()
            return result.get("tmpList", [])
        except Exception:
            return []

    def find_course(self, courses: list, jxbbh: str) -> dict | None:
        """从课程列表中按 jxbmc 精确匹配目标课程"""
        for c in courses:
            if c.get("jxbmc") == jxbbh:
                return {
                    "jxb_id":  c["jxb_id"],
                    "kch_id":  c.get("kch_id", ""),
                    "kch":     c.get("kch", ""),
                    "kcmc":    c.get("kcmc", ""),
                    "jxbzls":  c.get("jxbzls", "1"),
                    "jxbmc":   c.get("jxbmc", ""),
                    "kklxdm":  c.get("kklxdm", ""),
                }
        return None

    # ============================================================
    # 选课 API
    # ============================================================

    def check_in_cart(self, jxb_id: str) -> bool:
        """检查课程是否已在购物车"""
        url = f"{self.XK_CHECK_CART}?gnmkdm={self.config.gnmkdm}"
        resp = self.post(url, data={"jxb_id": jxb_id})
        try:
            return resp.text.strip() == '"2"'
        except Exception:
            return False

    def quick_select(self, jxb_ids: str) -> dict:
        """一键选课（直接提交，跳过购物车）

        Args:
            jxb_ids: 单个 jxb_id 或多个用逗号分隔
        """
        params = self._page_params or {}
        data = {
            "xkkz_id":  params.get("firstXkkzId", ""),
            "jxb_ids":  jxb_ids,
            "kklxdm":   params.get("firstKklxdm", "06"),
            "njdm_id":  params.get("njdm_id", ""),
            "zyh_id":   params.get("zyh_id", ""),
            "xkxnm":    params.get("xkxnm", ""),
            "xkxqm":    params.get("xkxqm", ""),
            "rwlx":     params.get("rwlx", "3"),
            "xklc":     params.get("xklc", "1"),
        }
        url = f"{self.XK_QUICKLY}?gnmkdm={self.config.gnmkdm}"
        resp = self.post(url, data=data)
        try:
            return resp.json()
        except Exception:
            return {"error": resp.text}

    def get_cart_contents(self) -> list:
        """查询购物车内容"""
        url = f"{self.XK_QUERY_CART}?gnmkdm={self.config.gnmkdm}&doType=query"
        resp = self.post(url)
        try:
            return resp.json()
        except Exception:
            return []

    def close(self):
        """关闭会话"""
        self.session.close()
