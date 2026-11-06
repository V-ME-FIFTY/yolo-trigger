# -*- coding: utf-8 -*-
"""「近距直取」收敛核查（2026-10-30）

问题：枪口离敌人头部较近时，自瞄转向很慢、要磨好几个检测周期才贴上。

这里把 main.py 的两段逻辑复刻出来跑数字：
  A) 检测帧：偏差 → 本周期预算（_lim = min(max_step, 偏差 × ratio)）
     ratio 的合成顺序 = budget_ratio →(未标定≤0.5) →近距直取→ fov/开镜/过冲三道硬保护
                        → [near_boost_px, far_px] 线性过渡
  B) 跟随线程：1ms 一个 tick 花预算（含 step_cap / max_step / abs_cap 三道限幅 / 死区）

对比"旧公式"（near_ratio 一路降到 0px、abs_cap=0.85）与"新公式"（贴脸段反向抬升、
abs_cap 放满），给出从各初始偏差收敛到死区所需的**检测帧数**与**毫秒数**。

用法：python _check_近距直取.py
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def ratio_new(ad, a):
    """main.py 现行顺序（2026-10-30 之后）。"""
    ratio = float(a["budget_ratio"])
    near_r = float(a["near_ratio"])
    far = float(a["far_px"])
    boost_px = float(a.get("near_boost_px", 0.0))
    boost_r = float(a.get("near_boost_ratio", 1.0))
    nb = max(1.0, boost_px)
    near_band = boost_px > 0.0 and ad < nb
    if near_band:
        tb = max(0.0, min(1.0, ad / nb))
        ratio = max(ratio, near_r + (boost_r - near_r) * (1.0 - tb * tb) ** 2)
    if far > nb + 1.0 and nb <= ad < far:
        t = (ad - nb) / (far - nb)
        ratio = min(ratio, near_r + (ratio - near_r) * t)
    return ratio, near_band


def ratio_old(ad, a):
    """2026-10-26~10-30 的旧顺序（贴脸段一路降到 near_ratio）。"""
    ratio = float(a["budget_ratio"])
    near_r = float(a["near_ratio"])
    far = float(a["far_px"])
    if far > 1.0 and ad < far:
        t = max(0.0, min(1.0, ad / far))
        ratio = min(ratio, near_r + (ratio - near_r) * t * t)
    return ratio, False


def simulate(ad0, a, dry, abs_cap_default):
    """按检测帧-跟随线程两级结构跑，返回 (收敛所用检测帧数, 剩余偏差序列)。"""
    ad = float(ad0)
    dead = float(a["dead_zone_px"])
    maxstep = float(a["max_step_px"])
    step_cap = float(a.get("step_cap", 0.18))
    tau_floor = float(a.get("follow_tau_floor", 0.04))
    k = 1.0
    frames = 0
    trail = [ad]
    while ad > dead and frames < 60:
        frames += 1
        ratio, near_band = ratio_new(ad, a) if not dry else ratio_old(ad, a)
        budget = min(maxstep, ad * ratio)
        abs_cap = float(a.get("abs_move_cap_near", 1.0)) if near_band else abs_cap_default
        gate = abs_cap * ad                     # 绝对闸：本周期最多走 观测偏差 × abs_cap
        sent = 0.0
        d = ad
        carry = 0.0
        for _ in range(200):                    # 一个检测周期内最多 200 个 1ms tick
            if d <= dead:
                break
            rate = min(1.0, budget / max(4.0, d))
            if 0.0 < step_cap < 1.0:
                rate = min(rate, step_cap)
            if rate < tau_floor:
                rate = min(tau_floor, 1.0)
            mv = min(d * rate, maxstep)
            eff = max(0.0, mv - max(0.3, mv * 0.04))      # 扣掉噪声部分
            if budget < eff:
                sc = budget / eff if eff > 1e-9 else 0.0
                mv *= sc
                eff = budget
            mx = mv / max(0.05, k)                        # 像素 → 鼠标计数
            ix = 0 if abs(mx + carry) < 0.5 else int(round(mx + carry))
            if ix == 0:
                carry += mx
                carry = max(-0.75, min(0.75, carry))
                budget = budget                            # 没走，预算不动
                if abs(carry) >= 0.5:
                    continue
                continue
            carry += mx - ix
            carry = max(-0.75, min(0.75, carry))
            step_px = min(ix * k, d)                       # 单调逼近：不越过目标
            d -= step_px
            sent += step_px
            budget = max(0.0, budget - eff * (max(1, abs(ix)) / max(0.5, abs(mx))))
            if budget <= 0.0 or sent >= gate:
                break
            if dry:
                # 旧版「反写钳位」：一步之后航位推算就认为已到目标（剩余偏差变 0），
                # 于是在这个检测周期里再也不发第二次 → 每周期只走一步。
                break
        ad = d
        trail.append(ad)
    return frames, trail


def main():
    a = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))["aim"]
    abs_cap_default = float(a.get("abs_move_cap", 0.85))
    fps = 30.0
    print("=" * 78)
    print("一、当前参数")
    print("=" * 78)
    for k in ("near_ratio", "far_px", "near_boost_px", "near_boost_ratio",
              "abs_move_cap", "abs_move_cap_near", "budget_ratio", "step_cap",
              "dead_zone_px", "max_step_px"):
        print("  %-20s %s" % (k, a.get(k)))

    print()
    print("=" * 78)
    print("二、偏差 → 本帧预算比例（旧 vs 新）")
    print("=" * 78)
    print("  %-8s | %-10s | %-10s | %s" % ("偏差px", "旧 ratio", "新 ratio", "说明"))
    for ad in (2, 5, 10, 20, 40, 60, 90, 120, 140, 180, 250, 320, 400, 460, 600):
        ro, _ = ratio_old(ad, a)
        rn, nb = ratio_new(ad, a)
        note = "贴脸带" if nb else ("过渡带" if ad < float(a["far_px"]) else "满预算")
        print("  %-8d | %-10.3f | %-10.3f | %s%s" %
              (ad, ro, rn, note, "   ← 变快 %.0f%%" % ((rn / ro - 1) * 100) if rn > ro + 1e-6 else ""))

    print()
    print("=" * 78)
    print("三、贴上去要几个检测帧（目标静止，30fps → 1 帧 = 33ms）")
    print("=" * 78)
    print("  %-8s | %-22s | %-22s | %s" %
          ("初始偏差", "旧：帧数 / 耗时 / 末值", "新：帧数 / 耗时 / 末值", "提速"))
    for ad0 in (15, 30, 60, 100, 140, 250, 400):
        fo, to = simulate(ad0, a, True, abs_cap_default)
        fn, tn = simulate(ad0, a, False, abs_cap_default)
        print("  %-8d | %-22s | %-22s | %s" %
              (ad0,
               "%d 帧 / %3.0fms / %.1fpx" % (fo, fo * 1000 / fps, to[-1]),
               "%d 帧 / %3.0fms / %.1fpx" % (fn, fn * 1000 / fps, tn[-1]),
               "%.0f%%" % ((fo / fn - 1) * 100) if fn and fo else "-"))

    print()
    print("=" * 78)
    print("四、收敛轨迹（偏差每帧末值，单位 px）")
    print("=" * 78)
    for ad0 in (60, 140):
        _, to = simulate(ad0, a, True, abs_cap_default)
        _, tn = simulate(ad0, a, False, abs_cap_default)
        print("  从 %dpx 起 旧：" % ad0)
        print("    " + " ".join("%.1f" % v for v in to[:10]))
        print("  从 %dpx 起 新：" % ad0)
        print("    " + " ".join("%.1f" % v for v in tn[:10]))


if __name__ == "__main__":
    main()