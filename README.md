# 智慧树刷课工具




---

## 环境安装

```powershell
pip install -r requirements.txt
```

依赖两个包：`pycryptodome`（AES 加解密）、`curl_cffi`（带浏览器指纹的 HTTP 客户端，必需）。

---

## 使用

### 一、登录

```powershell
python zhs.py login
```

终端会出二维码，用**智慧树 App** 扫码。二维码同时存成 `capture/qr_login.png`，终端扫不了就开图片。

> **共享课登录状态保持 24 小时，过期需重扫。** 新形态课程（AI 智慧课）寿命较长。

验证：

```powershell
python zhs.py doctor
```

```
✅ pycryptodome 已安装
✅ curl_cffi    已安装
✅ session.json  含 16 个域
结论：可以开刷
```

### 二、刷课（交互式）

```powershell
python zhs.py
```

```
  共享课
    [1] 马克思主义基本原理        进度 100.0%    张老师

  新形态课程（AI 智慧课）
    [2] 大学物理(A)II-2026秋冬   学习 70   掌握 100   李老师
    [3] 工程力学B                学习 100  掌握 99    王老师
    [4] 复变函数B-26秋冬         学习 100  掌握 0     赵老师

选择课程序号（q 退出）: 1
  已选：马克思主义基本原理  [共享课]

  [1] 刷视频
  [2] 刷作业（自动）
  [3] 每日签到（规律学习分）
  [4] 全刷（视频 + 作业）

选择操作（q 退出）: 4
  ...
```

**菜单按课程类型自动变化**：

| 课程类型 | 可选操作 |
|---|---|
| 新形态课程 | 刷学习进度 / 刷考试掌握度 / 全刷 |
| 共享课 | 刷视频 / 刷作业（自动） / 每日签到 / 全刷 |

课程列表是**实时抓的** —— 换账号、加退课都会正确反映。

### 三、也可以直接下命令

```powershell
python zhs.py list                  # 只列课程和进度
python zhs.py study  2              # 第 2 门：刷学习进度
python zhs.py exam   3 --rounds 6   # 第 3 门：刷考试掌握度
python zhs.py all    3              # 第 3 门：全刷
python zhs.py video  1              # 第 1 门：刷视频
python zhs.py hw     1              # 第 1 门：刷作业
python zhs.py checkin 1             # 第 1 门：每日签到
python zhs.py login                 # 登录 / 续期
python zhs.py doctor                # 环境自检
```

**序号就是列表里的编号**，也可以用课程名片段（如 `study 大学物理`），但不保证唯一，稳妥用序号。

---

## 滑块验证

共享课做作业时可能要求滑块验证。**本工具不处理滑块** —— 遇到会直接报错：

```
⚠️ 需要过滑块验证 —— 工具暂不处理
   人工做一次即可：打开作业页 → 点一题答案 → 拖动滑块
   验证是【会话级】的，过一次整条会话就通了（等待不会自动解除）
```

过一次之后，同一会话内的 API 调用就全部畅通了。

---

## 目录

```
zhs-toolkit/
├── zhs.py                     ★ 唯一入口
├── README.md                  本文件
├── requirements.txt
├── capture/                   数据（会话、题库、抓包结果）
│   ├── session.json           ★ 会话 cookie
│   ├── my_courses.json        课程列表缓存
│   ├── exam_answers.json      考试题库（新形态课）
│   ├── homework_answers.json  作业题库（共享课）
│   ├── daily_log.json         签到历史
│   └── capture_addon.py       mitmproxy 抓包插件
├── internal/                  内部实现，不用直接运行
│   ├── _课程总览.py
│   ├── _新课程_学习进度.py
│   ├── _新课程_考试掌握度.py
│   ├── _共享课_视频.py
│   ├── _共享课_自动作业.py
│   ├── _共享课_每日习惯分.py
│   ├── _共享课_登录.py
│   └── _共享课作业库.py
└── docs/                      协议逆向分析
```

---

## 免责声明

- 本项目为**协议逆向学习研究**产物，仅供技术交流。
- **禁止商业用途**，禁止倒卖、代刷等牟利行为。
- 使用者自行承担因使用本工具产生的一切后果，作者不对任何滥用行为负责。
