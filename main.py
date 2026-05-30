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
import plotting
import notify

KST = ZoneInfo(C.TIMEZONE)
REPO = os.environ.get("GITHUB_REPOSITORY", "")          # "user/repo"
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
SHEET_ID = os.environ["SHEET_ID"]

# 待推送队列：所有图先生成、存盘，等 commit+push 成功后再统一发 Bark，
# 这样点开通知图片一定已在线（不再等1分钟、不再404）。
_PENDING_PUSH = []


def queue_push(title, body, url=None, image=None, group=None):
    """把一条待推送加入队列，不立即发送。"""
    _PENDING_PUSH.append({"title": title, "body": body, "url": url,
                          "image": image, "group": group})


def flush_pushes():
    """统一发送队列里的所有推送（在图片已 push 到仓库之后调用）。"""
    for p in _PENDING_PUSH:
        try:
            notify.push(p["title"], p["body"], url=p["url"],
                        image=p["image"], group=p["group"])
        except Exception as e:
            print("推送失败:", e)
    print(f"[推送] 已发送 {len(_PENDING_PUSH)} 条")
    _PENDING_PUSH.clear()


def raw_url(rel_path):
    """把仓库内文件路径转成可公开访问的 raw 链接（看高清图用）。"""
    rel = rel_path.split("ytb-monitor/")[-1] if "ytb-monitor/" in rel_path else os.path.basename(rel_path)
    rel = os.path.relpath(rel_path, os.path.dirname(__file__))
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}"


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(s):
    return datetime.fromisoformat(s)


def commit_and_push():
    """
    把 data/（CSV + 图）提交并推送到仓库。
    带重试，解决多次运行并发导致的 git 冲突（之前 Commit data 失败的根因）。
    成功返回 True。
    """
    import subprocess

    def run(cmd):
        return subprocess.run(cmd, shell=True, cwd=os.path.dirname(__file__) or ".",
                              capture_output=True, text=True)

    run('git config user.name "github-actions"')
    run('git config user.email "actions@github.com"')
    # 确保在 main 分支上（GitHub Actions 默认可能是 detached HEAD）
    run(f"git checkout -B {BRANCH}")
    run("git add data/")
    # 没有变化就跳过
    staged = run("git diff --staged --quiet")
    if staged.returncode == 0:
        print("无数据变化，无需提交。")
        return True
    run(f'git commit -m "update data {now_utc().isoformat()}"')

    # 重试推送：每次先 rebase 远程最新；冲突则放弃本次合并、以远程为准，
    # 本地新数据下一轮会重新采集补上（绝不把冲突标记写进文件）。
    for attempt in range(5):
        run("git fetch origin")
        rb = run(f"git rebase origin/{BRANCH}")
        if rb.returncode != 0:
            run("git rebase --abort")
            # 以远程为准重置，丢弃本地这次提交（数据下轮补），保证文件干净
            run(f"git reset --hard origin/{BRANCH}")
            return True  # 本轮放弃提交，但仓库是干净的，不报错
        push = run(f"git push origin HEAD:{BRANCH}")
        if push.returncode == 0:
            print(f"[提交] 数据已推送（第{attempt+1}次尝试）")
            return True
        print(f"[提交] 推送失败，重试 {attempt+1}/5: {push.stderr.strip()[:120]}")
    print("[提交] 多次重试后仍失败")
    return False


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
            }
    # 表格里不再“运行”的（你手动勾了停止）：删除其运行态，下次再运行视为新一轮
    for key in list(state.keys()):
        if key not in running_keys:
            state.pop(key, None)

    # 注意：满 72h 后不再推图，是因为最后一个里程碑就是 72h，推完后
    # pushed_milestones 已包含全部里程碑，循环自然不再推任何图。
    # 此处【不删除 state】，这样：① 采集照常继续（只要表格状态是“运行”）
    # ② 不会因删状态而重新计时。你无需手动改“停止”。
    # 仅打印提示，便于你了解某场已过 72h。
    for key in list(running_keys):
        st = state.get(key)
        if st:
            elapsed_h = (now - parse_iso(st["start_utc"])).total_seconds() / 3600
            if elapsed_h >= C.AUTO_STOP_HOURS:
                print(f"{key} 已满 {C.AUTO_STOP_HOURS}h：不再推图，但继续记录数据。")

    # ---- 4. 载入全量时间序列做检测与画图 ----
    ts = storage.load_timeseries()
    df = pd.DataFrame(ts)
    if not df.empty:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        df["time_kst"] = df["timestamp_utc"].dt.tz_convert(KST)
        for col in ["views", "likes"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ---- 5. 里程碑定时推送（按场）----
    sessions = list(dict.fromkeys(it["session"] for it in items))
    for session in sessions:
        keys = [k for k in state if state[k]["session"] == session]
        if not keys:
            continue
        start = min(parse_iso(state[k]["start_utc"]) for k in keys)
        elapsed_min = (now - start).total_seconds() / 60
        pushed = set()
        for k in keys:
            pushed |= set(state[k]["pushed_milestones"])

        for mm, win in C.PUSH_MILESTONES:
            if mm in pushed:
                continue
            if elapsed_min >= mm:
                push_milestone(session, mm, win, df, start, keys, state)
                for k in keys:
                    if mm not in state[k]["pushed_milestones"]:
                        state[k]["pushed_milestones"].append(mm)

    storage.save_state(state)
    print("本轮运行完成。")


def _fmt_label(mm):
    """里程碑分钟 -> 人类可读标签：30min/1h/3h/6h/24h/48h/72h"""
    if mm < 60:
        return f"{mm}min"
    return f"{mm//60}h"


def push_milestone(session, mm, win, df, start, keys, state):
    """
    到达里程碑：推 2 张趋势图（조회수+좋아요），范围 = start+win[0] ~ start+win[1] 分钟。
    若该里程碑在 TABLE_MILESTONES 中，再推 1 张“排名快照”数据表（当前时刻）。
    """
    lo = start + timedelta(minutes=win[0])
    hi = start + timedelta(minutes=win[1])
    dseg = df[(df["session"] == session) &
              (df["timestamp_utc"] >= lo) &
              (df["timestamp_utc"] <= hi)].copy()
    if dseg.empty:
        print(f"[里程碑] {session} {_fmt_label(mm)} 区间内无数据，跳过")
        return

    label = _fmt_label(mm)
    # 时间范围文字（KST），用于图标题第二行
    lo_kst = lo.astimezone(KST).strftime("%m/%d %H:%M")
    hi_kst = hi.astimezone(KST).strftime("%m/%d %H:%M")
    range_txt = f"{lo_kst}~{hi_kst} KST"

    # ---- 2 张趋势图 ----
    for metric in C.METRICS:
        long = dseg.dropna(subset=[metric])[["time_kst", "member", metric]].rename(columns={metric: "value"})
        if long.empty:
            continue
        fname = f"{plotting._safe(session)}_{metric}_{mm}min.png"
        # 趋势图标题两行（图内不放 emoji，避免 matplotlib 显示方框；emoji 留在推送标题里）
        title2 = f"{session} | {C.METRIC_CN[metric]}\n{range_txt}"
        path = plotting.plot_trend(long, metric, session, KST,
                                   window=None, fname=fname, title_override=title2)
        link = raw_url(path)
        queue_push(f"📈 {session} | {C.METRIC_CN[metric]}",
                   f"⏰ {range_txt}", url=link, image=link, group=session)

    # ---- 数据表（排名快照，仅特定里程碑）----
    if mm in C.TABLE_MILESTONES:
        # 取“当前时刻”每个成员最新一条数据做排名
        snap = dseg.sort_values("timestamp_utc").groupby("member").tail(1)
        snap_time = dseg["timestamp_utc"].max().astimezone(KST).strftime("%m/%d %H:%M")
        table_fname = f"{plotting._safe(session)}_rank_{mm}min.png"
        table_title = f"📝 {session} | {label} | {snap_time} KST"
        tpath = plotting.plot_rank_table(snap, session, table_title, fname=table_fname)
        tlink = raw_url(tpath)
        queue_push(f"📝 {session} | {label}",
                   f"{snap_time} KST", url=tlink, image=tlink, group=session)

    print(f"[里程碑] {session} {label} 已入队（范围 {range_txt}）")


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

        _ts = now_utc().strftime("%Y%m%d%H%M%S")
        if want_views:
            long = dseg.dropna(subset=["views"])[["time_kst", "member", "views"]].rename(columns={"views": "value"})
            if not long.empty:
                p = plotting.plot_trend(long, "views", session, KST, fname=f"M_{plotting._safe(session)}_views_{_ts}.png")
                queue_push(f"🔔 {session} | {C.METRIC_CN['views']}", "", url=raw_url(p), image=raw_url(p), group=session)
        if want_likes:
            long = dseg.dropna(subset=["likes"])[["time_kst", "member", "likes"]].rename(columns={"likes": "value"})
            if not long.empty:
                p = plotting.plot_trend(long, "likes", session, KST, fname=f"M_{plotting._safe(session)}_likes_{_ts}.png")
                queue_push(f"🔔 {session} | {C.METRIC_CN['likes']}", "", url=raw_url(p), image=raw_url(p), group=session)
        if want_table:
            longt = dseg.melt(id_vars=["timestamp_utc", "time_kst", "member"],
                              value_vars=C.METRICS, var_name="metric", value_name="value").dropna(subset=["value"])
            if not longt.empty:
                tp = plotting.plot_table(longt, session, KST, fname=f"M_{plotting._safe(session)}_table_{_ts}.png")
                queue_push(f"🔔 {session} | 데이터", "", url=raw_url(tp), image=raw_url(tp), group=session)
        print(f"[手动推送] {session} 已推送")


if __name__ == "__main__":
    # 1. 采集+检测+里程碑（图都生成好、推送先入队，不立即发）
    main()
    # 2. 手动触发额外做灵活全量推送（由 do_push 控制，cron-job 传 no 不刷屏）
    _do_push = os.environ.get("MANUAL_DO_PUSH", "yes").strip().lower()
    _is_manual = os.environ.get("MANUAL_TRIGGER") == "true"
    if _is_manual and _do_push not in ("no", "false", "0", ""):
        manual_push()
    # 3. 先把数据和图提交并推送到仓库（带冲突重试）
    pushed_ok = commit_and_push()
    # 4. 图已在线后，再统一发 Bark 通知（点开即有图，不再等/不再404）
    if pushed_ok:
        flush_pushes()
    else:
        # 万一 push 失败，仍尝试发通知（图可能稍后到），不让通知彻底丢失
        print("[警告] 数据推送失败，仍尝试发送通知")
        flush_pushes()
