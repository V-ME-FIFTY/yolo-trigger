"""「总是拉过头」闭环仿真（2026-10-31 第四轮）

工程链路（与 main.py 一致）：
  检测帧：ratio 由 near_boost 曲线/ADS 宽限/过冲抑制/远冲近稳 决定 → budget = min(maxstep, ad*ratio)
          cap_px = min(abs_cap*ad, burst_cap_box_ratio*框高)
  跟随线程：每 1ms 一步，rate = min(budget/剩余, step_cap)，步长 = 剩余*rate
          单调逼近（不得越过目标）→ 单步 maxstep → 预算软刹车 → 像素除 k 发计数
关键：**实际像素位移 = counts × k_true**，而程序只知道 k_est。
      k 被低估（k_true/k_est > 1）时，每周期都多走一部分 → 系统性过冲，
      且"单调逼近"是在像素域算的，挡不住 k 带来的额外位移。

运行：python check_overshoot.py
"""
import sys

OK = True


def case(name, cond, detail=""):
    global OK
    OK &= bool(cond)
    print("  %s %-46s %s" % ("✅" if cond else "❌", name, detail))
    return bool(cond)


def ctl(**kw):
    c = dict(maxstep=520.0, far=460.0, near_r=0.55, nb=240.0, nb_ratio=1.0,
             budget=1.0, budget_init=1.0, first_ms=500.0, step_cap=0.75,
             abs_cap=0.85, abs_cap_near=0.95, bcr=0.75, damp=0.45, damp_near=0.92,
             fov_hold=0.15, ads_hold=0.45, ads_hold_near=0.92, dead=2.0)
    c.update(kw)
    return c


CUR = ctl()
# 保守档：把"每周期能走多少"整体收紧，且与 k 无关的那几项一起收
SAFE = ctl(nb_ratio=0.72, step_cap=0.35, abs_cap=0.55, abs_cap_near=0.55,
           bcr=0.30, damp=0.40, damp_near=0.55, budget=0.85, budget_init=0.55)


def ratio_for(ad, c, now_ms, new_ms, ads=False, fov=False, flip=False):
    r = c["budget_init"] if now_ms < new_ms else c["budget"]
    nb = c["nb"]
    near_band = nb > 0 and ad < nb
    if near_band:
        tb = max(0.0, min(1.0, ad / nb))
        r = max(r, c["near_r"] + (c["nb_ratio"] - c["near_r"]) * (1.0 - tb * tb) ** 2)
    if fov:
        r = min(r, c["fov_hold"])
    elif ads:
        ar = c["ads_hold"]
        if near_band:
            ar = max(ar, c["ads_hold_near"])
        r = min(r, ar)
    if flip:
        r = min(r, c["damp_near"] if near_band else c["damp"])
    if c["far"] > nb + 1.0 and nb <= ad < c["far"]:
        t = (ad - nb) / (c["far"] - nb)
        r = min(r, c["near_r"] + (r - c["near_r"]) * t)
    return r


def simulate(dist, bh, k_true_over_est, c, fps=60.0, frames=12, ads=False):
    """返回 (最大过冲px, 收敛帧数)"""
    ad = dist
    pos = 0.0                     # 准星相对起点（目标在 dist 处）
    over_max = 0.0
    settle = None
    frame_dt = 1.0 / fps
    bcr = c["bcr"]
    for f in range(frames):
        now_ms = f * frame_dt * 1000.0
        r = ratio_for(ad, c, now_ms, c["first_ms"], ads=ads)
        budget = min(c["maxstep"], ad * r)
        cap = (c["abs_cap_near"] if ad < c["nb"] else c["abs_cap"]) * ad
        if bcr > 0:
            cap = min(cap, bcr * bh)
        sent_px = 0.0
        # 跟随线程：一个检测周期内多步
        steps = int(frame_dt / 0.001)
        for _ in range(steps):
            rx = dist - pos               # 剩余偏差
            d = abs(rx)
            if d <= c["dead"]:
                break
            if budget <= 0.0:
                break
            rate = min(budget / max(4.0, d), c["step_cap"])
            mv = rx * rate
            # 单调逼近（像素域）
            if (pos + mv - dist) * rx > 0:
                mv = rx
            if abs(mv) > c["maxstep"]:
                mv = c["maxstep"] if mv > 0 else -c["maxstep"]
            # 预算软刹车
            eff = abs(mv)
            if budget < eff and eff > 1e-6:
                mv *= budget / eff
                eff = abs(mv)
            if cap > 0 and sent_px + eff > cap:
                eff = max(0.0, cap - sent_px)
                mv = eff if mv > 0 else -eff
            if eff <= 0:
                break
            # 像素 → 计数（用 k_est=1）→ 实际位移（k_true）
            counts = mv / 1.0
            real = counts * k_true_over_est
            pos += real
            sent_px += abs(real)
            budget -= eff
        over = pos - dist
        if over > over_max:
            over_max = over
        ad = abs(dist - pos)
        if settle is None and ad <= c["dead"] + 3.0:
            settle = f + 1
        if settle is not None and f > settle + 1:
            break
    return over_max, (settle if settle else frames)


print("=" * 88)
print("A. k 被低估时会不会冲过头（框高 300、偏差 300/150/60，60fps，12 帧内）")
print("=" * 88)
print("  %-10s %-16s %-16s %-16s" % ("k真/估", "偏差300 过冲/帧", "偏差150 过冲/帧", "偏差60 过冲/帧"))
cur_worst = 0.0
for err in (0.7, 1.0, 1.4, 1.8):
    row = []
    for dist in (300.0, 150.0, 60.0):
        o, s = simulate(dist, 300.0, err, CUR)
        cur_worst = max(cur_worst, o)
        row.append("%5.0fpx/%2d帧" % (o, s))
    print("  %-10s %-16s %-16s %-16s" % ("%.1f" % err, row[0], row[1], row[2]))

case("当前配置在 k 低估 1.8 倍时会明显过冲（复现用户问题）", cur_worst > 20.0,
     "最坏过冲 %.0fpx" % cur_worst)

print()
print("=" * 88)
print("B. 保守档（step_cap .35 / abs_cap .55 / bcr .30 / near_boost_ratio .72 / 首答 .55）")
print("=" * 88)
print("  %-10s %-16s %-16s %-16s" % ("k真/估", "偏差300 过冲/帧", "偏差150 过冲/帧", "偏差60 过冲/帧"))
safe_worst = 0.0
for err in (0.7, 1.0, 1.4, 1.8):
    row = []
    for dist in (300.0, 150.0, 60.0):
        o, s = simulate(dist, 300.0, err, SAFE)
        safe_worst = max(safe_worst, o)
        row.append("%5.0fpx/%2d帧" % (o, s))
    print("  %-10s %-16s %-16s %-16s" % ("%.1f" % err, row[0], row[1], row[2]))

case("保守档在 k 低估 1.8 倍时过冲被压住", safe_worst <= 15.0, "最坏过冲 %.0fpx" % safe_worst)
case("保守档仍能 12 帧内收敛", all(simulate(d, 300.0, 1.0, SAFE)[1] <= 12 for d in (300.0, 150.0, 60.0)),
     "k 准确时收敛帧数 " + str([simulate(d, 300.0, 1.0, SAFE)[1] for d in (300.0, 150.0, 60.0)]))
case("当前配置 k 准确时也不拖沓", all(simulate(d, 300.0, 1.0, CUR)[1] <= 12 for d in (300.0, 150.0, 60.0)))

print()
print("=" * 88)
print("C. 关键结论")
print("=" * 88)
print("  · 过冲量 ≈ 每周期预算 × (k真/k估 - 1) 的累积；单调逼近只在像素域生效，挡不住 k 偏差")
print("  · 与 k 无关的刹车只有两条：abs_cap（本周期位移≤×偏差）与 burst_cap_box_ratio（≤×框高）")
print("  · 所以治「总是拉过头」= 收紧这两条 + 关掉会凭空改 k 的分组系数（fire/ads 灵敏度拆分）")
print()
print("全部通过 ✅" if OK else "有失败项 ❌")
sys.exit(0 if OK else 1)