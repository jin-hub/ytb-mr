# -*- coding: utf-8 -*-
"""
画图：趋势图（按场，每场所有成员同图，不同颜色区分，客观无标注）+ 数据表格图。
- 时间用韩国时间 KST
- 韩文/中文字体用 Noto Sans CJK
- 不做高亮/红圈/灰底，纯客观
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
import pandas as pd
import numpy as np
from datetime import timedelta
import config as C

# 字体：优先用 KR 专用 otf（GitHub Actions 装 fonts-noto-cjk 后会有），
# 兜底用 CJK 合集 ttc。直接按文件路径设定，避免 ttc 落到日文子集导致缺字。
import glob

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansKR-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
# 再补一轮 glob 搜索，适配不同发行版路径
_FONT_CANDIDATES += glob.glob("/usr/share/fonts/**/NotoSansCJK*kr*.otf", recursive=True)
_FONT_CANDIDATES += glob.glob("/usr/share/fonts/**/NotoSansKR*.otf", recursive=True)
_FONT_CANDIDATES += glob.glob("/usr/share/fonts/**/NotoSansCJK-Regular.ttc", recursive=True)

CJK_FONT = None
for fp in _FONT_CANDIDATES:
    if os.path.exists(fp):
        try:
            font_manager.fontManager.addfont(fp)
            CJK_FONT = font_manager.FontProperties(fname=fp)
            break
        except Exception:
            continue

matplotlib.rcParams["axes.unicode_minus"] = False
if CJK_FONT is not None:
    matplotlib.rcParams["font.family"] = CJK_FONT.get_name()


def _fp():
    """返回可用于 fontproperties 的对象（None 时用默认）。"""
    return CJK_FONT

# 成员配色（自动循环）
PALETTE = ["#4F8DFD", "#F2776B", "#2BB673", "#9B59B6", "#E67E22",
           "#1ABC9C", "#34495E", "#E84393"]

OUT_DIR = os.path.join(os.path.dirname(__file__), "data", "charts")


def _ensure():
    os.makedirs(OUT_DIR, exist_ok=True)


def plot_trend(df_session, metric, session, kst_tz, window=None, fname=None):
    """
    df_session: 该场的长表，列 [time_kst(datetime), member, value]
    metric: 'views' or 'likes'
    window: (start_h, end_h) 仅画这段（相对本轮开始），None 为全部
    返回保存的图片路径
    """
    _ensure()
    fig, ax = plt.subplots(figsize=(12, 7), dpi=140)

    members = list(dict.fromkeys(df_session["member"].tolist()))
    for idx, m in enumerate(members):
        sub = df_session[df_session["member"] == m].sort_values("time_kst")
        if window is not None:
            t0 = df_session["time_kst"].min()
            lo = t0 + timedelta(hours=window[0])
            hi = t0 + timedelta(hours=window[1])
            sub = sub[(sub["time_kst"] >= lo) & (sub["time_kst"] <= hi)]
        if sub.empty:
            continue
        ax.plot(sub["time_kst"], sub["value"], "-", lw=2,
                color=PALETTE[idx % len(PALETTE)], label=m, marker="o", ms=3)

    metric_label = C.METRIC_CN[metric]
    title = f"{session}  {metric_label}趋势"
    if window is not None:
        title += f"  (第{int(window[0]//24)+1}天: {window[0]}~{window[1]}h)"
    ax.set_title(title, fontsize=15, fontweight="bold", fontproperties=_fp())
    ax.set_ylabel(metric_label, fontproperties=_fp())
    ax.set_xlabel("时间 (KST)", fontproperties=_fp())
    leg=ax.legend(loc="upper left", fontsize=10, ncol=2)
    [t.set_fontproperties(_fp()) for t in leg.get_texts()] if _fp() else None
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M", tz=kst_tz))
    fig.autofmt_xdate()
    plt.tight_layout()

    if fname is None:
        fname = f"{session}_{metric}.png".replace("/", "-").replace(" ", "_")
    path = os.path.join(OUT_DIR, fname)
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_table(df_session, session, kst_tz, fname=None):
    """
    数据表格图：行=时间(KST)，列=成员，分播放/点赞两块。
    为避免太长，最多显示最近 ~40 个时间点。
    """
    _ensure()
    # 透视成宽表
    def pivot(metric):
        d = df_session[df_session["metric"] == metric]
        p = d.pivot_table(index="time_kst", columns="member", values="value", aggfunc="last")
        return p.sort_index()

    pv_views = pivot("views")
    pv_likes = pivot("likes")

    # 只取最近 40 行
    pv_views = pv_views.tail(40)
    pv_likes = pv_likes.tail(40)

    members = list(dict.fromkeys(df_session["member"].tolist()))
    times = pv_views.index

    n = len(times)
    fig_h = max(3, 0.32 * n + 1.5)
    fig, axes = plt.subplots(1, 2, figsize=(max(8, 1.6 * len(members) + 2), fig_h), dpi=130)

    for ax, pv, name in zip(axes, [pv_views, pv_likes], ["播放量", "点赞量"]):
        ax.axis("off")
        ax.set_title(f"{session}  {name}", fontsize=12, fontweight="bold", fontproperties=_fp())
        cell_text = []
        for t in pv.index:
            row = [f"{int(pv.loc[t, m]):,}" if (m in pv.columns and pd.notna(pv.loc[t, m])) else "-"
                   for m in members]
            cell_text.append(row)
        row_labels = [pd.Timestamp(t).tz_convert(kst_tz).strftime("%m/%d %H:%M") for t in pv.index]
        tbl = ax.table(cellText=cell_text, rowLabels=row_labels, colLabels=members,
                       loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        [c.set_text_props(fontproperties=_fp()) for c in tbl.get_celld().values()] if _fp() else None
        tbl.scale(1, 1.2)

    plt.tight_layout()
    if fname is None:
        fname = f"{session}_table.png".replace("/", "-").replace(" ", "_")
    path = os.path.join(OUT_DIR, fname)
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
