# -*- coding: utf-8 -*-
"""开火态灵敏度核查（2026-10-30，用户「开火状态鼠标灵敏度下降考虑了吗」）

结论先说：**之前只分了腰射/开镜两套，开火没分** —— 而多数 FPS 开火瞬间会降灵敏度，
于是开火期间"同样一段鼠标计数换来的像素变少"，闭环增益偏小，表现就是"打起来准星跟不上、
越打越磨"。这一轮补上第三套系数。

本脚本验：
  1) config 里三套系数的开关/参数（fire_sense / fire_key / fire_grace_ms / fire_k_scale）
  2) main.py 里三套系数优先级：开火 > 开镜 > 腰射；标定回写到各自那一套
  3) 连发续期：不能只在按下沿给宽限（否则扫射 120ms 后悄悄切回去）
  4) 数值：不同 fire_k_scale 下，同一段计数换来的像素与闭环增益

用法：python _check_开火灵敏.py [--dir C:\\yolo-trigger]
退出码 0 = 全过。
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
src = io.open(os.path.join(root, "main.py"), encoding="utf-8").read()

print("=" * 76)
print("一、三套系数")
print("=" * 76)
print("  腰射    k = pixel_per_count %s" % (aim.get("pixel_per_count"),))
print("  开镜    ads_sense_by_key = %s（按下 %s 时用单独标定的一套）"
      % (aim.get("ads_sense_by_key"), aim.get("aim_key")))
print("  开火    fire_sense = %s | fire_key = %s | fire_grace_ms = %s | fire_k_scale = %s"
      % (aim.get("fire_sense"), aim.get("fire_key"),
         aim.get("fire_grace_ms"), aim.get("fire_k_scale")))

print()
print("=" * 76)
print("二、代码链路")
print("=" * 76)
checks = [
    ("开火键取真实按键", r"_fire_down = bool\(aim_fire_en and is_key_down\(aim_fire_vk\)\)"),
    ("连发每帧续期（不是只在按下沿）", r"if _fire_down:\s*\n\s*# 连发期间必须每帧续期"),
    ("松手后保留一小段", r"elif aim_fire_prev:\s*\n\s*aim_fire_until = now \+ aim_fire_grace"),
    ("优先级：开火 > 开镜 > 腰射", r"if _fire_use:\s*\n\s*_k_use = aim_k_fire\s*\n\s*elif _ads_use:"),
    ("标定回写分三套", r"if _fire_use:\s*\n\s*aim_k_fire = _k_use\s*\n\s*elif _ads_use:"),
    ("日志能看出当前用哪套", r"\[开火\].*\[开镜\]|\[开镜\].*\[开火\]"),
]
ok = True
for name, pat in checks:
    hit = bool(re.search(pat, src, re.S))
    ok = ok and hit
    print("  %s %s" % ("✅" if hit else "✗ 未找到:", name))

print()
print("=" * 76)
print("三、不分开火系数会怎样（k=像素/计数；灵敏度降到 scale 倍，程序却仍按腰射 k 发计数）")
print("=" * 76)
dead = float(aim.get("dead_zone_px", 2))

def frames(r0, actual_scale, fudge=0.0):
    r, n = float(r0), 0
    while r > dead and n < 20:
        r -= r * (actual_scale + fudge)      # 本周期想走 r，实际只走了 scale 倍
        n += 1
    return n

print("  %-12s | %-22s | %-22s | %s" %
      ("真实 scale", "不分系数：帧数@60px", "分了系数（k 已校准）：帧数", "差"))
for sc in (1.0, 0.9, 0.85, 0.75, 0.6):
    bad = frames(60, sc)
    good = frames(60, sc, fudge=1.0 / sc - 1.0)     # 用对了 k：换算回 100%
    print("  %-12.2f | %-22s | %-22s | %+d 帧（%+dms）" %
          (sc, "%d 帧" % bad, "%d 帧" % good, bad - good, (bad - good) * 33))
print("  说明：scale 越小（灵敏度降得越狠），不分系数的代价越大；连发时每 33ms 都在补这个差，")
print("        体感就是「打起来准星跟不上、越打越磨」。fire_k_scale 只是起步估值，")
print("        之后由自适应标定把它学准（三套各自独立学习）。")

print()
if ok:
    print("✅ 三套系数链路完整（腰射 / 开镜 / 开火，各自独立标定）")
else:
    raise SystemExit("✗ 链路有缺失")