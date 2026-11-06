"""k 标定矩阵（2026-10-31，P0-3）

背景：k = 像素/计数，是"计划像素 → 发多少鼠标计数"的唯一换算系数。
基准版只有一个全局 k（还按开镜/开火乘一个**猜出来的**倍率），一旦猜错就是
系统性过冲：过冲量 ≈ 每周期预算 × (k真/k估 − 1)，并且"总是"发生。

做法：把 k 按 **(姿态模式 × 距离带)** 分格学，每格独立样本、独立收敛：
    mode ∈ {hip 腰射, ads 开镜, fire 开火}
    band ∈ 按框高分箱（框越大 = 越近）：默认 [0,120,260] → 远/中/近
冷启动按 **同模式邻近带 → 同带其它模式 → 全局均值 → 先验** 逐级继承，
所以"第一次开镜"不会从 1.0 从零学（那就是开镜第一枪必拉过头的根）。
抗噪：每格保存最近 N 个测量值取中位数，再对更新步长限幅（默认 ±25%）。
持久化：k_matrix.json，跨局复用 —— 开机就准，不用每局重新学。

用法：
    km = KMatrix(cfg.get("kmatrix") or {})
    km.get("ads", 300.0)                    # -> (kx, ky)
    km.observe("ads", 300.0, counts, de, db) # 每检测帧喂一次
    km.save()
"""
from __future__ import annotations

import json
import math
import os
import statistics


class KMatrix:
    BANDS = (0.0, 120.0, 260.0)      # 框高分箱下界：远 / 中 / 近

    def __init__(self, cfg=None, path="k_matrix.json"):
        cfg = cfg or {}
        self.path = path
        self.bands = tuple(float(v) for v in (cfg.get("bands") or self.BANDS))
        self.win = int(cfg.get("window", 9))          # 每格中位数窗口
        self.step = float(cfg.get("max_step", 0.25))  # 单次更新最多改 ±25%
        self.min_counts = float(cfg.get("min_counts", 5.0))   # 计数太小不采信
        self.kmin, self.kmax = float(cfg.get("k_min", 0.05)), float(cfg.get("k_max", 30.0))
        self.prior = tuple(float(v) for v in (cfg.get("prior") or (1.0, 1.0)))
        self.data = {}          # "mode|band_idx" -> {"kx","ky","n","sx":[],"sy":[]}
        self._dirty = 0
        if cfg.get("persist", True):
            self.load()

    # ── 分箱 ──
    def band_of(self, box_h):
        h = float(box_h)
        idx = 0
        for i, lo in enumerate(self.bands):
            if h >= lo:
                idx = i
        return idx

    # ── 取值（逐级继承）──
    def _cell(self, mode, bi):
        return self.data.get("%s|%d" % (mode, bi))

    def get(self, mode, box_h):
        bi = self.band_of(box_h)
        c = self._cell(mode, bi)
        if c and c["n"] > 0:
            return (c["kx"], c["ky"])
        # 1) 同模式邻近带（取最近有数据的那一格）
        cands = [(abs(k - bi), self.data["%s|%d" % (mode, k)])
                 for k in range(len(self.bands)) if self._cell(mode, k)]
        if cands:
            cands.sort(key=lambda t: t[0])
            c = cands[0][1]
            return (c["kx"], c["ky"])
        # 2) 同带其它模式
        for m in ("ads", "hip", "fire"):
            c = self._cell(m, bi)
            if c and c["n"] > 0:
                return (c["kx"], c["ky"])
        # 3) 全库均值
        ks = [c["kx"] for c in self.data.values() if c["n"] > 0]
        if ks:
            return (statistics.median(ks), statistics.median(ks))
        return self.prior

    # ── 采样 ──
    def observe(self, mode, box_h, counts, de, db):
        """counts=(Σmx,Σmy) 本周期发出计数；de=Δe 偏差变化；db=ΔB 目标自身位移估计"""
        if db is None or counts is None or de is None:
            return
        if self.step <= 0.0:
            # 手动模式：完全不学，get() 永远返回 prior。
            # 用途：k 的自动学习会被"目标同时在移动"污染，一旦学偏就是系统性过冲；
            # 手动锁定后每一次改动都有确定因果，不再和调参互相打架。
            return
        cx, cy = float(counts[0]), float(counts[1])
        px, py = float(de[0]), float(de[1])
        bi = self.band_of(box_h)
        key = "%s|%d" % (mode, bi)
        c = self.data.setdefault(key, {"kx": self.get(mode, box_h)[0],
                                       "ky": self.get(mode, box_h)[1],
                                       "n": 0, "sx": [], "sy": []})
        changed = False
        for axis, cnt, de_a, db_a, tag in ((0, cx, px, float(db[0]), "x"),
                                           (1, cy, py, float(db[1]), "y")):
            if abs(cnt) < self.min_counts:
                continue
            k = (db_a - de_a) / cnt
            if not (self.kmin < k < self.kmax):
                continue
            arr = c["sx" if tag == "x" else "sy"]
            arr.append(k)
            if len(arr) > self.win:
                del arr[0]
            km = statistics.median(arr)
            cur = c["kx" if tag == "x" else "ky"]
            lo, hi = cur * (1.0 - self.step), cur * (1.0 + self.step)
            nk = max(lo, min(hi, km))
            if tag == "x":
                c["kx"] = nk
            else:
                c["ky"] = nk
            changed = True
        if changed:
            c["n"] += 1
            self._dirty += 1
            if self._dirty >= 8:
                self.save()

    # ── 持久化 ──
    def load(self):
        if not os.path.exists(self.path):
            return False
        try:
            raw = json.load(open(self.path, encoding="utf-8"))
            self.data = {k: {"kx": float(v["kx"]), "ky": float(v["ky"]),
                             "n": int(v.get("n", 0)),
                             "sx": [float(x) for x in v.get("sx", [])],
                             "sy": [float(x) for x in v.get("sy", [])]}
                         for k, v in (raw.get("cells") or {}).items()}
            return True
        except Exception:
            return False

    def save(self):
        try:
            json.dump({"cells": self.data}, open(self.path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            self._dirty = 0
            return True
        except Exception:
            return False

    def summary(self):
        if not self.data:
            return "k 矩阵：空（还没样本）"
        names = {0: "远", 1: "中", 2: "近"}
        rows = []
        for k in sorted(self.data):
            c = self.data[k]
            mode, bi = k.split("|")
            rows.append("%s/%s k=(%.2f,%.2f) n=%d"
                        % ({"hip": "腰射", "ads": "开镜", "fire": "开火"}.get(mode, mode),
                           names.get(int(bi), bi), c["kx"], c["ky"], c["n"]))
        return "k 矩阵：" + " | ".join(rows)