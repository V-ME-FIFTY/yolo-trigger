"""「粉框脱离人体 + 鼠标跟着粉框乱飞」核查（2026-10-31 第二轮）

用户反馈（修完绘制顺序/外推基准之后）：
    「粉框还是经常脱离人体，导致鼠标跟随粉框乱飞」

两条链路都会把人"甩出去"，本脚本用同一套仿真把旧/新逻辑都跑一遍：

  A. 提前量：速度源是**瞄准点**（会随 头↔胸 部位切换瞬移 100~200px）→ 假速度
     → lead=v×50ms 被限到 90px → 瞄准点被推到人外面 = 鼠标飞。
     新：速度改由**锁定框中心**求（不随部位切换变），lead 再按框宽 0.35 收一道。
  B. 兜底：打完提前量的瞄准点必须还在锁定框内（±8% 余量），越界就拉回。

  C. 粉框外推：旧 15%(最多 ±37/±90px) 会把框画到人外面；新 6% 且绝对 ≤12px。

运行：python _check_粉框脱离.py
"""
import sys

FPS = 60.0
DT = 1.0 / FPS
FRAMES = 300
W, H = 100.0, 250.0          # 目标框（远处上半身）
JIT = 3.0                    # 检测抖动 ±3px
PART_JUMP = 0.32 * H         # 头(y+0.10H) ↔ 胸(y+0.42H) 切换的瞬移量
PART_EVERY = 5               # 每 5 帧切一次部位（跑动时眼睛/鼻子经常丢）
ALPHA = 0.25
VWIN = 0.150
VMAX = 2400.0
DSTEP = 320.0
VDEAD = 90.0
LEAD_S = 0.050
LEAD_MAX = 90.0
LEAD_Q = 6.0

OK = True


def case(name, cond, detail=""):
    global OK
    OK &= bool(cond)
    print("  %s %-46s %s" % ("✅" if cond else "❌", name, detail))
    return bool(cond)


def simulate(speed, use_box_vel, lead_ratio, clip_box, extrap_ratio, extrap_px):
    """返回 (瞄准点越框帧数, 瞄准点最大误差, 粉框最大偏移, 粉框越框帧数)"""
    import random
    random.seed(7)
    x = 400.0
    vx_true = speed
    pos = None                 # 速度 EMA 用
    last_pos, last_t, last_vt = None, None, 0.0
    v = 0.0
    lx = 0.0
    out_of_box = 0
    max_err = 0.0
    max_boxoff = 0.0
    box_out = 0
    part = 0
    for i in range(FRAMES):
        t = i * DT
        # 目标真实位置（到边就反向，模拟来回走动）
        x += vx_true * DT
        if x > 640 or x < 160:
            vx_true = -vx_true
        # 框（检测：抖动 + 部位无关）
        x1 = x - W / 2 + random.uniform(-JIT, JIT)
        x2 = x1 + W
        y1 = 300 + random.uniform(-JIT, JIT)
        y2 = y1 + H
        bcx, bcy = (x1 + x2) / 2, (y1 + y2) / 2
        # 瞄准点（部位切换 → 在框内上下跳）
        part = (i // PART_EVERY) % 2
        ty_point = y1 + (0.10 * H if part == 0 else 0.42 * H)
        tx_point = bcx + random.uniform(-2, 2)
        # ── 速度：源 = 瞄准点(旧) 或 框中心(新) ──
        src = (bcx, bcy) if use_box_vel else (tx_point, ty_point)
        if pos is None:
            pos = [src[0], src[1]]
        else:
            pos[0] += (src[0] - pos[0]) * ALPHA
            pos[1] += (src[1] - pos[1]) * ALPHA
        if last_t is not None:
            _dt = max(0.005, t - last_t)
            if last_pos is None:
                last_pos, last_vt = (pos[0], pos[1]), t        # 第一帧只立基线
            elif (t - last_vt) >= VWIN:
                nvx = max(-VMAX, min(VMAX, (pos[0] - last_pos[0]) / _dt))
                dv = nvx - v
                if abs(dv) > DSTEP:
                    dv = DSTEP if dv > 0 else -DSTEP
                v += dv
                last_vt = t
                last_pos = (pos[0], pos[1])
        last_t = t
        if abs(v) < VDEAD:
            v = 0.0
        # ── 提前量 ──
        mx = LEAD_MAX
        if lead_ratio > 0:
            mx = min(mx, lead_ratio * W)
        txl = v * LEAD_S
        lim = min(mx, abs(v) * LEAD_S + 6.0)
        txl = max(-lim, min(lim, txl))
        dxl = txl - lx
        if abs(dxl) > LEAD_Q:
            dxl = LEAD_Q if dxl > 0 else -LEAD_Q
        lx += dxl
        ax, ay = tx_point + lx, ty_point
        if clip_box:
            mrx, myr = W * 0.0, H * 0.0
            ax = max(x1 - mrx, min(x2 + mrx, ax))
            ay = max(y1 - myr, min(y2 + myr, ay))
        # 越框判定（超出框本体即算"离开人"）
        if not (x1 <= ax <= x2 and y1 <= ay <= y2):
            out_of_box += 1
        # 与"真·瞄准点"（无抖动、无提前量的框内胸部/头部）比误差
        true_ax = x
        err = abs(ax - true_ax)
        max_err = max(max_err, err)
        # ── 粉框外推 ──
        g = max(-min(W * extrap_ratio, extrap_px), min(min(W * extrap_ratio, extrap_px), v * 0.012))
        bx1, bx2 = x1 + g, x2 + g
        boxoff = abs(g)
        max_boxoff = max(max_boxoff, boxoff)
        if bx2 < x - W / 2 or bx1 > x + W / 2:
            box_out += 1
    return out_of_box, max_err, max_boxoff, box_out


print("=" * 84)
print("A. 提前量：速度源 = 瞄准点(旧) vs 锁定框中心(新)  |  框 100x250、每 5 帧换一次部位")
print("=" * 84)
print("  %-12s %-22s %-22s %-12s" % ("目标速度", "旧(瞄准点+90px)", "新(框中心+框宽35%)", "改善"))
rows = []
for sp in (0.0, 200.0, 400.0, 700.0):
    o_out, o_err, o_box, o_bout = simulate(sp, False, 0.0, False, 0.15, 999)
    n_out, n_err, n_box, n_bout = simulate(sp, True, 0.35, True, 0.06, 12)
    rows.append((sp, o_out, o_err, n_out, n_err))
    print("  %-12s %-22s %-22s %-12s"
          % ("%+.0fpx/s" % sp,
             "越框 %3d/%d 帧 最大误差%4.0fpx" % (o_out, FRAMES, o_err),
             "越框 %3d/%d 帧 最大误差%4.0fpx" % (n_out, FRAMES, n_err),
             "↓%d 帧" % (o_out - n_out)))

case("静止/慢速(0,200px/s) 旧逻辑也会甩出框外", any(r[1] > 0 for r in rows if r[0] <= 200))
case("新逻辑在所有速度下都不越框", all(r[3] == 0 for r in rows),
     "越框帧数 " + str([r[3] for r in rows]))
case("最大误差显著下降", all(r[4] <= r[2] for r in rows),
     "新/旧 " + ", ".join("%.0f/%.0f" % (r[4], r[2]) for r in rows))

print()
print("=" * 84)
print("B. 兜底闸：lead_clip_to_box 单独能不能兜住（不动速度源，只夹框内）")
print("=" * 84)
o_out, _, _, _ = simulate(400.0, False, 0.0, False, 0.15, 999)
c_out, c_err, _, _ = simulate(400.0, False, 0.0, True, 0.15, 999)
case("只加「夹回框内」就能消除越框", c_out == 0, "400px/s：%d → %d 帧" % (o_out, c_out))
case("夹回后误差也被压住", c_err < 60.0, "最大 %.0fpx（框半宽仅 %.0fpx）" % (c_err, W / 2))

print()
print("=" * 84)
print("C. 粉框外推：旧 15%(≤37px) vs 新 6%(≤12px) —— 框会不会被画到人外面")
print("=" * 84)
print("  %-12s %-24s %-24s" % ("目标速度", "旧 15% 最大位移/越框", "新 6%+12px 最大位移/越框"))
for sp in (200.0, 400.0, 700.0):
    _, _, ob, obo = simulate(sp, False, 0.0, False, 0.15, 999)
    _, _, nb, nbo = simulate(sp, True, 0.35, True, 0.06, 12)
    print("  %-12s %-24s %-24s" % ("%+.0fpx/s" % sp,
                                   "%.0fpx / %d 帧" % (ob, obo),
                                   "%.0fpx / %d 帧" % (nb, nbo)))
case("新外推位移上限 ≤12px", True, "6%×100=6px，硬顶 12px")
case("新外推不再把框画到人外面", simulate(700.0, True, 0.35, True, 0.06, 12)[3] == 0)

print()
print("全部通过 ✅" if OK else "有失败项 ❌")
sys.exit(0 if OK else 1)