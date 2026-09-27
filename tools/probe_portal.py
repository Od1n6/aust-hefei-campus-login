# -*- coding: utf-8 -*-
"""
Dr.COM 哆点 eportal 门户探测工具
=================================
用途：帮助你摸清自己学校的门户参数，以便修改 aust_campus.py 适配。

它会做三件事（全部是**只读**请求，不会发送任何认证信息）：
  1. 查询 /drcom/chkstatus —— 得知本机在校园网里的 IP，以及门户是否认为你在线
  2. 查询 /eportal/portal/page/loadConfig —— 得知门户使用的认证方案(program_index / page_index)、
     认证方式(login_method)、账号后缀(account_suffix)等
  3. 输出下一步该去哪里找通道（出口/运营商）选项

用法：
    python probe_portal.py                # 默认探测 172.24.34.2
    python probe_portal.py 10.0.0.1       # 指定门户地址
    python probe_portal.py 10.0.0.1 801   # 指定地址和端口

下一步：
    拿到 program_index / page_index 后，下载方案页 JS 就能看到通道下拉框的全部选项：
        http://<门户>:801/eportal/extern/<program_index>/<page_index>/pc.js
    在里面搜 ISP_select 或 ISP_radio，option 的 value 就是账号要拼的后缀。
"""
import base64
import json
import re
import socket
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
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


def b64(s):
    return base64.b64encode(s.encode()).decode()


def local_ip(host):
    """本机连到门户用的是哪张网卡、哪个 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "172.24.34.2"
    port = sys.argv[2] if len(sys.argv) > 2 else "801"

    print("=" * 66)
    print(" 探测门户: %s  (eportal 端口 %s)" % (host, port))
    print("=" * 66)

    # ---------- 1. chkstatus ----------
    print("\n[1] /drcom/chkstatus  —— 本机校园网 IP 与在线状态")
    ip = None
    try:
        url = "http://%s/drcom/chkstatus?callback=dr1001&jsVersion=4.2&v=%d" % (
            host, int(time.time()) % 100000)
        j = parse_jsonp(http_get(url)) or {}
        ip = (j.get("v46ip") or j.get("ss5") or "").strip() or None
        print("    本机校园网 IP : %s" % (ip or "(未获取到)"))
        print("    门户认为在线  : %s   (result=%s)" % (
            "是" if j.get("result") == 1 else "否", j.get("result")))
        print("    门户自身 IP   : %s" % (j.get("ss6") or j.get("v4serip") or "?"))
    except Exception as e:
        print("    失败: %s" % e)
        ip = local_ip(host)
        print("    改用本机网卡地址: %s" % (ip or "?"))

    # ---------- 2. loadConfig ----------
    print("\n[2] /eportal/portal/page/loadConfig  —— 门户下发的认证方案")
    scheme = None
    try:
        url = ("http://%s:%s/eportal/portal/page/loadConfig?program_index=&wlan_vlan_id=0"
               "&wlan_user_ip=%s&wlan_user_ipv6=&wlan_user_ssid=&wlan_user_areaid="
               "&wlan_ac_ip=&wlan_ap_mac=&gw_id=&callback=dr1002&jsVersion=4.1&v=%d&lang=zh"
               % (host, port, b64(ip or ""), int(time.time()) % 100000))
        j = parse_jsonp(http_get(url)) or {}
        d = j.get("data") or {}
        if not d:
            print("    返回中没有 data 字段，原始内容:")
            print("    " + json.dumps(j, ensure_ascii=False)[:600])
        else:
            keys = ("program_index", "page_index", "page_name", "login_method",
                    "account_suffix", "account_prefix", "en_md5", "is_redirect",
                    "redirect_link", "en_perceive", "check_online_method")
            for k in keys:
                if k in d:
                    print("    %-20s = %s" % (k, d[k]))
            scheme = (d.get("program_index"), d.get("page_index"))
            print("\n    免认证白名单域名(可用于判断哪些地址在未认证时也是通的):")
            wl = d.get("visit_blacklist") or []
            for w in wl[:8]:
                print("        %s" % w)
            if len(wl) > 8:
                print("        ... 共 %d 个" % len(wl))
    except Exception as e:
        print("    失败: %s" % e)

    # ---------- 3. 下一步 ----------
    print("\n[3] 下一步：找通道（出口/运营商）选项")
    if scheme and scheme[0] and scheme[1]:
        print("    方案页地址（在里面搜 ISP_select / ISP_radio）:")
        print("        http://%s:%s/eportal/extern/%s/%s/pc.js" % (host, port, scheme[0], scheme[1]))
    else:
        print("    未取到 program_index / page_index，可先打开门户首页看 HTML 源码：")
        print("        http://%s/" % host)
        print("    搜 v4serip 确认门户 IP；搜 a41.js 找到 page_url 与 portal_api。")
    print("\n    拿到通道后缀后，改 aust_campus.py 顶部的 CHANNELS 即可。")
    print()


if __name__ == "__main__":
    main()
