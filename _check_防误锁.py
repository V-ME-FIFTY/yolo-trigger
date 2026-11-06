# -*- coding: utf-8 -*-
"""防误锁核查（2026-10-30，用户「经常锁定景物和手臂」）

用合成框 + 合成关键点跑真代码，逐条验：
  真人（站/蹲/背身）→ 必须留下
  横扁的手臂/枪、贴底的自己的手、方正的景物块、无姿态关键点的杂物 → 必须剔除
  另外验 lock.confirm_frames：单帧误检撑不过第二帧，连续出现的真人才允许上锁

用法：python _check_防误锁.py [--dir C:\\yolo-trigger]
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
# 2026-10-30 真实素材实测后：这套过滤在视频帧上误杀了 50% 的真实检出（贴底/宽高比 1.27），
# 出货配置把它关了（person_check.enabled=false）。本脚本是"过滤器本身"的单元测试，
# 所以在这里强制打开，并用自己的阈值，不受出货开关影响。
cfg["person_check"] = dict(cfg.get("person_check") or {})
cfg["person_check"].update({"enabled": True, "max_wh_ratio": 1.25, "min_kp": 3,
                            "self_bottom_ratio": 0.94})
W, H = 800, 800          # 抓屏区（region_size 800）


def person(cx, cy, h, cf=0.85, order=1.0):
    """造一个人的关键点：鼻/眼在头、肩在 0.25h、髋在 0.55h（order 可反转=Fake scene）。"""
    pts = [[0.0, 0.0] for _ in range(17)]
    cfs = [0.0] * 17
    pts[0] = [cx, cy - h * 0.40 * order]
    pts[1] = [cx - h * 0.05, cy - h * 0.42 * order]
    pts[2] = [cx + h * 0.05, cy - h * 0.42 * order]
    pts[5] = [cx - h * 0.13, cy - h * 0.15 * order]
    pts[6] = [cx + h * 0.13, cy - h * 0.15 * order]
    pts[11] = [cx - h * 0.10, cy + h * 0.15 * order]
    pts[12] = [cx + h * 0.10, cy + h * 0.15 * order]
    for i in (0, 1, 2, 5, 6, 11, 12):
        cfs[i] = cf
    return pts, cfs


def back(cx, cy, h, cf=0.85):
    """背身：只有双肩+双髋可见（看不到脸），这是最常见的真实情况。"""
    pts = [[0.0, 0.0] for _ in range(17)]
    cfs = [0.0] * 17
    pts[5] = [cx - h * 0.13, cy - h * 0.15]
    pts[6] = [cx + h * 0.13, cy - h * 0.15]
    pts[11] = [cx - h * 0.10, cy + h * 0.15]
    pts[12] = [cx + h * 0.10, cy + h * 0.15]
    for i in (5, 6, 11, 12):
        cfs[i] = cf
    return pts, cfs


def run(cases):
    boxes, kxy, kcf = [], [], []
    for name, box, kp in cases:
        boxes.append(box)
        if kp is None:
            kxy.append([[0.0, 0.0] for _ in range(17)])
            kcf.append([0.0] * 17)
        else:
            kxy.append(kp[0])
            kcf.append(kp[1])
    kb, kk, kc, n, why = M.filter_person_check(boxes, kxy, kcf, cfg, W, H)
    return kb, n, why


def main():
    pc = cfg.get("person_check") or {}
    print("=" * 74)
    print("person_check = %s" % json.dumps(pc, ensure_ascii=False))
    print("=" * 74)

    cases = [
        ("真人·中距站姿", (340, 260, 460, 620), person(400, 440, 360)),
        ("真人·贴脸大框", (300, 150, 500, 660), person(400, 400, 500)),
        ("真人·背身(只有肩髋可见)", (350, 300, 450, 620), back(400, 460, 320)),
        ("手臂/枪·横扁", (120, 560, 680, 700), person(400, 630, 140)),
        ("自己的手·贴底", (300, 700, 520, 800), person(410, 760, 100)),
        ("景物·方正石块", (120, 120, 300, 300), None),
        ("景物·有姿态但头在肩下", (200, 300, 380, 700), person(290, 500, 400, order=-1.0)),
        ("杂物·关键点出框", (600, 200, 660, 700),
         ([[900.0, 100.0]] * 17, [0.9] * 17)),
    ]
    print("  %-22s %-26s %s" % ("用例", "框", "结果"))
    kept = {}
    for name, box, kp in cases:
        kb, n, why = run([(name, box, kp)])
        ok = len(kb) == 1
        kept[name] = ok
        print("  %-22s %-26s %s %s" % (name, str(box), "保留" if ok else "剔除",
                                       "" if ok else "（%s）" % ",".join(why.keys())))

    assert kept["真人·中距站姿"] and kept["真人·贴脸大框"], "真人被误杀"
    assert kept["真人·背身(只有肩髋可见)"], "背身（看不到脸）被误杀 —— 会漏真目标"
    for n in ("手臂/枪·横扁", "自己的手·贴底", "景物·方正石块",
              "景物·有姿态但头在肩下", "杂物·关键点出框"):
        assert not kept[n], "该剔除的没剔除：%s" % n

    # ── confirm_frames：单帧误检不许上锁，连续两帧才允许 ──
    print()
    print("=" * 74)
    print("lock.confirm_frames = 2（本脚本内验证机制；出货配置=%s）" % cfg["lock"].get("confirm_frames"))
    print("=" * 74)
    # 出货配置已把 confirm_frames 回退成 1（索敌优先），这里单独验"机制本身"用 2
    st = {"box": None, "t": 0.0, "start": 0.0, "engage": False, "confirm_frames": 2}
    scene = [(100, 100, 300, 500)]
    got1 = M.pick_locked(scene, 200.0, 300.0, 10, st, 10.0, 0.4, 180.0, 0.55)
    got2 = M.pick_locked(scene, 200.0, 300.0, 10, st, 10.033, 0.4, 180.0, 0.55)
    print("  第 1 帧（刚出现）：锁定 = %s" % (got1,))
    print("  第 2 帧（连续出现）：锁定 = %s" % (got2,))
    assert got1 is None, "单帧误检竟然直接上锁：%s" % (got1,)
    assert got2 == scene[0], "连续出现却没上锁：%s" % (got2,)

    print()
    print("全部通过 ✅（真人全留、手臂/景物/杂物全剔、单帧误检不上锁）")


if __name__ == "__main__":
    main()