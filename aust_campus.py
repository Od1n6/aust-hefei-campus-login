# -*- coding: utf-8 -*-
"""
安徽理工大学合肥校区 校园网一键连接（图形界面版）
==================================================
门户: http://172.24.34.2:801/eportal/   (Dr.COM / 哆点 eportal)
认证: login_method = 1
通道: 学生移动出口 -> 账号后缀 @hfcmcc   (@hfcmcc = 合肥移动)

注意: 仅适用于【合肥校区】。淮南校区的认证入口与通道枚举均不同, 本项目不适用。

用法:
    双击 exe            -> 打开界面, 点「一键连接」
    exe --silent        -> 不显示界面, 直接静默登录(适合做快捷方式/开机启动)
    exe --check         -> 只检查状态, 不发认证请求
    exe --dry-run       -> 只打印将要发送的请求

账号密码保存在 %APPDATA%\\CampusNetLogin\\config.json,
密码用 Windows DPAPI 加密(只有当前 Windows 用户能解密), 不落明文。
"""
import base64
import ctypes
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = "安徽理工大学合肥校区校园网一键连接"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CampusNetLogin")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
LOG_FILE = os.path.join(APP_DIR, "login.log")
ICON_NAME = "campus_net.ico"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 6
JS_VERSION = "4.2"

# 通道: 显示名 -> 账号后缀(取自门户下发的通道下拉框)
CHANNELS = [
    ("学生移动出口", "@hfcmcc"),
    ("学生电信出口", "@aust"),
    ("访问校内资源", ""),
]

DEFAULT_CONFIG = {
    "portal_host": "172.24.34.2",
    "portal_port": 801,
    "username": "",          # 首次使用请在界面上填写自己的学号
    "password_enc": "",          # DPAPI 加密后的密码(base64)
    "password_plain": "",        # 仅当用户不勾选"记住密码"时临时使用, 不落盘
    "channel_label": "学生移动出口",
    "channel_suffix": "@hfcmcc",
    "remember_password": True,
    "auto_connect": False,
    "close_after_success": False,
}


# =====================================================================
#  配置读写 (密码用 Windows DPAPI 加密)
# =====================================================================
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data, encrypt):
    try:
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        src, _keep = _blob(data)
        dst = _BLOB()
        fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        args = [ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)]
        if not fn(*args):
            return None
        out = ctypes.string_at(dst.pbData, dst.cbData)
        kernel32.LocalFree(dst.pbData)
        return out
    except Exception:
        return None


def enc_password(text):
    if not text:
        return ""
    raw = _dpapi(text.encode("utf-8"), True)
    return base64.b64encode(raw).decode() if raw else ""


def dec_password(token):
    if not token:
        return ""
    try:
        raw = _dpapi(base64.b64decode(token), False)
        return raw.decode("utf-8") if raw else ""
    except Exception:
        return ""


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        data = dict(cfg)
        data.pop("password_plain", None)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# =====================================================================
#  日志
# =====================================================================
_log_sink = None          # GUI 可注册一个回调把日志显示到界面


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if _log_sink:
        try:
            _log_sink(line)
        except Exception:
            pass
    elif sys.stdout is not None:
        try:
            print(line)
        except Exception:
            pass


# =====================================================================
#  网络
# =====================================================================
def http_get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_jsonp(text):
    m = re.search(r"\((\{.*\})\)", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def internet_ok():
    """
    真实外网探测: HTTPS + 证书校验。
    未认证时门户只能劫持出 http 页面、无法伪造合法证书,
    因此不会把"被重定向到登录页"误判为"已联网";
    同时避开校园网免认证白名单域名(msftncsi / captive.apple.com 等)。
    """
    for url in ("https://www.163.com", "https://www.qq.com", "https://www.baidu.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=5) as r:
                body = r.read(1024)
                if r.status == 200 and body and b"Dr.COM" not in body and b"eportal" not in body:
                    return True
        except Exception:
            continue
    return False


def get_status(cfg):
    """返回 (本机校园网IP, 是否已在线, 错误信息)"""
    host = cfg["portal_host"]
    url = "http://%s/drcom/chkstatus?callback=dr1001&jsVersion=%s&v=%d" % (
        host, JS_VERSION, int(time.time()) % 100000)
    try:
        j = parse_jsonp(http_get(url))
    except Exception as e:
        return None, None, str(e)
    if not j:
        return None, None, "门户无响应"
    ip = (j.get("v46ip") or j.get("ss5") or "").strip()
    if not ip or ip == "0.0.0.0":
        ip = _local_ip(host)
    return ip, j.get("result") == 1, ""


def _local_ip(host):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def build_login_url(cfg, ip, password):
    q = urllib.parse.urlencode({
        "callback": "dr1003",
        "login_method": "1",
        "user_account": ",0," + cfg["username"] + cfg.get("channel_suffix", ""),
        "user_password": password,
        "wlan_user_ip": ip,
        "wlan_user_ipv6": "",
        "wlan_user_mac": "000000000000",
        "wlan_ac_ip": "",
        "wlan_ac_name": "",
        "jsVersion": JS_VERSION,
        "v": str(int(time.time() * 1000) % 100000),
        "lang": "zh",
    })
    return "http://%s:%d/eportal/portal/login?%s" % (cfg["portal_host"], cfg["portal_port"], q)


def _is_success(res):
    if not res:
        return False
    if str(res.get("result")) in ("1", "ok") or res.get("ret_code") == 2:
        return True
    msg = str(res.get("msg") or "")
    return "已经在线" in msg or "已在线" in msg


def _is_bad_credential(res):
    msg = str((res or {}).get("msg") or "")
    return ("密码" in msg and ("错误" in msg or "不正确" in msg)) or "账号不存在" in msg


def password_candidates(password):
    """密码里结尾的感叹号可能是全角/半角, 两个都备着"""
    out = [password]
    if password.endswith("！"):
        out.append(password[:-1] + "!")
    elif password.endswith("!"):
        out.append(password[:-1] + "！")
    return out


def run_login(cfg, password, dry_run=False, force=False, progress=None):
    """
    执行一次完整登录。返回 (成功?, 提示文本)
    progress: 可选回调, 用于把进度显示到界面
    """
    def say(msg):
        log(msg)
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    if not password:
        return False, "请先填写密码"
    if not cfg.get("username"):
        return False, "请先填写账号"

    # 1) 已经能上网就什么都不做, 绝不无谓地打扰认证服务器
    if not dry_run and not force:
        say("正在检测网络…")
        if internet_ok():
            say("网络已连通，无需登录（未向认证门户发送任何请求）。")
            return True, "当前网络已连通，无需重复登录"

    # 2) 联系门户
    say("正在连接认证门户 %s …" % cfg["portal_host"])
    ip, online, err = get_status(cfg)
    if ip is None:
        say("无法连接认证门户: %s" % err)
        return False, "连不上校园网认证门户 %s。\n请先连接校园网 WiFi / 网线后重试。" % cfg["portal_host"]
    say("本机校园网 IP: %s    门户状态: %s" % (ip, "在线" if online else "未认证"))

    if online and not force:
        say("门户显示已在线，无需重复登录。")
        return True, "账号已在线"

    # 3) 提交认证
    ok = False
    last_msg = ""
    for pwd in password_candidates(password):
        tag = "全角！" if pwd.endswith("！") else ("半角!" if pwd.endswith("!") else "普通")
        for attempt in range(1, 4):
            if dry_run:
                say("[试运行] 将发送: " + build_login_url(cfg, ip, pwd))
                return True, "试运行完成"
            try:
                res = parse_jsonp(http_get(build_login_url(cfg, ip, pwd))) or {}
            except Exception as e:
                say("第 %d 次请求失败: %s" % (attempt, e))
                time.sleep(2)
                continue

            last_msg = str(res.get("msg") or "")
            say("认证请求(密码尾字符=%s) 第 %d 次 → %s" % (
                tag, attempt, json.dumps(res, ensure_ascii=False)[:180]))

            if _is_success(res):
                ok = True
                break
            if _is_bad_credential(res):
                say("该密码被门户拒绝，尝试另一个候选密码。")
                break
            time.sleep(2)
        if ok:
            break

    if not ok and last_msg:
        return False, "认证被拒绝：%s" % last_msg

    # 4) 校验
    say("正在校验连通性…")
    for _ in range(8):
        if internet_ok():
            say("登录成功，外网已连通。")
            return True, "连接成功，现在可以上网了"
        time.sleep(1)

    return False, "已提交认证，但外网仍不通。\n请确认账号是否欠费、通道是否选对。"


# =====================================================================
#  界面配色
# =====================================================================
C_BG        = "#EAEEF5"   # 窗口底色
C_CARD      = "#FFFFFF"   # 卡片
C_TEXT      = "#0F172A"   # 主文字
C_MUTED     = "#64748B"   # 次要文字
C_BORDER    = "#E2E8F0"   # 边框
C_FIELD     = "#F8FAFC"   # 输入框底色
C_PRIMARY   = "#2563EB"
C_PRIMARY_H = "#1D4ED8"
C_SUCCESS   = "#16A34A"
C_WARN      = "#D97706"
C_DANGER    = "#DC2626"
C_LOG_BG    = "#0F172A"
C_LOG_FG    = "#CBD5E1"


# =====================================================================
#  图形界面
# =====================================================================
def run_gui():
    import customtkinter as ctk
    from tkinter import messagebox

    cfg = load_config()

    ctk.set_appearance_mode("light")
    app = ctk.CTk()
    app.title(APP_NAME)
    app.resizable(False, False)
    app.configure(fg_color=C_BG)
    try:
        icon = os.path.join(APP_DIR, ICON_NAME)
        if os.path.exists(icon):
            app.iconbitmap(icon)
    except Exception:
        pass

    F = "Microsoft YaHei UI"
    f_title = ctk.CTkFont(family=F, size=23, weight="bold")
    f_sub   = ctk.CTkFont(family=F, size=11)
    f_label = ctk.CTkFont(family=F, size=13)
    f_input = ctk.CTkFont(family=F, size=13)
    f_btn   = ctk.CTkFont(family=F, size=17, weight="bold")
    f_small = ctk.CTkFont(family=F, size=11)
    f_status= ctk.CTkFont(family=F, size=12, weight="bold")
    f_log   = ctk.CTkFont(family="Consolas", size=11)

    root = ctk.CTkFrame(app, fg_color="transparent")
    root.pack(fill="both", expand=True, padx=22, pady=(18, 16))

    # ---------------- 顶部标题 ----------------
    head = ctk.CTkFrame(root, fg_color="transparent")
    head.pack(fill="x")

    badge = ctk.CTkFrame(head, width=44, height=44, corner_radius=12, fg_color=C_PRIMARY)
    badge.pack(side="left")
    badge.pack_propagate(False)
    ctk.CTkLabel(badge, text="网", font=ctk.CTkFont(family=F, size=20, weight="bold"),
                 text_color="#FFFFFF").place(relx=0.5, rely=0.5, anchor="center")

    tt = ctk.CTkFrame(head, fg_color="transparent")
    tt.pack(side="left", padx=(12, 0))
    ctk.CTkLabel(tt, text="校园网一键连接", font=f_title, text_color=C_TEXT,
                 anchor="w").pack(anchor="w")
    ctk.CTkLabel(tt, text="安徽理工大学合肥校区 · 172.24.34.2 · 学生移动出口",
                 font=f_sub, text_color=C_MUTED, anchor="w").pack(anchor="w")

    # ---------------- 表单卡片 ----------------
    card = ctk.CTkFrame(root, corner_radius=16, fg_color=C_CARD, border_width=1,
                        border_color=C_BORDER)
    card.pack(fill="x", pady=(16, 0))
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=18, pady=(16, 2))
    inner.columnconfigure(1, weight=1)

    def row_label(text, r):
        ctk.CTkLabel(inner, text=text, font=f_label, text_color=C_MUTED,
                     width=44, anchor="w").grid(row=r, column=0, sticky="w", pady=7)

    # 账号
    row_label("账号", 0)
    var_user = ctk.StringVar(value=cfg.get("username", ""))
    ctk.CTkEntry(inner, textvariable=var_user, font=f_input, height=40, corner_radius=10,
                 fg_color=C_FIELD, border_width=1, border_color=C_BORDER,
                 text_color=C_TEXT).grid(row=0, column=1, sticky="ew", pady=7)

    # 密码
    row_label("密码", 1)
    pf = ctk.CTkFrame(inner, fg_color="transparent")
    pf.grid(row=1, column=1, sticky="ew", pady=7)
    pf.columnconfigure(0, weight=1)
    var_pwd = ctk.StringVar(value=dec_password(cfg.get("password_enc", "")) or cfg.get("password_plain", ""))
    ent_pwd = ctk.CTkEntry(pf, textvariable=var_pwd, font=f_input, height=40, corner_radius=10,
                           fg_color=C_FIELD, border_width=1, border_color=C_BORDER,
                           text_color=C_TEXT, show="●")
    ent_pwd.grid(row=0, column=0, sticky="ew")
    def toggle_show():
        showing = ent_pwd.cget("show") == ""
        ent_pwd.configure(show="●" if showing else "")
        toggle_btn.configure(text="显示" if showing else "隐藏")

    toggle_btn = ctk.CTkButton(pf, text="显示", command=toggle_show, font=f_small,
                               width=46, height=40, corner_radius=10, fg_color="transparent",
                               hover_color="#E2E8F0", text_color=C_MUTED, border_width=0)
    toggle_btn.grid(row=0, column=1, padx=(8, 0))

    # 通道
    row_label("通道", 2)
    cf = ctk.CTkFrame(inner, fg_color="transparent")
    cf.grid(row=2, column=1, sticky="ew", pady=7)
    cf.columnconfigure(0, weight=1)
    labels = [c[0] for c in CHANNELS]
    var_ch = ctk.StringVar(value=cfg.get("channel_label", labels[0]))
    cbo = ctk.CTkComboBox(cf, variable=var_ch, values=labels, state="readonly",
                          font=f_input, height=40, corner_radius=10, fg_color=C_FIELD,
                          border_width=1, border_color=C_BORDER, text_color=C_TEXT,
                          button_color=C_FIELD, button_hover_color="#EEF2F7",
                          dropdown_fg_color=C_CARD, dropdown_hover_color="#EFF6FF",
                          dropdown_text_color=C_TEXT, dropdown_font=f_input)
    cbo.grid(row=0, column=0, sticky="ew")
    var_suffix = ctk.StringVar(value=cfg.get("channel_suffix", "@hfcmcc"))
    ctk.CTkLabel(cf, textvariable=var_suffix, font=f_small, text_color=C_MUTED,
                 width=62, anchor="w").grid(row=0, column=1, padx=(10, 0))

    def on_channel(_e=None):
        for label, suffix in CHANNELS:
            if label == var_ch.get():
                var_suffix.set(suffix or "校内")
                return

    cbo.configure(command=on_channel)

    # ---------------- 选项 ----------------
    opt = ctk.CTkFrame(root, fg_color="transparent")
    opt.pack(fill="x", pady=(12, 0))
    var_remember = ctk.BooleanVar(value=cfg.get("remember_password", True))
    var_auto = ctk.BooleanVar(value=cfg.get("auto_connect", False))
    var_close = ctk.BooleanVar(value=cfg.get("close_after_success", False))
    for text, var in (("记住密码（加密）", var_remember),
                      ("打开后自动连接", var_auto),
                      ("成功后自动关闭", var_close)):
        ctk.CTkCheckBox(opt, text=text, variable=var, font=f_small, text_color=C_MUTED,
                        checkbox_width=17, checkbox_height=17, corner_radius=5,
                        border_width=2, fg_color=C_PRIMARY, hover_color=C_PRIMARY_H,
                        border_color="#CBD5E1").pack(side="left", padx=(0, 14))

    # ---------------- 主按钮 ----------------
    btn = ctk.CTkButton(root, text="一 键 连 接", font=f_btn, height=52, corner_radius=13,
                        fg_color=C_PRIMARY, hover_color=C_PRIMARY_H, text_color="#FFFFFF",
                        command=lambda: on_connect())
    btn.pack(fill="x", pady=(14, 0))

    prog = ctk.CTkProgressBar(root, height=4, corner_radius=2, mode="indeterminate",
                              fg_color="#DCE4F0", progress_color=C_PRIMARY)

    # ---------------- 状态 ----------------
    st = ctk.CTkFrame(root, fg_color="transparent")
    st.pack(fill="x", pady=(10, 0))
    dot = ctk.CTkFrame(st, width=9, height=9, corner_radius=5, fg_color="#94A3B8")
    dot.pack(side="left", pady=(5, 0))
    dot.pack_propagate(False)
    var_status = ctk.StringVar(value="正在检查网络状态…")
    lbl_status = ctk.CTkLabel(st, textvariable=var_status, font=f_status,
                              text_color=C_MUTED, anchor="w", justify="left")
    lbl_status.pack(side="left", padx=(8, 0))

    # ---------------- 日志 ----------------
    box = ctk.CTkTextbox(root, height=132, corner_radius=12, fg_color=C_LOG_BG,
                         text_color=C_LOG_FG, font=f_log, border_width=0, wrap="word")
    box.pack(fill="both", expand=True, pady=(10, 0))
    box.configure(state="disabled")

    def append_log(line):
        def do():
            box.configure(state="normal")
            box.insert("end", line + "\n")
            box.see("end")
            box.configure(state="disabled")
        try:
            app.after(0, do)
        except Exception:
            pass

    _log_sink_set(append_log)

    def set_status(text, color, dot_color=None):
        var_status.set(text)
        lbl_status.configure(text_color=color)
        dot.configure(fg_color=dot_color or color)

    # ---------------- 底部 ----------------
    foot = ctk.CTkFrame(root, fg_color="transparent")
    foot.pack(fill="x", pady=(10, 0))

    def open_folder():
        try:
            os.startfile(APP_DIR)
        except Exception:
            pass

    def show_log():
        try:
            os.startfile(LOG_FILE)
        except Exception:
            open_folder()

    for text, cmd in (("打开日志", show_log), ("配置文件", open_folder)):
        ctk.CTkButton(foot, text=text, command=cmd, font=f_small, height=30, width=76,
                      corner_radius=9, fg_color="transparent", hover_color="#DDE4EF",
                      text_color=C_MUTED, border_width=0).pack(side="left", padx=(0, 8))
    ctk.CTkButton(foot, text="退出", command=app.destroy, font=f_small, height=30, width=62,
                  corner_radius=9, fg_color="transparent", hover_color="#DDE4EF",
                  text_color=C_MUTED, border_width=0).pack(side="right")

    busy = {"v": False}

    def on_connect():
        if busy["v"]:
            return
        user = var_user.get().strip()
        pwd = var_pwd.get()
        if not user:
            messagebox.showwarning(APP_NAME, "请填写账号")
            return
        if not pwd:
            messagebox.showwarning(APP_NAME, "请填写密码")
            return

        cfg["username"] = user
        cfg["channel_label"] = var_ch.get()
        cfg["channel_suffix"] = var_suffix.get().replace("校内", "")
        cfg["remember_password"] = bool(var_remember.get())
        cfg["auto_connect"] = bool(var_auto.get())
        cfg["close_after_success"] = bool(var_close.get())
        if var_remember.get():
            cfg["password_enc"] = enc_password(pwd)
            cfg.pop("password_plain", None)
        else:
            cfg["password_enc"] = ""
            cfg["password_plain"] = pwd
        save_config(cfg)

        busy["v"] = True
        btn.configure(state="disabled", text="正在连接…", fg_color="#93B4F5")
        set_status("正在连接…", C_PRIMARY)
        prog.pack(fill="x", pady=(8, 0), before=st)
        prog.start()
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.configure(state="disabled")

        def worker():
            try:
                ok, msg = run_login(cfg, pwd, progress=append_log)
            except Exception as exc:
                ok, msg = False, "程序异常：%r" % (exc,)
            app.after(0, lambda: done(ok, msg))

        def done(ok, msg):
            busy["v"] = False
            prog.stop()
            prog.pack_forget()
            btn.configure(state="normal", text="一 键 连 接", fg_color=C_PRIMARY)
            if ok:
                set_status(msg, C_SUCCESS)
                append_log("完成：" + msg)
                if var_close.get():
                    app.after(1500, app.destroy)
            else:
                set_status("连接失败", C_DANGER)
                append_log("失败：" + msg)
                messagebox.showerror(APP_NAME, msg)

        threading.Thread(target=worker, daemon=True).start()

    def startup_check():
        def worker():
            if internet_ok():
                app.after(0, lambda: set_status("网络已连通，可以直接上网", C_SUCCESS))
                append_log("启动检查：外网已连通，无需登录。")
                return
            ip, online, err = get_status(cfg)
            if ip:
                app.after(0, lambda: set_status(
                    "尚未认证（本机 IP %s），点按钮即可连接" % ip, C_WARN))
                append_log("启动检查：本机 IP %s，门户状态=%s" % (ip, "在线" if online else "未认证"))
            else:
                app.after(0, lambda: set_status("未连接到校园网，请先连 WiFi / 网线", C_DANGER))
                append_log("启动检查：连不上认证门户（%s）" % err)
        threading.Thread(target=worker, daemon=True).start()

    app.update_idletasks()
    h = app.winfo_reqheight()
    w = 468
    x = (app.winfo_screenwidth() - w) // 2
    y = max(40, (app.winfo_screenheight() - h) // 3)
    app.geometry("%dx%d+%d+%d" % (w, h, x, y))

    app.after(150, startup_check)
    if cfg.get("auto_connect"):
        app.after(500, on_connect)
    app.mainloop()
    return 0


def _log_sink_set(fn):
    global _log_sink
    _log_sink = fn


# =====================================================================
#  命令行(静默)模式
# =====================================================================
def run_cli(argv):
    cfg = load_config()
    if "--check" in argv:
        ip, online, err = get_status(cfg)
        net = internet_ok()
        log("诊断: 外网连通=%s, 门户在线=%s, IP=%s" % (net, online, ip or err))
        return 0 if (net or online) else 1
    if "--dry-run" in argv:
        ip, _, _ = get_status(cfg)
        log("将发送: " + build_login_url(cfg, ip or "0.0.0.0", dec_password(cfg.get("password_enc", ""))))
        return 0

    pwd = dec_password(cfg.get("password_enc", "")) or cfg.get("password_plain", "")
    if not pwd:
        log("配置里没有密码，请先打开界面填写。")
        return 4
    ok, msg = run_login(cfg, pwd, force="--force" in argv)
    log(("成功: " if ok else "失败: ") + msg.replace("\n", " "))
    if not ok and "--silent" not in argv:
        try:
            ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME, 0x30 | 0x1000)
        except Exception:
            pass
    return 0 if ok else 1


def main():
    argv = sys.argv[1:]
    if argv and argv[0].startswith("--"):
        return run_cli(argv)
    return run_gui()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("未捕获异常: %r" % (exc,))
        sys.exit(9)
