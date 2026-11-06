"""弹道提前量（P1，2026-10-31）

用户给的实测前提（2026-10-31）：**这个游戏没有明显下坠，弹速 500~700 m/s**。
于是提前量退化成纯飞行时间：`lead = v_target × t`，`t = range / v_bullet`，
垂直方向不需要补偿（重力项保留但默认 g=0，将来有下坠可开）。

难点是 **range（米）拿不到**——我们只有像素。用"人高已知"反推：
    视高 h_px ∝ 1 / range   →   range_m = C / h_px,   C = f_px × H_person（像素·米）
f_px 由 FOV 决定（2560 宽 ≈ 90° 时 f≈1280），H_person≈1.75m → **C ≈ 2200 px·m**。
所以
    t = C / (h_px × v)                            （秒）
    lead_x = v_target_x × t,  lead_y = v_target_y × t + ¼·g·t²   （g 默认 0）

自检：h=460px → range 4.8m → t(600m/s)=8ms；h=100px → 22m → 37ms；h=50px → 44m → 73ms。
无下坠时竖直提前量恒为 0，横向随"距离×目标速度"增长，且被 lead_max_px 与框宽比例夹住。

用法：
    b = Ballistics(cfg.get("ballistics") or {})
    lx, ly = b.lead(box_h_px, vx_px_s, vy_px_s)      # 返回像素提前量
"""
from __future__ import annotations


class Ballistics:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.speed = float(c.get("speed_m_s", 600.0))        # 500~700，按武器调
        self.const = float(c.get("size_const_px_m", 2200.0))  # f_px × 人高
        self.gravity = float(c.get("gravity_m_s2", 0.0))     # 无下坠 = 0
        self.max_ms = float(c.get("max_flight_ms", 260.0))   # 飞行时间上限（防极端）
        self.gain = float(c.get("lead_gain", 1.0))           # 0.5~1.0 微调
        self.min_h = float(c.get("min_box_h", 24.0))         # 框太小不估距离
        self.px_per_m = float(c.get("px_per_m", 1.0))        # 竖直重力折算（像素/米）

    # ── 距离与飞行时间 ──
    def flight_time_s(self, box_h_px):
        h = max(self.min_h, float(box_h_px))
        if self.speed <= 0.0 or self.const <= 0.0:
            return 0.0
        t = (self.const / h) / self.speed
        return min(t, self.max_ms / 1000.0)

    def range_m(self, box_h_px):
        h = max(self.min_h, float(box_h_px))
        return self.const / h

    # ── 提前量 ──
    def lead(self, box_h_px, vx_px_s, vy_px_s, box_w_px=None, lead_max_px=None):
        if not self.enabled:
            return (0.0, 0.0, 0.0)
        t = self.flight_time_s(box_h_px)
        lx = float(vx_px_s) * t * self.gain
        ly = float(vy_px_s) * t * self.gain
        if self.gravity > 0.0:
            # 下坠补偿：子弹在 t 内掉了 ½gt²，米 → 像素
            ly -= 0.5 * self.gravity * t * t * self.px_per_m
        # 夹在 absolute 与 框宽比例 之内（提前量不许把人推出身体）
        lim = lead_max_px if lead_max_px else 90.0
        if box_w_px:
            lim = min(lim, max(4.0, 0.35 * float(box_w_px)))
        lx = max(-lim, min(lim, lx))
        ly = max(-lim, min(lim, ly))
        return (lx, ly, t)