# -*- coding: utf-8 -*-
"""「锁空白是粉框」核查（2026-10-30）

粉框 = 锁定框（main.py 画框颜色 255,0,255）。它落在空白处 = 锁到了假检出 / 或旧框残留。
本脚本分两段验：

A. 锁定闸 filter_lock_gate（只拦锁、不影响绿框显示）
   · 真人（关键点 10~17 个、宽高比 0.31~1.37）→ 放行
   · 地面杂物（宽高比 3+）、枪身横块、无姿态输出、碎小框 → 拦下

B. 残留：目标彻底消失后
   · pick_locked 立刻把锁清掉（不再返回旧框）
   · 主循环在"没有锁定"分支会清 pose 部位/瞄准点 EMA/目标速度/提前量，并把预算清零
     （= 跟随线程立刻停手，不会继续往空白处拉）

用法：python _check_锁空白.py [--dir C:\\yolo-trigger]
"""
import argparse
import io
import json
import os
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)
sys.path.insert(0, root)

import main as M  # noqa: E402

cfg = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
src = io.open(os.path.join(root, "main.py"), encoding="utf-8").read()
W, H = 800, 800


def person(cx, cy, h, n_kp=12, cf=0.8):
    pts = [[cx, cy - h * 0.3] for _ in range(17)]
    cfs = [0.0] * 17
    for i in range(n_kp):
        cfs[i] = cf
    return pts, cfs


def case(name, box, kp, expect_keep):
    boxes = [box]
    kps = [kp[0]] if kp else None
    cfs = [kp[1]] if kp else None
    kb, _, _, n = M.filter_lock_gate(boxes, kps, cfs, cfg, W, H)
    got = len(kb) == 1
    flag = "✅" if got == expect_keep else "✗"
    print("  %s %-26s %-24s 期望%s 实际%s" %
          (flag, name, str(box), "锁" if expect_keep else "不锁", "锁" if got else "不锁"))
    return got == expect_keep


print("=" * 80)
print("A. 锁定闸 lock_gate = %s" % json.dumps(cfg.get("lock_gate"), ensure_ascii=False))
print("=" * 80)
ok = True
ok &= case("真人·中距(12 关键点)", (340, 260, 460, 620), person(400, 440, 360), True)
ok &= case("真人·贴脸(17 关键点)", (300, 150, 500, 660), person(400, 400, 500, 17), True)
ok &= case("真人·宽高比 1.37(侧身)", (330, 280, 470, 620), person(400, 450, 340), True)
ok &= case("地面杂物(横扁 4.0)", (120, 560, 680, 700), person(400, 630, 140), False)
ok &= case("枪身横块(1.8)", (200, 600, 560, 800), person(380, 700, 200), False)
ok &= case("假框·关键点全低置信", (200, 200, 380, 600),
            ([[300.0, 400.0]] * 17, [0.10] * 17), False)
ok &= case("无姿态数据(只有框)", (200, 200, 380, 600), None, True)   # 没关键点就只看几何
ok &= case("碎小框(框高 2%)", (300, 300, 320, 316), person(310, 308, 16, 3), False)
assert ok, "锁定闸判定不符预期"

print()
print("=" * 80)
print("B. 残留清理")
print("=" * 80)
st = {"box": None, "t": 0.0, "start": 0.0, "engage": True,
      "confirm_frames": 1, "switch_hysteresis": 0.7, "switch_cooldown_ms": 400,
      "switch_confirm_frames": 2, "pred_gain": 0.5, "v": [0.0, 0.0],
      "switch_t": 0.0, "sw_pend": {}, "seen": {},
      "engage_relax": 2.0, "engage_extra_gap_s": 0.4, "engage_switch_max_px": 220,
      "engage_min_iou": 0.15, "stale_hold_ms": 120}
A = (300, 200, 380, 380)
t = 10.0
for _ in range(3):
    M.pick_locked([A], 340.0, 290.0, 10, st, t, 0.4, 180.0, 0.55)
    t += 1 / 30.0
assert st["box"] is not None, "正常目标没锁上"
prev_v = st["v"][:]
# 目标彻底消失（交战期也一样）
got = M.pick_locked([], 340.0, 290.0, 10, st, t, 0.4, 180.0, 0.55)
print("  目标消失那一帧：pick_locked -> %s，st['box'] = %s" % (got, st["box"]))
assert got is None and st["box"] is None, "消失后仍返回旧框 = 粉框锁空白"
assert st["v"] == [0.0, 0.0], "速度残留未清：%s（会导致下一次预测匹配跑偏）" % (st["v"],)
print("  ✅ 立刻清锁，速度预测也清零（之前交战期会拿旧框继续压 0.8s）")

checks = [
    ("没锁定时清 pose 部位", r"aim_pose_part = None\s*\n\s*aim_pose_lost = 0\s*\n\s*aim_px_sm = None"),
    ("没锁定时清目标速度/提前量", r"aim_vx = aim_vy = 0\.0\s*\n\s*aim_lx = aim_ly = 0\.0"),
    ("没锁定时预算清零（跟随线程停手）", r'aim_st\["budget"\] = 0\.0\s*# 预算清零'),
    ("保持态也不许继续花预算", r'aim_st\["hold"\] = True\s*\n\s*aim_st\["budget"\] = 0\.0'),
    ("锁定闸在 pick_locked 之前生效", r'_lock_boxes, _lk_k, _lk_c, _lg_n = filter_lock_gate'),
    ("pick_locked 用过滤后的列表", r'pick_locked\(_lock_boxes'),
]
for name, pat in checks:
    hit = bool(re.search(pat, src, re.S))
    ok = ok and hit
    print("  %s %s" % ("✅" if hit else "✗ 未找到:", name))

print()
print("全部通过 ✅" if ok else "✗ 有未通过项")
if not ok:
    raise SystemExit(1)