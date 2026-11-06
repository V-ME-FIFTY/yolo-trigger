# -*- coding: utf-8 -*-
"""第三轮调参（2026-10-30 22:40）：交战不换人 + 远距不过冲 + 近距再快一点

用户原话：「当正在进行一个敌人的瞄准时忽略其他锁定的敌人，而且离得较远时有时会拉过头，
          很近时稍微有点慢」

写进所有 config*.json：
  lock.engage_relax / engage_extra_gap_s / engage_switch_max_px
      → 交战期匹配半径放宽 2×、丢框多容忍 0.4s、重选人只认离旧框 ≤220px 的候选
  aim.burst_cap_box_ratio = 0.75
      → 每周期位移额外钳"0.75×框高"（远处框小自然收得紧，治远距拉过头）
  aim.mode_split.far_step_cap 0.9 → 0.6
      → 远档单步消比从"一步 90%"降到 60%，一个周期摊 3~4 步
  aim.mode_split.far_lead_scale = 0.5
      → 远档提前量打对折（远处框小，提前量最容易变成"跑到人前面"）
  aim.near_boost_px 140 → 200
      → 贴脸直取带放宽到 200px（用户"很近时稍微有点慢"，1 帧贴上覆盖到 200px）

用法：python _apply_第三轮.py [--dir C:\\yolo-trigger]
"""
import argparse
import glob
import io
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
root = os.path.abspath(ap.parse_args().dir)

LOCK = {"engage_relax": 2.0, "engage_extra_gap_s": 0.40, "engage_switch_max_px": 220.0}
MODE = {"far_step_cap": 0.6, "far_lead_scale": 0.5}
AIM = {"burst_cap_box_ratio": 0.75, "near_boost_px": 200.0}

n = 0
for p in sorted(glob.glob(os.path.join(root, "config*.json"))):
    cfg = json.load(io.open(p, encoding="utf-8"))
    aim = cfg.get("aim")
    if not isinstance(aim, dict):
        continue
    lk = cfg.setdefault("lock", {})
    lk.update(LOCK)
    aim.update(AIM)
    aim.setdefault("mode_split", {}).update(MODE)
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    n += 1

print("已更新 %d 份 config（%s）" % (n, root))
c = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
print("  当前：near_boost_px=%s burst_cap_box_ratio=%s | far_step_cap=%s far_lead_scale=%s"
      % (c["aim"]["near_boost_px"], c["aim"]["burst_cap_box_ratio"],
         c["aim"]["mode_split"]["far_step_cap"], c["aim"]["mode_split"]["far_lead_scale"]))
print("  lock：%s" % {k: c["lock"][k] for k in LOCK})