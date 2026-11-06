"""常加速度卡尔曼跟踪器（2026-10-31，P0 第一项）

目的：把"每帧重新投票"换成"每条目标一条 track"。
现状（基准版 pick_locked）：只有 IoU + 迟滞，速度靠差分，目标被挡 2 帧就掉锁，
速度一抖就成假速度 —— 用户反复报的"跟丢 / 粉框飘 / 拉过头"都跟这个有关。

本模块只做三件事，且与渲染、鼠标完全解耦（纯 numpy，可单测）：
  1) predict：没有新检测也能把框往前推（遮挡/漏检期间续命）
  2) update ：有检测就按卡尔曼增益修正（位置平滑、速度可信）
  3) associate：框与 track 的关联（IoU + 中心距离 + 尺寸相似度 的代价矩阵，贪心匹配）

状态量（每个目标、每轴独立）：[位置, 速度, 加速度]
  过程噪声 accel_q：目标加速度不确定度（px/s²），越大越"跟着新观测走"
  观测噪声 meas_r ：检测框中心抖动（px），越大越"平滑/迟钝"

用法：
    trk = Tracker(cfg)
    tracks = trk.update(boxes, now)          # 每检测帧调一次
    best = trk.pick(tracks, aim_x, aim_y)    # 选一个（返回 track）
    x1,y1,x2,y2 = best.box_at(now)           # 任意时刻都能取框（含预测）
"""
from __future__ import annotations

import numpy as np


class Track:
    """一个目标（2D 框 + 每轴 [p, v, a] 卡尔曼）"""

    _next_id = 0

    def __init__(self, box, now, q=900.0, r=25.0):
        self.id = Track._next_id
        Track._next_id += 1
        self.t = float(now)
        self.q = float(q)          # 加速度过程噪声 (px/s^2)^2
        self.r = float(r)          # 观测噪声 (px)^2
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        self.w, self.h = max(1.0, x2 - x1), max(1.0, y2 - y1)
        # 每轴状态 [p, v, a]；初始速度/加速度未知 → 0
        self.sx = np.array([cx, 0.0, 0.0], dtype=np.float64)
        self.sy = np.array([cy, 0.0, 0.0], dtype=np.float64)
        # 协方差：初始位置不确定 = 观测噪声，速度/加速度给大不确定度
        self.Px = np.diag([self.r, 400.0 ** 2, 400.0 ** 2]).astype(np.float64)
        self.Py = np.diag([self.r, 400.0 ** 2, 400.0 ** 2]).astype(np.float64)
        self.hits = 1              # 累计匹配次数
        self.misses = 0            # 连续未匹配帧数
        self.confirmed = False
        self.last_box = (x1, y1, x2, y2)

    # ── 预测 ──
    def _F(self, dt):
        return np.array([[1.0, dt, 0.5 * dt * dt],
                         [0.0, 1.0, dt],
                         [0.0, 0.0, 1.0]], dtype=np.float64)

    def _Q(self, dt):
        # 连续白噪声加速度模型（离散化近似）
        q = self.q
        dt2, dt3, dt4 = dt * dt, dt ** 3, dt ** 4
        return q * np.array([[dt4 / 4.0, dt3 / 2.0, dt2 / 2.0],
                             [dt3 / 2.0, dt2, dt],
                             [dt2 / 2.0, dt, 1.0]], dtype=np.float64)

    def predict_to(self, now):
        dt = float(now) - self.t
        if dt <= 0:
            return
        F = self._F(dt)
        Q = self._Q(dt)
        self.sx = F @ self.sx
        self.sy = F @ self.sy
        self.Px = F @ self.Px @ F.T + Q
        self.Py = F @ self.Py @ F.T + Q
        self.t = float(now)

    # ── 修正 ──
    def update(self, box):
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        zx, zy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        H = np.array([[1.0, 0.0, 0.0]], dtype=np.float64)
        for s, P, z in ((self.sx, self.Px, zx), (self.sy, self.Py, zy)):
            S = H @ P @ H.T + self.r
            K = (P @ H.T) / S[0][0]
            innov = z - (H @ s)[0]
            s += (K * innov)[:, 0]
            P -= K @ H @ P
        # 尺寸平滑（框大小抖动也影响"框贴不贴人"）
        self.w = 0.7 * self.w + 0.3 * max(1.0, x2 - x1)
        self.h = 0.7 * self.h + 0.3 * max(1.0, y2 - y1)
        self.last_box = (x1, y1, x2, y2)
        self.hits += 1
        self.misses = 0
        if not self.confirmed and self.hits >= 2:
            self.confirmed = True

    # ── 取框 ──
    def center(self, now=None):
        if now is not None:
            self.predict_to(now)
        return float(self.sx[0]), float(self.sy[0])

    def box_at(self, now, coast_decay=2.0):
        """返回 (x1,y1,x2,y2)：以预测中心 + 平滑尺寸拼框。

        ★遮挡期衰减★ 连续未匹配 m 帧时，把"预测位移"按 exp(-m/coast_decay) 打掉：
        直线走的目标前 2~3 帧仍拿得到大部分预测收益；目标正好在遮挡里掉头时，
        预测不会把人往反方向甩出去（这是"卡尔曼反而更偏"的唯一来源）。
        """
        pcx, pcy = self.center(now)
        lx = (self.last_box[0] + self.last_box[2]) / 2.0
        ly = (self.last_box[1] + self.last_box[3]) / 2.0
        if self.misses > 0:
            k = float(np.exp(-self.misses / max(0.5, float(coast_decay))))
            cx = lx + (pcx - lx) * k
            cy = ly + (pcy - ly) * k
        else:
            cx, cy = pcx, pcy
        return (cx - self.w / 2.0, cy - self.h / 2.0,
                cx + self.w / 2.0, cy + self.h / 2.0)

    def vel(self):
        return float(self.sx[1]), float(self.sy[1])

    def age(self, now):
        return float(now) - self.t + 0.0


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class Tracker:
    """多目标跟踪：关联 → 更新 → 生命周期"""

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self.q = float(self.cfg.get("accel_q", 900.0))
        self.r = float(self.cfg.get("meas_r", 25.0))
        self.gate_px = float(self.cfg.get("gate_px", 120.0))     # 关联门限（框心距离，按框高缩放）
        self.gate_iou = float(self.cfg.get("gate_iou", 0.05))
        self.coast_ms = float(self.cfg.get("coast_ms", 300.0))   # 无检测可续命多久
        self.confirm_hits = int(self.cfg.get("confirm_hits", 1))
        self.tracks = []

    def update(self, boxes, now, size_ref=250.0):
        """喂入本帧框，返回 track 列表（含正在 coast 的）"""
        boxes = [tuple(float(v) for v in b[:4]) for b in boxes]
        for t in self.tracks:
            t.predict_to(now)
        if boxes and self.tracks:
            # 代价 = 中心距离（按框高归一）+ (1 - IoU)；贪心一对一
            pairs = []
            for ti, t in enumerate(self.tracks):
                tcx, tcy = t.center()
                for bi, b in enumerate(boxes):
                    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                    d = ((tcx - bcx) ** 2 + (tcy - bcy) ** 2) ** 0.5
                    iou = _iou(t.last_box, b)
                    gate = max(1.0, self.gate_px * (t.h / max(1.0, size_ref)))
                    if d > gate and iou < self.gate_iou:
                        continue
                    cost = d / gate + (1.0 - iou)
                    pairs.append((cost, ti, bi))
            pairs.sort()
            used_t, used_b = set(), set()
            for cost, ti, bi in pairs:
                if ti in used_t or bi in used_b:
                    continue
                self.tracks[ti].update(boxes[bi])
                used_t.add(ti)
                used_b.add(bi)
            for ti, t in enumerate(self.tracks):
                if ti not in used_t:
                    t.misses += 1
        else:
            for t in self.tracks:
                t.misses += 1
        # 新生
        for bi, b in enumerate(boxes):
            if any(abs(((t.last_box[0] + t.last_box[2]) / 2.0) - ((b[0] + b[2]) / 2.0)) < 1e-6
                   and abs(((t.last_box[1] + t.last_box[3]) / 2.0) - ((b[1] + b[3]) / 2.0)) < 1e-6
                   for t in self.tracks):
                continue
            if not any(_iou(t.last_box, b) > 0.5 for t in self.tracks):
                nt = Track(b, now, q=self.q, r=self.r)
                nt.confirmed = self.confirm_hits <= 1
                self.tracks.append(nt)
        # 回收
        keep = []
        for t in self.tracks:
            if t.misses * 16.7 > self.coast_ms:      # 以 60fps 计
                continue
            keep.append(t)
        self.tracks = keep
        return self.tracks

    def pick(self, tracks, aim_x, aim_y, min_h=20.0):
        """选目标：已确认 + 高度够 → 离准星最近（可换成威胁评分）"""
        cands = [t for t in tracks if t.confirmed and t.h >= min_h]
        if not cands:
            cands = [t for t in tracks if t.h >= min_h]       # 还没确认也先给（旧行为不会平白掉锁）
        if not cands:
            return None
        def d(t):
            cx, cy = t.center()
            return ((cx - aim_x) ** 2 + (cy - aim_y) ** 2) ** 0.5
        return min(cands, key=d)

    # ── 粘滞选目标（治"多敌人来回转火"，2026-10-31）──
    def pick_sticky(self, tracks, aim_x, aim_y, min_h, st, now, cfg=None):
        """在"离准星最近"之上加四道闸，只有明确该换才换：
            ① 迟滞：新目标必须比旧目标再近 switch_hysteresis(0.7) 倍
            ② 确认：这个"更近"要连续 confirm_frames(2) 帧成立
            ③ 冷却：距上次换人 ≥ switch_cooldown_ms(400)
            ④ 粘住：旧目标只要还在 tracks 里（含 coast 预测期）就不许被"稍微近一点"的人抢走
        真正该换的三种情况仍然能换：旧目标被回收（死了/出视野）、旧目标跑到 keep_lost 之外、
        或新目标明显更近且持续两帧。
        """
        c = cfg or {}
        hyst = float(c.get("switch_hysteresis", 0.70))
        need = max(1, int(c.get("switch_confirm_frames", 2)))
        cool = float(c.get("switch_cooldown_ms", 400.0)) / 1000.0
        cands = [t for t in tracks if t.confirmed and t.h >= min_h]
        if not cands:
            cands = [t for t in tracks if t.h >= min_h]
        if not cands:
            st["id"] = None
            st["cand"] = None
            st["n"] = 0
            return None

        def dist(t):
            cx, cy = t.center(now)
            return ((cx - aim_x) ** 2 + (cy - aim_y) ** 2) ** 0.5

        best = min(cands, key=dist)
        cur = None
        cur_id = st.get("id")
        if cur_id is not None:
            for t in cands:
                if t.id == cur_id:
                    cur = t
                    break
        if cur is None:
            # 旧目标没了（被回收 / 掉出候选）→ 直接上新目标，不设迟滞
            st["id"] = best.id
            st["t_sw"] = now
            st["cand"] = None
            st["n"] = 0
            return best
        if best.id == cur.id:
            st["cand"] = None
            st["n"] = 0
            return cur
        # ── 快速换人旁路（2026-10-31 二版：用户反馈"锁得有点慢"）──
        # 粘滞是为了治"来回跳"，但下面三种情况是"人已经换了"，不该再等 400ms 冷却：
        #   ① 旧目标正在 coast（本帧没检测支撑，人已经离开/被挡）→ 换
        #   ② 准星已经落在新目标框里（玩家自己把准星甩过去了）→ 换
        #   ③ 新目标近到 fast_switch_ratio(0.45) 以内 → 换
        _in_box = bool(c.get("fast_switch_in_box", True))
        _on_coast = bool(c.get("fast_switch_on_coast", True))
        _cur_coast = int(getattr(cur, "misses", 0)) > 0
        _bb = best.last_box
        _cb = cur.last_box
        _aim_in_best = (_bb[0] <= aim_x <= _bb[2] and _bb[1] <= aim_y <= _bb[3])
        _aim_in_cur = (_cb[0] <= aim_x <= _cb[2] and _cb[1] <= aim_y <= _cb[3])
        # 注意：不要用"新目标近到 xx%"当旁路 —— 两人贴脸交叉时它会变成新的来回跳来源
        # （实测 ±60px 场景换人 0→16 次）。只认"旧目标丢了"和"准星已压到新目标身上"。
        if (_on_coast and _cur_coast) or (_in_box and _aim_in_best and not _aim_in_cur):
            st["id"] = best.id
            st["t_sw"] = now
            st["cand"] = None
            st["n"] = 0
            return best
        # 想换人：先看迟滞
        if dist(best) > dist(cur) * hyst:
            st["cand"] = None
            st["n"] = 0
            return cur
        # 冷却
        if (now - float(st.get("t_sw", 0.0))) < cool:
            return cur
        # 连续确认
        if st.get("cand") == best.id:
            st["n"] = int(st.get("n", 0)) + 1
        else:
            st["cand"] = best.id
            st["n"] = 1
        if int(st.get("n", 0)) >= need:
            st["id"] = best.id
            st["t_sw"] = now
            st["cand"] = None
            st["n"] = 0
            return best
        return cur
