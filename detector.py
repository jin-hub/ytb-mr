# -*- coding: utf-8 -*-
"""
异常检测：台阶检测法（抓机器人刷量造成的“孤立陡峰”）。

思路（全部相对各成员自己的“平时速度”，与纵轴缩放无关）：
  1. 把累计值转成“每分钟速度”（相邻两点的增量/分钟），缺失段不算。
  2. 找“台阶”：某个速度点冲到平时速度的 SPIKE_MULT 倍以上（明显变陡），
     且之后 FALLBACK_LOOKAHEAD 个点内又落回平时的 FALLBACK_MULT 倍以内（回落）。
     —— 必须“冲上去又落回来”，一直陡不回落的不算（可能真的火了）。
  3. 排除“全员一起涨”：该突变时刻，如果同时陡峰的成员超过 GROUP_SPIKE_FRAC，
     判定为自然现象，不报。

因为要等“回落”确认，报警会比突变事件晚约 10~15 分钟，这是设计上的取舍（换误报大幅减少）。
"""
import numpy as np
import config as C


def compute_rates(times_min, values):
    """
    times_min: 每个点相对起点的分钟数（递增）
    values: 对应累计值
    返回 (rate_times_min, rates)：每段“每分钟速度”；跨缺失段标记为 nan。
    rate 的第 i 个值对应原始数据第 i+1 个点的时刻。
    """
    times_min = np.asarray(times_min, float)
    values = np.asarray(values, float)
    rt, rates = [], []
    for i in range(1, len(values)):
        dt = times_min[i] - times_min[i - 1]
        if dt <= 0:
            continue
        if dt > C.GAP_THRESHOLD_MIN:
            rt.append(times_min[i]); rates.append(np.nan)
            continue
        rt.append(times_min[i]); rates.append((values[i] - values[i - 1]) / dt)
    return np.asarray(rt), np.asarray(rates)


def _baseline(rates, idx):
    """idx 之前窗口内的平时速度（中位数）。不足则返回 None。"""
    lo = max(0, idx - C.BASELINE_WINDOW)
    base = rates[lo:idx]
    base = base[~np.isnan(base)]
    if len(base) < C.MIN_POINTS_TO_JUDGE:
        return None
    return float(np.median(base))


def _is_spike_at(rates, idx):
    """
    判断 rates 在 idx 处是否构成“台阶”（陡峰+回落）。
    返回 (是否台阶, 平时速度, 该点速度)。
    """
    if idx >= len(rates) or np.isnan(rates[idx]):
        return False, None, None
    med = _baseline(rates, idx)
    if med is None or med <= 0:
        return False, None, None
    # 防低基数虚高：平时涨得太慢的成员（平直线），不参与台阶判断
    if med < C.MIN_BASELINE_RATE:
        return False, med, rates[idx]
    cur = rates[idx]
    # 陡峰条件①：当前速度 ≥ 平时的 SPIKE_MULT 倍
    if cur < C.SPIKE_MULT * med:
        return False, med, cur
    # 陡峰条件②（双保险）：这一段“多涨出来的累计量”要够大
    #   速度是“每分钟”，乘以该段时长（约5分钟）估算多涨的绝对量
    extra_per_min = cur - med
    seg_minutes = 5.0
    if extra_per_min * seg_minutes < C.MIN_SPIKE_ABS:
        return False, med, cur
    # 回落条件：之后 LOOKAHEAD 个点内，必须出现落回平时 FALLBACK_MULT 倍以内
    fell_back = False
    for j in range(idx + 1, min(len(rates), idx + 1 + C.FALLBACK_LOOKAHEAD)):
        if np.isnan(rates[j]):
            continue
        if rates[j] <= C.FALLBACK_MULT * med:
            fell_back = True
            break
    return (fell_back, med, cur)


def detect_step(member_series, group_rates_at):
    """
    扫描该成员的速度序列，找最近一个“已确认回落”的台阶。
    member_series: (times_min, values) 该成员
    group_rates_at: 函数 idx -> 当时全组各成员在该 idx 的速度 list（用于全员判断）

    返回 dict 或 None：
      {spike_rate_idx, spike_time_min, baseline, spike_rate}
    spike_time_min 是突变点对应的“相对起点分钟数”，供画图定位。
    """
    tmin, vals = member_series
    rt, rates = compute_rates(tmin, vals)
    if len(rates) < C.MIN_POINTS_TO_JUDGE + 2:
        return None

    # 只在“已经能看到回落”的范围内找台阶：即 idx 后面至少有1个点
    # 从较新往较旧扫，优先报最近的台阶
    last_checkable = len(rates) - 2  # 保证 idx 后至少有一个点可判断回落
    for idx in range(last_checkable, -1, -1):
        ok, med, cur = _is_spike_at(rates, idx)
        if not ok:
            continue
        # 条件③：全员一起涨？统计该 idx 同时陡峰的成员比例
        try:
            group = group_rates_at(idx)  # list of (rates_array) 各成员
            spiking = 0
            total = 0
            for g_rates in group:
                if g_rates is None or idx >= len(g_rates) or np.isnan(g_rates[idx]):
                    continue
                gmed = _baseline(g_rates, idx)
                if gmed is None or gmed <= 0:
                    continue
                total += 1
                if g_rates[idx] >= C.SPIKE_MULT * gmed:
                    spiking += 1
            if total >= 2 and (spiking / total) > C.GROUP_SPIKE_FRAC:
                continue  # 大家一起陡 → 自然现象，跳过
        except Exception:
            pass
        return {
            "spike_rate_idx": int(idx),
            "spike_time_min": float(rt[idx]),
            "baseline": float(med),
            "spike_rate": float(cur),
        }
    return None
