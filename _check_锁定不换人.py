# -*- coding: utf-8 -*-
"""交战期不换人 核查（2026-10-30）

用户原话：「当正在进行一个敌人的瞄准时忽略其他锁定的敌人」。

场景：A 正在被瞄（准星附近），B 突然出现在更靠近准星的位置；同时 A 的框因为快速横移
跳出了 match_px。旧实现会立刻改用 B（"就近重选"）。现在要求：engage=True 时只咬 A。

用法：python _check_锁定不换人.py [--dir C:\\yolo-trigger]
退出码 0 = 四条断言全过。
"""
import argparse
import io
import json
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)
sys.path.insert(0, root)

import main as M  # noqa: E402

cfg = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
lk = cfg.get("lock") or {}
MATCH = float(lk.get("match_px", 180))
HOLD = float(lk.get("hold_ms", 550)) / 1000.0

A_OLD = (100, 100, 300, 400)      # 已锁定的 A（中心 200,250）
A_NEW = (420, 100, 620, 400)      # A 横移后（中心 520,250），离旧框 320px > match_px
B = (250, 100, 450, 400)          # 更靠近准星的另一个人（中心 350,250）
AIM = (350.0, 250.0)              # 准星在 B 身上


def fresh(engage):
    return {"box": A_OLD, "t": 10.0, "start": 9.0, "engage": engage,
            "engage_relax": float(lk.get("engage_relax", 2.0)),
            "engage_extra_gap_s": float(lk.get("engage_extra_gap_s", 0.40)),
            "engage_switch_max_px": float(lk.get("engage_switch_max_px", 220.0))}


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


print("=" * 72)
print("参数：match_px=%.0f hold_ms=%.0f engage_relax=%.1f extra_gap=%.2fs switch_max=%.0f"
      % (MATCH, HOLD * 1000, lk.get("engage_relax", 2.0),
         lk.get("engage_extra_gap_s", 0.40), lk.get("engage_switch_max_px", 220.0)))
print("=" * 72)

# ① engage=True：A 跳出 match_px → 必须保留 A，不许改瞄 B
st = fresh(True)
got = M.pick_locked([A_NEW, B], AIM[0], AIM[1], 10, st, 10.1, HOLD, MATCH, 0.8)
print("① engage=True，A 跳出 match_px：锁定框中心 = %s" % (center(got),))
assert got == A_OLD, "交战期竟然换人了：%s" % (got,)

# ② engage=True：A 在放宽半径内、且放宽圈里只有它一个（不会歧义）→ 用新框继续咬
st = fresh(True)
A_SLIDE = (330, 100, 530, 400)    # 中心 430,250，离旧框 230px（>match 180，<360）
got = M.pick_locked([A_SLIDE], AIM[0], AIM[1], 10, st, 10.1, HOLD, MATCH, 0.8)
print("② engage=True，放宽圈里只有 A：锁定框中心 = %s（应为 A 的新位置）" % (center(got),))
assert got == A_SLIDE, "放宽匹配没生效：%s" % (got,)

# ③ engage=False（没在瞄准）：保持旧行为 —— 就近重选，允许换到 B
st = fresh(False)
got = M.pick_locked([A_NEW, B], AIM[0], AIM[1], 10, st, 10.1, HOLD, MATCH, 0.8)
print("③ engage=False：锁定框中心 = %s（旧行为，就近重选）" % (center(got),))
assert got == B, "非交战期不该被锁死：%s" % (got,)

# ④ 旧框彻底过期（目标真的没了）→ 必须能重新锁定，否则会盯着空气不动
st = fresh(True)
st["t"] = 10.1 - (HOLD + 1.0)     # 旧框已过期
st["box"] = A_OLD
# 旧框过期后是新目标上锁 → 要过 confirm_frames（默认 2 帧），所以连喂两帧
M.pick_locked([B], AIM[0], AIM[1], 10, st, 10.100, HOLD, MATCH, 0.8)
got = M.pick_locked([B], AIM[0], AIM[1], 10, st, 10.133, HOLD, MATCH, 0.8)
print("④ engage=True 且旧框过期、只剩 B：锁定框中心 = %s（应允许重新锁定）" % (center(got),))
assert got == B, "过期后该允许重新锁定：%s" % (got,)

# ⑤ 交战期、A 仍在同一位置附近，但 B 更近准星 → 仍必须锁 A（IoU 判定）
st = fresh(True)
A_STILL = (120, 100, 320, 400)    # A 几乎没动，中心 220,250，IoU 高
B2 = (280, 100, 480, 400)         # B 中心 380,250，比 A 更靠近准星
got = M.pick_locked([A_STILL, B2], 380.0, 250.0, 10, st, 10.1, HOLD, MATCH, 0.8)
print("⑤ engage=True、B 更近准星：锁定框中心 = %s（应仍为 A）" % (center(got),))
assert got == A_STILL, "被更近的 B 抢走了：%s" % (got,)

print()
print("全部通过 ✅（交战期只咬一个人；非交战期行为不变）")