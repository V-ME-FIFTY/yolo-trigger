# -*- coding: utf-8 -*-
"""测量真实帧上的检出统计，用来定"锁定时怎样才算像人"的阈值（2026-10-30）

用户说"锁空白是粉框"——粉框就是锁定框（画框颜色 255,0,255），它落在空白处说明
锁到了一个**假检出**。这里先用真引擎把真实视频帧的检出统计打出来，
再据此定"锁定时"的宽松闸值（只拦锁，不影响绿框显示）。

用法：python _measure_锁闸.py
"""
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2  # noqa: E402
from pose_trt import PoseTRT  # noqa: E402

cfg = json.load(io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "config.json"), encoding="utf-8"))
b = PoseTRT(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "yolo11n_pose_512_fp16.engine"), imgsz=512, conf=0.25, iou=0.45)
files = sorted(glob.glob(os.path.join(r"C:\Workzone\.scratch\vt", "c*.jpg")))
print("帧数 %d" % len(files))

rows = []
for f in files:
    img = cv2.imread(f)
    if img is None:
        continue
    boxes, kps, conf = b.infer(img)
    if not len(boxes):
        continue
    h, w = img.shape[:2]
    for i in range(len(boxes)):
        x1, y1, x2, y2 = [float(v) for v in boxes[i]]
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        cf = kps[i][:, 2]
        rows.append((float(conf[i]), bw / bh, int((cf >= 0.35).sum()),
                     int((cf >= 0.45).sum()), bh / h, os.path.basename(f)))

print("%-8s %-8s %-8s %-8s %-8s %s" %
      ("检测分", "宽高比", "kp>=.35", "kp>=.45", "框高/屏", "文件"))
for r in sorted(rows, key=lambda t: -t[0]):
    print("%-8.3f %-8.2f %-8d %-8d %-8.3f %s" % r)

if rows:
    print()
    print("临界分析（锁定时只放行像人的框）：")
    for mc in (0.30, 0.35, 0.40, 0.45, 0.50):
        keep = sum(1 for r in rows if r[0] >= mc)
        print("  检测分 >= %.2f 保留 %d / %d" % (mc, keep, len(rows)))
    for nk in (1, 2, 3, 4):
        keep = sum(1 for r in rows if r[2] >= nk)
        print("  kp(>=.35) >= %d 保留 %d / %d" % (nk, keep, len(rows)))