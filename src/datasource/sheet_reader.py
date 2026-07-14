# -*- coding: utf-8 -*-
"""
读取 Google 表格里的监控配置。
表格结构见 SOP。每一行 = 一个要监控的视频。
列：场次标题 | 成员名 | YouTube链接 | 状态(运行/停止)
脚本读取所有“运行”状态的行进行监控。
"""
import json
import os
import re
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _client():
    """用服务账号 json 凭证连接。凭证从环境变量 GOOGLE_CREDENTIALS 读取（GitHub Secret）。"""
    raw = os.environ["GOOGLE_CREDENTIALS"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def extract_video_id(url: str) -> str:
    """从各种 YouTube 链接格式里提取 11 位视频 ID。"""
    url = url.strip()
    patterns = [
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
        r"(?:shorts/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    # 兜底：如果用户直接填了 11 位 ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    raise ValueError(f"无法从链接解析视频ID: {url}")


def load_config(sheet_id: str, worksheet_name: str = "监控列表"):
    """
    返回一个列表，每项是一个 dict:
    {session, member, url, video_id, status}
    只返回 status 为“运行”的行。
    """
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.sheet1

    records = ws.get_all_records()  # 用首行作表头
    items = []
    for i, row in enumerate(records, start=2):
        # 兼容中英文表头
        session = str(row.get("场次标题") or row.get("session") or "").strip()
        member = str(row.get("成员名") or row.get("member") or "").strip()
        url = str(row.get("YouTube链接") or row.get("url") or "").strip()
        status = str(row.get("状态") or row.get("status") or "").strip()

        if not url or not session or not member:
            continue
        if status not in ("运行", "RUN", "run", "运行中", "开始"):
            continue  # 只监控“运行”状态的行；“停止”或空则跳过

        try:
            vid = extract_video_id(url)
        except ValueError:
            print(f"[警告] 第{i}行链接无法解析，跳过: {url}")
            continue

        items.append({
            "session": session,
            "member": member,
            "url": url,
            "video_id": vid,
            "status": "运行",
            "row": i,
        })
    return items
