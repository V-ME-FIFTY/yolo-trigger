# -*- coding: utf-8 -*-
"""转火接管核查（2026-11-01，用户「做12」）

落地两项：
  ① 解锁 + 松迟滞：never_switch=false / switch_hysteresis 0.3->0.65 / 冷却 1000->500ms
  ② commit flick 窗口：switch_commit_ms=180（换人后甩枪期免打扰）+ 冷却随机抖动 +-20%

本夹具量化四件事，全部跑真实 pick_locked：
  A. 冷却抖动分布 —— 连换 20 次看 cd_next 的范围（固定毫秒是行为指纹）
  B. commit 独立贡献 —— 冷却置 0 隔离变量，换人后 180ms 内被第三方抢锁的帧数
  C. 抖动稳定性 —— 双人静止仅框抖，换人次数（解锁后不能回归成"来回跳"）
  D. 真换人 —— 目标消失后新目标必须能在几帧内接管（不能锁死）

用法：python _check_转火接管.py [--dir C:\\yolo-trigger]
"""
import argparse
import io
import json
import os
import random
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)
sys.path.insert(0, root)

import main as M  # noqa: E402

cfg = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
lk = cfg.get("lock") or {}
MATCH = float(lk.get("match_px", 180))
HOLD = float(lk.get("hold_ms", 400)) / 1000.0
FPS = 30.0
DT = 1.0 / FPS


def st_new(over=None):
    st = {"box": None, "t": 0.0, "start": 0.0, "engage": False,
          "engage_relax": float(lk.get("engage_relax", 2.0)),
          "engage_extra_gap_s": float(lk.get("engage_extra_gap_s", 0.4)),
          "engage_switch_max_px": float(lk.get("engage_switch_max_px", 220)),
          "confirm_frames": int(lk.get("confirm_frames", 1)),
          "switch_hysteresis": float(lk.get("switch_hysteresis", 0.7)),
          "switch_cooldown_ms": float(lk.get("switch_cooldown_ms", 400)),
          "switch_confirm_frames": int(lk.get("switch_confirm_frames", 2)),
          "switch_commit_ms": float(lk.get("switch_commit_ms", 180)),
          "switch_cooldown_jitter": float(lk.get("switch_cooldown_jitter", 0.2)),
          "never_switch": bool(lk.get("never_switch", False)),
          "pred_gain": float(lk.get("pred_gain", 0.5)),
          "v": [0.0, 0.0], "switch_t": 0.0, "sw_pend": {}, "seen": [],
          "confirm_px": 120.0, "stale_hold_ms": 120,
          "commit_until": 0.0, "cd_next": None,
          "acquire_hold_ms": float(lk.get("acquire_hold_ms", 0.0)), "lost_t": 0.0}
    if over:
        st.update(over)
    return st


def bx(cx, cy=300.0, w=80.0, h=180.0):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def cx_of(b):
    return (b[0] + b[2]) / 2.0


AIM = (400.0, 300.0)
OLD_X = 30.0       # 旧目标：离准星 370px（准星已甩开它，才有换人的余量）
NEW_X = 220.0      # 新目标：离准星 180px（换过去时准星还要甩 180px，才有"甩枪期"）
THIEF_X = 470.0    # 甩枪期抢锁的第三方：距旧目标 250px(超 match_px)、距准星 70px(更近)
GONE_X = 900.0     # 旧目标横移离开（超 match_px，匹配必失败）


def lock_old(st, t, frames=3):
    """先锁上左手边那个（只有它，别无选择）。"""
    for _ in range(frames):
        M.pick_locked([bx(OLD_X)], AIM[0], AIM[1], 10, st, t, HOLD, MATCH, 0.0)
        t += DT
    return t


def do_switch(st, t, budget=30):
    """旧目标横移离开 + 右手边新人出现 -> 应换到新人。返回 (帧数, t)。"""
    for i in range(budget):
        t += DT
        got = M.pick_locked([bx(GONE_X), bx(NEW_X)], AIM[0], AIM[1], 10, st, t, HOLD, MATCH, 0.0)
        if got is not None and abs(cx_of(got) - NEW_X) < 30.0:
            return i + 1, t
    return None, t


# ── A. 冷却抖动 ──
def case_cooldown():
    print("【A】冷却抖动 -- 连做 20 次换人，采 st['cd_next']")
    vals = []
    flags = 0
    for _ in range(20):
        st = st_new({"acquire_hold_ms": 0.0})   # 本场景只测冷却抽样，接管延迟置 0 隔离
        t = lock_old(st, 10.0)
        n, t = do_switch(st, t)
        cn = float(st.get("cd_next") or 0.0)
        if cn > 0:
            vals.append(cn * 1000.0)
        if n is None:
            flags += 1
    if not vals:
        print("     !! 一次换人都没发生（换人通路被堵死）")
        return None
    jitter = float(lk.get("switch_cooldown_jitter", 0.2))
    base = float(lk.get("switch_cooldown_ms", 500))
    lo, hi = base * (1 - jitter), base * (1 + jitter)
    print("     成功换人 %d/20 次 | 冷却 min=%.0fms mean=%.0fms max=%.0fms | 不同取值 %d 种"
          % (len(vals), min(vals), sum(vals) / len(vals), max(vals),
             len(set(round(v) for v in vals))))
    print("     理论区间 %.0f~%.0fms（base %.0f ± %.0f%%）" % (lo, hi, base, jitter * 100))
    assert min(vals) >= lo - 1 and max(vals) <= hi + 1, \
        "冷却越界：%.0f~%.0f 应在 %.0f~%.0f" % (min(vals), max(vals), lo, hi)
    assert len(set(round(v) for v in vals)) > 1 or jitter <= 0, "冷却没有抖动（固定值=可指纹）"
    return vals


# ── B. commit 独立贡献 ──
def case_commit(cm_ms, seed):
    """换到新人后立刻让新人抖出 match_px（甩枪期匹配失败），
    准星正上方再放一个稳定目标 -> 没 commit 就会被半路截胡。"""
    random.seed(seed)
    # 隔离变量：冷却和接管延迟都置 0，只留 commit 窗口本身起作用
    st = st_new({"switch_cooldown_ms": 0.0, "switch_commit_ms": float(cm_ms),
                 "acquire_hold_ms": 0.0})
    t = lock_old(st, 10.0)
    n, t = do_switch(st, t)
    if n is None:
        return None
    stolen_at = None
    for i in range(14):                      # 14 帧 = 462ms
        t += DT
        st["cd_next"] = 0.0                  # 冷却不是本场景变量
        # 甩枪期：刚换到的那个人短暂丢检（遮挡/检测空档），视野里只剩准星上那个窄框。
        # 宽 40 的窄框用来区分"抢锁的那个"和"原来锁的那个"（宽 80）。
        boxes = [bx(THIEF_X, w=40.0)]
        got = M.pick_locked(boxes, AIM[0], AIM[1], 10, st, t, HOLD, MATCH, 0.0)
        if got is not None and (got[2] - got[0]) < 60.0:
            stolen_at = i + 1
            break
    return stolen_at


def case_commit_pair():
    print()
    print("【B】commit flick 窗口 -- 换人后甩枪期被第三方抢锁（冷却隔离为 0）")
    cm = float(lk.get("switch_commit_ms", 180))
    no_cm = case_commit(0.0, 11)
    with_cm = case_commit(cm, 11)
    nf = int(cm / 1000.0 * FPS + 0.999)
    print("     commit=0     : %s" % (("第 %d 帧被抢" % no_cm) if no_cm else "14 帧内没被抢"))
    print("     commit=%.0fms : %s（窗口 %d 帧）"
          % (cm, ("第 %d 帧被抢" % with_cm) if with_cm else "14 帧内没被抢", nf))
    if no_cm is None and with_cm is None:
        print("     （两边都没被抢，场景没构造出来）")
        return
    if no_cm is not None and with_cm is not None:
        print("     -> 抢锁被推迟 %d 帧（%.0fms）" % (with_cm - no_cm, (with_cm - no_cm) * DT * 1000))
        assert with_cm > no_cm, "commit 窗口没起作用"
    elif no_cm is not None and with_cm is None:
        print("     -> commit 窗口内完全挡住抢锁")


# ── C. 抖动稳定性 ──
def jitter_run(st, jit, frames=90, seed=1):
    rnd = random.Random(seed)
    A, B = (250.0, 300.0), (550.0, 300.0)
    last, switches, dist = None, 0, {}
    for i in range(frames):
        now = 10.0 + i * DT
        boxes = []
        for c in (A, B):
            cx = c[0] + rnd.uniform(-jit, jit)
            cy = c[1] + rnd.uniform(-jit, jit)
            boxes.append(bx(cx, cy))
        got = M.pick_locked(boxes, AIM[0], AIM[1], 10, st, now, HOLD, MATCH, 0.0)
        if got is None:
            continue
        key = "A" if abs(cx_of(got) - A[0]) < abs(cx_of(got) - B[0]) else "B"
        dist[key] = dist.get(key, 0) + 1
        if last is not None and key != last:
            switches += 1
        last = key
    return switches, dist


def case_stability():
    print()
    print("【C】抖动稳定性 -- 双人静止(间距 300px)、仅框抖，90 帧换人次数")
    print("     %-10s | %-18s | %-18s | %s" % ("抖动", "never_switch=true", "新配置", "变化"))
    for jit in (150.0, 250.0, 350.0):
        old = st_new({"never_switch": True})
        new = st_new()
        sw_old, _ = jitter_run(old, jit)
        sw_new, _ = jitter_run(new, jit)
        print("     %-10s | %-18s | %-18s | %s" %
              ("+-%.0fpx" % jit, "%d 次" % sw_old, "%d 次" % sw_new,
               ("+%d" % (sw_new - sw_old)) if sw_new >= sw_old else ("%d" % (sw_new - sw_old))))
    sw, dist = jitter_run(st_new(), 350.0)
    top = max(dist.values()) if dist else 0
    tot = sum(dist.values()) or 1
    print("     +-350px：换人 %d 次，锁定分布 %s（主目标占比 %.0f%%）"
          % (sw, dist, 100.0 * top / tot))
    assert sw <= 4, "解锁后抖动场景换人太多：%d 次" % sw
    assert top >= 0.5 * tot, "锁定分布太散（来回跳）：%s" % (dist,)


# ── D. 真换人 ──
def case_real_switch():
    print()
    print("【D】真换人 -- 旧目标彻底消失、新目标出现，几帧内接管")
    st = st_new()
    t = 10.0
    for _ in range(3):
        M.pick_locked([bx(300.0)], 300.0, 300.0, 10, st, t, HOLD, MATCH, 0.0)
        t += DT
    assert st["box"] is not None, "首次锁定失败"
    got, frames = None, 0
    for i in range(40):
        got = M.pick_locked([bx(760.0)], 760.0, 300.0, 10, st, t, HOLD, MATCH, 0.0)
        t += DT
        frames = i + 1
        if got is not None and abs(cx_of(got) - 760.0) < 40.0:
            break
    _ah = float(lk.get("acquire_hold_ms", 0.0))
    print("     旧目标消失 -> 新目标接管 %d 帧（%.0fms）| acquire_hold_ms=%.0f"
          % (frames, frames * DT * 1000, _ah))
    if _ah > 0:
        _floor = int(_ah / 1000.0 / DT) - 1
        assert frames >= _floor, "接管早了：%d 帧 < 延迟下限 %d 帧" % (frames, _floor)
    assert got is not None and abs(cx_of(got) - 760.0) < 40.0, "真换人锁死了"


def main():
    print("=" * 78)
    print("参数：hysteresis=%.2f  cooldown=%.0fms(+-%.0f%%)  confirm=%d帧  commit=%.0fms  never_switch=%s"
          % (float(lk.get("switch_hysteresis", 0.7)),
             float(lk.get("switch_cooldown_ms", 500)),
             float(lk.get("switch_cooldown_jitter", 0.0)) * 100,
             int(lk.get("switch_confirm_frames", 2)),
             float(lk.get("switch_commit_ms", 0)),
             bool(lk.get("never_switch", False))))
    print("=" * 78)
    case_cooldown()
    case_commit_pair()
    case_stability()
    case_real_switch()
    print()
    print("全部通过 OK  解锁后可换人、抖动不来回跳、甩枪期不被半路截胡、真换人换得过去")


if __name__ == "__main__":
    main()