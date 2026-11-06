# -*- coding: utf-8 -*-
"""抢救：把 _patch_转火.py 误删的函数区（POSE_PREF → filter_ignore_zones）从备份拼回来，
再补回 20:12/23:00 三轮改动（ignore_bottom_x_ratio / 瞄准点 EMA 自适应 / person_check）。

备份来源：_待删-20261030/main.py.bak-20261030-200114-视频取证前（20:12 版，md5 0b38a796）
用法：python _rescue_函数区.py
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "main.py")
BAK = os.path.join(HERE, "_待删-20261030", "main.py.bak-20261030-200114-视频取证前")

cur = io.open(P, encoding="utf-8").read()
bak = io.open(BAK, encoding="utf-8").read()

A = "POSE_PREF = {\"chest\""
B = "def pick_targets(boxes_xyxy"
i0 = bak.find(A)
i1 = bak.find(B)
assert i0 > 0 and i1 > i0, "备份里找不到函数区边界"
region = bak[i0:i1]

j = cur.find(B)
assert j > 0, "当前文件找不到 pick_targets"
assert "POSE_PREF" not in cur, "当前文件已有 POSE_PREF，可能已经拼过了"
cur = cur[:j] + region + cur[j:]

# ── 补回 A：ignore_bottom_x_ratio（20:12 视频取证那轮）──
old = "    x_lo, x_hi = cx_mid - cr * frame_w, cx_mid + cr * frame_w\n"
new = ("    # 底部屏蔽的横向范围：ignore_bottom_x_ratio=1.0 表示整条底部全宽都挡\n"
       "    # （第一人称的枪身/护木在画面右下，只挡中心±30% 挡不住）\n"
       "    _xr = iz.get(\"ignore_bottom_x_ratio\", None)\n"
       "    if _xr is None:\n"
       "        half_w = cr * frame_w                 # 老配置：沿用 ±center_ratio，行为不变\n"
       "    else:\n"
       "        half_w = 0.5 * max(0.0, float(_xr)) * frame_w\n"
       "    x_lo, x_hi = cx_mid - half_w, cx_mid + half_w\n")
assert old in cur, "filter_ignore_zones 里没找到 x_lo 那一行"
cur = cur.replace(old, new, 1)

# ── 补回 B：瞄准点 EMA alpha 随框高自适应（2.5）──
anchor = "    cf = kps_cf[bi] if kps_cf is not None and bi < len(kps_cf) else None\n"
add = ("    # 瞄准点平滑强度随框高（≈距离）自适应：贴脸少平滑跟得紧、远处仍抗抖\n"
       "    _psm = float(aim_cfg.get(\"pose_smooth\", 0.5))\n"
       "    _big = float(aim_cfg.get(\"pose_smooth_big_px\", 0.0) or 0.0)\n"
       "    _psm_near = float(aim_cfg.get(\"pose_smooth_near\", 0.9))\n"
       "    if _big > 1.0 and _psm_near > _psm:\n"
       "        _bhk = max(1.0, float(y2) - float(y1))\n"
       "        _wsm = max(0.0, min(1.0, (_bhk - _big) / _big))\n"
       "        _psm = _psm + (_psm_near - _psm) * _wsm\n")
assert anchor in cur, "pick_pose_point 里没找到 cf 赋值行"
cur = cur.replace(anchor, anchor + add, 1)
cur = cur.replace("    psm = float(aim_cfg.get(\"pose_smooth\", 0.5))\n", "    psm = _psm\n")
cur = cur.replace("        psm = float(aim_cfg.get(\"pose_smooth\", 0.5))\n", "        psm = _psm\n")

# ── 补回 C：filter_person_check（23:00 那一轮，人形校验）──
PC = '''
def filter_person_check(boxes, kps_xy, kps_cf, cfg, frame_w, frame_h):
    """人形校验 + 自手/景物专项过滤（2026-10-30，用户"经常锁定景物和手臂"）

    出货默认关闭（person_check.enabled=false）：真实帧里它误杀过 50% 的真检出，
    但代码留着，改成宽松阈值后随时能开（wh≤1.8 / 贴底 0.98 / min_kp 3）。
    五道闸：宽高比 / 可信关键点数量 / 头肩髋竖直顺序 / 关键点在框内比例 / 贴底自手。
    返回 (boxes, kps, kcf, 剔除数, 原因统计)
    """
    pc = cfg.get("person_check") or {}
    if not pc.get("enabled", True) or len(boxes) == 0:
        return boxes, kps_xy, kps_cf, 0, {}
    min_wh = float(pc.get("min_wh_ratio", 0.14))
    max_wh = float(pc.get("max_wh_ratio", 1.80))
    min_kp = int(pc.get("min_kp", 3))
    kp_conf = float(pc.get("kp_conf", 0.40))
    inside_ratio = float(pc.get("inside_ratio", 0.55))
    b_ratio = float(pc.get("self_bottom_ratio", 0.98))
    b_min_w = float(pc.get("self_min_w_ratio", 0.18))
    need_order = bool(pc.get("check_order", True))

    keep_b, keep_k, keep_c = [], [], []
    why = {}
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        wh = bw / bh
        pts = kps_xy[i] if (kps_xy is not None and i < len(kps_xy)) else None
        cfs = kps_cf[i] if (kps_cf is not None and i < len(kps_cf)) else None

        def drop(reason):
            why[reason] = why.get(reason, 0) + 1

        if wh < min_wh or wh > max_wh:
            drop("宽高比 %.2f" % wh)
            continue
        if (y2 >= frame_h * b_ratio) and bw >= frame_w * b_min_w:
            drop("贴底手臂")
            continue
        if pts is None or cfs is None:
            drop("无姿态")
            continue
        good = [i2 for i2 in range(min(len(pts), len(cfs))) if float(cfs[i2]) >= kp_conf]
        if len(good) < min_kp:
            drop("关键点<%d" % min_kp)
            continue
        ex, ey = bw * 0.08, bh * 0.08
        inside = sum(1 for i2 in good
                     if x1 - ex <= float(pts[i2][0]) <= x2 + ex and
                     y1 - ey <= float(pts[i2][1]) <= y2 + ey)
        if inside / float(len(good)) < inside_ratio:
            drop("关键点出框")
            continue
        if need_order:
            def yy(idx):
                return float(pts[idx][1]) if (idx < len(pts) and float(cfs[idx]) >= kp_conf) else None
            ys = yy(0)
            if ys is None:
                _c = [v for v in (yy(1), yy(2)) if v is not None]
                ys = min(_c) if _c else None
            sh = [v for v in (yy(5), yy(6)) if v is not None]
            hp = [v for v in (yy(11), yy(12)) if v is not None]
            sh_y = sum(sh) / len(sh) if sh else None
            hp_y = sum(hp) / len(hp) if hp else None
            if ys is not None and sh_y is not None and not (ys < sh_y + 0.02 * bh):
                drop("头不低于肩")
                continue
            if sh_y is not None and hp_y is not None and not (sh_y < hp_y + 0.02 * bh):
                drop("肩不低于髋")
                continue
        keep_b.append(box)
        if kps_xy is not None:
            keep_k.append(pts)
        if kps_cf is not None:
            keep_c.append(cfs)
    n = len(boxes) - len(keep_b)
    return (keep_b,
            keep_k if kps_xy is not None else kps_xy,
            keep_c if kps_cf is not None else kps_cf,
            n, why)


'''
anchor2 = "def filter_ignore_zones(boxes, kps_xy, kps_cf, cfg, frame_h, frame_w):"
assert anchor2 in cur and "def filter_person_check" not in cur
cur = cur.replace(anchor2, PC.lstrip("\n") + anchor2, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(cur)
print("抢救完成：函数区已拼回 + 三项改动已补（%d 字节）" % len(cur))