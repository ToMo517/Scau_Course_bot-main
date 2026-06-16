# 华农教务系统自动选课

华南农业大学正方教务系统 V9.0 自动选课工具。纯 HTTP 请求方案，无需浏览器，通过 RSA 加密登录后直接调用 Quickly API 一键选课。

项目仅供学习参考，出现任何问题后果自负。

## 借鉴项目

- [new-school-sdk](https://github.com/FarmerChillax/new-school-sdk) — 参考了 `requests.Session` 会话管理、RSA 密码加密（PyRsa 模块）以及正方教务系统的登录流程
- [PKUAutoElective](https://github.com/zhongxinghong/PKUAutoElective) — 参考了 HTTP 客户端设计模式、Session cookie 持久化以及选课 API 的调用方式

## 原理

1. 直接 HTTP POST 登录教务系统（RSA 加密密码），获取 Session
2. 通过 PartDisplay API 查询所有可选课程，按教学班编号精确匹配目标
3. 提取服务器选课开始时间，实时倒计时
4. 窗口开启时调用 Quickly API 一键选课（支持多门课程批量提交）

相比 CDP（Chrome DevTools Protocol）方案，无需启动浏览器、无需 WebSocket，更稳定、更快速。

## 环境要求

- Python 3.10+
- 能访问华农教务系统的网络环境（校园网或 VPN）

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### 图形界面（推荐）

```bash
python gui.py
```

操作流程：

1. 输入学号密码，点击「登录」
2. 点击「扫描课程」获取全部可选课程
3. 双击或手动输入添加目标课程
4. 设置选课时间，点击「开始监控」
5. 到点自动调用 quick_select 一键选课

### 命令行

修改 `course_bot/config.py` 中的配置项：

```python
student_id: str = "你的学号"
password: str = "你的密码"

target_courses: list = field(default_factory=lambda: [
    {"jxbbh": "202620271-610023-001-乒乓球02", "kklxdm": "06"},
    {"jxbbh": "202620271-604792-005",         "kklxdm": "09"},
])

base_url: str = "https://jwzf.scau.edu.cn"
window_open: str = "2026-06-18 12:29:55"
```

```bash
python -m course_bot.main
python -m course_bot.main --window "2026-06-18 12:29:55"
```

> **如何找到 jxbbh？** 在教务系统"自主选课"页面搜索目标课程，课程名称旁边的编号即为 jxbbh。

## 注意事项

- 脚本仅在本地运行，不经过第三方服务器
- 选课时间以服务器端为准
- 校园网环境建议使用内网地址 `http://10.42.100.1` 以获得更低延迟
- 按 `Ctrl+C` 可安全退出

## License

MIT

---

created by yuanarcsin、ToMo
