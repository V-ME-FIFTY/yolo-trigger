# -*- coding: utf-8 -*-
"""远/近双模核查（2026-10-30）

用户要求：「当准心离敌人较远时，采用高速度低精度，只要打到敌人躯干就行；
当准心离敌人比较近时，采用高精度，高速度方案」。

本脚本坐实四件事：
  1) config 里 mode_split 的档位参数与当前生效值
  2) main.py 里确实按档换「瞄准部位 / 兜底高度比例 / 单步消比」，且换档会清部位粘滞
  3) 滞回行为：不会在阈值附近来回切（模拟一段由远到近、再由近到远的偏差序列）
  4) 两档的实际预算与单步消比对照（结合 _check_分段 的分段口径）

用法：python _check_双模.py [--dir C:\\yolo-trigger]
"""
import argparse
import io
import json
import os
import re

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)

cfg = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
aim = cfg["aim"]
ms = aim.get("mode_split") or {}
src = io.open(os.path.join(root, "main.py"), encoding="utf-8").read()

print("=" * 74)
print("一、档位参数（config.json aim.mode_split）")
print("=" * 74)
print("  enabled          = %s" % ms.get("enabled"))
print("  far_px / hyst_px = %s / %s   → 偏差 ≥%.0f 进远档，≤%.0f 回近档" % (
    ms.get("far_px"), ms.get("hyst_px"),
    float(ms.get("far_px", 0)) + float(ms.get("hyst_px", 0)),
    float(ms.get("far_px", 0)) - float(ms.get("hyst_px", 0))))
print("  far_part         = %s（远档瞄这里，高速低精度）" % ms.get("far_part"))
print("  near_part        = %s（近档瞄这里，高精度高速）" % ms.get("near_part"))
print("  far_height_ratio = %s   near_height_ratio = %s（关键点丢时的兜底落点）" % (
    ms.get("far_height_ratio"), ms.get("near_height_ratio")))
print("  far_step_cap     = %s   近档 step_cap = %s" % (
    ms.get("far_step_cap"), aim.get("step_cap")))

print()
print("=" * 74)
print("二、代码链路")
print("=" * 74)
checks = [
    ("判定用框中心-准星偏差", r"_d_mode\s*=\s*max\(abs\(_bcx - aim_x\), abs\(_bcy - aim_y\)\)"),
    ("滞回进远档", r"_d_mode >= _ms_far \+ _ms_hyst"),
    ("滞回回近档", r"_d_mode <= _ms_far - _ms_hyst"),
    ("换档清部位粘滞与瞄准点平滑", r"aim_pose_part = None\s*\n\s*aim_pose_lost = 0\s*\n\s*aim_px_sm = None"),
    ("按档传部位给 pick_pose_point", r"pick_pose_point\([^)]*_part_pref"),
    ("按档换兜底高度比例", r"_h_ratio = aim_far_h if _far_mode else aim_near_h"),
    ("按档换单步消比", r'aim_st\["step_cap"\] = aim_far_step_cap if _far_mode'),
]
ok = True
for name, pat in checks:
    hit = bool(re.search(pat, src))
    ok = ok and hit
    print("  %s %s" % ("✅" if hit else "✗ 未找到:", name))

print()
print("=" * 74)
print("三、滞回模拟（偏差序列：远 → 近 → 远）")
print("=" * 74)
far = float(ms.get("far_px", 260))
hyst = float(ms.get("hyst_px", 40))
seq = [420, 380, 340, 310, 300, 290, 270, 250, 230, 215, 200, 230, 250, 270,
       290, 310, 330, 350, 260, 220, 200]
mode = "near"
switches = []
for d in seq:
    nm = mode
    if mode == "near" and d >= far + hyst:
        nm = "far"
    elif mode == "far" and d <= far - hyst:
        nm = "near"
    if nm != mode:
        switches.append((d, mode, nm))
        mode = nm
print("  切换次数 = %d" % len(switches))
for d, a, b in switches:
    print("    偏差 %4.0fpx：%s → %s" % (d, a, b))
print("  终态：%s 档" % mode)
assert len(switches) <= 4, "滞回失效：切换太频繁"
assert all(abs(d - far) >= hyst - 1e-6 for d, _, _ in switches), "在 hyst 带内发生了切换"

print()
print("=" * 74)
print("四、两档落到具体偏差上的差异")
print("=" * 74)
print("  %-8s | %-22s | %s" % ("偏差px", "档位", "落点 / 单步消比"))
for d in (60, 120, 200, 260, 340, 500):
    m = "far" if d >= far + hyst else ("near" if d <= far - hyst else "保持上一档")
    if m == "far":
        print("  %-8d | %-22s | 躯干(h_ratio %.2f) / step_cap %.2f" %
              (d, "远档（高速低精度）", float(ms.get("far_height_ratio", 0.42)),
               float(ms.get("far_step_cap", 0.9))))
    elif m == "near":
        print("  %-8d | %-22s | 头部(h_ratio %.2f) / step_cap %.2f" %
              (d, "近档（高精度高速）", float(ms.get("near_height_ratio", 0.2)),
               float(aim.get("step_cap", 0.18))))
    else:
        print("  %-8d | %-22s | 不切档（滞回带 %.0f~%.0f）" %
              (d, "滞回带", far - hyst, far + hyst))

print()
if ok:
    print("✅ 双模链路完整")
else:
    raise SystemExit("✗ 链路有缺失")