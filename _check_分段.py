# -*- coding: utf-8 -*-
"""像素分段（远冲近稳 / 限幅分段 / 近距直取）机制核查（2026-10-30 更新）

问题：「换成开镜锁，根据不同 px 分段的机制还在吗？」
结论在这里量化：分段只跟"偏差 px"和"当前是否在 fov 稳定期/开镜宽限"有关，
跟"要不要按右键才锁人"（require_aim_key）完全无关。

复刻 main.py 现行顺序（2026-10-30「近距直取」之后）：
  _ratio = budget_ratio（首答期间 budget_ratio_initial）
  → 未标定 2 样本内 ≤0.5
  → **近距直取**：偏差 < near_boost_px 时按 (1-t²)² 反向抬到 near_boost_ratio
  → fov 稳定期 ≤fov_hold_ratio / 开镜宽限 ≤ads_hold_ratio（二者互斥，fov 优先）
  → 过冲抑制期 ≤near_damp_ratio（贴脸带）/ damp_ratio（带外）
  → [near_boost_px, far_px] 线性过渡：near_ratio → 满预算
  → _maxs = max_step_px（fov 期 ≤fov_maxstep_px，开镜宽限 ≤ads_maxstep_px）
  → 本帧预算 _lim = min(_maxs, 偏差 × _ratio)
  → 跟随线程再用 abs_move_cap_near（贴脸带）/ abs_move_cap 做绝对闸

每一帧能贴多快（收敛帧数）见 _check_近距直取.py。

用法：python _check_分段.py
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def frame_budget(ad, a, mode="normal", first_burst=False, calib=False):
    ratio = float(a["budget_ratio_initial"]) if first_burst else float(a["budget_ratio"])
    if first_burst and calib:
        ratio = min(ratio, 0.5)
    near_r = float(a["near_ratio"])
    far = float(a["far_px"])
    boost_px = float(a.get("near_boost_px", 0.0) or 0.0)
    boost_r = float(a.get("near_boost_ratio", 1.0))
    nb = max(1.0, boost_px)
    near_band = boost_px > 0.0 and ad < nb
    if near_band:                                   # 近距直取（先抬，后面三道硬保护仍可压）
        tb = max(0.0, min(1.0, ad / nb))
        ratio = max(ratio, near_r + (boost_r - near_r) * (1.0 - tb * tb) ** 2)
    if mode == "fov":
        ratio = min(ratio, float(a["fov_hold_ratio"]))
    elif mode == "ads":
        _ar = float(a["ads_hold_ratio"])
        if near_band:                      # 贴脸带放宽（开镜锁每次开镜不再先磨 260ms）
            _ar = max(_ar, float(a.get("near_ads_hold_ratio", 0.85)))
        ratio = min(ratio, _ar)
    elif mode == "damp":
        ratio = min(ratio, float(a.get("near_damp_ratio", 0.85)) if near_band
                    else float(a.get("damp_ratio", 0.45)))
    if far > nb + 1.0 and nb <= ad < far:           # 过渡带（带内不再降）
        t = (ad - nb) / (far - nb)
        ratio = min(ratio, near_r + (ratio - near_r) * t)
    maxs = float(a["max_step_px"])
    if mode == "fov":
        maxs = min(maxs, float(a["fov_maxstep_px"]))
    elif mode == "ads":
        _am = float(a["ads_maxstep_px"])
        if near_band:
            _am = max(_am, float(a.get("near_ads_maxstep_px", 160)))
        maxs = min(maxs, _am)
    lim = min(maxs, ad * ratio)
    return ratio, maxs, lim


def main():
    cfg = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    a = cfg["aim"]

    print("=" * 78)
    print("一、当前生效的分段参数（config.json）")
    print("=" * 78)
    for k in ("max_dist_px", "budget_ratio", "budget_ratio_initial", "far_px",
              "near_ratio", "near_boost_px", "near_boost_ratio",
              "abs_move_cap", "abs_move_cap_near", "damp_ratio", "near_damp_ratio",
              "max_step_px", "step_cap", "fov_change_ratio", "fov_change_ref_px",
              "fov_settle_ms", "fov_hold_ratio",
              "fov_maxstep_px", "ads_grace_ms", "ads_hold_ratio", "ads_maxstep_px",
              "require_aim_key", "ads_sense_by_key", "first_burst_ms"):
        print("  %-22s %s" % (k, a.get(k)))

    print()
    print("=" * 78)
    print("二、按 px 分段的实际预算（普通帧 vs 开镜宽限帧 vs fov 缩放帧）")
    print("=" * 78)
    print("  %-8s | %-24s | %-24s | %-20s" %
          ("偏差px", "普通帧 ratio/限幅/预算px", "开镜宽限 ratio/限幅/预算px", "fov期 ratio/限幅/预算px"))
    for ad in (10, 20, 60, 120, 140, 250, 400, 460, 600, 800):
        row = []
        for mode in ("normal", "ads", "fov"):
            r, m, lim = frame_budget(ad, a, mode)
            row.append("%.2f / %.0f / %.0f" % (r, m, lim))
        print("  %-8d | %-24s | %-24s | %-20s" % (ad, row[0], row[1], row[2]))

    print()
    print("=" * 78)
    print("三、门控 vs 分段：门控只决定「执行不执行」，不改变分段数值")
    print("=" * 78)
    for ad in (120, 300, 600):
        r, m, lim = frame_budget(ad, a, "normal")
        print("  偏差 %-4d 分段预算 %.0fpx ｜ 常驻锁人(require=false)：松右键也执行 → %.0fpx ｜ "
              "开镜锁(require=true)：松右键不执行 → 0px，按住右键 → %.0fpx"
              % (ad, lim, lim, lim))

    print()
    print("=" * 78)
    print("四、分段在哪些情况下被收紧 / 放松（谁在压预算，谁在抬预算）")
    print("=" * 78)
    for ad, mode, note in ((600, "normal", "远目标：吃满 1.0×偏差，撞 max_step"),
                           (250, "normal", "中距：过渡带线性降一点"),
                           (140, "normal", "带口：到这里为止仍按 near_ratio"),
                           (60, "normal", "贴脸：近距直取接管，预算≈100% 偏差（旧版只有 55%）"),
                           (20, "normal", "更贴脸：一帧走完，剩余交给死区"),
                           (600, "ads", "开镜宽限 260ms 内：ratio≤0.45 且限幅≤80px"),
                           (600, "fov", "开镜缩放期：ratio≤0.30 且限幅≤50px"),
                           (60, "damp", "贴脸带里刚翻过符号：只降到 near_damp_ratio(0.85)，不冻住"),
                           (600, "damp", "带外刚冲过目标 250ms 内：ratio≤0.45")):
        r, m, lim = frame_budget(ad, a, mode)
        print("  %-5s 偏差 %-4d ratio %.2f 限幅 %.0fpx → 本帧预算 %.0fpx   %s" %
              (mode, ad, r, m, lim, note))


if __name__ == "__main__":
    main()