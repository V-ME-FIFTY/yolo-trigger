"""后坐力补偿（P1，2026-10-31）

你没有给具体上飘量级，所以先按**通用曲线**出一版：绝大多数自动武器是
  · 开局 前 4~6 发几乎线性上飘（垂直为主，速度最快）
  · 之后进入平台：上飘速度明显下降，但出现左右摆动（S 形）
松开扳机后 300~500ms 内完全复位。

于是补偿就是一个**随时间变化的下压速率**（像素/秒），叠在跟随线程的位移上：
    t < switch_s      : rate = peak1
    t >= switch_s     : rate 从 peak1 平滑过渡到 peak2（指数混合，避免速度跳变）
    水平              : sway = A·sin(2πft)，幅值很小，纯抵消左右摆
    not held          : rate = 0

不用管"第几发"——按时间积分和按发数积分在等射速下等价，而按时间不需要知道射速。

参数（config.ballistics 同级 `recoil`）：
    peak1_px_s  前段下压速度，默认 110（≈ 8px/发 × 13 发/秒 量级）
    peak2_px_s  平台段，默认 55
    switch_s    两段切换时刻，默认 0.55
    ramp_s      过渡时间，默认 0.12
    sway_px_s   左右摆幅，默认 6（觉得抖就调 0）
    sway_hz     摆动频率，默认 0.7
    scale       总倍率（先 0.6 试，稳了再往上推）
"""
from __future__ import annotations

import math


class RecoilComp:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.peak1 = float(c.get("peak1_px_s", 110.0))
        self.peak2 = float(c.get("peak2_px_s", 55.0))
        self.switch_s = float(c.get("switch_s", 0.55))
        self.ramp_s = max(0.02, float(c.get("ramp_s", 0.12)))
        self.sway = float(c.get("sway_px_s", 6.0))
        self.sway_hz = float(c.get("sway_hz", 0.7))
        self.scale = float(c.get("scale", 0.6))
        self.max_px_s = float(c.get("max_px_s", 400.0))

    def rates(self, held, now, press_t):
        """返回 (vx, vy) 像素/秒；y 正方向=屏幕向下（补偿上飘就往 +y 推）"""
        if not self.enabled or not held or press_t is None:
            return (0.0, 0.0)
        t = max(0.0, float(now) - float(press_t))
        if t < self.switch_s:
            rate = self.peak1
        else:
            # 指数过渡：切换点处速度连续，不会"突然变轻"
            k = 1.0 - math.exp(-(t - self.switch_s) / self.ramp_s)
            rate = self.peak1 * (1.0 - k) + self.peak2 * k
        rate *= self.scale
        if self.max_px_s > 0:
            rate = min(rate, self.max_px_s)
        sway = 0.0
        if self.sway != 0.0:
            sway = self.sway * math.sin(2.0 * math.pi * self.sway_hz * t) * self.scale
        return (sway, rate)

    def accumulated_px(self, hold_s, dt=0.001):
        """整段按压累计下压量（给探针/自检用）"""
        total = 0.0
        vx_total = 0.0
        t = 0.0
        while t < hold_s:
            _, vy = self.rates(True, t, 0.0)
            total += vy * dt
            vx_total += self.rates(True, t, 0.0)[0] * dt
            t += dt
        return (vx_total, total)