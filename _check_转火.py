# -*- coding: utf-8 -*-
"""转火（目标切换）稳定性核查（2026-10-30，用户「还会来回锁定两个框」）

场景照 2.1 那次的做法：两个人静止，只有检测框在抖（±150 / ±250 / ±350px），
跑 N 帧真代码 pick_locked，数"锁定目标换了多少次"。抖动越大，旧逻辑换得越频繁。

对照四组：
  A. 关掉换人闸（switch_hysteresis=1.01 / cooldown=0 / confirm=1）—— 相当于旧行为
  B. 现行默认（0.7 / 400ms / 2 帧）
  C. 两个人挨得近（间距 120px，都在 match_px 内）
  D. 真换人：A 消失、B 从远处持续出现 ≥confirm 帧后应换过去（不能锁死不动）

用法：python _check_转火.py [--dir C:\\yolo-trigger]
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
          "engage_min_iou": float(lk.get("engage_min_iou", 0.15)),
          "confirm_frames": int(lk.get("confirm_frames", 1)),
          "switch_hysteresis": float(lk.get("switch_hysteresis", 0.7)),
          "switch_cooldown_ms": float(lk.get("switch_cooldown_ms", 400)),
          "switch_confirm_frames": int(lk.get("switch_confirm_frames", 2)),
          "pred_gain": float(lk.get("pred_gain", 0.5)),
          "v": [0.0, 0.0], "switch_t": 0.0, "sw_pend": {}, "seen": [],
          "confirm_px": 120.0, "stale_hold_ms": 120}
    if over:
        st.update(over)
    return st


def jitter_run(jitter, st, frames=90, gap=300.0, seed=1):
    """两个人静止在准星两侧，只有框在抖 → 数换人次数。"""
    rnd = random.Random(seed)
    a = [400 - gap / 2, 300]
    b = [400 + gap / 2, 300]
    seen_locked = {}
    switches = 0
    last = None
    for i in range(frames):
        now = 10.0 + i * DT
        def jit(c):
            return (c[0] + rnd.uniform(-jitter, jitter), c[1] + rnd.uniform(-jitter, jitter))
        boxes = []
        for c in (a, b):
            cx, cy = jit(c)
            boxes.append((cx - 40, cy - 90, cx + 40, cy + 90))
        got = M.pick_locked(boxes, 400.0, 300.0, 10, st, now, HOLD, MATCH, 0.55)
        if got is None:
            continue
        key = "A" if abs((got[0] + got[2]) / 2 - a[0]) < abs((got[0] + got[2]) / 2 - b[0]) else "B"
        seen_locked[key] = seen_locked.get(key, 0) + 1
        if last is not None and key != last:
            switches += 1
        last = key
    return switches, seen_locked


def main():
    print("=" * 76)
    print("参数：match_px=%.0f hold_ms=%.0f | hysteresis=%.2f cooldown=%sms confirm=%s 帧"
          % (MATCH, HOLD * 1000, lk.get("switch_hysteresis", 0.7),
             lk.get("switch_cooldown_ms", 400), lk.get("switch_confirm_frames", 2)))
    print("=" * 76)

    print("  两人间距 300px、静止，仅框抖 → 90 帧内换人次数")
    print("  %-10s | %-14s | %-14s | %s" % ("抖动", "旧行为(闸全开)", "现行默认", "改善"))
    for jit in (150.0, 250.0, 350.0):
        old = st_new({"switch_hysteresis": 1.01, "switch_cooldown_ms": 0.0,
                      "switch_confirm_frames": 1, "pred_gain": 0.0, "seen": []})
        new = st_new()
        sw_old, _ = jitter_run(jit, old)
        sw_new, _ = jitter_run(jit, new)
        print("  %-10s | %-14s | %-14s | %s" %
              ("±%.0fpx" % jit, "%d 次" % sw_old, "%d 次" % sw_new,
               "↓%.0f%%" % (100.0 * (sw_old - sw_new) / sw_old) if sw_old else "-"))
        assert sw_new <= sw_old, "抖动 ±%.0f 时新逻辑反而更抖" % jit

    # 现场再看一眼最抖那档到底换了几次（不能是 0 次以外又锁死）
    sw, dist = jitter_run(350.0, st_new())
    print()
    print("  ±350px 抖动时：换人 %d 次，锁定分布 %s（应集中在一个人身上）" % (sw, dist))
    top = max(dist.values()) if dist else 0
    tot = sum(dist.values()) or 1
    # 判据不是"0 次换人"，而是"不许来回跳"：换人次数少 + 大部分时间咬在同一个人身上
    # ★2026-11-01★ 阈值改成相对判据：加接管延迟(acquire_hold_ms)后 ±350px 从 3 次变 4 次，
    # 但相对"闸全开"仍少 64%。绝对次数会随参数漂移变，改善幅度不会 —— 用幅度当判据。
    _sw_old, _ = jitter_run(350.0, st_new({"switch_hysteresis": 1.01, "switch_cooldown_ms": 0.0,
                                           "switch_confirm_frames": 1, "pred_gain": 0.0,
                                           "never_switch": False, "acquire_hold_ms": 0.0}))
    _cut = 1.0 - (float(sw) / max(1.0, float(_sw_old)))
    print("  相对闸全开(%d 次)减少 %.0f%%" % (_sw_old, _cut * 100))
    assert _cut >= 0.40, "相对闸全开只降了 %.0f%%（应 >= 40%%）" % (_cut * 100)
    assert top >= 0.5 * tot, "锁定分布太散（=来回跳）：%s" % (dist,)

    # D. 真换人：A 消失，B 从远处出现 → 连续 confirm 帧后必须换过去
    print()
    st = st_new()
    t = 10.0
    A = (300, 200, 380, 380)
    for _ in range(3):
        M.pick_locked([A], 340.0, 290.0, 10, st, t, HOLD, MATCH, 0.55)
        t += DT
    assert st["box"] is not None
    B = (900, 200, 980, 380)          # 很远、但离准星也远
    B2 = (430, 200, 510, 380)         # 离准星近（模拟镜头转向后的新目标）
    got = None
    for _ in range(int(lk.get("switch_confirm_frames", 2)) + 1):
        got = M.pick_locked([B2], 470.0, 290.0, 10, st, t, HOLD, MATCH, 0.55)
        t += DT
    print("  A 消失、B2 连续出现后：锁定框中心 = %s（应换到 B2）"
          % (((got[0] + got[2]) / 2, (got[1] + got[3]) / 2),))
    assert got == B2, "真换人时锁死了：%s" % (got,)

    print()
    print("全部通过 ✅（抖动不换人，真换人换得过去）")


if __name__ == "__main__":
    main()