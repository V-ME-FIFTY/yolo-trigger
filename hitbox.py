"""命中盒模型（P1，2026-10-31）

现在瞄哪里 = `aim_height_ratio`（框高 × 0.2）或 `pick_pose_point` 的头/胸/鼻三选一。
问题：
  · 只按"框高比例"取点，蹲下/趴下/侧身时点会跑到身体外（框是斜的、腿也占一半）；
  · 头/胸/鼻三选一没有**几何**依据，鼻子丢帧就掉到"框中心"；
  · 没有命中优先级：远处该打躯干（面积大、容错高），贴脸才该打头。

本模块用 17 关键点重建三个命中区域，再按"距离 + 可见性 + 优先级"打分选点：
    head   : 鼻/眼/耳 为上沿，肩线以下不要；退化时取框顶 0~0.12H
    chest  : 肩中点到髋中点之间（腰以上）
    belly  : 髋中点附近（0.45~0.60H）
每个候选点带 score = w_part × 可见性 × 离准星距离项，切换有迟滞（不掉帧乱跳）。

用法：
    hb = Hitbox(cfg.get("hitbox") or {})
    pt = hb.pick(box, kps_xy, kps_cf, aim_x, aim_y, state)   # state 传 dict 做迟滞
    # -> (x, y, part, score) 或 None（交给旧的 pick_pose_point 兜底）
"""
from __future__ import annotations

# COCO-17 关键点下标
NOSE, LEYE, REYE, LEAR, REAR = 0, 1, 2, 3, 4
LSHO, RSHO, LELB, RELB, LWRI, RWRI = 5, 6, 7, 8, 9, 10
LHIP, RHIP, LKNE, RKNE, LANK, RANK = 11, 12, 13, 14, 15, 16


class Hitbox:
    def __init__(self, cfg=None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.kp_conf = float(c.get("kp_conf", 0.45))
        self.w_head = float(c.get("w_head_far", 0.60))      # 远处打头收益低（面积小）
        self.w_head_near = float(c.get("w_head_near", 1.25))  # 贴脸打头收益高
        self.w_chest = float(c.get("w_chest", 0.95))
        self.w_belly = float(c.get("w_belly", 0.60))
        self.near_h = float(c.get("near_box_h", 260.0))     # 框高超过它就偏打头
        self.near_lo = float(c.get("near_box_lo", 200.0))   # 低于它一律打躯干
        self.switch_margin = float(c.get("switch_margin", 1.25))  # 换部位要"明确更好"
        self.hold_frames = int(c.get("hold_frames", 6))     # 部位丢失后容忍帧数
        # 退化比例（无关键点时）
        self.r_head = float(c.get("r_head", 0.08))
        self.r_chest = float(c.get("r_chest", 0.28))
        self.chest_t = float(c.get("chest_t", 0.25))   # 肩→髋之间的取点位置（越小越靠上）
        self.r_belly = float(c.get("r_belly", 0.48))

    # ── 关键点工具 ──
    @staticmethod
    def _kp(kps, cf, i, thr):
        if kps is None or cf is None:
            return None
        try:
            if i >= len(kps) or i >= len(cf):
                return None
            if float(cf[i]) < thr:
                return None
            x, y = float(kps[i][0]), float(kps[i][1])
            if x == 0.0 and y == 0.0:
                return None
            return (x, y)
        except Exception:
            return None

    def _mid(self, a, b):
        if a and b:
            return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        return a or b

    def regions(self, box, kps, cf):
        """返回 {'head':(x,y),'chest':(x,y),'belly':(x,y)}（可能缺项）"""
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        h = max(1.0, y2 - y1)
        cx = (x1 + x2) / 2.0
        out = {}
        nose = self._kp(kps, cf, NOSE, self.kp_conf)
        leye = self._kp(kps, cf, LEYE, self.kp_conf)
        reye = self._kp(kps, cf, REYE, self.kp_conf)
        lear = self._kp(kps, cf, LEAR, self.kp_conf)
        rear = self._kp(kps, cf, REAR, self.kp_conf)
        lsho = self._kp(kps, cf, LSHO, self.kp_conf)
        rsho = self._kp(kps, cf, RSHO, self.kp_conf)
        lhip = self._kp(kps, cf, LHIP, self.kp_conf)
        rhip = self._kp(kps, cf, RHIP, self.kp_conf)
        sho_mid = self._mid(lsho, rsho)
        hip_mid = self._mid(lhip, rhip)
        # 头：眼/鼻优先，其次耳中点；且不许低于肩线
        eyes = self._mid(leye, reye)
        ears = self._mid(lear, rear)
        head = nose or eyes or ears
        if head is not None:
            if sho_mid is not None and head[1] > sho_mid[1]:
                head = None                    # 点跑到肩下 = 关键点错位，丢弃
            else:
                out["head"] = head
        if "head" not in out:
            out["head"] = (cx, y1 + h * self.r_head)     # 退化：框顶 10%
        # 胸：肩髋之间（腰以上），没有髋就用肩下 0.25H
        if sho_mid is not None and hip_mid is not None:
            yc = sho_mid[1] + (hip_mid[1] - sho_mid[1]) * self.chest_t
            out["chest"] = ((sho_mid[0] + hip_mid[0]) / 2.0, yc)
        elif sho_mid is not None:
            out["chest"] = (sho_mid[0], sho_mid[1] + h * 0.18)
        else:
            out["chest"] = (cx, y1 + h * self.r_chest)
        # 腹：髋中点；没有髋就用 0.48H
        out["belly"] = hip_mid if hip_mid is not None else (cx, y1 + h * self.r_belly)
        # 全部夹回框内
        for k in list(out):
            px, py = out[k]
            out[k] = (max(x1, min(x2, px)), max(y1, min(y2, py)))
        return out

    # ── 选点（带优先级与迟滞）──
    def pick(self, box, kps, cf, aim_x, aim_y, state=None):
        if not self.enabled:
            return None
        st = state if state is not None else {}
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        h = max(1.0, y2 - y1)
        reg = self.regions(box, kps, cf)
        # 距离项：越近越偏头（贴脸打头收益高、容错也够）
        # 贴脸程度：框高 < near_lo 记 0（打躯干），> near_h 记 1（打头）
        _lo = max(1.0, self.near_lo)
        _hi = max(_lo + 1.0, self.near_h)
        near = min(1.0, max(0.0, (h - _lo) / (_hi - _lo)))
        # 头：远处降权、贴脸升权；腹：贴脸降权（那时打头/胸更值）
        weights = {"head": self.w_head + (self.w_head_near - self.w_head) * near,
                   "chest": self.w_chest,
                   "belly": self.w_belly * (1.0 - 0.5 * near)}
        best, best_s = None, -1e9
        for part, (px, py) in reg.items():
            d = ((px - aim_x) ** 2 + (py - aim_y) ** 2) ** 0.5
            # 距离项：500px 外基本不看，近处主导
            score = weights[part] * (1.0 - min(1.0, d / 500.0) ** 2)
            if part == st.get("part") and not st.get("lost", 0):
                score *= self.switch_margin      # 迟滞：没坏就继续用同一个部位
            if score > best_s:
                best, best_s = part, score
        if best is None:
            return None
        px, py = reg[best]
        st["part"] = best
        st["lost"] = 0
        return (px, py, best, best_s)