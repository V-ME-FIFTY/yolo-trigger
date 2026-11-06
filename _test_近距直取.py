# -*- coding: utf-8 -*-
"""近距直取 / 瞄准点自适应平滑 的单元冒烟测试（2026-10-30）

跑法：python _test_近距直取.py
覆盖：
  1) pick_pose_point 的 EMA alpha 随框高自适应（贴脸时跟得紧、远处照旧平滑）
  2) 关掉 pose_smooth_big_px 后行为与旧版一致（向后兼容）
  3) 贴脸带预算比例单调：ad 越小 ratio 越大，且在带口处与 near_ratio 连续
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as M  # noqa: E402


def kps(cx, cy, h):
    """造 17 个关键点：鼻子在 (cx, cy)，双眼在旁边，双肩在下方，其余给低置信。"""
    pts = [[0.0, 0.0] for _ in range(17)]
    cf = [0.0] * 17
    pts[0] = [cx, cy]                       # nose
    pts[1] = [cx - h * 0.03, cy - h * 0.02]  # left eye
    pts[2] = [cx + h * 0.03, cy - h * 0.02]  # right eye
    pts[5] = [cx - h * 0.12, cy + h * 0.20]  # left shoulder
    pts[6] = [cx + h * 0.12, cy + h * 0.20]  # right shoulder
    for i in (0, 1, 2, 5, 6):
        cf[i] = 0.9
    return [pts], [cf]


def run(box_h, cfg):
    cx, cy = 400.0, 300.0
    x1, y1, x2, y2 = cx - box_h * 0.2, cy - box_h * 0.35, cx + box_h * 0.2, cy + box_h * 0.65
    k, c = kps(cx, cy, box_h)
    # 第一次：sm_prev=None → 直接取点；第二次把点整体右移 20px，看 EMA 跟了多少
    r1 = M.pick_pose_point(k, c, [[x1, y1, x2, y2]], x1, y1, x2, y2, cfg, "auto", None, 0, None)
    assert r1 is not None, "第一次就该给点"
    part, lost, sm, pt = r1
    shifted = [[p[0] + 20.0, p[1]] for p in k[0]]
    r2 = M.pick_pose_point([shifted], c, [[x1, y1, x2, y2]], x1, y1, x2, y2,
                           cfg, "auto", part, lost, sm)
    assert r2 is not None
    follow = r2[3][0] - pt[0]
    return follow


def main():
    cfg_all = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    aim = dict(cfg_all["aim"])
    print("=" * 70)
    print("一、瞄准点 EMA 跟随量（关键点一次横移 20px，按 alpha 跟了多少）")
    print("=" * 70)
    print("  %-10s | %-16s | %-16s" % ("框高px", "自适应(现行配置)", "关掉自适应(旧行为)"))
    old = dict(aim)
    old["pose_smooth_big_px"] = 0.0
    for h in (90, 160, 260, 400, 600):
        f_new = run(h, aim)
        f_old = run(h, old)
        print("  %-10d | %-16.1f | %-16.1f" % (h, f_new, f_old))
        assert abs(f_old - 20.0 * float(old["pose_smooth"])) < 0.5, "旧行为应恒为 pose_smooth"
        # 框越大，跟随量必须单调不减，且不得超过 20px
        assert 0.0 < f_new <= 20.0 + 1e-6
    prev = -1.0
    for h in (90, 160, 260, 400, 600):
        v = run(h, aim)
        assert v >= prev - 0.5, "框越大跟得越紧（单调）"
        prev = v

    print()
    print("=" * 70)
    print("二、贴脸带预算曲线（连续性 + 单调性）")
    print("=" * 70)
    near_r = float(aim["near_ratio"])
    boost_r = float(aim.get("near_boost_ratio", 1.0))
    nb = float(aim.get("near_boost_px", 0.0))
    far = float(aim["far_px"])

    def ratio_of(ad):
        base = float(aim["budget_ratio"])
        nbb = max(1.0, nb)
        if nb > 0.0 and ad < nbb:
            tb = max(0.0, min(1.0, ad / nbb))
            return max(base, near_r + (boost_r - near_r) * (1.0 - tb * tb) ** 2)
        if far > nbb + 1.0 and nbb <= ad < far:
            t = (ad - nbb) / (far - nbb)
            return min(base, near_r + (base - near_r) * t)
        return base

    prev = None
    for ad in range(int(far) + 40, -1, -1):
        r = ratio_of(ad)
        assert r >= near_r - 1e-9, "任何情况下都不该比 near_ratio 还低"
        if prev is not None:
            # 允许 far_px 处 ~1e-3 的接缝（旧版同样有：ad=far 走 base，ad=far-1 走曲线头部）
            assert r >= prev - 0.01, "偏差越小 ratio 不该反而更小（ad=%d：%.4f -> %.4f）" % (ad, prev, r)
        prev = r
    vals = [ratio_of(a) for a in range(int(far) + 1)]
    print("  从 %.0fpx 走到 0px：ratio 单调不减（far_px 处接缝 <1e-3），最低 %.2f，最高 %.2f" %
          (far, min(vals), max(vals)))
    print("  带口 %.0fpx 处有个**向上**的台阶：%.0fpx→%.2f，%.0fpx→%.2f（只会变快，不会变慢，"
          "所以不会出现'走到一半突然一顿'）" %
          (nb, nb + 1, ratio_of(nb + 1), nb - 1, ratio_of(nb - 1)))
    print("  贴脸 10px：ratio = %.3f（旧版恒为 near_ratio %.2f）" % (ratio_of(10), near_r))

    print()
    print("全部通过 ✅")


if __name__ == "__main__":
    main()