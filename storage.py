# -*- coding: utf-8 -*-
"""
数据与状态持久化。所有文件存在仓库的 data/ 目录，
每次运行后由 GitHub Actions commit 回仓库，实现“跨运行记忆”。

文件结构：
  data/timeseries.csv   —— 所有采集到的原始数据点（真实时间戳）
  data/state.json       —— 每场每成员的监控状态：本轮开始时间、已推送的里程碑、上次报警时间等
"""
import os
import json
import csv
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TS_FILE = os.path.join(DATA_DIR, "timeseries.csv")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

TS_HEADER = ["timestamp_utc", "session", "member", "video_id", "views", "likes"]


def ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def append_timeseries(rows):
    """rows: list of dict，含 TS_HEADER 字段。追加写入。"""
    ensure_dir()
    exists = os.path.exists(TS_FILE)
    with open(TS_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TS_HEADER)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def load_timeseries():
    """返回 list of dict。自动跳过 git 冲突标记行和损坏行。"""
    if not os.path.exists(TS_FILE):
        return []
    # 先按行清理掉 git 冲突标记（<<<<<<<、=======、>>>>>>>），避免污染数据
    good_lines = []
    with open(TS_FILE, encoding="utf-8") as f:
        for line in f:
            s = line.lstrip()
            if s.startswith("<<<<<<<") or s.startswith("=======") or s.startswith(">>>>>>>"):
                continue
            good_lines.append(line)
    import io
    reader = csv.DictReader(io.StringIO("".join(good_lines)))
    rows = []
    for r in reader:
        # 时间戳必须像 ISO 格式（以 20 开头的年份），否则视为损坏行跳过
        ts = (r.get("timestamp_utc") or "").strip()
        if not ts.startswith("20"):
            continue
        rows.append(r)
    return rows


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    ensure_dir()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def session_member_key(session, member):
    return f"{session}|||{member}"
