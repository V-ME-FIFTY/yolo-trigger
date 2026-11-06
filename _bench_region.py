# -*- coding: utf-8 -*-
"""检测区大小 → 抓屏/推理耗时 与 覆盖半径 基准（2026-10-30）

用户要求「降低检测框大小，提高转向与检测帧率」。这里给硬数字：
  · dxcam 抓 R×R 的耗时中位数（区域越小越快，这是唯一的 fps 收益来源）
  · resize 到 imgsz(512) 的耗时
  · TensorRT 推理耗时（跟区域大小无关，512 固定）
  · 覆盖半径 ±R/2 屏内像素、占屏比，以及「锁定后 ±R/2 之外根本看不到」的后果

转向变快的机理顺带说清：检测帧率 f↑ → 同一个检测周期能消掉的偏差次数↑；
贴脸按 _check_近距直取.py 的口径，f 从 30 → 45 时，140px 收敛从 2 帧/67ms → 2 帧/44ms。

用法：python _bench_region.py [--dir C:\\yolo-trigger] [--sizes 800 1000 1200]
"""
import argparse
import os
import statistics
import sys
import time

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
ap.add_argument("--sizes", type=int, nargs="+", default=[700, 800, 900, 1000, 1200])
args = ap.parse_args()
ROOT = os.path.abspath(args.dir)
sys.path.insert(0, ROOT)

import cv2  # noqa: E402


def grab_cam():
    for name in ("dxcam", "bettercam"):
        try:
            mod = __import__(name)
            cam = mod.create(output_color="BGR")
            if cam is not None:
                return name, cam
        except Exception:
            continue
    return None, None


def main():
    name, cam = grab_cam()
    if cam is None:
        print("没有可用抓屏后端（dxcam/bettercam），无法测抓屏耗时")
        return
    W, H = cam.width, cam.height
    print("=" * 78)
    print("抓屏后端 %s  屏幕 %dx%d" % (name, W, H))
    print("=" * 78)
    print("  %-8s | %-10s | %-10s | %-12s | %-10s | %s" %
          ("区域", "抓屏ms", "resize ms", "推理ms(512)", "单帧合计", "覆盖 / 占屏"))

    rows = []
    for size in args.sizes:
        half = size // 2
        cx, cy = W // 2, H // 2
        region = (max(0, cx - half), max(0, cy - half),
                  min(W, cx + half), min(H, cy + half))
        for _ in range(10):                      # 预热
            cam.grab(region=region)
        # dxcam 换区域后第一帧要等复制器重新出帧：抓到为止（最多 1.5s）
        frame = None
        t_end = time.time() + 1.5
        while time.time() < t_end:
            f = cam.grab(region=region)
            if f is not None:
                frame = f
                break
            time.sleep(0.004)
        ts = []
        for _ in range(30):
            t0 = time.perf_counter()
            f = cam.grab(region=region)
            dt = (time.perf_counter() - t0) * 1000.0
            if f is not None:
                ts.append(dt)
                frame = f
            time.sleep(0.002)                    # 给桌面留出产帧时间，避免全是 None
        gt = statistics.median(ts) if ts else float("nan")
        if frame is None:
            print("  区域 %-4d 抓不到帧，跳过" % size)
            continue
        h, w = frame.shape[:2]
        t0 = time.perf_counter()
        for _ in range(30):
            cv2.resize(frame, (512, 512), interpolation=cv2.INTER_LINEAR)
        rt = (time.perf_counter() - t0) * 1000.0 / 30.0
        rows.append((size, w, h, gt, rt, frame))

    # 推理耗时（区域无关，测一次就够）
    infer_ms = None
    try:
        from pose_trt import PoseTRT
        eng = os.path.join(ROOT, "yolo11n_pose_512_fp16.engine")
        if os.path.isfile(eng):
            b = PoseTRT(eng, imgsz=512, conf=0.30, iou=0.45)
            img = cv2.resize(rows[0][5], (512, 512))
            for _ in range(5):
                b.infer(img)
            ts = []
            for _ in range(15):
                t0 = time.perf_counter()
                b.infer(img)
                ts.append((time.perf_counter() - t0) * 1000.0)
            infer_ms = statistics.median(ts)
    except Exception as exc:
        print("  推理基准跳过：%s: %s" % (type(exc).__name__, exc))

    for size, w, h, gt, rt, _ in rows:
        tot = gt + rt + (infer_ms or 0.0)
        cov = "±%d px（横 %.0f%% / 纵 %.0f%% 屏）" % (
            size // 2, 100.0 * size / W, 100.0 * size / H)
        print("  %-8d | %-10.2f | %-10.2f | %-12s | %-10.2f | %s" %
              (size, gt, rt, ("%.2f" % infer_ms) if infer_ms else "-", tot, cov))
        print("           └ 每帧 %0.1fms → 检测帧率上限 %.0f fps（detect_every=1）"
              % (tot, 1000.0 / tot))

    print()
    print("说明：推理 512 固定，区域大小只省抓屏与 resize；区域缩小换来的是覆盖半径变小 ——")
    print("      锁定后框中心离准星超过 ±R/2 的目标直接看不见（不会误锁，也锁不到）。")


if __name__ == "__main__":
    main()