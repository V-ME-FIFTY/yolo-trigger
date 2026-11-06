# -*- coding: utf-8 -*-
"""把 pick_locked 换成"预测匹配 + 换人闸"版（2026-10-30，用户「还会来回锁定两个框」）

参考 SORT/ByteTrack 一类多目标跟踪的通行做法（IoU + 匀速预测 + 迟滞 + 冷却），
但保持单目标、无外部依赖，只加四件事：
  ① 用**预测中心**（上一帧速度外推）做匹配 → 目标横移快了也不会被误判成"换人"
  ② 换人闸：必须 (a) 明确更近（hysteresis 0.7）、(b) 连续 switch_confirm_frames 帧、
     (c) 距上次换人 switch_cooldown_ms 以上
  ③ 闸不过 → 保持原目标（而不是"就近重选"）
  ④ 交战期(engage)仍走上一轮的"忽略其他敌人"规则

用法：python _patch_转火.py     （直接改写 main.py，幂等：已改过会提示）
"""
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(HERE, "main.py")
s = io.open(P, encoding="utf-8").read()

s_head = s[:s.find(START)]
if "switch_hysteresis" in s and "POSE_PREF" in s:
    raise SystemExit("main.py 里已有 POSE_PREF 与 switch_hysteresis —— 这个脚本已经跑过，别再跑"
                     "（它会按 def pick_locked…def pick_targets 切片，重跑会再删掉中间的函数区）")

START = "def pick_locked(boxes, aim_x, aim_y, min_h, st, now, hold_s, match_px, sticky_s, radius_px=0.0):"
END = "def pick_targets(boxes_xyxy, aim_x, aim_y, cfg):"
i0, i1 = s.find(START), s.find(END)
assert i0 > 0 and i1 > i0, "找不到 pick_locked 边界"

NEW = '''def pick_locked(boxes, aim_x, aim_y, min_h, st, now, hold_s, match_px, sticky_s, radius_px=0.0):
    """目标锁定：咬住一个目标，只有"明确该换"才换 —— 治"来回锁定两个框"。

    ★2026-10-30 第二轮（用户「还会来回锁定两个框」，参考 SORT/ByteTrack 的通行做法）★
    四个机制一起用，缺一个都会在两个框之间来回跳：
      ① **预测匹配**：用上一帧的框心速度外推一个"预测中心"，按预测中心算距离/IoU。
         目标横移快了也不会因为"中心偏了 match_px"被当成换人（旧码就是这么丢锁的）。
      ② **换人闸**（三道同时满足才许换）：
         a) 新目标必须**明确更近**：离准星距离 ≤ 旧目标 × switch_hysteresis(0.7)；
         b) 这个"更近"要**连续 switch_confirm_frames 帧**成立（单帧误检不算）；
         c) 距上次换人 ≥ switch_cooldown_ms（换了就咬住，别马上又换回去）。
      ③ 闸不过就**保持原目标**：以前是"就近重选"，等于每帧都在投票，两个人挨着就来回跳。
      ④ 交战期（按住开镜/瞄准键）额外：IoU 校验 + 放宽圈里唯一候选 + 保留旧框。
    """
    cands = []
    _r = float(radius_px or 0.0)
    for b in boxes:
        x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        if (y2 - y1) < min_h:
            continue
        if _r > 0.0:
            _bx, _by = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            if ((_bx - aim_x) ** 2 + (_by - aim_y) ** 2) ** 0.5 > _r:
                continue        # 离准星太远不许锁：防锁到自己的手 / 影子 / 边角杂物
        cands.append((x1, y1, x2, y2))

    def cen(b):
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def d_aim(b):
        c = cen(b)
        return ((c[0] - aim_x) ** 2 + (c[1] - aim_y) ** 2) ** 0.5

    def iou(a, b):
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return (inter / ua) if ua > 0 else 0.0

    lb = st.get("box")
    lt = st.get("t", 0.0)
    _eng = bool(st.get("engage", False))
    _relax = max(1.0, float(st.get("engage_relax", 2.0)))
    _extra = max(0.0, float(st.get("engage_extra_gap_s", 0.40)))
    _swmax = float(st.get("engage_switch_max_px", 220.0))
    _iou_min = float(st.get("engage_min_iou", 0.15))
    _hyst = float(st.get("switch_hysteresis", 0.7))
    _cd = max(0.0, float(st.get("switch_cooldown_ms", 400)) / 1000.0)
    _scf = max(1, int(st.get("switch_confirm_frames", 2)))
    _pg = float(st.get("pred_gain", 0.5))
    _pv = st.get("v") or [0.0, 0.0]

    # ── 新目标确认帧：没锁定时，候选要连续出现 confirm_frames 帧才允许上锁 ──
    _cf = max(1, int(st.get("confirm_frames", 1)))
    _have_lock = lb is not None and (now - lt) <= hold_s
    if _cf > 1 and not _have_lock and cands:
        _seen = st.setdefault("seen", {})
        _ok = []
        for c in cands:
            _k = (int(cen(c)[0] // 60), int(cen(c)[1] // 60))
            _cnt = int(_seen.get(_k, (0, now))[0]) + 1
            _seen[_k] = (_cnt, now)
            if _cnt >= _cf:
                _ok.append(c)
        for _k in [_k for _k, (_c, _t) in _seen.items() if now - _t > 2.0]:
            _seen.pop(_k, None)
        cands = _ok

    # ── 有锁定：先按"预测中心"匹配 ──
    if lb is not None and (now - lt) <= hold_s + (_extra if _eng else 0.0):
        lcx, lcy = cen(lb)
        pcx, pcy = lcx + _pv[0] * _pg, lcy + _pv[1] * _pg
        best, bd, biou = None, 1e18, -1.0
        for c in cands:
            ccx, ccy = cen(c)
            d = ((ccx - pcx) ** 2 + (ccy - pcy) ** 2) ** 0.5
            if d > match_px:
                continue
            cur_iou = iou(lb, c)
            if _eng and cur_iou < _iou_min:
                continue          # 交战期还要求框叠得上（旁边那个人中心也可能很近）
            if cur_iou > biou + 1e-9 or (abs(cur_iou - biou) <= 1e-9 and d < bd):
                best, bd, biou = c, d, cur_iou
        if best is None and _eng:
            # 放宽圈里恰好一个候选 → 只能是这个人横移快了
            _in = [c for c in cands
                   if ((cen(c)[0] - pcx) ** 2 + (cen(c)[1] - pcy) ** 2) ** 0.5 <= match_px * _relax]
            if len(_in) == 1:
                best, bd = _in[0], 0.0
        if best is not None:
            _dt = max(1e-3, now - lt)
            bcx, bcy = cen(best)
            nvx = (bcx - lcx) / _dt
            nvy = (bcy - lcy) / _dt
            st["v"] = [_pv[0] * 0.6 + nvx * 0.4, _pv[1] * 0.6 + nvy * 0.4]
            st["box"], st["t"] = best, now
            st["sw_pend"] = {}
            return best

        # ── 没匹配上：换人闸 ──
        cand_target = None
        if cands:
            cand_target = min(cands, key=d_aim)
            d_old = d_aim(lb)
            d_new = d_aim(cand_target)
            cd_ok = (now - float(st.get("switch_t", 0.0))) >= _cd
            pend = st.get("sw_pend") or {}
            key = (int(cen(cand_target)[0] // 40), int(cen(cand_target)[1] // 40))
            cnt = int(pend.get(key, 0)) + 1
            st["sw_pend"] = {key: cnt}
            closer = d_new <= d_old * _hyst
            near_ok = (not _eng) or (((cen(cand_target)[0] - lcx) ** 2 +
                                      (cen(cand_target)[1] - lcy) ** 2) ** 0.5 <= _swmax)
            if cd_ok and cnt >= _scf and closer and near_ok:
                st["box"], st["t"], st["start"] = cand_target, now, now
                st["switch_t"] = now
                st["v"] = [0.0, 0.0]
                st["sw_pend"] = {}
                return cand_target
        if _eng or (now - lt) <= hold_s:
            return lb            # 闸不过 / 交战期：保持原目标，不许"就近重选"
        # 否则旧目标确实过期太久 → 落下去重新选

    if cands:
        best = min(cands, key=d_aim)
        st["box"], st["t"], st["start"] = best, now, now
        st["v"] = [0.0, 0.0]
        st["sw_pend"] = {}
        return best
    st["box"] = None
    return None


'''

s = s[:i0] + NEW + s[i1:]
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("pick_locked 已替换（%d → %d 字节）" % (i1 - i0, len(NEW)))