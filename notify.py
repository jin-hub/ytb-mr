# -*- coding: utf-8 -*-
"""
Bark 推送。
- 用 image 参数让图片直接显示在通知里（下拉通知即可看图）
- 不再传 url 参数：点通知只进 App 看图，不会跳浏览器
- 图片链接以纯文本形式拼在 body 末尾（黑色文字、不变蓝，长按可复制兜底）
- 支持多个 Bark key（推给你和朋友），BARK_KEY 里用英文逗号分隔
"""
import os
import requests


def _keys():
    raw = os.environ.get("BARK_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def push(title, body, url=None, group=None, image=None, link_in_body=None):
    """
    title/body: 通知标题与正文
    image: 直接显示在通知里的图片 URL（下拉即看）
    group: 通知分组（按场分组）
    link_in_body: 若提供，把该链接作为纯文本拼到 body 末尾（不变蓝、不跳转）
    url: 兼容旧调用；为避免点击跳浏览器，这里【不再发送】url 参数
    """
    base = os.environ.get("BARK_SERVER", "https://api.day.app").rstrip("/")

    # 把链接以纯文本放到正文末尾（黑色、可长按复制；不放进 url 参数所以不变蓝/不跳转）
    final_body = body or ""
    if link_in_body:
        final_body = (final_body + "\n" + link_in_body) if final_body else link_in_body

    ok_all = True
    for key in _keys():
        payload = {
            "title": title,
            "body": final_body,
            "isArchive": 1,
        }
        if image:
            payload["image"] = image      # 通知里直接显示图片
        # 注意：故意不再设置 payload["url"]，点通知只进 App 不跳浏览器
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
