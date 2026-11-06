# -*- coding: utf-8 -*-
"""给所有 config*.json 补 aim.mode_split（远/近双模，2026-10-30）

远档（偏差 ≥ far_px+hyst）：瞄躯干、单步消比 0.9，高速低精度
近档（偏差 ≤ far_px-hyst）：瞄头部、单步消比按 step_cap，高精度高速
档间滞回 hyst_px，避免阈值附近来回换部位。

用法：python _apply_双模.py [--dir C:\\yolo-trigger]
"""
import argparse
import glob
import io
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)

MODE = {
    "enabled": True,
    "far_px": 260,             # 判定阈值（两轴偏差取大）
    "hyst_px": 40,             # 滞回：远档要 >300 才进，近档要 <220 才回
    "far_part": "chest",       # 远档瞄躯干（关键点不可靠，命中率优先）
    "near_part": "head",       # 近档瞄头
    "far_height_ratio": 0.42,  # 远档没有关键点时按框高比例（躯干中部）
    "near_height_ratio": 0.2,  # 近档兜底比例（头部）
    "far_step_cap": 0.9,       # 远档单步消比（一步吃 90% 剩余，甩过去就上身）
}

n_new = n_upd = 0
for p in sorted(glob.glob(os.path.join(root, "config*.json"))):
    cfg = json.load(io.open(p, encoding="utf-8"))
    aim = cfg.get("aim")
    if not isinstance(aim, dict):
        continue
    if "mode_split" in aim:
        aim["mode_split"].update(MODE)
        n_upd += 1
    else:
        aim["mode_split"] = dict(MODE)
        n_new += 1
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")

print("mode_split：新写入 %d 份，更新 %d 份（目录 %s）" % (n_new, n_upd, root))