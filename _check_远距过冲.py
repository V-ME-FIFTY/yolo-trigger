# -*- coding: utf-8 -*-
"""远距过冲核查（2026-10-30）

用户反馈「离得较远时有时会拉过头」。远距过冲的物理来源只有两个：
  ① k（像素/计数）估歪 → 发出的计数换算成像素比预想的大；
  ② 每周期允许走的像素太多 → 一次就越过目标。
本脚本按"每周期位移上限"算出 k 误差下的真实落点，对比加 burst_cap_box_ratio 前后。

用法：python _check_远距过冲.py [--dir C:\\yolo-trigger]
"""
import argparse
import io
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
a = json.load(io.open(os.path.join(os.path.abspath(ap.parse_args().dir), "config.json"),
                     encoding="utf-8"))["aim"]

abs_cap = float(a.get("abs_move_cap", 0.85))
bcr = float(a.get("burst_cap_box_ratio", 0.75))

print("=" * 78)
print("参数：abs_move_cap=%.2f  burst_cap_box_ratio=%.2f" % (abs_cap, bcr))
print("=" * 78)
print("  每周期允许位移 = min(abs_cap×偏差, burst_cap_box_ratio×框高)")
print()
print("  %-8s %-8s %-12s %-12s %-14s %s" %
      ("偏差px", "框高px", "旧上限px", "新上限px", "k=1.5 实际走", "结果"))

for ad, bh in ((340, 150), (340, 220), (260, 180), (500, 160)):
    old = abs_cap * ad
    new = min(old, bcr * bh)
    real_old = old * 1.5
    real_new = new * 1.5
    verdict_old = ("过冲 %.0fpx" % (real_old - ad)) if real_old > ad else "到位"
    verdict_new = ("过冲 %.0fpx" % (real_new - ad)) if real_new > ad else "到位"
    print("  %-8d %-8d %-12.0f %-12.0f %-14s 旧:%s / 新:%s" %
          (ad, bh, old, new, "%.0fpx" % real_new, verdict_old, verdict_new))

print()
print("结论：远处框小，0.75×框高比 0.85×偏差先触顶 —— 即使 k 估大 1.5 倍也不会飞出一个身子；")
print("      近处框大（框高 400+），这条上限 ≈300px 基本不生效，不影响贴脸速度。")