# -*- coding: utf-8 -*-
"""
异常检测：基于“每分钟速率”+ 三条件投票。
- 速率用真实时间戳计算，消除采集间隔不均的影响
- 缺失段（间隔过大）不参与判断
- 无固定阈值，全部相对各成员自己的滚动基线
条件：
  A 偏离自己基线（中位数 + K*MAD）
  B 脱离全组（自己速率 / 全组中位速率 远高于平时比例）
  C 速率突然跳变（权重最高）
触发：C 命中即报警；或 A 与 B 同时命中也报警。
"""
import numpy as np
import config as C


def _mad(x):
    x = np.asarray(x, float)
    if len(x) == 0:
        return 1e-9
    return np.median(np.abs(x - np.median(x))) + 1e-9


def compute_rates(times_min, values):
    """
    times_min: 每个点相对起点的分钟数（float，递增）
    values: 对应累计值
    返回 (rate_times_min, rates)；跨缺失段的速率标记为 np.nan
    """
    times_min = np.asarray(times_min, float)
    values = np.asarray(values, float)
    rt, rates = [], []
    for i in range(1, len(values)):
        dt = times_min[i] - times_min[i - 1]
        if dt <= 0:
            continue
        if dt > C.GAP_THRESHOLD_MIN:
            rt.append(times_min[i]); rates.append(np.nan)  # 缺失，不参与判断
            continue
        rt.append(times_min[i]); rates.append((values[i] - values[i - 1]) / dt)
    return np.asarray(rt), np.asarray(rates)


def detect_latest(member_series, group_series_at_index):
    """
    判断“最新一个速率点”是否异常（每次新数据到来时调用）。
    member_series: 该成员的 (times_min, values)
    group_series_at_index: 函数，给定速率索引 i，返回当时全组各成员的速率 list（用于 B 条件）
    返回 dict: {is_alert, level, condA, condB, condC, rate, baseline}
              或 None（无法判断/正常）
    """
    tmin, vals = member_series
    rt, rates = compute_rates(tmin, vals)
    if len(rates) == 0:
        return None
    i = len(rates) - 1
    cur = rates[i]
    if np.isnan(cur):
        return None  # 最新点跨越缺失段，不判断

    # 基线：当前点之前窗口内的有效速率
    lo = max(0, i - C.BASELINE_WINDOW)
    base = rates[lo:i]
    base = base[~np.isnan(base)]
    if len(base) < C.MIN_POINTS_TO_JUDGE:
        return None  # 冷启动

    med = np.median(base)
    spread = _mad(base)

    # 条件 A：偏离自己基线
    condA = cur > med + C.K_DEVIATION * spread

    # 条件 B：脱离全组
    condB = False
    try:
        group_now = np.asarray(group_series_at_index(i), float)
        group_now = group_now[~np.isnan(group_now)]
        if len(group_now) >= 2:
            gmed = np.median(group_now) + 1e-9
            ratio_now = cur / gmed
            # 自己平时的相对比例
            past_ratios = []
            for j in range(lo, i):
                gj = group_series_at_index(j)
                gj = np.asarray(gj, float)
                gj = gj[~np.isnan(gj)]
                if len(gj) >= 2 and not np.isnan(rates[j]):
                    past_ratios.append(rates[j] / (np.median(gj) + 1e-9))
            ratio_base = np.median(past_ratios) if past_ratios else 1.0
            condB = ratio_now > max(C.B_RATIO_MIN, ratio_base * C.B_RATIO_MULT)
    except Exception:
        condB = False

    # 条件 C：速率突然跳变（权重最高）
    prev = rates[i - 1] if i >= 1 and not np.isnan(rates[i - 1]) else med
    condC = (cur - prev > C.K_DEVIATION * spread) and (cur > C.C_JUMP_MULT * med)

    # 触发规则：C命中即报警；或 A 与 B 同时命中
    is_alert = bool(condC or (condA and condB))
    if not is_alert:
        return None

    score = (C.W_A if condA else 0) + (C.W_B if condB else 0) + (C.W_C if condC else 0)
    level = "RED" if score >= 5 else "ORANGE"

    return {
        "is_alert": True,
        "level": level,
        "condA": bool(condA),
        "condB": bool(condB),
        "condC": bool(condC),
        "rate": float(cur),
        "baseline": float(med),
        "score": int(score),
    }
