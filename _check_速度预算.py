# -*- coding: utf-8 -*-
"""速度预算体检（2026-11-01）

问题：测试版功能加了一倍，实际比基准版钝。这个脚本把「钝」拆成可测的数字。

链路（与 main.py 一致，见 check_overshoot.py）：
  检测帧: ratio = 首答/近距曲线/远冲近稳/开镜宽限/过冲抑制 逐层 min
          budget = min(max_step, 偏差 × ratio)
          cap_px = min(abs_move_cap × 偏差, burst_cap_box_ratio × 框高)
  跟随线程: 每 1ms 一步，rate = min(budget/剩余, step_cap)，像素 → 计数 → ×k

指标：
  收敛帧数 —— 从偏差 D 到贴上目标要几个检测周期（60fps，16.7ms/帧），越小越快
  最大过冲 —— 超过目标的像素数，越大越"拉过头"

三档对照 + 逐项敏感度（同一份代码，每次只改一个变量回基准值）。

用法：python _check_速度预算.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# main.py 里 config 缺键时的默认值
DEFAULTS = dict(maxstep=45.0, far=260.0, near_r=0.30, nb=140.0, nb_ratio=1.0,
                budget=0.55, budget_init=0.95, first=160.0, step_cap=0.18,
                abs_cap=1.0, abs_cap_near=0.95, bcr=0.75, damp=0.45,
                damp_near=0.85, fov_hold=0.15, ads_hold=0.45, ads_hold_near=0.92,
                dead=2.0)

KEYMAP = dict(maxstep="max_step_px", far="far_px", near_r="near_ratio",
              nb="near_boost_px", nb_ratio="near_boost_ratio",
              budget="budget_ratio", budget_init="budget_ratio_initial",
              first="first_burst_ms", step_cap="step_cap",
              abs_cap="abs_move_cap", abs_cap_near="abs_move_cap_near",
              bcr="burst_cap_box_ratio", damp="damp_ratio",
              damp_near="near_damp_ratio", fov_hold="fov_hold_ratio",
              ads_hold="ads_hold_ratio", ads_hold_near="near_ads_hold_ratio")


def ctl_from(cfg):
    a = cfg.get("aim") or {}
    c = {"dead": 2.0}
    for k, key in KEYMAP.items():
        c[k] = float(a.get(key, DEFAULTS[k]))
    return c


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


def simulate(dist, bh, k_err, c, fps=60.0, frames=12):
    """返回 (最大过冲px, 收敛帧数, 首帧实际位移px)"""
    ad = dist
    pos = 0.0
    over_max = 0.0
    settle = None
    first_move = 0.0
    frame_dt = 1.0 / fps
    for f in range(frames):
        now_ms = f * frame_dt * 1000.0
        r = ratio_for(ad, c, now_ms, c["first"])
        budget = min(c["maxstep"], ad * r)
        cap = (c["abs_cap_near"] if ad < c["nb"] else c["abs_cap"]) * ad
        if c["bcr"] > 0:
            cap = min(cap, c["bcr"] * bh)
        sent = 0.0
        steps = int(frame_dt / 0.001)
        for _ in range(steps):
            rx = dist - pos
            d = abs(rx)
            if d <= c["dead"] or budget <= 0.0:
                break
            rate = min(budget / max(4.0, d), c["step_cap"])
            mv = rx * rate
            if (pos + mv - dist) * rx > 0:
                mv = rx
            if abs(mv) > c["maxstep"]:
                mv = c["maxstep"] if mv > 0 else -c["maxstep"]
            eff = abs(mv)
            if budget < eff and eff > 1e-6:
                mv *= budget / eff
                eff = abs(mv)
            if cap > 0 and sent + eff > cap:
                eff = max(0.0, cap - sent)
                mv = eff if mv > 0 else -eff
            if eff <= 0:
                break
            real = (mv / 1.0) * k_err
            pos += real
            sent += abs(real)
            budget -= eff
        if f == 0:
            first_move = pos
        over = pos - dist
        if over > over_max:
            over_max = over
        ad = abs(dist - pos)
        if settle is None and ad <= c["dead"] + 3.0:
            settle = f + 1
        if settle is not None and f > settle + 1:
            break
    return over_max, (settle if settle else frames), first_move


def row(c, bh=300.0, k=1.0):
    out = []
    for d in (300.0, 150.0, 60.0):
        o, s, fm = simulate(d, bh, k, c)
        out.append((d, o, s, fm))
    return out


def show(title, c, bh=300.0):
    print("  %s" % title)
    print("    %-8s | %-16s | %-16s | %-16s" % ("偏差", "首帧走多少px", "收敛帧数", "最大过冲"))
    for d, o, s, fm in row(c, bh):
        print("    %-8s | %-16s | %-16s | %-16s"
              % ("%.0fpx" % d, "%.0f px (%.0f%%)" % (fm, 100.0 * fm / d),
                 "%d 帧 (%.0fms)" % (s, s * 16.7), "%.0f px" % o))
    return row(c, bh)


def main():
    now = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    base_path = r"C:\yolo-trigger-基准-20261026\config.json"
    base = json.load(io.open(base_path, encoding="utf-8")) if os.path.exists(base_path) else None

    bal_path = os.path.join(HERE, "config-平衡档.json")
    ful_path = os.path.join(HERE, "config-满载档.json")
    bal = json.load(io.open(bal_path, encoding="utf-8")) if os.path.exists(bal_path) else None
    ful = json.load(io.open(ful_path, encoding="utf-8")) if os.path.exists(ful_path) else None

    cn, cb = ctl_from(now), (ctl_from(base) if base else None)
    co, cf = (ctl_from(bal) if bal else None), (ctl_from(ful) if ful else None)

    print("=" * 84)
    print("一、三档对照（框高 300px，k 准确，60fps）")
    print("=" * 84)
    if cb:
        show("基准版 20261026", cb)
        print()
    show("当前测试版", cn)
    if co:
        print()
        show("优化档 平衡", co)
    if cf:
        print()
        show("优化档 满载", cf)

    print()
    print("=" * 84)
    print("二、卡在哪：单周期能走多少（偏差 300px、框高 300、首帧）")
    print("=" * 84)
    print("  %-14s %-12s %-12s %s" % ("档位", "ratio", "budget_px", "cap_px(真正上限)"))
    for name, c in (("基准", cb), ("测试版", cn), ("平衡档", co), ("满载档", cf)):
        if not c:
            continue
        r = ratio_for(300.0, c, 0.0, c["first"])
        budget = min(c["maxstep"], 300.0 * r)
        cap = min(c["abs_cap"] * 300.0, c["bcr"] * 300.0)
        print("  %-14s %-12.2f %-12.0f %.0f   <- 实际每周期最多走 %.0fpx"
              % (name, r, budget, cap, min(budget, cap)))

    print()
    print("=" * 84)
    print("三、敏感度：从当前测试版出发，每次只把一项改回基准值，看收敛帧数怎么变")
    print("=" * 84)
    if cb:
        base_d = 300.0
        o0, s0, f0 = simulate(base_d, 300.0, 1.0, cn)
        print("  %-22s %-14s %-14s %s" % ("改这一项", "测试版", "改回基准", "收敛帧数变化"))
        rows = []
        for k in ("bcr", "abs_cap", "abs_cap_near", "budget", "budget_init",
                  "nb_ratio", "damp_near", "step_cap", "maxstep", "first"):
            c2 = dict(cn)
            c2[k] = cb[k]
            o, s, f = simulate(base_d, 300.0, 1.0, c2)
            rows.append((s0 - s, k, cn[k], cb[k], s))
        rows.sort(reverse=True)
        for gain, k, nv, bv, s in rows:
            flag = "快 %d 帧" % gain if gain > 0 else ("慢 %d 帧" % -gain if gain < 0 else "无变化")
            print("  %-22s %-14s %-14s %s" % ("%s %.2f -> %.2f" % (k, nv, bv), "%d 帧" % s0, "%d 帧" % s, flag))

    print()
    print("=" * 84)
    print("四、k 估偏时会不会拉过头（k真/k估 = 1.4）")
    print("=" * 84)
    for name, c in (("基准", cb), ("测试版", cn), ("平衡档", co), ("满载档", cf)):
        if not c:
            continue
        worst = max(simulate(d, 300.0, 1.4, c)[0] for d in (300.0, 150.0, 60.0))
        print("  %-10s 最坏过冲 %.0f px" % (name, worst))


if __name__ == "__main__":
    main()