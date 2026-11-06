# -*- coding: utf-8 -*-
"""开镜门控核查（2026-10-30）

用户原话：「我需要的是开镜才锁」。
结论在这里坐实三件事：
  1) config.json 的 aim.require_aim_key 决定"锁不锁人"（false=常驻锁人 / true=只有按住开镜键才锁）
  2) main.py 里两条链是**解耦**的：aim_key_ok 管锁不锁，_ads_key_down 管这一帧用腰射还是开镜的 k
  3) 三档数值对照：常驻锁人 / 开镜锁-极速 / 开镜锁-起步更快

用法：python _check_开镜门控.py [--dir C:\\yolo-trigger]
"""
import argparse
import io
import json
import os
import re

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
args = ap.parse_args()
root = os.path.abspath(args.dir)

cfg = json.load(io.open(os.path.join(root, "config.json"), encoding="utf-8"))
aim = cfg["aim"]
src = io.open(os.path.join(root, "main.py"), encoding="utf-8").read()

print("=" * 72)
print("一、当前生效（%s）" % root)
print("=" * 72)
print("  require_aim_key   = %s   → %s" % (
    aim.get("require_aim_key"),
    "按住开镜键才锁（开镜锁）" if aim.get("require_aim_key") else "松手也锁（常驻锁人）"))
print("  aim_key           = %s" % aim.get("aim_key"))
print("  ads_sense_by_key  = %s   → 开镜/腰射各用一套像素/计数系数" % aim.get("ads_sense_by_key"))
print("  ads_grace_ms      = %s" % aim.get("ads_grace_ms"))
print("  ads_hold_ratio    = %s" % aim.get("ads_hold_ratio"))
print("  ads_maxstep_px    = %s" % aim.get("ads_maxstep_px"))

print()
print("=" * 72)
print("二、代码里两条链是否解耦")
print("=" * 72)
checks = [
    ("门控：aim_key_ok = (not aim_require_key) or _ads_key_down",
     r"aim_key_ok\s*=\s*\(not\s+aim_require_key\)\s*or\s+_ads_key_down"),
    ("开镜状态取真实按键：_ads_key_down = is_key_down(aim_key_vk)",
     r"_ads_key_down\s*=\s*is_key_down\(aim_key_vk\)"),
    ("只有 aim_key_ok 为真才进瞄准块",
     r"aim_on\s+and\s+allow_fire\s+and\s+aim_key_ok\s+and\s+lock_box\s+is\s+not\s+None"),
]
ok = True
for name, pat in checks:
    hit = bool(re.search(pat, src))
    ok = ok and hit
    print("  %s %s" % ("✅" if hit else "✗ 未找到:", name))

print()
print("=" * 72)
print("三、三档数值对照")
print("=" * 72)
tiers = [
    ("常驻锁人（上一版 config.json）", None, dict(require_aim_key=False,
                                                  ads_grace_ms=260, ads_hold_ratio=0.45, ads_maxstep_px=80)),
    ("开镜锁-极速（现用）", "config-开镜锁-极速.json", None),
    ("开镜锁-起步更快", "config-开镜锁-起步更快.json", None),
]
print("  %-28s | %-14s | %-12s | %-12s | %s" % ("档", "require_aim_key", "ads_grace", "ads_hold", "ads_maxstep"))
for name, f, over in tiers:
    a = dict(aim)
    if f and os.path.isfile(os.path.join(root, f)):
        a.update(json.load(io.open(os.path.join(root, f), encoding="utf-8"))["aim"])
    if over:
        a.update(over)
    print("  %-28s | %-14s | %-12s | %-12s | %s" % (
        name, a.get("require_aim_key"), a.get("ads_grace_ms"), a.get("ads_hold_ratio"), a.get("ads_maxstep_px")))

print()
print("=" * 72)
print("四、判定")
print("=" * 72)
if aim.get("require_aim_key") and ok:
    print("  ✅ 现在是「开镜才锁」：不按 %s 时 aim_key_ok=False，整个瞄准块跳过，一个像素都不发；"
          "按住时用开镜那套系数。" % aim.get("aim_key"))
    print("  ⚠ 别忘了先在游戏里按 F9 把自瞄总开关打开（日志：热键 F9 开关；当前: 开）。")
else:
    print("  ✗ 未完全生效，检查 require_aim_key 与 main.py 的门控三行。")
    raise SystemExit(1)