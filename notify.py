# -*- coding: utf-8 -*-
"""
Bark 推送。
- 用 image 参数让图片直接显示在通知里（下拉通知即可看图，无需点链接跳转）
- 同时保留 url，长按/点开仍可跳浏览器看高清原图
- 支持多个 Bark key（推给你和朋友），BARK_KEY 里用英文逗号分隔多个 key 即可
"""
import os
import requests


def _keys():
    raw = os.environ.get("BARK_KEY", "")
    # 支持用逗号分隔多个 key：key1,key2
    return [k.strip() for k in raw.split(",") if k.strip()]


def push(title, body, url=None, group=None, image=None):
    """
    title/body: 通知标题与正文
    url: 点击通知后打开的链接（高清原图）
    image: 直接显示在通知里的图片 URL（下拉即看）
    group: 通知分组（按场分组）
    """
    base = os.environ.get("BARK_SERVER", "https://api.day.app").rstrip("/")
    ok_all = True
    for key in _keys():
        # 用 POST + JSON，避免 URL 里中文/韩文编码问题
        payload = {
            "title": title,
            "body": body,
            "isArchive": 1,
        }
        if image:
            payload["image"] = image      # 通知里直接显示图片
        if url:
            payload["url"] = url          # 点击跳转看原图
        if group:
            payload["group"] = group
        endpoint = f"{base}/{key}"
        try:
            r = requests.post(endpoint, json=payload, timeout=20)
            if r.status_code != 200:
                ok_all = False
                print(f"[Bark] {key[:6]}... 状态码 {r.status_code}")
        except Exception as e:
            ok_all = False
            print(f"[Bark] 推送失败: {e}")
    return ok_all
