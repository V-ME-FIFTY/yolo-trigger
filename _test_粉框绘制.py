"""粉框绘制实像素测试（2026-10-31）

不靠读代码猜，直接把 main.py 里那段绘制代码抠出来，喂一张黑图跑一遍，
数像素：
  ① 粉框（BGR 255,0,255）必须真的出现在图上；
  ② 锁定命中框与锁定框同坐标时，粉框不能被红框整条盖住（旧顺序的核心 bug）；
  ③ 外推量限幅后，粉框不许跑出帧（100x250 的框、2400px/s 尖峰）；
  ④ 目标不在时 lock_box=None → 一个粉像素都不许有。

运行：python _test_粉框绘制.py
"""
import json
import os
import re
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

OK = True


def case(name, cond, detail=""):
    global OK
    OK &= bool(cond)
    print("  %s %-44s %s" % ("✅" if cond else "❌", name, detail))
    return bool(cond)


# ── 从 main.py 抠出绘制段（绿框 → 命中红框 → 粉框）──
start = SRC.find("                for box in boxes:\n                    x1, y1, x2, y2 = [int(v) for v in box[:4]]")
end = SRC.find("                frames += 1", start)
assert start > 0 and end > start, "抠不到绘制段"
BLOCK = "\n".join(l[16:] for l in SRC[start:end].split("\n"))   # 去掉 16 空格缩进


class _St:
    def __init__(self):
        self.d = {"det_t": time.time(), "vx": 0.0, "vy": 0.0}
        self.lock = type("L", (), {"__enter__": lambda s: s,
                                   "__exit__": lambda s, *a: False})()

    def __getitem__(self, k):
        return self.lock

    def get(self, k, default=None):
        return self.d.get(k, default)


def run(lock_box, boxes, hits, aim_on=True, vx=0.0, vy=0.0, det_lag_ms=0.0, now=None):
    vis = np.zeros((720, 800, 3), np.uint8)
    st = _St()
    st.d["det_t"] = time.time() - det_lag_ms / 1000.0
    st.d["vx"], st.d["vy"] = vx, vy
    ns = dict(cv2=cv2, vis=vis, lock_box=lock_box, boxes=boxes, hits=hits,
              aim_x=400.0, aim_y=360.0, _cx=400, _cy=360, aim_height=0.2,
              aim_st=st, aim_cfg=CFG["aim"], aim_on=aim_on, aim_log=False,
              now=now if now is not None else time.time(), cfg=CFG, time=time,
              _boxes_t=st.d["det_t"], _lock_draw_n=0, _lock_dbg_last=0.0,
              np=np, print=lambda *a, **k: None)
    exec(compile(BLOCK, "<draw>", "exec"), ns)
    return vis


def count(img, bgr, tol=6):
    b, g, r = bgr
    m = ((abs(img[:, :, 0].astype(int) - b) <= tol) &
         (abs(img[:, :, 1].astype(int) - g) <= tol) &
         (abs(img[:, :, 2].astype(int) - r) <= tol))
    return int(m.sum())


PINK = (255, 0, 255)
RED = (0, 0, 255)

print("=" * 78)
print("① 锁定后必须有粉框（命中框同坐标也不许盖掉）")
print("=" * 78)
box = (150.0, 200.0, 250.0, 450.0)          # 100x250 的人
hits_same = [(120.0, 200.0, 250.0, 100.0, 250.0,
              box[0], box[1], box[2], box[3])]   # 锁定后 hits 就是 lock_box
img = run(box, [box], hits_same)
npink = count(img, PINK)
nred = count(img, RED)
case("粉框像素存在", npink > 300, "粉 %d px" % npink)
case("粉框没被红框盖光", npink > nred, "粉 %d vs 红 %d px" % (npink, nred))

# 旧顺序复现（粉框先画 → 绿 → 红），证明这就是"看不见粉框"的原因
OLD = """
cv2.rectangle(vis, (int(lock_box[0]), int(lock_box[1])), (int(lock_box[2]), int(lock_box[3])), (255, 0, 255), 2)
for b in boxes:
    x1, y1, x2, y2 = [int(v) for v in b[:4]]
    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 1)
for dist, bx, by, bh, bw, x1, y1, x2, y2 in hits:
    cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
"""
vis_old = np.zeros((720, 800, 3), np.uint8)
exec(compile(OLD, "<old>", "exec"),
     dict(cv2=cv2, vis=vis_old, lock_box=box, boxes=[box], hits=hits_same))
npink_old = count(vis_old, PINK)
case("旧顺序下粉框确实被盖掉（对照）", npink_old < npink,
     "旧 %d px → 新 %d px" % (npink_old, npink))

print()
print("=" * 78)
print("② 未锁定时不许有粉框")
print("=" * 78)
img = run(None, [box], [])
case("lock_box=None → 0 粉像素", count(img, PINK) == 0, "粉 %d px" % count(img, PINK))
case("但有绿框（能看到检出）", count(img, (0, 255, 0)) > 0)

print()
print("=" * 78)
print("③ 外推限幅：速度尖峰也不许把粉框甩出帧 / 甩离人")
print("=" * 78)
ratio = float(CFG["aim"].get("extrap_max_ratio", 0.15))
for v in (300.0, 900.0, 2400.0):
    img = run(box, [box], hits_same, vx=v, vy=0.0, det_lag_ms=60.0)
    m = ((img[:, :, 0] == 255) & (img[:, :, 1] == 0) & (img[:, :, 2] == 255))
    xs = np.where(m.any(axis=0))[0]
    shift = float(xs.min()) - box[0] if xs.size else 0.0
    lim = (box[2] - box[0]) * ratio
    case("v=%4.0fpx/s 60ms 粉框横移 %.0fpx ≤ %.0fpx" % (v, shift, lim + 1),
         abs(shift) <= lim + 1.5, "限幅 ratio=%.2f" % ratio)

print()
print("=" * 78)
print("④ 粉框落在人身上（不外推时四边与框边重合，另有准星连线）")
print("=" * 78)
img = run(box, [box], hits_same, vx=0.0, vy=0.0)
m = ((img[:, :, 0] == 255) & (img[:, :, 1] == 0) & (img[:, :, 2] == 255))


def pink_at(x, y):
    return bool(m[max(0, y - 1):y + 2, max(0, x - 1):x + 2].any())


case("左上角在框上", pink_at(int(box[0]), int(box[1])))
case("右上角在框上", pink_at(int(box[2]), int(box[1])))
case("左下角在框上", pink_at(int(box[0]), int(box[3])))
case("右下角在框上", pink_at(int(box[2]), int(box[3])))
# 顶边中点必须是粉的（框线本体，不是角标）
_case_row = int(box[1])
case("顶边中点在框上", bool(m[_case_row - 1:_case_row + 2,
                             int((box[0] + box[2]) / 2) - 1:int((box[0] + box[2]) / 2) + 2].any()))
case("准星→瞄点有粉色连线", pink_at(320, 316))

print()
print("全部通过 ✅" if OK else "有失败项 ❌")
sys.exit(0 if OK else 1)