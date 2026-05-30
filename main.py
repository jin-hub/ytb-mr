-*- coding: utf-8 -*-
"""
主程序：每次 GitHub Actions 运行执行一遍。
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
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
SHEET_ID = os.environ["SHEET_ID"]


def raw_url(rel_path):
    rel = os.path.relpath(rel_path, os.path.dirname(__file__))
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}"


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(s):
    return datetime.fromisoformat(s)


def force_test_push():
    """测试用：强制把每个场次当前的趋势图推一次，不管到没到里程碑。"""
    ts = storage.load_timeseries()
    if not ts:
        notify.push("🔔 测试推送", "暂无数据，稍等几分钟有数据后再试。", group="测试")
        return
    df = pd.DataFrame(ts)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["time_kst"] = df["timestamp_utc"].dt.tz_convert(KST)
    for col in ["views", "likes"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for session in df["session"].unique():
        dseg = df[df["session"] == session]
        for metric in C.METRICS:
            long = dseg.dropna(subset=[metric])[["time_kst", "member", metric]].rename(columns={metric: "value"})
            if long.empty:
                continue
            fname = f"TEST_{session}_{metric}.png".replace("/", "-").replace(" ", "_")
            path = plotting.plot_trend(long, metric, session, KST, fname=fname)
            link = raw_url(path)
            notify.push(f"🔔 测试 | {session} {C.METRIC_CN[metric]}",
                        "测试推送，点开看当前趋势图。", url=link, group=session)
        # 数据表
        longt = dseg.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                          value_vars=C.METRICS, var_name="metric", value_name="value").dropna(subset=["value"])
        if not longt.empty:
            tpath = plotting.plot_table(longt, session, KST, fname=f"TEST_{session}_table.png".replace("/", "-").replace(" ", "_"))
            notify.push(f"🔔 测试 | {session} 数据表", "测试推送，点开看数据表。", url=raw_url(tpath), group=session)


def main():
    now = now_utc()
    items = sheet_reader.load_config(SHEET_ID)
    if not items:
        print("没有处于“运行”状态的视频，跳过。")
        return

    # 1. 抓数据
    vids = list({it["video_id"] for it in items})
    stats = youtube_fetch.fetch_stats(vids)

    # 2. 写时间序列
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

    # 3. 状态管理
    state = storage.load_state()
    running_keys = set()
    for it in items:
        key = storage.session_member_key(it["session"], it["member"])
        running_keys.add(key)
        if key not in state:
            state[key] = {
                "session": it["session"], "member": it["member"],
                "start_utc": now.isoformat(),
                "pushed_milestones": [],
                "last_alert": {},
            }
    for key in list(state.keys()):
        if key not in running_keys:
            state.pop(key, None)

    for key in list(running_keys):
        st = state.get(key)
        if st:
            elapsed_h = (now - parse_iso(st["start_utc"])).total_seconds() / 3600
            if elapsed_h >= C.AUTO_STOP_HOURS:
                print(f"{key} 已满 {C.AUTO_STOP_HOURS}h，本轮自动停止。")
                state.pop(key, None)
                running_keys.discard(key)

    # 4. 载入全量数据
    ts = storage.load_timeseries()
    df = pd.DataFrame(ts)
    if not df.empty:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        df["time_kst"] = df["timestamp_utc"].dt.tz_convert(KST)
        for col in ["views", "likes"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 5. 异常检测
    for it in items:
        key = storage.session_member_key(it["session"], it["member"])
        st = state.get(key)
        if not st:
            continue
        start = parse_iso(st["start_utc"])
        dseg = df[(df["session"] == it["session"]) & (df["timestamp_utc"] >= start)].copy()
        if dseg.empty:
            continue
        for metric in C.METRICS:
            run_for_metric_alert(it, metric, st, df, start, now)

    # 6. 里程碑推送
    sessions = list(dict.fromkeys(it["session"] for it in items))
    for session in sessions:
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
    session, member = it["session"], it["member"]
    dseg_all = df[(df["session"] == session)].copy()
    dmem = dseg_all[(dseg_all["member"] == member) & (dseg_all["timestamp_utc"] >= start)].sort_values("timestamp_utc")
    dmem = dmem.dropna(subset=[metric])
    if len(dmem) < C.MIN_POINTS_TO_JUDGE + 1:
        return

    t0 = dmem["timestamp_utc"].min()
    tmin = [(t - t0).total_seconds() / 60 for t in dmem["timestamp_utc"]]
    vals = dmem[metric].tolist()
    members = dseg_all["member"].unique().tolist()

    def group_at(i):
        out = []
        if i + 1 >= len(dmem):
            return out
        t_ref = dmem["timestamp_utc"].iloc[i + 1]
        for mm in members:
            dm = dseg_all[(dseg_all["member"] == mm) & (dseg_all["timestamp_utc"] >= start)].sort_values("timestamp_utc").dropna(subset=[metric])
            if len(dm) < 2:
                out.append(float("nan")); continue
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

    last = st["last_alert"].get(metric)
    if last and (now - parse_iso(last)).total_seconds() / 60 < C.ALERT_DEDUP_MIN:
        return
    st["last_alert"][metric] = now.isoformat()

    at = dmem["timestamp_utc"].iloc[-1]
    lo = at - timedelta(hours=C.ALERT_CONTEXT_HOURS)
    hi = at + timedelta(hours=C.ALERT_CONTEXT_HOURS)
    dctx = dseg_all[(dseg_all["timestamp_utc"] >= lo) & (dseg_all["timestamp_utc"] <= hi)].copy()
    long = dctx.dropna(subset=[metric])[["time_kst", "member", metric]].rename(columns={metric: "value"})
    fname = f"ALERT_{session}_{member}_{metric}_{at.strftime('%Y%m%d%H%M')}.png".replace("/", "-").replace(" ", "_")
    path = plotting.plot_trend(long, metric, session, KST, window=None, fname=fname)

    link = raw_url(path)
    at_kst = at.astimezone(KST).strftime("%m/%d %H:%M")
    notify.push(
        title=f"⚠️ 异常 | {session}",
        body=f"{member} 的{C.METRIC_CN[metric]}出现疑似异常增长（{at_kst} KST）。点开看趋势。",
        url=link, group=session,
    )
    print(f"[报警] {session} {member} {metric} @ {at_kst}")


def push_milestone(session, mh, df, start, keys, state):
    window = C.WINDOWED_MILESTONES.get(mh)
    dseg = df[(df["session"] == session) & (df["timestamp_utc"] >= start)].copy()
    if dseg.empty:
        return

    links = {}
    for metric in C.METRICS:
        long = dseg.dropna(subset=[metric])[["time_kst", "member", metric]].rename(columns={metric: "value"})
        if long.empty:
            continue
        fname = f"{session}_{metric}_{int(mh)}h.png".replace("/", "-").replace(" ", "_")
        path = plotting.plot_trend(long, metric, session, KST, window=window, fname=fname)
        links[metric] = raw_url(path)

    longt = dseg.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                      value_vars=C.METRICS, var_name="metric", value_name="value").dropna(subset=["value"])
    if window is not None:
        lo = start + timedelta(hours=window[0]); hi = start + timedelta(hours=window[1])
        longt = longt[(longt["timestamp_utc"] >= lo) & (longt["timestamp_utc"] <= hi)]
    table_path = plotting.plot_table(longt, session, KST,
                                     fname=f"{session}_table_{int(mh)}h.png".replace("/", "-").replace(" ", "_"))
    table_link = raw_url(table_path)

    label = f"{mh}h" if window is None else f"第{int(window[0]//24)+1}天({window[0]}~{window[1]}h)"
    if "likes" in links:
        notify.push(f"📊 {session} | {label} 点赞趋势", "点开看高清点赞趋势图。", url=links["likes"], group=session)
    if "views" in links:
        notify.push(f"📊 {session} | {label} 播放趋势", "点开看高清播放趋势图。", url=links["views"], group=session)
    notify.push(f"📋 {session} | {label} 数据表", "点开看播放/点赞数据表格。", url=table_link, group=session)
    print(f"[里程碑] {session} {label} 已推送")


if name == "__main__":
    main()
    if os.environ.get("FORCE_PUSH") == "1":
        force_test_push()
