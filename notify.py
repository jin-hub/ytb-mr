# -*- coding: utf-8 -*-
"""
Bark 推送。把消息和高清图链接推到 iPhone。
图片已 commit 到公开仓库，用 raw 链接，点开看高清大图。
"""
import os
import requests
import urllib.parse


def push(title, body, url=None, group=None):
    """
    title/body: 通知标题与正文
    url: 点击通知后打开的链接（高清图或表格图）
    group: 通知分组（按场分组，便于归类）
    """
    bark_key = os.environ["BARK_KEY"]  # 你的 Bark 设备 key
    base = os.environ.get("BARK_SERVER", "https://api.day.app").rstrip("/")

    params = {}
    if url:
        params["url"] = url
        params["icon"] = url  # 通知里显示缩略预览
    if group:
        params["group"] = group
    params["isArchive"] = "1"

    t = urllib.parse.quote(title, safe="")
    b = urllib.parse.quote(body, safe="")
    endpoint = f"{base}/{bark_key}/{t}/{b}"
    try:
        r = requests.get(endpoint, params=params, timeout=20)
        return r.status_code == 200
    except Exception as e:
        print(f"[Bark] 推送失败: {e}")
        return False
