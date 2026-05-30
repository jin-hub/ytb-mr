# -*- coding: utf-8 -*-
"""
主程序：每次 GitHub Actions 运行执行一遍。
流程：
  1. 读 Google 表格配置（哪些场/成员/链接在“运行”）
  2. 抓 YouTube 当前播放量/点赞数
  3. 追加到时间序列 CSV
  4. 管理每场每成员的“本轮开始时间 / 停止 / 自动停止”状态
  5. 异常检测（速率+三条件），命中则推局部放大图 + 文字
  6. 到达里程碑(0.5/1/3/6/24/48/72h)推该跨度趋势图 + 数据表格图
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
from zoneinfo import ZoneInfo

import config as C
import sheet_reader
import youtube_fetch
import storage
import detector
import plotting
import notify

KST = ZoneInfo(C.TIMEZONE)
REPO = os.environ.get("GITHUB_REPOSITORY", "")          # "user/repo"
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
SHEET_ID = os.environ["SHEET_ID"]


def raw_url(rel_path):
    """把仓库内文件路径转成可公开访问的 raw 链接（看高清图用）。"""
    rel = rel_path.split("ytb-monitor/")[-1] if "ytb-monitor/" in rel_path else os.path.basename(rel_path)
    rel = os.path.relpath(rel_path, os.path.dirname(__file__))
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}"


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(s):
    return datetime.fromisoformat(s)


def main():
    now = now_utc()
    items = sheet_reader.load_config(SHEET_ID)
    if not items:
        print("没有处于“运行”状态的视频，跳过。")
        # 仍要处理“刚被停止”的场的状态收尾（略），这里直接返回
        return

    # ---- 1. 抓数据 ----
    vids = list({it["video_id"] for it in items})
    stats = youtube_fetch.fetch_stats(vids)

    # ---- 2. 写时间序列 ----
    rows = []
    for it in items:
        s = stats.get(it["video_id"])
        if not s:
            continue
        rows.append({
            "timestamp_utc": now.isoformat(),
            "session": it["session"],
            "member": it["member"],
            "video_id": it["video_id"],
            "views": s["views"] if s["views"] is not None else "",
            "likes": s["likes"] if s["likes"] is not None else "",
        })
    storage.append_timeseries(rows)

    # ---- 3. 状态管理 ----
    state = storage.load_state()
    running_keys = set()
    for it in items:
        key = storage.session_member_key(it["session"], it["member"])
        running_keys.add(key)
        if key not in state:
            # 本轮开始
            state[key] = {
                "session": it["session"], "member": it["member"],
                "start_utc": now.isoformat(),
                "pushed_milestones": [],
                "last_alert": {},   # metric -> iso time
            }
    # 表格里不再“运行”的（被勾停止）：标记结束本轮，下次再运行视为新一轮
    for key in list(state.keys()):
        if key not in running_keys:
            # 该轮结束，删除其运行态（重新开始时会重建，从新 start 计时）
            state.pop(key, None)

    # 自动停止：超过 AUTO_STOP_HOURS
    for key in list(running_keys):
        st = state.get(key)
        if st:
            elapsed_h = (now - parse_iso(st["start_utc"])).total_seconds() / 3600
            if elapsed_h >= C.AUTO_STOP_HOURS:
                print(f"{key} 已满 {C.AUTO_STOP_HOURS}h，本轮自动停止。")
                # 仍保留数据；从运行集合移除，状态删除
                state.pop(key, None)
                running_keys.discard(key)

    # ---- 4. 载入全量时间序列做检测与画图 ----
    ts = storage.load_timeseries()
    df = pd.DataFrame(ts)
    if not df.empty:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        df["time_kst"] = df["timestamp_utc"].dt.tz_convert(KST)
        for col in ["views", "likes"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ---- 5. 异常检测（按 场+成员+指标） ----
    for it in items:
        key = storage.session_member_key(it["session"], it["member"])
        st = state.get(key)
        if not st:
            continue
        start = parse_iso(st["start_utc"])
        # 本轮数据
        dseg = df[(df["session"] == it["session"]) &
                  (df["timestamp_utc"] >= start)].copy()
        if dseg.empty:
            continue

        for metric in C.METRICS:
            run_for_metric_alert(it, metric, st, df, start, now)

    # ---- 6. 里程碑定时推送（按场） ----
    sessions = list(dict.fromkeys(it["session"] for it in items))
    for session in sessions:
        # 该场任意成员的最早 start 作为该场本轮起点
        keys = [k for k in state if state[k]["session"] == session]
        if not keys:
            continue
        start = min(parse_iso(state[k]["start_utc"]) for k in keys)
        elapsed_h = (now - start).total_seconds() / 3600
        pushed = set()
        for k in keys:
            pushed |= set(state[k]["pushed_milestones"])

        for mh in C.PUSH_MILESTONES_H:
            if mh in pushed:
                continue
            if elapsed_h >= mh:
                push_milestone(session, mh, df, start, keys, state)
                for k in keys:
                    if mh not in state[k]["pushed_milestones"]:
                        state[k]["pushed_milestones"].append(mh)

    storage.save_state(state)
    print("本轮运行完成。")


def run_for_metric_alert(it, metric, st, df, start, now):
    """对单个 场+成员+指标 做异常检测并推送。"""
    session, member = it["session"], it["member"]
    dseg_all = df[(df["session"] == session)].copy()
    dmem = dseg_all[(dseg_all["member"] == member) &
                    (dseg_all["timestamp_utc"] >= start)].sort_values("timestamp_utc")
    dmem = dmem.dropna(subset=[metric])
    if len(dmem) < C.MIN_POINTS_TO_JUDGE + 1:
        return

    t0 = dmem["timestamp_utc"].min()
    tmin = [(t - t0).total_seconds() / 60 for t in dmem["timestamp_utc"]]
    vals = dmem[metric].tolist()

    # 全组在某速率索引处的速率（B条件用）
    members = dseg_all["member"].unique().tolist()

    def group_at(i):
        out = []
        # 第 i 个速率点对应 dmem 的第 i+1 个数据时间
        if i + 1 >= len(dmem):
            return out
        t_ref = dmem["timestamp_utc"].iloc[i + 1]
        for mm in members:
            dm = dseg_all[(dseg_all["member"] == mm) &
                          (dseg_all["timestamp_utc"] >= start)].sort_values("timestamp_utc").dropna(subset=[metric])
            if len(dm) < 2:
                out.append(float("nan")); continue
            # 取离 t_ref 最近的两个点算速率
            dm2 = dm[dm["timestamp_utc"] <= t_ref].tail(2)
            if len(dm2) < 2:
                out.append(float("nan")); continue
            dt = (dm2["timestamp_utc"].iloc[1] - dm2["timestamp_utc"].iloc[0]).total_seconds() / 60
            if dt <= 0 or dt > C.GAP_THRESHOLD_MIN:
                out.append(float("nan")); continue
            out.append((dm2[metric].iloc[1] - dm2[metric].iloc[0]) / dt)
        return out

    res = detector.detect_latest((tmin, vals), group_at)
    if not res:
        return

    # 去抖
    last = st["last_alert"].get(metric)
    if last and (now - parse_iso(last)).total_seconds() / 60 < C.ALERT_DEDUP_MIN:
        return
    st["last_alert"][metric] = now.isoformat()

    # 画异常前后 ±ALERT_CONTEXT_HOURS 的局部图（客观、无标注、全场成员）
    at = dmem["timestamp_utc"].iloc[-1]
    lo = at - timedelta(hours=C.ALERT_CONTEXT_HOURS)
    hi = at + timedelta(hours=C.ALERT_CONTEXT_HOURS)
    dctx = dseg_all[(dseg_all["timestamp_utc"] >= lo) & (dseg_all["timestamp_utc"] <= hi)].copy()
    long = dctx.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                     value_vars=[metric], var_name="metric", value_name="value").dropna(subset=["value"])
    long = long.rename(columns={})
    fname = f"ALERT_{plotting._safe(session)}_{metric}_{at.strftime('%Y%m%d%H%M')}.png"
    df_for_plot = long[["time_kst", "member", "value"]]
    path = plotting.plot_trend(
        pd.DataFrame({"time_kst": df_for_plot["time_kst"],
                      "member": df_for_plot["member"],
                      "value": df_for_plot["value"]}),
        metric, session, KST, window=None, fname=fname)

    link = raw_url(path)
    at_kst = at.astimezone(KST).strftime("%m/%d %H:%M")
    notify.push(
        title=f"⚠️ {session}",
        body=f"{member} {C.METRIC_CN[metric]} ({at_kst} KST)",
        url=link, image=link, group=session,
    )
    print(f"[报警] {session} {member} {metric} @ {at_kst}")


def push_milestone(session, mh, df, start, keys, state):
    """到达里程碑，推该场趋势图（点赞+播放）+ 数据表格图。"""
    window = C.WINDOWED_MILESTONES.get(mh)  # 48->(24,48), 72->(48,72), 否则 None
    dseg = df[(df["session"] == session) & (df["timestamp_utc"] >= start)].copy()
    if dseg.empty:
        return

    # 趋势图：两张
    links = {}
    for metric in C.METRICS:
        long = dseg.dropna(subset=[metric])[["time_kst", "member", metric]].rename(columns={metric: "value"})
        if long.empty:
            continue
        fname = f"{plotting._safe(session)}_{metric}_{mh:g}h.png"
        path = plotting.plot_trend(long, metric, session, KST, window=window, fname=fname)
        links[metric] = raw_url(path)

    # 数据表格图
    longt = dseg.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                      value_vars=C.METRICS, var_name="metric", value_name="value").dropna(subset=["value"])
    if window is not None:
        lo = start + timedelta(hours=window[0]); hi = start + timedelta(hours=window[1])
        longt = longt[(longt["timestamp_utc"] >= lo) & (longt["timestamp_utc"] <= hi)]
    table_path = plotting.plot_table(longt, session, KST,
                                     fname=f"{plotting._safe(session)}_table_{mh:g}h.png")
    table_link = raw_url(table_path)

    # 里程碑标签：0.5 显示为 "0.5h"，整数显示为 "1h/3h..."，避免 int(0.5)=0 变成 "0h"
    _mh_label = (f"{mh:g}h")  # 0.5->'0.5h', 1.0->'1h', 24.0->'24h'
    label = _mh_label if window is None else f"Day{int(window[0]//24)+1}({int(window[0])}~{int(window[1])}h)"
    if "likes" in links:
        notify.push(f"📊 {session} | {label} 좋아요",
                    "", url=links["likes"], image=links["likes"], group=session)
    if "views" in links:
        notify.push(f"📊 {session} | {label} 조회수",
                    "", url=links["views"], image=links["views"], group=session)
    notify.push(f"📋 {session} | {label}",
                "", url=table_link, image=table_link, group=session)
    print(f"[里程碑] {session} {label} 已推送")


def _parse_kst(s):
    """宽容解析用户填的 KST 时间，失败返回 None。支持多种格式。"""
    if not s or not s.strip():
        return None
    s = s.strip().replace("/", "-")
    fmts = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M", "%Y-%m-%d"]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if "%Y" not in f:  # 没填年份，用今年
                dt = dt.replace(year=datetime.now(KST).year)
            return dt.replace(tzinfo=KST).astimezone(timezone.utc)
        except ValueError:
            continue
    return "ERROR"  # 区分“没填”(None)和“填错”(ERROR)


def manual_push():
    """灵活手动推送：按场次 + 时间范围 + 多选指标。带容错。"""
    session_q = os.environ.get("MANUAL_SESSION", "").strip()
    start_q = os.environ.get("MANUAL_START", "")
    end_q = os.environ.get("MANUAL_END", "")
    what_q = os.environ.get("MANUAL_WHAT", "all").strip().lower()

    # 解析时间
    start_utc = _parse_kst(start_q)
    end_utc = _parse_kst(end_q)
    if start_utc == "ERROR" or end_utc == "ERROR":
        notify.push("⚠️ 시간 형식 오류",
                    "时间格式不对，请用：2026-05-30 14:00", group="manual")
        return

    # 解析“看什么”（多选，逗号分隔；留空或 all = 全部）
    if what_q in ("", "all", "全部"):
        want_views = want_likes = want_table = True
    else:
        want_views = any(k in what_q for k in ["播放", "조회", "views", "view"])
        want_likes = any(k in what_q for k in ["点赞", "좋아", "likes", "like"])
        want_table = any(k in what_q for k in ["数据", "表", "table"])
        if not (want_views or want_likes or want_table):
            want_views = want_likes = want_table = True  # 啥都没匹配上=全给

    ts = storage.load_timeseries()
    if not ts:
        notify.push("🔔 데이터 없음", "暂无数据。", group="manual")
        return
    df = pd.DataFrame(ts)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["time_kst"] = df["timestamp_utc"].dt.tz_convert(KST)
    for col in ["views", "likes"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    all_sessions = list(dict.fromkeys(df["session"].tolist()))
    # 选场次
    if session_q:
        sessions = [s for s in all_sessions if s == session_q]
        if not sessions:
            notify.push("⚠️ 장면 없음",
                        f"没找到场次「{session_q}」。现有: {', '.join(all_sessions)}",
                        group="manual")
            return
    else:
        sessions = all_sessions  # 留空=全部

    for session in sessions:
        dseg = df[df["session"] == session].copy()
        # 按时间范围筛（自动去掉范围外的空白；筛完图自动贴合真实数据）
        if start_utc:
            dseg = dseg[dseg["timestamp_utc"] >= start_utc]
        if end_utc:
            dseg = dseg[dseg["timestamp_utc"] <= end_utc]
        if dseg.empty:
            notify.push(f"⚠️ {session}", "该时间段无数据。", group=session)
            continue

        if want_views:
            long = dseg.dropna(subset=["views"])[["time_kst", "member", "views"]].rename(columns={"views": "value"})
            if not long.empty:
                p = plotting.plot_trend(long, "views", session, KST, fname=f"M_{plotting._safe(session)}_views.png")
                notify.push(f"🔔 {session} | {C.METRIC_CN['views']}", "", url=raw_url(p), image=raw_url(p), group=session)
        if want_likes:
            long = dseg.dropna(subset=["likes"])[["time_kst", "member", "likes"]].rename(columns={"likes": "value"})
            if not long.empty:
                p = plotting.plot_trend(long, "likes", session, KST, fname=f"M_{plotting._safe(session)}_likes.png")
                notify.push(f"🔔 {session} | {C.METRIC_CN['likes']}", "", url=raw_url(p), image=raw_url(p), group=session)
        if want_table:
            longt = dseg.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                              value_vars=C.METRICS, var_name="metric", value_name="value").dropna(subset=["value"])
            if not longt.empty:
                tp = plotting.plot_table(longt, session, KST, fname=f"M_{plotting._safe(session)}_table.png")
                notify.push(f"🔔 {session} | 데이터", "", url=raw_url(tp), image=raw_url(tp), group=session)
        print(f"[手动推送] {session} 已推送")


if __name__ == "__main__":
    # 无论定时还是手动，都先跑 main()（采集+检测+里程碑，这些该推的会推）
    main()
    # 是否执行"灵活全量推送"由 do_push 控制：
    # - 你在 GitHub 网页手动 Run workflow：do_push 默认 'yes' → 推送
    # - cron-job.org 外部定时：请求体显式传 do_push='no' → 只采集，不刷屏
    _do_push = os.environ.get("MANUAL_DO_PUSH", "yes").strip().lower()
    _is_manual = os.environ.get("MANUAL_TRIGGER") == "true"
    if _is_manual and _do_push not in ("no", "false", "0", ""):
        manual_push()
