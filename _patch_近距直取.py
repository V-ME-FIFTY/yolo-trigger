# -*- coding: utf-8 -*-
"""把「近距直取」相关的 aim 配置项补进所有 config*.json（2026-10-30）

只补缺失的键，已有值一律不动；保持 2 空格缩进与键顺序（新键插在 ads_sense_by_key 之后）。
用法：python _patch_近距直取.py
"""
import glob
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 新键 + 默认值（含义见 main.py 里的注释块）
NEW = {
    "near_boost_px": 140.0,        # 贴脸直取带：偏差小于它就开始"越近越肯走"
    "near_boost_ratio": 1.0,       # 带内最里端（偏差→0）的预算比例：1.0=本帧走完
    "abs_move_cap_near": 1.0,      # 带内绝对闸放宽到 1.0（旧值 0.85=每帧最多走 85%）
    "damp_ratio": 0.45,            # 过冲抑制期的预算上限（原硬编码 0.45）
    "near_damp_ratio": 0.85,       # 贴脸段过冲抑制上限（符号翻转多为抖动，别冻死）
    "fov_change_ref_px": 160.0,    # 缩放判定阈值随框高放大的参考框高
    "pose_smooth_big_px": 200.0,   # 瞄准点 EMA 自适应：框高超过它开始减少平滑
    "pose_smooth_near": 0.9,       # 贴脸时的 EMA alpha（0=完全不动, 1=不平滑）
}


def patch(path):
    with io.open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    aim = cfg.get("aim")
    if not isinstance(aim, dict):
        return None
    added = []
    for k, v in NEW.items():
        if k not in aim:
            aim[k] = v
            added.append(k)
    if not added:
        return []
    # 重新排序：新键紧跟 ads_sense_by_key（若没有就放最后），其余保持原顺序
    order = [k for k in aim.keys() if k not in NEW]
    tail = [k for k in NEW if k in aim]
    if "ads_sense_by_key" in order:
        i = order.index("ads_sense_by_key") + 1
        keys = order[:i] + tail + order[i:]
    else:
        keys = order + tail
    cfg["aim"] = {k: aim[k] for k in keys}
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return added


def main():
    files = sorted(glob.glob(os.path.join(HERE, "config*.json")))
    for p in files:
        added = patch(p)
        name = os.path.basename(p)
        if added is None:
            print("  跳过 %-34s （没有 aim 段）" % name)
        elif not added:
            print("  已是最新 %-32s" % name)
        else:
            print("  补 %-2d 项 %-32s %s" % (len(added), name, ", ".join(added)))


if __name__ == "__main__":
    main()