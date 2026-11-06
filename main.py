#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YOLO 人物识别自动开火（本地测试脚本 · 含人类化随机）

链路：屏幕捕获(DXGI/GDI) -> YOLO 目标检测(COCO person) -> 判定是否靠近准星 -> SendInput 发开火键

人类化随机（randomize.enabled=true 时生效）：
  1) 反应延迟：检测到目标后不立刻开枪，延迟 reaction_delay_ms 区间内的随机时间
  2) 冷却随机：两次开火间隔在 cooldown_ms 区间内随机，而不是固定值
  3) 按压随机：按下保持时长在 key_hold_ms 区间内随机
  4) 概率跳过：skip_chance 的几率本次不开火
  5) 闪失取消：目标消失超过 target_lost_cancel_ms 就取消这次待开火（像人一样跟丢就不打了）

用法：
  python main.py --test                      只显示识别窗口，绝不发键
  python main.py                             实战：检测到准星附近的人就按开火键
  python main.py --source 图片路径 --test     用图片离线测试模型
  python main.py --conf 0.6 --radius 90       临时覆盖配置
  python main.py --no-random                 本次关闭随机化（对照测试用）

热键：F9 = 开/关自动开火（config.json 可改）；窗口里按 q 退出
"""

import argparse
import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
from ctypes import wintypes

import numpy as np

# ────────────────────────────── SendInput ──────────────────────────────

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100

MOUSE_BUTTONS = {
    "LBUTTON": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "RBUTTON": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "MBUTTON": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "XBUTTON1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, 1),
    "XBUTTON2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, 2),
}

# 鼠标键的虚拟键码（GetAsyncKeyState 用得到），补上 _to_vk 原本不支持的鼠标键名
MOUSE_VK = {
    "LBUTTON": 0x01, "LEFTBUTTON": 0x01, "MOUSE1": 0x01, "LEFT": 0x01,
    "RBUTTON": 0x02, "RIGHTBUTTON": 0x02, "MOUSE2": 0x02, "RIGHT": 0x02,
    "MBUTTON": 0x04, "MIDDLEBUTTON": 0x04, "MOUSE3": 0x04, "MIDDLE": 0x04,
    "XBUTTON1": 0x05, "MOUSE4": 0x05,
    "XBUTTON2": 0x06, "MOUSE5": 0x06,
}

KEY_NAMES = {
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "SHIFT": 0x10, "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D, "HOME": 0x24, "END": 0x23,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "PGUP": 0x21, "PGDN": 0x22,
    "CAPSLOCK": 0x14,
}
for _i in range(1, 25):
    KEY_NAMES["F%d" % _i] = 0x6F + _i
for _i in range(10):
    KEY_NAMES["NUMPAD%d" % _i] = 0x60 + _i


def _to_vk(name):
    key = (name or "").strip().upper()
    if not key:
        return 0
    if key in MOUSE_VK:            # 鼠标键（RButton / XButton2 ...）
        return MOUSE_VK[key]
    if len(key) == 1 and key.isalnum():
        return ord(key)
    return KEY_NAMES.get(key, 0)


def press_key(name, hold_ms=30):
    """【2026-11-01 已禁用自动开火】发键通路被强制关闭，恒不发送。
    保留签名只为不牵动 4 处调用点；要恢复开火把这一行 return False 去掉即可。"""
    return False
    key = (name or "").strip().upper()
    if key in MOUSE_BUTTONS:
        down_flag, up_flag, extra = MOUSE_BUTTONS[key]
        data = extra << 16
        down = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, down_flag, 0, 0)))
        up = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, up_flag, 0, 0)))
    else:
        vk = _to_vk(key)
        if vk == 0:
            return False
        down = INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, 0, 0, 0)))
        up = INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)))

    n1 = ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    if hold_ms > 0:
        time.sleep(hold_ms / 1000.0)
    n2 = ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    if not n1 or not n2:
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        print(f"[警告] SendInput 失败 (down={n1} up={n2}) 键={key} err={err}")
        return False
    return True



def key_event(name, down):
    """【2026-11-01 已禁用自动开火】按住模式同样恒不发送。"""
    return False
    key = (name or "").strip().upper()
    if key in MOUSE_BUTTONS:
        down_flag, up_flag, extra = MOUSE_BUTTONS[key]
        data = extra << 16
        flag = down_flag if down else up_flag
        inp = INPUT(type=INPUT_MOUSE,
                    u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data, flag, 0, 0)))
    else:
        vk = _to_vk(key)
        if vk == 0:
            return False
        inp = INPUT(type=INPUT_KEYBOARD,
                    u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0, 0)))
    n = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if not n:
        print(f"[警告] SendInput 失败 key={key} down={down}")
        return False
    return True




def start_aim_worker(q, steps, interval_ms):
    """把一次位移拆成 steps 小步发送，让准星移动看起来连续而不是一帧一跳"""
    steps = max(1, int(steps))
    interval = max(0.0, float(interval_ms) / 1000.0)

    def worker():
        while True:
            item = q.get()
            if item is None:
                return
            dx, dy = item
            sx = dx / steps
            sy = dy / steps
            acc_x = acc_y = 0.0
            for i in range(steps):
                if i == steps - 1:
                    mx, my = dx - acc_x, dy - acc_y
                else:
                    mx, my = sx, sy
                    acc_x += sx
                    acc_y += sy
                mouse_move(mx, my)
                if interval > 0 and i != steps - 1:
                    time.sleep(interval)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


MOUSEEVENTF_MOVE = 0x0001



def start_aim_follow(st, interval_ms):
    """持续跟随线程：以远高于检测帧率的频率微调准星（消除"一顿一顿"的阶梯感）。

    ★ 坐标链（2026-10-25 二轮，务必一致，写错就是"自瞄完全不动"）★
      检测帧写进 st 的是【目标相对屏幕中心的绝对坐标】 tx/ty（不是偏差！）：
          tx = 目标瞄准点屏幕x - 屏幕中心x
      跟随线程自己维护【准星相对屏幕中心的绝对坐标】 (cx,cy)，由发出的鼠标像素量累加。
      每一小步的移动量 = (tx - cx) 的一小口 → 与检测帧率无关，且天生不会冲过头。
      检测帧同步只是把"准星真实位置"（由该帧偏差反推）对齐回来，绝不重置目标坐标。

    防过冲三条硬约束：
      ① **剩余偏差而非旧偏差**：整帧只用检测帧那一个偏差、配上大 gain，会在头 1~2 步把
         整段距离花光 → 准星飞过目标、要等下一帧才拉回来（用户口中的"拉过头"）。
         改成每步都用"目标-准星"的实时剩余距离，逼近过程自动减速。
      ② **单调逼近**：步长永远 ≤ 剩余偏差，两轴各自钳位 → 数学上不可能越过目标。
      ③ **预算只当软刹车**：本帧配额在整个检测周期内均匀花掉，且只统计有效移动（不含噪声），
         避免"头两步花光→剩下 30ms 一动不动"的阶梯感。
    """
    dt = max(0.002, float(interval_ms) / 1000.0)
    # 线程是模块级函数，看不到 main() 的局部变量：随机源必须自己拿一份
    rng = st.get("rnd") or random.Random()

    def worker():
        err_shown = 0
        # 航位推算：准星相对屏幕中心的绝对坐标（由"我们发出去的像素"累加出来）
        cx = cy = None
        last_sync = 0
        edge_from = None            # 跳变限速的起点（=本帧开始时的瞄准点）
        last_obs = None             # 上一帧实测偏差（丢目标保持时用它维持"本次已走多远"）
        _carry_x = _carry_y = 0.0   # 不足 1 计数的位移残量（下一轮补，避免亚计数被 round 丢掉）
        while True:
            time.sleep(dt)
            try:
                st["ticks"] = st.get("ticks", 0) + 1
                with st["lock"]:
                    valid = st["valid"]
                    ttx, tty = st["tx"], st["ty"]    # 目标绝对坐标（相对屏幕中心）
                    obs = st.get("obs")              # 该帧观测到的真实偏差（用于对齐准星）
                    budget = st["budget"]
                    tau = float(st.get("tau", 0.045))
                    tau_floor = float(st.get("tau_floor", 0.04))
                    _abs_cap = float(st.get("abs_cap", 1.0))
                    dead = float(st.get("dead", 4.0))
                    noise = float(st.get("noise", 0.0))
                    kx, ky = st["k"]
                    maxstep = float(st.get("maxstep", 45.0))
                    sync_t = float(st.get("sync_t", 0.0))
                    hold = bool(st.get("hold", False))     # 按住瞄准键、目标短暂丢失的保持态
                    _jcap = float(st.get("jump_cap_px", 90.0))
                    _clamp_fix = bool(st.get("follow_clamp", True))
                # 检测帧刚同步过：把航位推算对齐到"真实准星位置"，只用来纠偏，不影响目标坐标本身。
                # ★符号校对（2026-10-26 复核）★
                #   检测帧写进来的 obs 是【准星还要走多少才到瞄准点】，符号 = 目标 - 准星。
                #   准星真实位置 = 目标 - obs。这一条是**对的**，别改（改成正号会立刻发散）。
                if sync_t > last_sync:
                    if obs is not None:
                        cx, cy = ttx - obs[0], tty - obs[1]
                        last_obs = (obs[0], obs[1])
                    last_sync = sync_t
                    with st["lock"]:
                        st["cmd"] = [0.0, 0.0]      # 每周期清零：绝对闸只统计本周期发出的量
                if not valid and not hold:
                    continue
                tx, ty = ttx, tty
                if cx is None or cy is None:
                    # 还没拿到过观测：先用目标坐标当起点（第一帧不会鲁莽拉枪）
                    cx, cy = tx, ty
                    continue
                # ── 跳变限速 ──
                # 瞄准点中途跳到很远处（换锁/框跳）时，本帧最多再挪 _jcap 像素，
                # 防止准星"瞬移"式甩枪；连续跟踪同一目标时几乎不生效（本帧起点已是新点）
                edge_from = (cx, cy)
                if _jcap > 0.0:
                    _nd = max(abs(tx - cx), abs(ty - cy))
                    if _nd > _jcap:
                        _s = _jcap / _nd
                        tx = cx + (tx - cx) * _s
                        ty = cy + (ty - cy) * _s
                # ── 绝对防过冲闸（按"本周期累计发出的鼠标像素量"算，与 k 无关）──
                # 本周期累计实际发出的像素量 max(|Σmx·kx|,|Σmy·ky|) 不许超过该周期实测偏差×_abs_cap。
                # 与航位推算互补：即使像素/计数系数 k 估歪，也不可能一帧内拉过头。
                # （旧版没有这层：k 一歪就是"飞过去、下一帧再拉回来"= 用户说的拉过头）
                if last_obs is not None:
                    _cap = _abs_cap * max(abs(last_obs[0]), abs(last_obs[1]))
                    # 检测帧还给一条更硬的像素位移上限（cap_px，含 0.75×框高）：
                    # 远处框小 → 自然收得紧，一个周期别把准星甩出一个身子
                    # （用户反馈的「离得较远时有时会拉过头」）。
                    _cpp = float(st.get('cap_px', 0.0) or 0.0)
                    if _cpp > 0.0:
                        _cap = min(_cap, _cpp) if _cap > 0.0 else _cpp
                    if _cap > 0.0:
                        with st["lock"]:
                            _cm0 = st.get("cmd") or [0.0, 0.0]
                        if max(abs(_cm0[0] * kx), abs(_cm0[1] * ky)) >= _cap:
                            continue                    # 本周期额度用尽，等下一帧观测
                if budget <= 0.0:              # 本帧额度已花完，等下一帧（不叠加多余位移）
                    continue
                # 单轴剩余偏差 = 目标绝对坐标 - 航位推算的准星绝对坐标
                rx, ry = tx - cx, ty - cy
                if max(abs(rx), abs(ry)) <= dead:
                    continue                                   # 已在死区内，不再抖
                d = max(abs(rx), abs(ry))
                # ── 步长分配：不是"每 5ms 吃固定一口"，而是"本周期剩余预算 ÷ 剩余偏差" ──
                # 旧写法 tau=0.05 时，第一步只消掉 5% 偏差、随即撞死区停下，整周期只走 5%
                # → 收敛慢得像没在拉枪。这里让本周期预算（budget_ratio×偏差）在一帧内
                # 基本走完：初速 = 该帧该消掉的比例，快而不过冲。
                denom = max(4.0, d)
                _rate = budget / denom
                if _rate > 1.0:
                    _rate = 1.0
                # ── 单步消比上限（2026-10-28，治"滑动太僵硬不平滑"）──
                # 旧写法 budget/denom 在"偏差 200px、budget 188px"时 = 0.94，
                # 等于**一步就走掉 94% 的距离**，视觉上是一帧"唰"地跳过去、再慢慢微调
                # = 用户说的"僵硬/一顿一顿"。
                # 这里把单步消比压到 step_cap（默认 0.18），同样的预算摊成 6~8 个小步
                # （每步约 1.5ms）走完，轨迹连续；总耗时只增加几毫秒。
                _step_cap = float(st.get("step_cap", 0.18))
                if 0.0 < _step_cap < 1.0:
                    _rate = min(_rate, _step_cap)
                if _rate < tau_floor:
                    _rate = min(tau_floor, 1.0)
                px, py = cx, cy                                # 本步起点的准星位置
                mvx = rx * _rate
                mvy = ry * _rate
                # 单调逼近：任何一轴都不得越过目标（斜向目标也不会冲过对角线）
                if (px + mvx - tx) * rx > 0.0:
                    mvx = rx
                if (py + mvy - ty) * ry > 0.0:
                    mvy = ry
                if max(abs(mvx), abs(mvy)) > maxstep:          # 单步限幅
                    sc = maxstep / max(abs(mvx), abs(mvy))
                    mvx *= sc
                    mvy *= sc
                # 噪声只作用在"最终位置"上，不计入有效移动量（否则预算被噪声瞬间吃光）
                if noise > 0:
                    mvx += rng.gauss(0.0, noise * min(3.0, max(0.4, abs(mvx))))
                    mvy += rng.gauss(0.0, noise * min(3.0, max(0.4, abs(mvy))))
                mag = max(abs(mvx), abs(mvy))
                eff = max(0.0, mag - max(0.3, mag * noise))    # 有效（非噪声）移动量
                if budget < eff:                               # 预算软刹车（按比例缩，不硬切断）
                    sc = max(0.0, budget / eff) if eff > 1e-6 else 0.0
                    mvx *= sc
                    mvy *= sc
                # 像素域 → 鼠标计数：两轴各自除，k 估偏了也不至于把一轴拉爆。
                # ★不足 1 计数不许丢（2026-10-26 平滑性修正）★
                # 旧写法 `if abs(mx)<1 and abs(my)<1: continue` 把亚计数位移整块扔掉：
                # 趋近到 1~3px 时每步都算出 0.x 计数 → 一步不发、停住不动，
                # 等误差长大再猛地跳一下，用户看到的就是"移动一顿一顿 / 贴不上"。
                # 现在把不足 1 计数的部分存进 carry，下一轮把它补进来（不丢位移、不引入漂移）。
                mx = mvx / max(0.05, kx)                        # 像素域 → 鼠标计数
                my = mvy / max(0.05, ky)
                # ★亚计数用"残量累加"处理，别整块扔掉★（2026-10-26 修："一顿一顿 / 贴不上"）
                # 旧写法 `if abs(mx)<1 and abs(my)<1: continue` 把不足 1 计数的位移全丢掉：
                # 每步都算出 0.x 计数 → 一步不发、停住不动，等误差长大再猛地跳一下。
                # 注意两点（都踩过）：
                #   ① 残量必须**累加**（`+= mx - 本步发出的整数`），不能"只在真发出去时才更新"——
                #      否则 0.34 每轮都被重算成 0.34、永远长不到 1 → 永久卡死（实测偏差 2px 不动）。
                #   ② 累加的是"本步剩的小数"（mx - _ix），所以正常大步时残量恒在半个计数以内，
                #      平均速度与实际位移一致，不会引入漂移。
                _ix = 0 if abs(mx + _carry_x) < 0.5 else int(round(mx + _carry_x))
                _iy = 0 if abs(my + _carry_y) < 0.5 else int(round(my + _carry_y))
                if _ix == 0 and _iy == 0:
                    _carry_x += mx
                    _carry_y += my
                elif mouse_move(_ix, _iy):
                    # 扣掉本步额度，剩下的小数留到下一步继续攒
                    _carry_x += mx - _ix
                    _carry_y += my - _iy
                else:
                    _carry_x += mx
                    _carry_y += my
                # 残量钳在 ±0.75 计数：正常情况下它本来就小于半个计数；
                # 万一 k 估得离谱，也不许残量把准星带成来回抽。
                _carry_x = max(-0.75, min(0.75, _carry_x))
                _carry_y = max(-0.75, min(0.75, _carry_y))
                if _ix == 0 and _iy == 0:
                    continue                                   # 残量还不够半个计数，攒着
                # 航位推算步进：用**实际发出的整数计数**折算像素（与桌面真实位移一致），
                # 并硬钳在"不过目标"的一侧
                _spx, _spy = _ix * kx, _iy * ky
                nx = px + _spx
                ny = py + _spy
                # 硬钳在"不过目标"的一侧。★2026-10-30 修正★
                # 旧写法 `min(px,tx) if nx>tx else max(px,tx) if nx<tx else nx`
                # 两个分支的 min/max 是**反的**：
                #   步长没越过目标（nx<tx，正常情况）→ 取 max(px,tx)=tx，
                #   等于把航位推算的准星位置直接当成"已经到目标了"。
                # 后果：剩余偏差立刻变 0 → 跟随线程每个检测周期只发出**一步**就停，
                # 预算里剩下的份额全被浪费。这就是"枪口靠近头部时磨半天贴不上"的机械原因
                # （也是 10-28 那次"一顿一顿"的根：每周期只走一步）。
                # 正确写法：越过了才钳回目标，没越过就走多少算多少。
                if (nx - tx) * rx > 0.0:
                    nx = tx
                if (ny - ty) * ry > 0.0:
                    ny = ty
                if not _clamp_fix:      # 回退开关：follow_clamp=false 走旧（反写）行为
                    nx = min(px, tx) if (px + _spx) > tx else (max(px, tx) if (px + _spx) < tx else (px + _spx))
                    ny = min(py, ty) if (py + _spy) > ty else (max(py, ty) if (py + _spy) < ty else (py + _spy))
                cx, cy = nx, ny
                st["sent"] = st.get("sent", 0) + 1
                # ★预算记账口径（2026-10-27 复修"慢了"）★
                # 两种都试过，都有毛病：
                #   · 按"实际发出的整数位移"扣 → 每步扣太狠，一周期能发的步数变少 → 跟枪变慢
                #     （实测"k 估歪一半"场景首答 106ms 退化到 139ms，用户体感就是"慢了"）
                #   · 按"打算消掉的量(减噪声)"扣 → 每步只碰上、还没走完就把预算花光 → 走不动
                # 取中：按**成比例的位移量**扣 —— 本步用量占计划量的比例 × 计划有效量。
                # mx 介于 0.5~1 计数时会被取整放大到 1，这时只按放大的比例多扣一点点，
                # 既保住"取整催速度"（旧代码的快就来自这里），又不会把没走的量算成已走。
                _den = max(0.5, abs(mx), abs(my))
                _eff_scaled = eff * (max(abs(_ix), abs(_iy)) / _den)
                with st["lock"]:
                    st["budget"] = budget - max(0.0, _eff_scaled)
                    # 本周期累计（供"绝对防过冲闸"用，随每帧同步清零）
                    _cm = st.get("cmd") or [0.0, 0.0]
                    st["cmd"] = [_cm[0] + mx, _cm[1] + my]
                    # 另一份只增不减的累计（供标定算"两帧之间真正发了多少计数"用；
                    # 不能和 cmd 共用——cmd 每个检测帧都清零，会让标定样本恒为 0、系数永远学不动）
                    _cc = st.get("cc") or [0.0, 0.0]
                    st["cc"] = [_cc[0] + mx, _cc[1] + my]
            except Exception as exc:
                # 绝不让线程静默死掉：报一次错，然后继续跑
                if err_shown < 3:
                    err_shown += 1
                    print("[瞄准] 跟随线程异常:", type(exc).__name__, exc)
                time.sleep(0.05)

    threading.Thread(target=worker, daemon=True).start()


def mouse_move(dx, dy):
    """相对移动鼠标（SendInput）"""
    if int(round(dx)) == 0 and int(round(dy)) == 0:
        return False
    inp = INPUT(type=INPUT_MOUSE,
                u=_INPUTUNION(mi=MOUSEINPUT(int(round(dx)), int(round(dy)), 0,
                                            MOUSEEVENTF_MOVE, 0, 0)))
    return bool(ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))



def pick_locked(boxes, aim_x, aim_y, min_h, st, now, hold_s, match_px, sticky_s, radius_px=0.0):
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
    # ── ★2026-11-01 转火接管（用户「做12」）★ ──
    # ① 冷却抖动：每次换人后按 switch_cooldown_jitter 重新抽一次冷却时长，
    #    固定毫秒的换人间隔是最容易做行为指纹的特征之一。
    # ② commit flick 窗口：换人成功后的 switch_commit_ms 内，准星正朝新目标甩，
    #    这段时间里**不许再评估换人**（包括"旁边有人更近"）。不然甩枪路径上
    #    路过第三个框就会被半路截胡，表现为"换过去又弹回来"。
    _cdj = max(0.0, min(0.9, float(st.get("switch_cooldown_jitter", 0.2))))
    _cdn = st.get("cd_next")            # ★没抽过就回退到基准值，不能把初始 0 当成"冷却 0"
    _cd_eff = _cd if _cdn is None else max(0.0, float(_cdn))
    _cm = max(0.0, float(st.get("switch_commit_ms", 180)) / 1000.0)
    # ── ★2026-11-01 用户「新目标接管变成 20 帧」★ ──
    # 旧目标丢掉之后不立刻接管下一个，等 acquire_hold_ms 再选 —— 人类发现"人没了"
    # 到把准星送上下一个目标要 300~700ms，瞬间接管是最明显的机器特征之一。
    # 计时从"最后一次确认旧目标"起算，锁上新目标时清零。
    _ahold = max(0.0, float(st.get("acquire_hold_ms", 0.0)) / 1000.0)
    # 接管延迟是否还在生效：旧目标丢掉还没满 acquire_hold_ms → 不许接管任何人
    _lost_t0 = float(st.get("lost_t") or 0.0)
    _ahold_wait = bool(_ahold > 0.0 and _lost_t0 > 0.0 and (now - _lost_t0) < _ahold)
    _committing = now < float(st.get("commit_until", 0.0))
    if not st.get("switch_t"):          # 初始 0 → now-0 极大，首次换人白拿冷却豁免
        st["switch_t"] = now
    _scf = max(1, int(st.get("switch_confirm_frames", 2)))
    _pg = float(st.get("pred_gain", 0.5))
    _pv = st.get("v") or [0.0, 0.0]

    # ── 新目标确认帧：没锁定时，候选要连续出现 confirm_frames 帧才允许上锁 ──
    _cf = max(1, int(st.get("confirm_frames", 1)))
    _have_lock = lb is not None and (now - lt) <= hold_s
    if _cf > 1 and not _have_lock and cands:
        # 用"距离邻近"累计，而不是把坐标量化成格子：检测框本来就在抖，
        # 格子法（60px 桶）在抖动大时会每帧换桶 → 计数永远到不了 2 → 目标看着在那儿却一直不锁。
        _seen = st.setdefault("seen", [])
        _cpx = float(st.get("confirm_px", 120.0))
        _ok = []
        for c in cands:
            ccx, ccy = cen(c)
            hit = None
            for ent in _seen:
                if ((ent[0] - ccx) ** 2 + (ent[1] - ccy) ** 2) ** 0.5 <= _cpx:
                    hit = ent
                    break
            if hit is None:
                _seen.append([ccx, ccy, 1, now])
                continue
            hit[0], hit[1] = ccx, ccy
            hit[2] += 1
            hit[3] = now
            if hit[2] >= _cf:
                _ok.append(c)
        _seen[:] = [e for e in _seen if now - e[3] <= 2.0]
        cands = _ok

    # ── 有锁定：先按"预测中心"匹配 ──
    _stale = float(st.get("stale_hold_ms", 120)) / 1000.0
    _horizon = hold_s + (min(_extra, _stale) if _eng else 0.0)
    if _committing:
        # commit 窗口比 hold 长时，锁定不能先过期 —— 一旦过期就掉到"就近重选"，
        # 等于窗口白设（实测：hold 120ms 先到，180ms 的窗口根本没机会起作用）。
        _horizon = max(_horizon, (float(st.get("commit_until", 0.0)) - now) + hold_s)
    if lb is not None and (now - lt) <= _horizon:
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
            st["lost_t"] = 0.0                 # 咬住了：接管计时清零
            return best

        # ── 没匹配上 ──
        #★2026-10-30 自查「锁空白」★ 视野里一个候选都没有 = 目标真的没了。
        # 这一条必须放在"交战期保留旧框"之前：否则开镜锁常在交战态（engage=True），
        # 它会拿着一个早就不存在的位置继续压枪 0.8 秒 —— 就是用户说的"锁空白"。
        if not cands:
            if not st.get("lost_t"):
                st["lost_t"] = now           # ★接管计时起点：旧目标确认没了
            st["box"] = None
            st["v"] = [0.0, 0.0]
            st["sw_pend"] = {}
            return None
        # ── ★2026-11-01★ commit flick 窗口：甩枪途中不重选、不换人 ★ ──
        # 刚换人，准星还在朝新目标走的路上；新框可能暂时出了 match_px 匹配不上。
        # 此时若是按"就近重选"，甩到一半路过谁就锁谁 —— 半路截胡。
        # 窗口内一律续命旧框，窗口结束再走正常换人闸。
        if _committing:
            st["box"], st["t"] = lb, now
            st["v"] = [0.0, 0.0]
            return lb
        # ★2026-11-01★ 走到这里 = commit 窗口已过、旧目标确实丢了（有候选但匹配不上）。
        # 从这一刻开始计"接管延迟"，人类发现目标没了到送上下一个也要 300~700ms。
        if not st.get("lost_t"):
            st["lost_t"] = now
        # ── ★2026-11-01★ 锁死不换人（never_switch）★ ──
        # 来回锁的根因：匹配失败后按"离准星最近"重选，而准星正被自瞄驱动去追 A，
        # 视角一动 B 就可能变最近 → 换 B → A 又近 → 无限振荡。调 hysteresis/cooldown
        # 只能压低频，压不掉。这里直接封死换人通路：锁定期间绝不改锁，
        # 只有"视野里一个候选都没有"（上面已处理）或 hold 过期才解锁。
        if bool(st.get("never_switch", False)):
            if (now - lt) <= hold_s:
                st["box"], st["t"] = lb, now      # 续命旧框，不换人
                st["sw_pend"] = {}
                return lb
            st["box"] = None                      # 过期才允许重新选（下一帧走首次锁定）
            st["v"] = [0.0, 0.0]
            st["sw_pend"] = {}
            return None
        # ── 有候选但匹配不上：换人闸 ──
        cand_target = None
        if cands:
            cand_target = min(cands, key=d_aim)
            d_old = d_aim(lb)
            d_new = d_aim(cand_target)
            cd_ok = ((not _committing) and (not _ahold_wait)
                     and (now - float(st.get("switch_t", 0.0))) >= _cd_eff)
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
                # 进入 commit 窗口 + 抽下一次冷却（±jitter）
                st["commit_until"] = now + _cm
                st["cd_next"] = _cd * (1.0 + random.uniform(-_cdj, _cdj))
                st["v"] = [0.0, 0.0]
                st["sw_pend"] = {}
                st["lost_t"] = 0.0          # 换到新目标 = 接管完成
                return cand_target
        if _eng or (now - lt) <= hold_s:
            return lb            # 闸不过 / 交战期：保持原目标，不许"就近重选"
        # 否则旧目标确实过期太久 → 落下去重新选

    if cands:
        # ★2026-11-01「新目标接管 20 帧」★ 旧目标丢掉后先等够 acquire_hold_ms 再接管
        if _ahold_wait:
            st["box"] = None
            return None
        best = min(cands, key=d_aim)
        st["box"], st["t"], st["start"] = best, now, now
        st["commit_until"] = now + _cm     # 首锁同样进 commit，避免刚锁上就被人抢走
        st["lost_t"] = 0.0
        st["v"] = [0.0, 0.0]
        st["sw_pend"] = {}
        return best
    st["box"] = None
    return None


POSE_PREF = {"chest": ("chest", "head", "nose"),
             "head": ("head", "chest", "nose"),
             "nose": ("nose", "head", "chest"),
             "auto": ("chest", "head", "nose"),
             "center": ("chest", "head", "nose")}


def pick_pose_point(kps_xy, kps_cf, boxes, x1, y1, x2, y2, aim_cfg, pose_point,
                    part_prev, lost_prev, sm_prev):
    """从姿态关键点里挑一个稳定、可信的瞄准点（胸优先，可退回头/鼻）。

    为什么不是"每帧硬选一个最靠中间的点"（实测会"一次定位不准"）：
      · 远景 / 背身 / 遮挡时，鼻子、眼睛、双肩随时丢，帧间还会在"鼻子"与"双肩中点"
        之间来回跳 → 首答落点一会儿头一会儿胸，用户就觉得"第一次总是不准"。
    这里的做法：
      · 置信度门槛（pose_conf）过滤不可信关键点；一个都不剩就返回 None（上层退回按框高比例）
      · 候选：head = 鼻+双眼（可信者求平均）、chest = 双肩中点、nose = 鼻子单点
      · **粘住上一次的部位**：当前部位还在就继续用它；连续丢超过 pose_hold_frames 才按
        优先级换（auto = 胸 → 头 → 鼻，胸面积最大、最抗遮挡）
      · 选中点做 EMA 平滑（pose_smooth），把单帧 3~8px 的关键点抖动压到 1~2px

    返回 (部位, 连续丢失帧数, 平滑点, 瞄准点) 或 None（本帧没有任何可用候选）
    """
    if kps_xy is None or not len(kps_xy):
        return None
    # 找出与锁定框对应的那一条姿态（按框中心距离最近）
    bi, bd = None, 1e18
    lcx, lcy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    for i in range(min(len(kps_xy), len(boxes))):
        b = boxes[i]
        dd = abs((b[0] + b[2]) / 2.0 - lcx) + abs((b[1] + b[3]) / 2.0 - lcy)
        if dd < bd:
            bd, bi = dd, i
    if bi is None or bd >= 40:
        return None
    pts = kps_xy[bi]
    cf = kps_cf[bi] if kps_cf is not None and bi < len(kps_cf) else None
    # 瞄准点平滑强度随框高（≈距离）自适应：贴脸少平滑跟得紧、远处仍抗抖
    _psm = float(aim_cfg.get("pose_smooth", 0.5))
    _big = float(aim_cfg.get("pose_smooth_big_px", 0.0) or 0.0)
    _psm_near = float(aim_cfg.get("pose_smooth_near", 0.9))
    if _big > 1.0 and _psm_near > _psm:
        _bhk = max(1.0, float(y2) - float(y1))
        _wsm = max(0.0, min(1.0, (_bhk - _big) / _big))
        _psm = _psm + (_psm_near - _psm) * _wsm
    cfth = float(aim_cfg.get("pose_conf", 0.35))

    def kp(k):
        """取关键点 (x, y, 置信度, 是否本来就有置信度)"""
        if k >= len(pts):
            return None
        q = pts[k]
        x, y = float(q[0]), float(q[1])
        if x <= 0 or y <= 0:
            return None
        if cf is not None and k < len(cf):
            try:
                c, have = float(cf[k]), True
            except Exception:
                c, have = 1.0, False
        else:
            c, have = 1.0, False          # 这个后端不给置信度：当"可信"处理
        return (x, y, c, have)

    def ok(p):
        """可信 = 本来就没有置信度信息，或置信度达标"""
        return bool(p) and (not p[3] or p[2] > cfth)

    nose = kp(0)
    eyes = [q for q in (kp(1), kp(2)) if ok(q)]
    sh = [q for q in (kp(5), kp(6)) if ok(q)]

    def avg(lst):
        return (sum(q[0] for q in lst) / len(lst), sum(q[1] for q in lst) / len(lst))

    # ── 几何合理性闸（2026-10-26 加：治"瞄准点在头胸旁边/打在空气上"）──
    # 锁定框是这一帧**最可信的几何先验**（它一定套在人身上）。关键点只是"模型猜的位置"，
    # 远景/遮挡/背身时经常猜偏 20~40px 甚至飞到框外。旧代码只按置信度筛，
    # 于是"conf 刚过线的瞎猜点"照样当瞄准点 → 准星贴着人但不在身上，就是打空气。
    # 这里再用框几何卡一道：头必须在框上方 45% 以内、胸必须落在框高 12%~70% 之间、
    # 横向不许跑出框（留 12% 余量，肩比框宽是正常的），越界就当这一部位本帧不可用。
    _bw = max(1.0, x2 - x1)
    _bh = max(1.0, y2 - y1)

    def plausible(part, pt):
        """候选点是否落在"人身上"的合理范围内（相对锁定框）。"""
        rx = (pt[0] - x1) / _bw
        ry = (pt[1] - y1) / _bh
        if part == "head":
            # 头必须在框内、且不超过框高的 head_y_max（默认 45%）
            y_ok = 0.0 <= ry <= float(aim_cfg.get("head_y_max", 0.30))
        elif part == "chest":
            y_ok = float(aim_cfg.get("chest_y_min", 0.12)) <= ry <= \
                float(aim_cfg.get("chest_y_max", 0.70))
        else:
            y_ok = 0.0 <= ry <= 0.45
        # 横向：框有点紧是常态（肩比框宽），给 12% 余量；再多就是猜飞了
        x_ok = -0.12 <= rx <= 1.12
        return bool(y_ok and x_ok)

    # 候选点：置信度不达标的点不能单独成候选（否则模型"瞎猜"的位置也会被当成瞄准点，
    # 实测就是"首答飘一下"）；再过一遍几何合理性闸。
    # 全部不可用时按 allow_low_conf_pose 决定是否用鼻子保底。
    cans = {}
    hpts = ([nose] if ok(nose) else []) + eyes
    if hpts:
        _h = avg(hpts)
        if plausible("head", _h):
            cans["head"] = _h
    if sh:
        _c = avg(sh)
        if plausible("chest", _c):
            cans["chest"] = _c
    if ok(nose):
        _n = (nose[0], nose[1])
        if plausible("nose", _n):
            cans["nose"] = _n
    if not cans:
        if nose is None or not bool(aim_cfg.get("allow_low_conf_pose", False)):
            return None                # 一个可信关键点都没有 → 退回按框高比例瞄
        _fn = (nose[0], nose[1])
        if not plausible("nose", _fn):
            return None                # 鼻子也明显不在框里 → 宁可按框高比例瞄
        part_f = "nose"
        psm = _psm
        if sm_prev is None:
            sm = [_fn[0], _fn[1]]
        else:
            sm = [sm_prev[0] + (_fn[0] - sm_prev[0]) * psm,
                  sm_prev[1] + (_fn[1] - sm_prev[1]) * psm]
        return (part_f, 0, sm, (sm[0], sm[1]))

    order = POSE_PREF.get(pose_point, POSE_PREF["auto"])
    part, lost = part_prev, lost_prev
    pickpt = None
    if part in cans:
        pickpt = cans[part]                      # 粘住原部位
    else:
        if not part:
            # ★首次选点（还没有任何部位）必须立刻按优先级定下来★
            # 旧写法把它当成"丢了"，要等 pose_hold_frames 帧之后才选 →
            # 表现就是用户说的"首答定位不准"（前几帧还在按框高比例瞄，随后才跳到胸/头）
            for k in order:
                if k in cans:
                    pickpt, part = cans[k], k
                    break
        else:
            lost += 1
            if lost > int(aim_cfg.get("pose_hold_frames", 4)):
                for k in order:
                    if k in cans:
                        pickpt, part = cans[k], k
                        break
    if pickpt is None:
        return (part, lost, sm_prev, None)       # 还得继续粘，本轮不给点
    lost = 0
    psm = _psm
    if sm_prev is None:
        sm = [pickpt[0], pickpt[1]]
    else:
        sm = [sm_prev[0] + (pickpt[0] - sm_prev[0]) * psm,
              sm_prev[1] + (pickpt[1] - sm_prev[1]) * psm]
    # ── 瞄准点单帧跳变限速（默认关；pose_jump_cap_px>0 才生效）──
    # 部位切换/关键点瞬时猜飞时，瞄准点会一步挪动上百像素 —— 准星跟着抽一下。
    # 开了就把单帧位移钳住，被钳掉的量下一帧继续追（不丢位移）。
    _pj = float(aim_cfg.get("pose_jump_cap_px", 0.0) or 0.0)
    if _pj > 0.0 and sm_prev is not None:
        _dx, _dy = sm[0] - sm_prev[0], sm[1] - sm_prev[1]
        _dm = max(abs(_dx), abs(_dy))
        if _dm > _pj:
            _s = _pj / _dm
            sm = [sm_prev[0] + _dx * _s, sm_prev[1] + _dy * _s]
    return (part, lost, sm, (sm[0], sm[1]))


def is_new_target(prev, cur, aim_x, aim_y, cfg):
    """判定"锁定目标换人了"，对开镜缩放免疫。

    prev / cur = (框中心x, 框中心y, 框高)，图像坐标（800x800 抓屏区）。

    为什么不能用"绝对 60px 跳变"（旧写法）：
      开镜时视野以准星为中心放大 z 倍，同一个敌人的框中心相对准星的偏移也放大
      z 倍（400px → 1600px），框高同步放大。旧写法把这当成"换人" → 每个检测帧
      都触发首答脉冲（预算抬到 budget_ratio_initial + 标定样本清零）→ 整个开镜
      动画期间满预算猛拽 = 用户说的「开镜视角异常移动」。
      实测（_test_ads.py）：真实开镜动画下 17 帧里有 16 帧被误判成换人。

    正确的不变量：以准星为缩放中心做**尺度补偿**后再比残差。
      预测中心 = 准星 + (上一帧中心 - 准星) × (框高比)
      残差 ÷ 框高   —— 纯缩放时 ≈ 0；真的换人时很大。
    """
    if prev is None or cur is None:
        return True
    px, py, ph = float(prev[0]), float(prev[1]), float(prev[2])
    cx, cy, ch = float(cur[0]), float(cur[1]), float(cur[2])
    if ph <= 1.0 or ch <= 1.0:
        return True
    s = max(0.35, min(2.8, ch / ph))     # 缩放比限幅（防止框抖动被当成几何变化）
    pred_x = aim_x + (px - aim_x) * s
    pred_y = aim_y + (py - aim_y) * s
    resid = ((cx - pred_x) ** 2 + (cy - pred_y) ** 2) ** 0.5 / ch
    return resid > float(cfg.get("new_target_resid", 0.55))


def update_aim_k(aim_k, samples, prev_sent, prev_err, db, ex, ey, adaptive=True):
    """自适应标定「像素/计数」k。★这是"第一定位不准"的根修★

    记号：B = 目标在世界里的方位（折算成屏幕像素），P = 准星位置，e = B - P。
    两帧之间：

        Δe = ΔB - k × 本次发出的鼠标计数

    所以 **k = (ΔB - Δe) / 计数**。

    难点是 ΔB（目标自己的世界位移）没法直接看见——**画面里目标的位移 Δe 已经把
    我们自己造成的位移算进去了**。所以只能这样估 ΔB：

      · 只在"这一帧几乎没发移动"（|计数| < 3）时，Δe ≈ ΔB → 记下来当世界位移速率
      · 其余帧用这个速率 × 间隔帧数作为 ΔB 的估计

    这条区分非常关键：上一版就是把"画面里的位移"当成了 ΔB，结果 k 被越学越小
    （真实 1.0 被学成 0.47），闭环增益反而变大、开始左右甩。

    另外：首个样本若动作量足够大（预计 ≥50px），直接采信（限制在 0.4~3.0 之间），
    否则用学习率混合；每次最多改 ±33%，噪声样本不会把系数带飞。

    返回 (aim_k, samples)
    """
    if (not adaptive or prev_sent is None or prev_err is None or db is None):
        return aim_k, samples
    psx, psy = prev_sent
    pex, pey = prev_err
    de_x = ex - pex               # Δe
    de_y = ey - pey
    learn = 0.60 if samples < 6 else (0.25 if samples < 20 else 0.10)
    if abs(psx) >= 5:
        kx = (db[0] - de_x) / psx
        if 0.05 < kx < 30:
            _big = abs(psx) * max(0.10, aim_k[0]) >= 50.0 and abs(psx) >= 12
            if samples == 0 and _big:
                aim_k[0] = max(0.50, min(2.0, kx))
            else:
                nk = aim_k[0] * (1 - learn) + kx * learn
                aim_k[0] = max(aim_k[0] * 0.80, min(aim_k[0] * 1.20, nk))
            samples += 1
    if abs(psy) >= 5:
        ky = (db[1] - de_y) / psy
        if 0.05 < ky < 30:
            _bigy = abs(psy) * max(0.10, aim_k[1]) >= 50.0 and abs(psy) >= 12
            if samples == 0 and _bigy:
                aim_k[1] = max(0.50, min(2.0, ky))
            else:
                nk = aim_k[1] * (1 - learn) + ky * learn
                aim_k[1] = max(aim_k[1] * 0.80, min(aim_k[1] * 1.20, nk))
            samples += 1
    return aim_k, samples


def is_key_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)


# ────────────────────────────── 抓屏（多后端） ──────────────────────────────

class ScreenGrabber:
    """dxcam -> bettercam -> mss，用第一个能真正抓到帧的后端"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.backend = None
        self._cam = None
        self._sct = None
        self.width = ctypes.windll.user32.GetSystemMetrics(0)
        self.height = ctypes.windll.user32.GetSystemMetrics(1)
        self.region = self._compute_region()

        for mod_name in ("dxcam", "bettercam"):
            try:
                mod = __import__(mod_name)
                cam = mod.create(output_color="BGR")
                if cam is None:
                    continue
                if not self._probe(cam):
                    try:
                        cam.release()
                    except Exception:
                        pass
                    continue
                self._cam = cam
                self.backend = mod_name
                try:
                    self.width, self.height = cam.width, cam.height
                except Exception:
                    pass
                self.region = self._compute_region()
                return
            except Exception as exc:
                print(f"[信息] {mod_name} 不可用（{type(exc).__name__}）")

        try:
            import mss
            self._sct = mss.mss()
            mon = self._sct.monitors[1]
            self.width, self.height = mon["width"], mon["height"]
            self.region = self._compute_region()
            self.backend = "mss"
        except Exception as exc:
            print("[信息] mss 不可用:", exc)

    def _probe(self, cam):
        try:
            for _ in range(30):
                if cam.grab() is not None:
                    return True
                time.sleep(0.005)
            return True
        except Exception as exc:
            print(f"[信息] {type(cam).__module__} 抓帧失败：{type(exc).__name__}: {exc}")
            return False

    def _compute_region(self):
        if self.cfg.get("region"):
            return tuple(int(v) for v in self.cfg["region"])
        size = int(self.cfg.get("region_size", 640))
        half = size // 2
        cx, cy = self.width // 2, self.height // 2
        return (max(0, cx - half), max(0, cy - half),
                min(self.width, cx + half), min(self.height, cy + half))

    def grab(self):
        if self.backend is None:
            return None
        if self.backend == "mss":
            l, t, r, b = self.region
            shot = self._sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
            return np.asarray(shot)[:, :, :3]
        return self._cam.grab(region=self.region)

    def close(self):
        try:
            if self._cam is not None:
                self._cam.release()
        except Exception:
            pass
        try:
            if self._sct is not None:
                self._sct.close()
        except Exception:
            pass


class PreviewThread:
    """显示线程：主循环只塞"最新带标注帧"，imshow/waitKey 在这个线程里跑。

    实测 imshow+waitKey 阻塞主循环 6.4ms/轮（占实战一轮 13%）。摘出去之后
    主循环只管算，画面按显示器刷新率自己画。
    """

    def __init__(self, name="YOLO trigger"):
        self.name = name
        self._lock = threading.Lock()
        self._img = None
        self._seq = 0
        self._seen = 0
        self._stop = False
        self.quit = False
        self.err = None
        self.drawn = 0
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, img):
        with self._lock:
            self._img = img
            self._seq += 1

    def _run(self):
        import cv2
        # ★窗口必须在本线程创建★ 主线程建、别的线程画 → Windows 下一帧都不显示
        try:
            cv2.namedWindow(self.name, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(self.name, cv2.WND_PROP_TOPMOST, 1)
            cv2.resizeWindow(self.name, 520, 520)
            cv2.moveWindow(self.name, 20, 20)
        except Exception as exc:
            self.err = "建窗失败 %s: %s" % (type(exc).__name__, exc)
            return
        while not self._stop:
            with self._lock:
                img, s = self._img, self._seq
            if img is None or s == self._seen:
                time.sleep(0.002)
                continue
            self._seen = s
            try:
                cv2.imshow(self.name, img)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    self.quit = True
                self.drawn += 1
            except Exception as exc:
                if self.err is None:
                    self.err = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(0.05)
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

    def close(self):
        self._stop = True


class LatestFrame:
    """抓屏线程 + 最新帧信箱：让"抓屏"与"推理"并行。

    为什么需要：实测 dxcam 抓 800x800 只要 3.8ms，但它是同步阻塞的，
    串在推理前面白占 30% 帧预算。拆成独立线程后主循环只取"最新一帧"，
    每轮 ≈ max(抓屏, 推理) 而不是两者相加（实测合计 13.2ms → 目标 8~9ms）。

    低延迟优先：永远丢旧帧取最新，绝不排队（排队只会让框更慢）。
    """

    def __init__(self, grabber):
        self.g = grabber
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._stop = False
        self.ticks = 0
        self.err = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop:
            try:
                f = self.g.grab()
                if f is None:
                    time.sleep(0.0005)
                    continue
                with self._lock:
                    self._frame = f
                    self._seq += 1
                self.ticks += 1
            except Exception as exc:      # 绝不让线程静默死掉
                if self.err is None:
                    self.err = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(0.01)

    def latest(self, after_seq=None, wait_ms=8.0):
        """取最新帧；after_seq 给定时只接受更新的帧（最多等 wait_ms 毫秒）"""
        t_end = time.perf_counter() + wait_ms / 1000.0
        while True:
            with self._lock:
                f, s = self._frame, self._seq
            if f is not None and (after_seq is None or s != after_seq):
                return f, s
            if time.perf_counter() >= t_end:
                return f, s
            time.sleep(0.0004)

    def close(self):
        self._stop = True


# ────────────────────────────── 工具 ──────────────────────────────

def imread_u(path):
    """读图（兼容中文路径，不依赖 cv2）"""
    from PIL import Image
    return np.array(Image.open(path).convert("RGB"))[:, :, ::-1]


def imwrite_u(path, img):
    from PIL import Image
    Image.fromarray(img[:, :, ::-1]).save(path)
    return True


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _circularity(cnt):
    """轮廓有多接近圆：4πA/P²，正圆=1，细长/锯齿形明显偏低"""
    import cv2
    a = cv2.contourArea(cnt)
    per = cv2.arcLength(cnt, True)
    if per <= 1e-6:
        return 0.0
    return 4.0 * 3.14159265 * a / (per * per)


def _circle_fill(cnt):
    """轮廓占其最小外接圆的比例：正圆≈1.0，正方形≈0.64，长条更低。
    这个比 4πA/P² 更能区分"圆"和"方"（正方形圆度 0.785 会骗过 0.55 的阈值）。"""
    import cv2
    a = cv2.contourArea(cnt)
    (_, _), r = cv2.minEnclosingCircle(cnt)
    if r <= 1e-6:
        return 0.0
    return a / (3.14159265 * r * r)


def _ring_green_ratio(mask, cx, cy, r, n=96, tol=2):
    """圆周上采样 n 个点，看有多少点落在绿色掩码里（0~1）。圆环=接近 1，杂色=低。"""
    hit = 0
    H, W = mask.shape[:2]
    for k in range(n):
        a = 6.2831853 * k / n
        x = int(round(cx + r * __import__("math").cos(a)))
        y = int(round(cy + r * __import__("math").sin(a)))
        x0, x1 = max(0, x - tol), min(W, x + tol + 1)
        y0, y1 = max(0, y - tol), min(H, y + tol + 1)
        if x1 > x0 and y1 > y0 and mask[y0:y1, x0:x1].any():
            hit += 1
    return hit / float(n)


def teammate_by_ring(frame, boxes, tcfg):
    """框里套着**绿色圆环**的是队友（2026-10-28 用户截图指出：圆形标识）

    比"头顶有颜色"可靠，因为不依赖图标位置（截图上那个圆环是套在身上的）：
      ① 整帧算一次绿色掩码（便宜）
      ② 每个框在其外扩 15% 的小图上跑霍夫圆
      ③ 候选圆要满足：半径 = 框高的 min_r_ratio~max_r_ratio 倍、
         圆心离框中心不超过 max_off×框宽、
         圆周上的绿色占比 >= min_green_ratio（这个是关键：真圆环接近 1）
    返回判为队友的框下标集合。
    """
    if not tcfg.get("enabled", False) or len(boxes) == 0:
        return set()
    try:
        import cv2
    except Exception:
        return set()
    ranges = tcfg.get("hsv_ranges") or [[30, 90, 60, 110, 255, 255]]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for r in ranges:
        if len(r) == 6:
            mask |= cv2.inRange(hsv, np.array([r[0], r[1], r[2]], np.uint8),
                                np.array([r[3], r[4], r[5]], np.uint8))
    if not mask.any():
        return set()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape[:2]
    min_rr = float(tcfg.get("min_r_ratio", 0.30))
    max_rr = float(tcfg.get("max_r_ratio", 1.30))
    max_off = float(tcfg.get("max_center_off", 0.60))
    min_gr = float(tcfg.get("min_green_ratio", 0.65))
    pad = float(tcfg.get("roi_pad", 0.15))
    max_boxes = int(tcfg.get("max_boxes", 3))
    out = set()
    for i, b in enumerate(boxes[:max_boxes]):
        x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        rx1 = int(max(0, x1 - pad * bw)); rx2 = int(min(W, x2 + pad * bw))
        ry1 = int(max(0, y1 - pad * bh)); ry2 = int(min(H, y2 + pad * bh))
        if rx2 - rx1 < 30 or ry2 - ry1 < 30:
            continue
        sub = gray[ry1:ry2, rx1:rx2]
        # ★性能关键★ 霍夫圆在原尺寸上极慢（实测 3 个框 86ms/帧，直接废掉帧率）。
        # 两步把它压到 4ms/框：
        #   ① 半径搜索范围按"框宽"收窄（0.30~0.80×框宽）—— 圆环半径本来就约等于框宽的一半，
        #      不按框高算（旧写法半径上限到 1.56×框宽，搜索空间大 4 倍）；
        #   ② 灰度图缩到 0.4 倍再检测，半径按比例换算回来。
        # 实测：86ms -> 4.2ms/框，候选圆仍能命中那个绿环。
        sc = float(tcfg.get("down_scale", 0.4))
        small = cv2.resize(sub, (max(40, int((rx2 - rx1) * sc)), max(40, int((ry2 - ry1) * sc))),
                           interpolation=cv2.INTER_AREA) if sc < 1.0 else sub
        try:
            circles = cv2.HoughCircles(small, cv2.HOUGH_GRADIENT, dp=1.2,
                                       minDist=max(8, int(0.4 * bh * sc)),
                                       param1=110, param2=int(tcfg.get("hough_param2", 36)),
                                       minRadius=max(4, int(min_rr * bw * sc)),
                                       maxRadius=max(6, int(max_rr * bw * sc)))
        except Exception:
            continue
        if circles is None:
            continue
        ccx, ccy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        best = 0.0
        for (scx, scy, sr) in circles[0][:10]:
            gx, gy = rx1 + float(scx) / max(1e-6, sc), ry1 + float(scy) / max(1e-6, sc)
            gr = float(sr) / max(1e-6, sc)
            if not (min_rr * bw <= gr <= max_rr * bw):
                continue
            if ((gx - ccx) ** 2 + (gy - ccy) ** 2) ** 0.5 > max_off * bw:
                continue
            ratio = _ring_green_ratio(mask, gx, gy, gr)
            if ratio > best:
                best = ratio
            if ratio >= min_gr:
                out.add(i)
                break
        if i not in out and best > 0.35 and tcfg.get("verbose"):
            print("[队友调试] 框%d 最佳绿环比 %.2f（门槛 %.2f）" % (i + 1, best, min_gr))
    return out


def teammate_by_icon(frame, boxes, tcfg):
    """头顶有**圆形图标**的是队友（2026-10-28 用户指出这个特征）

    比"头顶有颜色就算队友"可靠得多：颜色之外还要求形状是圆。
      ① 只在框顶上方一条带里找（图标在头顶上面一点，不在人身上）
      ② 颜色先过 HSV 色域筛（默认绿/白两段，可配多段）
      ③ 轮廓必须接近圆（circularity >= min_circularity），面积在 [min_area, max_area]
         —— 树叶、草地、UI 文字条这些"有颜色但不是圆"的统统不算队友
    返回：判定为队友的框下标集合（调用方负责按索引剔除框与关键点）。
    """
    if not tcfg.get("enabled", False) or len(boxes) == 0:
        return set()
    try:
        import cv2
    except Exception:
        return set()
    ranges = tcfg.get("hsv_ranges") or [[35, 60, 60, 95, 255, 255]]
    up = float(tcfg.get("head_up_ratio", 0.75))
    down = float(tcfg.get("head_down_ratio", 0.05))
    xexp = float(tcfg.get("x_expand_ratio", 0.3))
    min_circ = float(tcfg.get("min_circularity", 0.55))
    min_fill = float(tcfg.get("min_circle_fill", 0.78))
    min_area = float(tcfg.get("min_area", 25))
    max_area = float(tcfg.get("max_area", 6000))
    need = int(tcfg.get("min_icons", 1))
    H, W = frame.shape[:2]
    out = set()
    for i, b in enumerate(boxes):
        x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        ty0 = int(max(0, y1 - up * bh))
        ty1 = int(min(H, y1 + down * bh))
        tx0 = int(max(0, x1 - xexp * bw))
        tx1 = int(min(W, x2 + xexp * bw))
        # 头顶区太小（目标贴着画面顶端 / 远处小人）就判不了，直接放过：
        # 宁可漏过滤，也不能因为"看不清"就把人当敌人锁上，更不能凭空判成队友。
        if (ty1 - ty0) < int(tcfg.get("min_roi_h", 12)) or tx1 - tx0 < 10:
            continue
        roi = frame[ty0:ty1, tx0:tx1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for r in ranges:
            if len(r) != 6:
                continue
            mask |= cv2.inRange(hsv, np.array([r[0], r[1], r[2]], np.uint8),
                                np.array([r[3], r[4], r[5]], np.uint8))
        if not mask.any():
            continue
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        n_icon = 0
        for c in cnts:
            a = cv2.contourArea(c)
            if a < min_area or a > max_area:
                continue
            if _circularity(c) >= min_circ and _circle_fill(c) >= min_fill:
                n_icon += 1
                if n_icon >= need:
                    break
        if n_icon >= need:
            out.add(i)
    return out


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


def filter_lock_gate(boxes, kps_xy, kps_cf, cfg, frame_w, frame_h):
    """锁定时才过的一道"像人"闸（2026-10-30，用户「锁空白是粉框」）

    粉框 = 锁定框（画框颜色 255,0,255）。它落在空白处说明**锁到了一个假检出**。
    人形校验（person_check）之前因为误杀真检出被关掉了；这里换成只作用于"锁定"的
    宽松闸，绿框照常显示（能看见检出，只是不去咬它）：
      ① 宽高比落在 [0.12, 1.6]（人形竖长；地面杂物/枪身/横扁块被挡）
      ② 框内至少 min_kp(3) 个关键点置信度 ≥ kp_conf(0.35)（景物块基本没有姿态输出）
      ③ 框高 ≥ min_h_ratio(0.03) × 屏高（太小的碎块不锁）
    实测依据（_measure_锁闸.py，真实视频帧 21 个检出）：真人框内可信关键点 10~17 个、
    宽高比 0.31~1.37，所以这三条对真人几乎零误杀。
    返回 (boxes, kps, kcf, 剔除数)
    """
    lg = cfg.get("lock_gate") or {}
    if not lg.get("enabled", True) or len(boxes) == 0:
        return boxes, kps_xy, kps_cf, 0
    min_wh = float(lg.get("min_wh_ratio", 0.12))
    max_wh = float(lg.get("max_wh_ratio", 1.60))
    min_kp = int(lg.get("min_kp", 3))
    kp_conf = float(lg.get("kp_conf", 0.35))
    min_h_ratio = float(lg.get("min_h_ratio", 0.03))
    keep_b, keep_k, keep_c = [], [], []
    for i, box in enumerate(boxes):
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        wh = bw / bh
        if wh < min_wh or wh > max_wh or bh < min_h_ratio * frame_h:
            continue
        pts = kps_xy[i] if (kps_xy is not None and i < len(kps_xy)) else None
        cfs = kps_cf[i] if (kps_cf is not None and i < len(kps_cf)) else None
        if pts is not None and cfs is not None:
            ex, ey = bw * 0.10, bh * 0.10
            good = 0
            for j in range(min(len(pts), len(cfs))):
                if float(cfs[j]) >= kp_conf:
                    px, py = float(pts[j][0]), float(pts[j][1])
                    if x1 - ex <= px <= x2 + ex and y1 - ey <= py <= y2 + ey:
                        good += 1
            if good < min_kp:
                continue
        keep_b.append(box)
        if kps_xy is not None:
            keep_k.append(pts)
        if kps_cf is not None:
            keep_c.append(cfs)
    return (keep_b,
            keep_k if kps_xy is not None else kps_xy,
            keep_c if kps_cf is not None else kps_cf,
            len(boxes) - len(keep_b))


def filter_ignore_zones(boxes, kps_xy, kps_cf, cfg, frame_h, frame_w):
    """剔除"自己的手/枪/影子"这类必然误检的区域（2026-10-28，用户"会锁自己的手脚和影子"）

    第一人称下自己的手和枪固定在画面**底部中央**，模型偶尔把它和地上的影子认成人；
    锁定逻辑又不限制距离，于是准星被拽向自己的手。这里两道闸：
      1) 底部中心区：框中心落在画面底部 ignore_bottom_ratio（默认 0.26）以内，
         且横向在 中心 ±ignore_center_ratio（默认 0.30）x 屏宽 内 -> 判为自己的手/枪
      2) 超大框：框高 > max_box_height_ratio（默认 0.55）x 屏高 -> 贴脸误检，真敌人不会占半屏
    返回 (boxes, kps, kcf, 剔除数)
    """
    iz = cfg.get("ignore_zone") or {}
    if not iz.get("enabled", True) or len(boxes) == 0:
        return boxes, kps_xy, kps_cf, 0
    br = float(iz.get("ignore_bottom_ratio", 0.26))
    cr = float(iz.get("ignore_center_ratio", 0.30))
    mh = float(iz.get("max_box_height_ratio", 0.55))
    y_cut = frame_h * (1.0 - br)
    cx_mid = frame_w / 2.0
    # 底部屏蔽的横向范围：ignore_bottom_x_ratio=1.0 表示整条底部全宽都挡
    # （第一人称的枪身/护木在画面右下，只挡中心±30% 挡不住）
    _xr = iz.get("ignore_bottom_x_ratio", None)
    if _xr is None:
        half_w = cr * frame_w                 # 老配置：沿用 ±center_ratio，行为不变
    else:
        half_w = 0.5 * max(0.0, float(_xr)) * frame_w
    x_lo, x_hi = cx_mid - half_w, cx_mid + half_w
    keep = []
    for i in range(len(boxes)):
        b = boxes[i]
        x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        cxb, cyb = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if cyb >= y_cut and x_lo <= cxb <= x_hi:
            continue
        if (y2 - y1) > mh * frame_h:
            continue
        keep.append(i)
    dropped = len(boxes) - len(keep)
    if dropped == 0:
        return boxes, kps_xy, kps_cf, 0
    nb = boxes[keep] if isinstance(boxes, np.ndarray) else [boxes[i] for i in keep]
    nk = kps_xy[keep] if (kps_xy is not None and len(kps_xy) == len(boxes)) else kps_xy
    nc = kps_cf[keep] if (kps_cf is not None and len(kps_cf) == len(boxes)) else kps_cf
    return nb, nk, nc, dropped



def pick_targets(boxes_xyxy, aim_x, aim_y, cfg):
    min_h = cfg.get("min_box_height_px", 40)
    max_h = cfg.get("max_box_height_px", 100000)
    radius = cfg.get("trigger_radius_px", 110)
    hits = []
    for box in boxes_xyxy:
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        bw, bh = x2 - x1, y2 - y1
        if bh < min_h or bh > max_h:
            continue
        bx, by = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dist = ((bx - aim_x) ** 2 + (by - aim_y) ** 2) ** 0.5
        hits.append((dist, bx, by, bh, bw, x1, y1, x2, y2))
    hits.sort(key=lambda t: t[0])
    return [t for t in hits if t[0] <= radius]


# ────────────────────────────── 主程序 ──────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="YOLO 人物识别自动开火")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    ap.add_argument("--test", action="store_true", help="只识别不发键")
    ap.add_argument("--source", default=None, help="用图片/目录离线测试")
    ap.add_argument("--conf", type=float, default=None)
    ap.add_argument("--radius", type=float, default=None)
    ap.add_argument("--region-size", type=int, default=None)
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--no-random", action="store_true", help="关闭人类化随机（对照用）")
    ap.add_argument("--seconds", type=float, default=0)
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.conf is not None:
        cfg["conf"] = args.conf
    if args.radius is not None:
        cfg["trigger_radius_px"] = args.radius
    if args.region_size is not None:
        cfg["region_size"] = args.region_size

    allow_fire = not args.test
    show_window = bool(cfg.get("show_window", True)) and not args.no_window

    rnd = random.Random()
    rz = dict(cfg.get("randomize") or {})
    if args.no_random:
        rz["enabled"] = False
    rz_on = bool(rz.get("enabled", False))

    def rng_ms(pair, fallback):
        """从 [min,max] 取随机毫秒；随机关闭时用固定 fallback"""
        if not rz_on:
            return float(fallback)
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return rnd.uniform(float(pair[0]), float(pair[1]))
        return float(fallback)

    react_pair = rz.get("reaction_delay_ms", [0, 0])
    cool_pair = rz.get("cooldown_ms", [cfg.get("cooldown_ms", 150)] * 2)
    hold_pair = rz.get("key_hold_ms", [cfg.get("key_hold_ms", 30)] * 2)
    skip_chance = float(rz.get("skip_chance", 0.0)) if rz_on else 0.0
    cancel_ms = float(rz.get("target_lost_cancel_ms", 0.0)) if rz_on else 0.0
    burst_pair = rz.get("burst_shots", [cfg.get("burst_shots", 1)] * 2)

    aim_cfg = cfg.get("aim") or {}
    aim_on = bool(aim_cfg.get("enabled", False))
    aim_gain = float(aim_cfg.get("gain", 0.35))
    aim_maxstep = float(aim_cfg.get("max_step_px", 45))
    aim_dead = float(aim_cfg.get("dead_zone_px", 5))
    aim_noise = float(aim_cfg.get("noise", 0.10))
    aim_at = str(aim_cfg.get("aim_at", "center")).lower()
    aim_height = float(aim_cfg.get("aim_height_ratio",
                                    0.25 if aim_at == "head" else 0.50))
    aim_skip = float(aim_cfg.get("skip_chance", 0.0))
    # ── 远/近双模（2026-10-30，用户要求「远距高速低精度打躯干，近距高精度高速」）──
    # 参照公开实现的通行做法（RN_AI_cpp / yolov8_aimbot 一类）：按"偏差/框大小"分档，
    # 远档把目标点从头部放回躯干（关键点在小目标上最不可靠，躯干命中率换速度），
    # 近档回到头部并用满预算；档位之间加滞回，避免在阈值附近来回换点。
    _ms = aim_cfg.get("mode_split") or {}
    aim_split_on = bool(_ms.get("enabled", True))
    _ms_far = float(_ms.get("far_px", 260))
    _ms_hyst = max(0.0, float(_ms.get("hyst_px", 40)))
    aim_split_far_part = str(_ms.get("far_part", "chest")).lower()
    aim_split_near_part = str(_ms.get("near_part", "head")).lower()
    aim_far_h = float(_ms.get("far_height_ratio", 0.42))
    aim_near_h = float(_ms.get("near_height_ratio", aim_height))
    aim_far_step_cap = float(_ms.get("far_step_cap", 0.9))
    aim_mode = "near"      # 首答一律从精确档起步（宁可慢一帧，不要一开始就打偏）
    aim_min_h = float(aim_cfg.get("min_box_height_px", 40))
    aim_overshoot = float(aim_cfg.get("overshoot_chance", 0.0))
    aim_settle = float(aim_cfg.get("settle_jitter_px", 0.0))
    aim_adaptive = bool(aim_cfg.get("adaptive", True))
    _ppc = aim_cfg.get("pixel_per_count", [1.0, 1.0])
    try:
        aim_k = [float(_ppc[0]), float(_ppc[1])]
    except Exception:
        aim_k = [1.0, 1.0]
    aim_prev_sent = None
    aim_prev_err = None
    aim_obs_prev = None       # 上一帧偏差（算"实测位移/计划位移"过冲比用）
    aim_ovr_sm = None         # 过冲比 EMA（>1.05 = 实际走得比计划远 = 拉过头）
    aim_k_samples = 0         # 标定样本数（先快后稳）
    aim_wm = (0.0, 0.0)       # "目标自己的世界位移速率"估计（只在没发移动的帧上观测）
    aim_wm_t = 0.0            # 上一次更新 aim_wm 的时刻
    aim_prev_sgn = 0          # 上一帧偏差符号（用来识别"冲过目标了"）
    aim_damp_until = 0.0      # 过冲抑制截止时刻（冲过去了就临时降预算）
    aim_last_tc = None
    aim_max_dist = float(aim_cfg.get("max_dist_px", 350))
    aim_budget_ratio = float(aim_cfg.get("budget_ratio", 0.55))
    aim_budget_ratio_init = float(aim_cfg.get("budget_ratio_initial", 0.95))
    aim_burst_s = float(aim_cfg.get("first_burst_ms", 160)) / 1000.0
    aim_new_until = 0.0       # 新目标首答脉冲截止时刻
    aim_lock_c = None         # 上一帧锁定框 (中心x, 中心y, 框高)（换人判定用）
    aim_prev_bh = 0.0         # 上一帧锁定框高度（识别开镜/收镜的视野缩放）
    aim_fov_until = 0.0       # 视野缩放稳定期截止（期间只允许微量修正）
    aim_ads_prev = False      # 上一帧开镜状态（取上升沿）
    aim_ads_until = 0.0       # 开镜宽限期截止（视野未变时的兜底）
    aim_k_ads = [aim_k[0], aim_k[1]]   # 开镜灵敏度下的 k（与腰射分开标定）
    # ── 开火态灵敏度（2026-10-30，用户「开火状态鼠标灵敏度下降考虑了吗」）──
    # 很多游戏开火瞬间会降灵敏度（防抖），这时"同样一段鼠标计数换算出的像素"变少；
    # 若和腰射/开镜共用一套 k，闭环增益就偏小 → 打起来准星跟不上、越打越磨。
    # 所以单列第三套系数：按 LButton 判定开火态，松手后再宽限 fire_grace_ms。
    aim_fire_en = bool(aim_cfg.get("fire_sense", True))
    aim_fire_vk = _to_vk(aim_cfg.get("fire_key", "LButton"))
    aim_fire_grace = float(aim_cfg.get("fire_grace_ms", 120)) / 1000.0
    _fks = float(aim_cfg.get("fire_k_scale", 0.85))
    aim_k_fire = [max(0.05, aim_k[0] * _fks), max(0.05, aim_k[1] * _fks)]
    aim_fire_until = 0.0
    aim_fire_prev = False
    aim_lead_s = float(aim_cfg.get("lead_ms", 0)) / 1000.0
    aim_lead_max = float(aim_cfg.get("lead_max_px", 45))
    aim_tau = float(aim_cfg.get("follow_tau", 0.045))
    aim_tau_floor = float(aim_cfg.get("follow_tau_floor", 0.04))
    aim_abs_cap = float(aim_cfg.get("abs_move_cap", 1.0))
    aim_jump_cap = float(aim_cfg.get("jump_cap_px", 0))
    # ── 近距直取（2026-10-30，修「枪口贴到头附近时自瞄转得很慢」）──
    # 旧行为：偏差掉到 far_px(460) 以下后，预算按平方曲线一路降到 near_ratio(0.55)，
    # **而且一直降到 0px** —— 于是"最后几十像素"每帧只肯走 55%，贴得越近越钝，
    # 表现为准星到了头边就开始"磨"，要 5~8 个检测周期才贴上。
    # 现在：偏差 ≤ near_boost_px 时把预算抬到 near_boost_ratio（默认 1.0 = 本帧走完），
    # 跟随线程的绝对闸同时放宽到 abs_move_cap_near。不会冲过头 ——
    # 底下"单调逼近 + 剩余偏差实时扣减 + 死区"三层保证任何一轴都不越过目标。
    aim_near_boost_px = float(aim_cfg.get("near_boost_px", 140.0))
    aim_near_boost_ratio = float(aim_cfg.get("near_boost_ratio", 1.0))
    aim_abs_cap_near = float(aim_cfg.get("abs_move_cap_near", 0.95))
    # 跟随线程的"不过目标"钳位：true=正确钳位（2026-10-30 修），false=旧的（反写）行为，
    # 只留作一键回退对比用。
    aim_follow_clamp = bool(aim_cfg.get("follow_clamp", True))
    # 跳变限速默认关（0）：实测它与"每周期预算"重合（都是 ~90px/帧），开了只会让远距离
    # 首答更慢；真正的防过冲靠"单调逼近 + 每周期绝对闸"。想压首答猛拉可以调 60~90。
    aim_hold_s = float(aim_cfg.get("hold_track_ms", 250)) / 1000.0
    aim_vdead = float(aim_cfg.get("velocity_dead_px_s", 35))
    pose_point = str(aim_cfg.get("pose_point", "nose")).lower()
    aim_pose_part = None      # 当前粘住的部位：head / chest / nose（逐帧粘住，不来回跳）
    aim_pose_lost = 0         # 当前部位连续丢了多少帧
    aim_px_sm = None          # 平滑后的瞄准点（关键点单帧抖动 3~8px，平滑后落点稳）
    aim_cc_last = None        # 上一帧的"只增不减计数器"快照（标定用）
    aim_last_tcp = None       # 上一帧的瞄准点绝对坐标（丢目标保持时用）
    aim_lost_t = 0.0          # 上一次"有锁定"的时刻
    aim_vx = 0.0
    aim_vy = 0.0
    aim_lx = 0.0          # 提前量（平滑后的偏移量，不是每帧直接用瞬时速度乘系数）
    aim_ly = 0.0
    aim_spos = None       # 去抖后的**瞄准点**位置（标定用）
    aim_bpos = None       # ★2026-10-31★ 去抖后的**锁定框中心**：速度/提前量的真源（见 vel_from_box）
    aim_maxv = float(aim_cfg.get("vel_max_px_s", 1800.0))   # 目标速度硬上限（像素/秒）
    aim_last_vt = 0.0     # 上一次"速度采样"的时刻（速度按长窗口更新）
    aim_last_t = None
    move_test_hotkey_vk = int(cfg.get("move_test_hotkey_vk", 0x74))  # F5
    prev_move_test = False
    aim_st = {"lock": threading.Lock(), "rnd": rnd, "ex": 0.0, "ey": 0.0, "valid": False,
              "budget": 0.0, "k": [aim_k[0], aim_k[1]], "gain": aim_gain,
              "dead": aim_dead, "noise": aim_noise, "maxstep": aim_maxstep,
              "tau": aim_tau, "step_cap": float(aim_cfg.get("step_cap", 0.18)), "sync_t": 0.0, "cmd": [0.0, 0.0], "cc": [0.0, 0.0],
              "tx": 0.0, "ty": 0.0, "obs": None, "tau_floor": aim_tau_floor,
              "abs_cap": aim_abs_cap, "hold": False, "jump_cap_px": aim_jump_cap,
              "follow_clamp": aim_follow_clamp,
              "det_t": 0.0, "vx": 0.0, "vy": 0.0}
    if aim_on:
        start_aim_follow(aim_st, aim_cfg.get("follow_interval_ms", 5))
    _ch = aim_cfg.get("crosshair_offset_px", [0, 0])
    try:
        crosshair_dx = float(_ch[0])
        crosshair_dy = float(_ch[1])
    except Exception:
        crosshair_dx = crosshair_dy = 0.0
    aim_invert_x = bool(aim_cfg.get("invert_x", False))
    aim_invert_y = bool(aim_cfg.get("invert_y", False))
    aim_log = bool(aim_cfg.get("log", False))
    last_aimdbg = 0.0
    fire_req_aim = bool(cfg.get("fire_require_aim_key", False))
    trigger_radius = float(cfg.get("trigger_radius_px", 110))
    min_box_height = float(cfg.get("min_box_height_px", 55))
    fire_hyst_ms = float(cfg.get("fire_hysteresis_ms", 250))
    fire_hold_t = 0.0
    perf_acc = [0.0, 0.0, 0.0]
    perf_n = 0
    perf_last = 0.0
    _pt_a = _pt_b = _pt_c = 0.0
    _lk = cfg.get("lock") or {}
    lock_on = bool(_lk.get("enabled", True))
    # ── 队友识别（头顶圆形图标）（2026-10-28）──
    _tic_cfg = cfg.get("team_icon") or {}
    _tic_on = bool(_tic_cfg.get("enabled", False))
    _tic_skip_n = 0
    _last_ticdbg = 0.0
    if _tic_on:
        print("[信息] 队友识别: 开（模式 %s，色域 %s，绿环比>=%.2f）"
              % (_tic_cfg.get("mode", "ring"), _tic_cfg.get("hsv_ranges"),
                 float(_tic_cfg.get("min_green_ratio", 0.65))))
    # ── 反误锁：底部中心区 + 超大框 + 锁定半径（2026-10-28）──
    _iz_cfg = cfg.get("ignore_zone") or {}
    _iz_on = bool(_iz_cfg.get("enabled", True))
    _iz_drop_n = 0
    _last_izdbg = 0.0
    _pc_drop_n = 0        # 人形校验剔除计数
    _last_pcdbg = 0.0
    _lg_drop_n = 0        # 锁定闸（不像人 → 不锁）计数
    _last_lgdbg = 0.0
    lock_radius_px = float(_lk.get("radius_px", 0) or 0)
    if _iz_on:
        _xr_show = _iz_cfg.get("ignore_bottom_x_ratio", None)
        _xr_txt = ("中心±%.0f%%屏宽" % (100.0 * float(_iz_cfg.get("ignore_center_ratio", 0.30)))
                   if _xr_show is None else "全宽%.0f%%" % (100.0 * float(_xr_show)))
        print("[信息] 反误锁: 开（屏蔽底部 %.0f%% 高 × %s / 超大框>%.0f%%屏高 / 锁定半径 %.0fpx）"
              % (100.0 * float(_iz_cfg.get("ignore_bottom_ratio", 0.26)),
                 _xr_txt,
                 100.0 * float(_iz_cfg.get("max_box_height_ratio", 0.55)),
                 lock_radius_px))

    lock_hold_s = float(_lk.get("hold_ms", 450)) / 1000.0
    lock_match_px = float(_lk.get("match_px", 90))
    lock_sticky_s = float(_lk.get("sticky_ms", 500)) / 1000.0
    lock_min_h = float(_lk.get("min_box_height_px", 40))
    lock_st = {"box": None, "t": 0.0, "start": 0.0,
               # 交战期不换人（2026-10-30）：放宽匹配半径 / 丢框多容忍多久 / 换人半径上限
               "engage": False,
               "engage_relax": float(_lk.get("engage_relax", 2.0)),
               "engage_extra_gap_s": float(_lk.get("engage_extra_gap_s", 0.40)),
               "engage_switch_max_px": float(_lk.get("engage_switch_max_px", 220.0)),
               # 换人闸（2026-10-30 第二轮）：迟滞 / 冷却 / 连续确认 / 速度外推增益
               "switch_hysteresis": float(_lk.get("switch_hysteresis", 0.7)),
               "switch_cooldown_ms": float(_lk.get("switch_cooldown_ms", 400)),
               "switch_confirm_frames": int(_lk.get("switch_confirm_frames", 2)),
               "pred_gain": float(_lk.get("pred_gain", 0.5)),
               "confirm_frames": int(_lk.get("confirm_frames", 1)),
               "never_switch": bool(_lk.get("never_switch", False)),
               # ★2026-11-01★ commit flick 窗口（换人后甩枪期免打扰）+ 冷却随机抖动
               "switch_commit_ms": float(_lk.get("switch_commit_ms", 180)),
               "switch_cooldown_jitter": float(_lk.get("switch_cooldown_jitter", 0.2)),
               "v": [0.0, 0.0], "switch_t": 0.0, "sw_pend": {},
               "commit_until": 0.0, "cd_next": None, "lost_t": 0.0,
               # ★2026-11-01 用户「新目标接管变成 20 帧」★
               "acquire_hold_ms": float(_lk.get("acquire_hold_ms", 0.0))}

    # ── ★2026-11-01 从 kalman 分支移植（用户「把三样东西移植进 2.0 主线」）★ ──
    # 三样各自独立开关，文件缺失或异常时自动退回原逻辑，不影响现有两档：
    #   hitbox   命中盒分区：用 17 关键点按框高重建 头/胸/腹，按 距离×可见性×优先级 打分
    #   recoil   后坐力曲线：按住开火键时按"前段陡→平台缓→左右微摆"下压
    #   tracker  卡尔曼 coast 外推：丢检后按速度继续推，目标被遮挡也不立刻丢锁
    _hitbox = None
    _hb_state = {}
    _hb_cfg = cfg.get("hitbox") or {}
    if bool(_hb_cfg.get("enabled", False)):
        try:
            from hitbox import Hitbox
            _hitbox = Hitbox(_hb_cfg)
            print("[信息] 命中盒: 开（头/胸/腹，近框 %.0fpx 起偏头）"
                  % float(_hb_cfg.get("near_box_h", 260.0)))
        except Exception as _he:
            _hitbox = None
            print("[警告] 命中盒不可用，沿用 pose_point：%s" % _he)

    _recoil = None
    _rcl_press = None
    _rcl_acc = [0.0, 0.0]
    _rcl_last = 0.0
    _rcl_key = "LButton"
    _rcl_vk = 0
    _rcl_cfg = cfg.get("recoil") or {}
    if bool(_rcl_cfg.get("enabled", False)):
        try:
            from recoil import RecoilComp
            _recoil = RecoilComp(_rcl_cfg)
            _rcl_key = str(_rcl_cfg.get("key", "LButton"))
            _rcl_vk = _to_vk(_rcl_key)
            print("[信息] 后坐力补偿: 开（键 %s / 前段 %.0fpx/s → 平台 %.0fpx/s @%.2fs / 倍率 %.2f）"
                  % (_rcl_key, _recoil.peak1, _recoil.peak2, _recoil.switch_s, _recoil.scale))
        except Exception as _re:
            _recoil = None
            print("[警告] 后坐力补偿不可用：%s" % _re)

    _tracker = None
    _trk_sel = {}
    _trk_dbg = 0.0
    _tcfg = cfg.get("tracker") or {}
    if bool(_tcfg.get("enabled", False)):
        try:
            from tracker import Tracker
            _tracker = Tracker(_tcfg)
            print("[信息] 卡尔曼跟踪: 开（coast %.0fms / 门限 %.0fpx / 确认 %d 帧）"
                  % (float(_tcfg.get("coast_ms", 300.0)),
                     float(_tcfg.get("gate_px", 120.0)),
                     int(_tcfg.get("confirm_hits", 1))))
        except Exception as _te:
            _tracker = None
            print("[警告] 卡尔曼跟踪不可用，退回 pick_locked：%s" % _te)

    _rz = cfg.get("randomize") or {}
    max_hold_ms = float(_rz.get("max_hold_ms", 0) or 0)
    hold_block_pair = _rz.get("hold_block_ms", [150, 350])
    aim_hot_vk = int(cfg.get("aim_hotkey_vk", 0x75))  # F6
    aim_require_key = bool(aim_cfg.get("require_aim_key", False))
    aim_key_vk = _to_vk(aim_cfg.get("aim_key", "RButton"))

    print("=" * 66)
    print(" YOLO 人物识别自动开火")
    print(f" 模型 {cfg.get('model')} | 设备 {cfg.get('device')} | 置信度 {cfg.get('conf')} | 触发半径 {cfg.get('trigger_radius_px')}px")
    print(f" 开火键 {cfg.get('trigger_key')} | 基准冷却 {cfg.get('cooldown_ms')}ms | 基准按压 {cfg.get('key_hold_ms')}ms")
    if rz_on:
        print(f" 人类化随机: 开 | 反应延迟 {react_pair}ms | 冷却 {cool_pair}ms | 按压 {hold_pair}ms | "
              f"跳过 {skip_chance * 100:.0f}% | 闪失取消 {cancel_ms:.0f}ms")
    else:
        print(" 人类化随机: 关（固定时序）")
    print(" 自动瞄准:", "开" if aim_on else "关", "(F6 切换) | 增益", aim_gain,
          "| 限幅", str(aim_maxstep) + "px", "| 死区", str(aim_dead) + "px", "| 瞄准点", aim_at)
    print(" 模式:", "【测试·不发键】" if not allow_fire else "【实战·会自动开火】")
    print("=" * 66)

    try:
        from ultralytics import YOLO
    except ImportError:
        print("[错误] 缺少 ultralytics：python -m pip install ultralytics")
        return 1
    try:
        import torch
    except ImportError:
        print("[错误] 缺少 torch：python -m pip install torch")
        return 1

    model_path = cfg.get("model", "yolo11n.pt")
    if not os.path.isabs(model_path) and not os.path.exists(model_path):
        model_path = os.path.join(os.path.dirname(os.path.abspath(args.config)), model_path)
    print("[信息] 加载模型:", model_path)
    # ── 可选：自写的 pose TensorRT 后端（绕开 ultralytics，速度快 4~6 倍）──
    pose_backend = None
    pose_crop = None
    _pe = str(cfg.get("pose_engine") or "").strip()
    if _pe:
        try:
            _here = os.path.dirname(os.path.abspath(__file__))
            if _here not in sys.path:
                sys.path.insert(0, _here)
            from pose_trt import PoseTRT
            _pep = _pe if os.path.isabs(_pe) else os.path.join(_here, _pe)
            pose_backend = PoseTRT(_pep, imgsz=int(cfg.get("imgsz", 512)),
                                   conf=float(cfg.get("conf", 0.35)),
                                   iou=float(cfg.get("iou", 0.45)))
            print("[信息] 姿态后端: 自写 TensorRT 推理 |", os.path.basename(_pep))
            # 锁定后用的"裁剪精算"后端（小图 = 便宜 + 分辨率更高）
            _ce = str(cfg.get("pose_engine_crop") or "").strip()
            if _ce:
                _cep = _ce if os.path.isabs(_ce) else os.path.join(_here, _ce)
                _csz = int(cfg.get("crop_side", 256))
                try:
                    pose_crop = PoseTRT(_cep, imgsz=_csz,
                                        conf=float(cfg.get("conf", 0.35)),
                                        iou=float(cfg.get("iou", 0.45)))
                    print("[信息] 裁剪精算后端: %s（边 %dpx）" % (os.path.basename(_cep), _csz))
                except Exception as _exc2:
                    pose_crop = None
                    print("[警告] 裁剪后端不可用，只用全图:", type(_exc2).__name__, str(_exc2)[:100])
        except Exception as _exc:
            print("[警告] 自写 pose 后端不可用，回退 ultralytics:",
                  type(_exc).__name__, str(_exc)[:140])
            pose_backend = None

    # ── 只有一套 engine 时，裁剪也复用它（显存零额外成本，实测这是唯一可行的路）──
    if (pose_crop is None and pose_backend is not None
            and bool((cfg.get("crop_track") or {}).get("enabled", False))):
        pose_crop = pose_backend
        print("[信息] 裁剪精算复用主后端（不额外占显存）")

    # pose 模型必须显式声明任务类型，否则 ultralytics 可能按 detect 解析
    _task = "pose" if "pose" in os.path.basename(str(model_path)).lower() else "detect"
    if pose_backend is None:
        model = YOLO(model_path, task=_task)
        print(f"[信息] 模型任务: {_task}")
    else:
        model = None

    device = str(cfg.get("device", "0"))
    if device != "cpu" and not torch.cuda.is_available():
        print("[警告] CUDA 不可用 -> 改用 CPU（会明显变慢）")
        device = "cpu"
    print("[信息] 推理设备:", device, "| torch", torch.__version__,
          "| CUDA", torch.version.cuda if torch.cuda.is_available() else "-")

    # GPU 优化：cuDNN 自动调优（CPU 时关闭）
    if device != "cpu":
        try:
            torch.backends.cudnn.benchmark = True
        except Exception:
            pass
    print("[信息] GPU 加速:", "已启用 cuDNN benchmark" if device != "cpu" else "CPU 模式")

    classes = cfg.get("classes") or None

    # ── 离线图片 ──
    if args.source:
        paths = []
        if os.path.isdir(args.source):
            for name in sorted(os.listdir(args.source)):
                if os.path.splitext(name)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                    paths.append(os.path.join(args.source, name))
        else:
            paths.append(args.source)
        for p in paths:
            try:
                img = imread_u(p)
            except Exception as exc:
                print("[跳过]", p, exc)
                continue
            # ── 2026-10-30 修：自写 TensorRT 后端下 model 是 None ──
            # 旧写法无条件 model.predict(...) → 离线测试直接崩：
            #   AttributeError: 'NoneType' object has no attribute 'predict'
            # 所以 --source 在装了 engine 的机器上从来跑不通（本轮视频取证就是被它挡住的）。
            # 现在优先走与运行时同一条推理路径 pose_backend.infer。
            if pose_backend is not None:
                _pb, _pk, _pc = pose_backend.infer(img)
                if len(_pb):
                    _boxes = np.asarray(_pb, dtype=np.float32).reshape(-1, 4)
                else:
                    _boxes = np.zeros((0, 4), np.float32)
            elif model is not None:
                _res = model.predict(img, imgsz=cfg.get("imgsz", 640), conf=cfg.get("conf", 0.45),
                                     iou=cfg.get("iou", 0.45), classes=classes,
                                     device=device, verbose=False)[0]
                _boxes = (_res.boxes.xyxy.cpu().numpy()
                          if (_res.boxes is not None and len(_res.boxes))
                          else np.zeros((0, 4), np.float32))
            else:
                print("[错误] 没有可用的推理后端（engine 与 .pt 都加载失败）")
                return 1
            n = len(_boxes)
            # imread_u 读回来的数组可能是只读/非连续（tifffile 风格），
            # cv2.rectangle 会直接报 "Layout of the output array img is incompatible"。
            # 画框前先拷一份连续内存。
            img = np.ascontiguousarray(img).copy()
            print(f"[图片] {os.path.basename(p)}  尺寸 {img.shape[1]}x{img.shape[0]}  检测到 {n} 个 person")
            for b in _boxes:
                x1, y1, x2, y2 = [int(v) for v in b[:4]]
                print(f"    框 ({x1},{y1})-({x2},{y2}) 高 {y2 - y1}px")
                try:
                    import cv2
                    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                except ImportError:
                    img[y1:y1 + 2, x1:x2] = (0, 255, 0)
                    img[y2 - 2:y2, x1:x2] = (0, 255, 0)
                    img[y1:y2, x1:x1 + 2] = (0, 255, 0)
                    img[y1:y2, x2 - 2:x2] = (0, 255, 0)
            out = os.path.splitext(p)[0] + "_detected.png"
            imwrite_u(out, img)
            print("    已保存:", out)
        return 0

    # ── 实时屏幕 ──
    grabber = ScreenGrabber(cfg)
    if grabber.backend is None:
        print("[错误] 没有可用的抓屏后端（试过 dxcam / bettercam / mss）")
        print("       解决：python -m pip install mss")
        return 1
    print(f"[信息] 抓屏后端: {grabber.backend}  屏幕 {grabber.width}x{grabber.height}")
    print(f"[信息] 抓屏区域 (l,t,r,b) = {grabber.region}  尺寸 "
          f"{grabber.region[2] - grabber.region[0]}x{grabber.region[3] - grabber.region[1]}")

    fire_q = queue.Queue()

    def fire_worker():
        while True:
            item = fire_q.get()
            if item is None:
                return
            hold, shots = item
            for i in range(shots):
                try:
                    press_key(cfg.get("trigger_key", "8"), hold)
                except Exception as exc:
                    print("[警告] 发键失败:", exc)
                if i + 1 < shots:
                    time.sleep(rnd.uniform(0.04, 0.10))

    if allow_fire:
        threading.Thread(target=fire_worker, daemon=True).start()

    toggle_vk = int(cfg.get("toggle_hotkey_vk", 120))
    test_fire_vk = int(cfg.get("test_fire_hotkey_vk", 0x76))  # F7
    enabled = bool(cfg.get("start_enabled", False))
    fire_mode = str(cfg.get("fire_mode", "tap")).lower()
    tkey = cfg.get("trigger_key", "8")

    release_pair = rz.get("hold_release_delay_ms", [40, 120])
    prev_down = False
    prev_test = False
    last_target_t = 0.0
    pending_at = 0.0
    holding = False
    hold_arm_at = 0.0
    release_at = 0.0
    hold_start_t = 0.0
    hold_block_until = 0.0
    hold_enter_t = 0.0          # 目标第一次进命中圈的时刻（用来量"进圈→真的按住"的延迟）
    have_pending = False
    need_reaction = False
    prev_target_present = False
    last_fire = 0.0
    next_cool = 0.0
    last_log = 0.0
    _vis_n = 0          # 显示降频计数：每 show_every 帧才标注+刷新一次窗口
    frames = 0
    t0 = time.time()
    quit_flag = False
    stat_fire = 0
    stat_skip = 0
    stat_aim = 0
    aim_q = queue.Queue(maxsize=max(1, int((cfg.get('aim') or {}).get('queue_max', 2))))
    _smooth = (cfg.get('aim') or {}).get('smooth') or {}
    if aim_on and bool(_smooth.get('enabled', True)):
        start_aim_worker(aim_q, _smooth.get('steps', 4), _smooth.get('interval_ms', 3))
    last_aim_log = 0.0
    prev_aim_hot = False
    stat_cancel = 0

    try:
        import cv2
        have_cv2 = True
    except ImportError:
        have_cv2 = False
        if show_window:
            print("[信息] 没有 opencv，改用 PIL 预览窗口（较慢）")
            from PIL import Image

    print(f"[信息] 热键 {cfg.get('toggle_hotkey_name', 'F9')} 开关；当前: {'开' if enabled else '关'}")
    preview = None
    if show_window and have_cv2:
        # 窗口在显示线程里创建（HighGUI 有线程归属，跨线程 imshow 不显示）
        print("[信息] 检测窗口由显示线程创建并置顶（可拖动）；按 q 关闭")

    # ── 裁剪精算状态 ──
    _cc_cfg = cfg.get("crop_track") or {}
    _crop_on = bool(_cc_cfg.get("enabled", False)) and pose_crop is not None
    _crop_side = int(cfg.get("crop_side") or cfg.get("imgsz", 512))
    _crop_every = max(1, int(_cc_cfg.get("full_every", 3)))   # 每 N 帧回一次全图找新目标
    # ── 隔帧推理（2026-10-28）──
    # 游戏把 GPU 吃满时（实测 96%、显存 6.7/8.2G），每帧都提交推理会互相排队，
    # 单帧从 8ms 涨到 100~400ms。隔帧推理把提交量减半，每个请求反而更容易抢到时间片，
    # 检测率不一定降，但"卡一下跳一下"会明显减少。detect_every=1 即关闭。
    _det_every = max(1, int(cfg.get("detect_every", 1)))
    _det_skip = 0
    _det_last_boxes = None
    _det_last_kps = None
    _det_last_kpf = None
    _det_last_shape = None
    _det_reuse_n = 0
    _boxes_t = 0.0            # 当前 boxes 对应的抓帧时刻（粉框外推用）
    _lock_draw_n = 0          # 粉框实际画了几帧（诊断）
    _lock_dbg_last = 0.0
    _lead_clip_n = 0          # 提前量把瞄准点推出框、被夹回的次数（诊断）
    _lead_clip_last = 0.0
    _stale_n = 0              # 锁定框失去当帧支撑、被清掉的次数（诊断）
    _stale_dbg = 0.0
    if _det_every > 1:
        print("[信息] 隔帧推理: 每 %d 帧推理一次（省 GPU，GPU 满载时更稳）" % _det_every)
    _crop_skip = 0            # 还差几帧回全图
    _crop_cx = _crop_cy = None   # 下一帧裁剪窗口的中心（上一帧目标位置）
    _crop_hits = 0
    _crop_miss = 0
    if _crop_on:
        print("[信息] 裁剪精算: 开（锁定后 %dpx 小图，每 %d 帧回一次全图）"
              % (_crop_side, _crop_every))
    frame_src = None
    _last_fseq = None
    if grabber.backend in ("dxcam", "bettercam"):
        frame_src = LatestFrame(grabber)
        print("[信息] 抓屏流水线: 独立线程 + 最新帧信箱（抓屏与推理并行）")

    while not quit_flag:
        now = time.time()

        # ── ★2026-11-01 移植 kalman 后坐力曲线（recoil.enabled）★ ──
        # 按住开火键期间按"前段陡→平台缓→左右微摆"积分出该下压多少像素，
        # 后面直接叠到瞄准点 ty 上（正向 y 是屏幕向下 = 枪口上跳的方向）。
        # 松手立刻清零，下次开火重新走前段曲线。
        if _recoil is not None:
            _held_r = bool(is_key_down(_rcl_vk))
            if _held_r and _rcl_press is None:
                _rcl_press = now
            elif not _held_r:
                _rcl_press = None
            if _held_r:
                _dt_r = max(0.001, min(0.05, now - _rcl_last))
                _rvx, _rvy = _recoil.rates(True, now, _rcl_press)
                _rcl_acc[0] += _rvx * _dt_r
                _rcl_acc[1] += _rvy * _dt_r
            else:
                _rcl_acc[0] = 0.0
                _rcl_acc[1] = 0.0
            _rcl_last = now

        now_down = is_key_down(toggle_vk)
        if now_down and not prev_down:
            enabled = not enabled
            have_pending = False
            print("[热键] 自动开火 ->", "开" if enabled else "关")
        prev_down = now_down

        # F7 = 手动发一次开火键，用来单独验证"发键通路"（与识别/触发无关）
        td = is_key_down(test_fire_vk)
        if td and not prev_test:
            ok = press_key(cfg.get("trigger_key", "8"), float(cfg.get("key_hold_ms", 30)))
            print(f"[测试发键] 手动按下 {cfg.get('trigger_key')} -> "
                  f"{'SendInput 成功（游戏里应开枪）' if ok else 'SendInput 失败！'}")
        prev_test = td

        # F6 = 开/关自动瞄准
        ad = is_key_down(aim_hot_vk)
        if ad and not prev_aim_hot:
            aim_on = not aim_on
            print("[热键] 自动瞄准 ->", "开" if aim_on else "关")
        prev_aim_hot = ad

        # F5 = 测试鼠标移动通路：右移 80 计数再移回来
        #   游戏里按下后：准星/视角动了 -> 移动通路 OK；完全不动 -> 游戏拦了合成移动
        mt = is_key_down(move_test_hotkey_vk)
        if mt and not prev_move_test:
            o1 = mouse_move(80, 0)
            time.sleep(0.05)
            o2 = mouse_move(-80, 0)
            print(f"[测试] mouse_move 右 80 -> {o1} | 左 80 -> {o2}"
                  f"  （游戏里视角若纹丝不动，说明合成移动被游戏丢弃：请确认用管理员启动）")
        prev_move_test = mt

        _pt_a = time.perf_counter()
        if frame_src is not None:
            frame, _fseq = frame_src.latest(after_seq=_last_fseq)
            if frame is None or _fseq == _last_fseq:
                time.sleep(0.0004)      # 没有新帧：等下一帧，不重复推理同一张
                continue
            _last_fseq = _fseq
        else:
            frame = grabber.grab()
            if frame is None:
                time.sleep(0.001)
                continue

        # dxcam 给的就是连续 BGR：只有不连续/通道数不对时才拷贝（原来每帧白拷 1.9MB）
        if (not frame.flags["C_CONTIGUOUS"]) or frame.ndim != 3 or frame.shape[2] != 3:
            frame = np.ascontiguousarray(frame[:, :, :3])
        _pt_b = time.perf_counter()
        _frame_t = time.time()          # ★2026-10-31★ 本帧抓取时刻（粉框外推的基准，别用 aim 时刻）
        _reuse_now = False
        if (_det_every > 1 and _det_skip > 0 and _det_last_boxes is not None
                and _det_last_shape == frame.shape[:2]):
            # 这一帧不推理：复用上一次的框/关键点（坐标基准相同才敢复用，shape 变了就重推）
            boxes = _det_last_boxes
            kps_xy = _det_last_kps
            kps_cf = _det_last_kpf
            _det_skip -= 1
            _reuse_now = True
            _det_reuse_n += 1
        if not _reuse_now and pose_backend is not None:
            _fh0, _fw0 = frame.shape[:2]
            _done = False
            if (_crop_on and _crop_skip <= 0 and lock_st.get("box") is not None
                    and (lock_st["box"][3] - lock_st["box"][1]) <= _crop_side * 0.95):
                _lbx = lock_st["box"]
                _pkx = _crop_cx if _crop_cx is not None else (_lbx[0] + _lbx[2]) / 2.0
                _pky = _crop_cy if _crop_cy is not None else (_lbx[1] + _lbx[3]) / 2.0
                _x0 = int(round(_pkx - _crop_side / 2.0))
                _y0 = int(round(_pky - _crop_side / 2.0))
                _cb, _ck, _cc, _used = pose_crop.infer_crop(frame, _x0, _y0, _crop_side)
                if len(_cb):
                    boxes = _cb
                    kps_xy = _ck[:, :, :2] if _ck is not None and len(_ck) else None
                    kps_cf = _ck[:, :, 2] if _ck is not None and len(_ck) else None
                    _done = True
                    _crop_hits += 1
                    _crop_skip = _crop_every
                    _crop_win = (_used[0], _used[1], _used[0] + _used[2], _used[1] + _used[2])
                else:
                    _crop_miss += 1      # 目标跑出小窗 -> 当帧立刻回退全图
                    _crop_win = None
            else:
                _crop_win = None
            if not _done:
                _pb, _pk, _pc = pose_backend.infer(frame)
                boxes = _pb if len(_pb) else np.zeros((0, 4), np.float32)
                kps_xy = _pk[:, :, :2] if len(_pk) else None
                kps_cf = _pk[:, :, 2] if len(_pk) else None
                _crop_cx = _crop_cy = None    # 回全图后重新用小窗对准
            if _crop_skip > 0:
                _crop_skip -= 1
        elif not _reuse_now:
            res = model.predict(frame, imgsz=cfg.get("imgsz", 640), conf=cfg.get("conf", 0.45),
                                iou=cfg.get("iou", 0.45), classes=classes,
                                device=device, verbose=False)[0]
            boxes = res.boxes.xyxy.cpu().numpy() if res.boxes is not None and len(res.boxes) else []
            kps_xy = None
            kps_cf = None
            try:
                if getattr(model, "task", "") == "pose" and res.keypoints is not None:
                    kps_xy = res.keypoints.xy.cpu().numpy()
                    kps_cf = res.keypoints.conf.cpu().numpy() if res.keypoints.conf is not None else None
            except Exception:
                kps_xy = None
                kps_cf = None

        if not _reuse_now:
            _det_last_boxes = boxes
            _det_last_kps = kps_xy
            _det_last_kpf = kps_cf
            _det_last_shape = frame.shape[:2]
            _det_skip = _det_every - 1
            _boxes_t = _frame_t        # 当前这套 boxes 属于这一帧
        # （复用帧沿用上一次的 _boxes_t：boxes 是旧的，别拿新帧时刻当它的时间戳）

        _pt_c = time.perf_counter()
        # 准星位置（屏幕中心 + 可调偏移）—— 替换推理段时被误删过，务必保留
        fh, fw = frame.shape[:2]
        aim_x = fw / 2.0 + crosshair_dx
        aim_y = fh / 2.0 + float(cfg.get("aim_point_offset_y", 0)) + crosshair_dy

        # ── 头顶圆形图标 = 队友，不锁（2026-10-28）──
        if _tic_on and len(boxes):
            _tidx = (teammate_by_ring(frame, boxes, _tic_cfg)
                     if str(_tic_cfg.get("mode", "ring")).lower() == "ring"
                     else teammate_by_icon(frame, boxes, _tic_cfg))
            if _tidx:
                _keep = [i for i in range(len(boxes)) if i not in _tidx]
                boxes = boxes[_keep] if isinstance(boxes, np.ndarray) else [boxes[i] for i in _keep]
                if kps_xy is not None and len(kps_xy) == len(_keep) + len(_tidx):
                    kps_xy = kps_xy[_keep]
                    if kps_cf is not None:
                        kps_cf = kps_cf[_keep]
                _tic_skip_n += len(_tidx)
                if aim_log and now - _last_ticdbg > 2.0:
                    _last_ticdbg = now
                    print("[队友] 框内绿色圆环 -> 跳过 %d 人（累计 %d）" % (len(_tidx), _tic_skip_n))

        # ── 剔除"自己的手/枪/影子"（底部中心区 + 超大框）（2026-10-28）──
        if _iz_on and len(boxes):
            boxes, kps_xy, kps_cf, _iz_n = filter_ignore_zones(boxes, kps_xy, kps_cf, cfg, fh, fw)
            if _iz_n:
                _iz_drop_n += _iz_n
                if aim_log and now - _last_izdbg > 2.0:
                    _last_izdbg = now
                    print("[过滤] 自己的手/影子 -> 剔除 %d（累计 %d）" % (_iz_n, _iz_drop_n))

        # ── 人形校验：挡散落的景物块 / 伸到画面中部的自己的手臂（2026-10-30）──
        if len(boxes):
            boxes, kps_xy, kps_cf, _pc_n, _pc_why = filter_person_check(
                boxes, kps_xy, kps_cf, cfg, fw, fh)
            if _pc_n:
                _pc_drop_n += _pc_n
                _tk = max(_pc_why.items(), key=lambda kv: kv[1])[0] if _pc_why else "-"
                if aim_log and now - _last_pcdbg > 2.0:
                    _last_pcdbg = now
                    print("[人形校验] 剔除 %d（累计 %d）主因：%s" % (_pc_n, _pc_drop_n, _tk))


        hits = pick_targets(boxes, aim_x, aim_y, cfg)

        # ── 锁定专用闸：只拦"锁"，不影响绿框显示（2026-10-30「粉框锁空白」）──
        _lock_boxes = boxes
        if len(boxes):
            _lock_boxes, _lk_k, _lk_c, _lg_n = filter_lock_gate(boxes, kps_xy, kps_cf, cfg, fw, fh)
            if _lg_n:
                _lg_drop_n += _lg_n
                if aim_log and now - _last_lgdbg > 2.0:
                    _last_lgdbg = now
                    print("[锁定闸] 不像人 -> 不锁 %d（累计 %d，绿框照常显示）" % (_lg_n, _lg_drop_n))

        # ── 目标锁定：咬住一个目标，避免多目标时来回跳 ──
        lock_box = None
        if lock_on:
            # 交战态：按住开镜/瞄准键 → 整段交火只咬一个人（2026-10-30）
            lock_st["engage"] = bool(aim_on and ((not aim_require_key) or is_key_down(aim_key_vk)))
            # ── ★2026-11-01 移植 kalman 卡尔曼跟踪（tracker.enabled）★ ──
            # 默认关：2.0 的 pick_locked 已经有"预测中心 + 换人闸 + commit 窗口"，
            # 两套接管逻辑会互相打架（tracker 的 coast 会把已经判死的目标续回来）。
            # 想试就开 tracker.enabled=true，它按 coast 外推接管锁定，pick_locked 只做兜底。
            if _tracker is not None:
                try:
                    _tracks = _tracker.update(_lock_boxes, now,
                                              size_ref=max(1.0, lock_min_h * 4.0))
                    _tb = _tracker.pick_sticky(_tracks, aim_x, aim_y, lock_min_h,
                                               _trk_sel, now, _tcfg)
                except Exception as _tre:
                    _tb = None
                    if aim_log and now - _trk_dbg > 5.0:
                        _trk_dbg = now
                        print("[卡尔曼] 异常，本条退回 pick_locked：%s" % _tre)
                if _tb is not None:
                    lock_box = _tb
                else:
                    lock_box = pick_locked(_lock_boxes, aim_x, aim_y, lock_min_h, lock_st, now,
                                           lock_hold_s, lock_match_px, lock_sticky_s, lock_radius_px)
            else:
                lock_box = pick_locked(_lock_boxes, aim_x, aim_y, lock_min_h, lock_st, now,
                                       lock_hold_s, lock_match_px, lock_sticky_s, lock_radius_px)
            # ★2026-10-31 三轮★ 锁定框必须有"当帧检出"支撑，否则很快清掉。
            # 用户对照基准版发现「基准版粉框都不乱飘」：基准版没有锁闸/队友绿环/底部屏蔽
            # 这一堆**过滤器**，而现在这些过滤器会把正在锁的那个人从候选里剔掉
            # （绿环误判成队友、人贴到画面底部、宽高比卡在 0.12/1.6 边上），
            # pick_locked 于是保持旧框 hold_ms(400ms)：粉框挂在人早就不在的地方，
            # 瞄准点又照这个旧框算 = 用户说的「粉框脱离人体、鼠标跟着飞」。
            # 处理：没有当帧支撑就只在 stale_clear_ms(默认 60ms≈2~4帧) 内容忍，
            # 超时立即清锁（连速度/瞄准一起停），宁可不锁也不咬空气。
            if lock_box is not None:
                _lock_fresh = False
                for _cb in _lock_boxes:
                    _ix = max(0.0, min(_cb[2], lock_box[2]) - max(_cb[0], lock_box[0]))
                    _iy = max(0.0, min(_cb[3], lock_box[3]) - max(_cb[1], lock_box[1]))
                    _ia = _ix * _iy
                    _ua = ((_cb[2] - _cb[0]) * (_cb[3] - _cb[1])
                           + (lock_box[2] - lock_box[0]) * (lock_box[3] - lock_box[1]) - _ia)
                    if _ua > 0 and (_ia / _ua) >= 0.25:
                        _lock_fresh = True
                        break
                if _lock_fresh:
                    lock_st["unsup_t"] = 0.0
                else:
                    if not lock_st.get("unsup_t"):
                        lock_st["unsup_t"] = now
                    _stale_ms = float((cfg.get("lock") or {}).get("stale_clear_ms", 60.0))
                    if (now - lock_st["unsup_t"]) * 1000.0 >= _stale_ms:
                        lock_box = None
                        lock_st["box"] = None
                        lock_st["t"] = 0.0
                        lock_st["v"] = [0.0, 0.0]
                        lock_st["unsup_t"] = 0.0
                        _stale_n += 1
                        if aim_log and now - _stale_dbg > 2.0:
                            _stale_dbg = now
                            print("[锁定] 当帧无检出支撑 -> 清锁（累计 %d 次，阈值 %.0fms）"
                                  % (_stale_n, _stale_ms))
            if lock_box is not None:
                _lcx = (lock_box[0] + lock_box[2]) / 2.0
                _lcy = (lock_box[1] + lock_box[3]) / 2.0
                _lh = lock_box[3] - lock_box[1]
                _ld = ((_lcx - aim_x) ** 2 + (_lcy - aim_y) ** 2) ** 0.5
                # 只有准星落进目标框的"中心区域"才算对准：
                # 近距离目标框很大，若按"框边缘"判定，准星离敌人还很远就算命中 -> 提前开火
                _bw = lock_box[2] - lock_box[0]
                _bh2 = lock_box[3] - lock_box[1]
                _inside = (abs(_lcx - aim_x) <= _bw * 0.15 and
                           abs(_lcy - aim_y) <= _bh2 * 0.15)
                _ok_now = (_ld <= trigger_radius or _inside) and _lh >= min_box_height
                if _ok_now:
                    fire_hold_t = now
                # 迟滞：刚满足过就保持一小会儿，避免准星抖动让红框一闪一闪
                if _ok_now or (fire_hold_t > 0 and (now - fire_hold_t) * 1000 < fire_hyst_ms):
                    hits = [(_ld, _lcx, _lcy, _lh, lock_box[2] - lock_box[0],
                             lock_box[0], lock_box[1], lock_box[2], lock_box[3])]
                else:
                    hits = []
            else:
                hits = []

        target_present = bool(hits)
        if target_present:
            if not prev_target_present:
                need_reaction = True
            last_target_t = now
        else:
            hold_enter_t = 0.0          # 目标离开命中圈：下次重新计时（别把上一枪的延迟算进来）
        prev_target_present = target_present

        # ── 丢目标期间：把"目标速度估计"衰减掉，避免复现时拿着过期速度打提前量 ──
        if lock_box is None:
            aim_vx *= 0.7
            aim_vy *= 0.7
            if abs(aim_vx) < 5.0:
                aim_vx = 0.0
            if abs(aim_vy) < 5.0:
                aim_vy = 0.0
            aim_last_t = None
            aim_last_tc = None
            aim_last_vt = 0.0
            aim_spos = None
            aim_bpos = None
            aim_lx *= 0.85
            aim_ly *= 0.85
            if abs(aim_lx) < 1.0:
                aim_lx = 0.0
            if abs(aim_ly) < 1.0:
                aim_ly = 0.0

        # ── 自动瞄准：检测帧只负责"喂目标偏差 + 每帧预算"，实际移动交给跟随线程 ──
        # ── 常驻锁人（2026-10-30）──
        # require_aim_key=false → aim_key_ok 恒真：不按右键也一直锁人、一直拉枪。
        # 但"开镜/腰射两套 k 系数"必须继续跟**真实按键**走：游戏里开镜灵敏度与腰射
        # 不同，若常驻锁人后一律用腰射 k，一开镜增益就失准（视角来回抽）。
        # 故两件事解耦：
        #   aim_key_ok     = 要不要锁人（可配成常驻）
        #   _ads_key_down  = 此刻是不是真的开着镜（决定用哪套 k / 预算 / 单步限幅）
        _ads_key_down = is_key_down(aim_key_vk)
        aim_key_ok = (not aim_require_key) or _ads_key_down
        # ── 开镜状态：上升沿给宽限期；开镜与腰射用**两套** 像素/计数 系数 ──
        # 游戏里开镜灵敏度倍率与腰射不同（常见 0.6~0.9），k 混用会让闭环增益失准：
        # 增益 >1 就是"冲过头→下一帧再拉回来"，眼里的"开镜后视角来回抽"。
        if _ads_key_down and not aim_ads_prev:
            aim_ads_until = now + float(aim_cfg.get("ads_grace_ms", 260)) / 1000.0
            if aim_log:
                print("[瞄准] 开镜 -> 宽限 %.0fms（预算≤%.2f 限幅≤%.0fpx，改用开镜系数）"
                      % (float(aim_cfg.get("ads_grace_ms", 260)),
                         float(aim_cfg.get("ads_hold_ratio", 0.45)),
                         float(aim_cfg.get("ads_maxstep_px", 80))))
        aim_ads_prev = _ads_key_down
        # 开镜系数是否跟随按键：默认跟（ads_sense_by_key=true）。
        # 若游戏里"右键不是开镜"（比如右键是别的动作），把它设 false 就恒用腰射系数。
        _ads_use = bool(aim_cfg.get("ads_sense_by_key", True)) and _ads_key_down
        # 开火态：按住开火键（默认 LButton）→ 用开火那套 k；松手后再宽限 fire_grace_ms，
        # 免得点射时在两套系数之间来回跳（每跳一次就是一次增益突变）。
        _fire_down = bool(aim_fire_en and is_key_down(aim_fire_vk))
        if _fire_down:
            # 连发期间必须每帧续期：只在"按下沿"给 120ms 的话，扫射到第 121ms 就会
            # 悄悄切回腰射/开镜系数（增益突变，体感是"打着打着跟不上了"）。
            aim_fire_until = now + aim_fire_grace
            if not aim_fire_prev and aim_log:
                print("[瞄准] 开火 -> 改用开火系数 k=(%.2f,%.2f)（腰射 %.2f / 开镜 %.2f）"
                      % (aim_k_fire[0], aim_k_fire[1], aim_k[0], aim_k_ads[0]))
        elif aim_fire_prev:
            aim_fire_until = now + aim_fire_grace      # 松手：保留一小段，点射不抖
        aim_fire_prev = _fire_down
        _fire_use = bool(aim_fire_en and (now < aim_fire_until))
        if _fire_use:
            _k_use = aim_k_fire
        elif _ads_use:
            _k_use = aim_k_ads
        else:
            _k_use = aim_k
        if aim_on and allow_fire and aim_key_ok and lock_box is not None:
            _x1, _y1, _x2, _y2 = lock_box
            # ── 新目标判定（首答脉冲）──
            # 没锁过 / 锁定的框中心一次跳了 60px 以上（换人）→ 认为"新目标出现"，
            # 在 first_burst_ms 内用高预算把准星一次拉到身上（解"不能第一时间移动到人身上"）
            _bcx = (_x1 + _x2) / 2.0
            _bcy = (_y1 + _y2) / 2.0
            _bh = max(1.0, _y2 - _y1)
            # ── 视野几何变化检测（开镜/收镜的缩放动画）──
            # 框高在两帧之间明显变化 = 视野正在缩放。此时"像素偏差"整体被放大/缩小，
            # 但"准星是否已经对着目标"根本没变 —— 照常按放大后的像素偏差拉枪，就是
            # 把一个不存在的误差当真，于是视角被拽偏（用户说的「开镜视角异常移动」）。
            _zr = 1.0
            if aim_prev_bh > 1.0:
                _zr = _bh / aim_prev_bh
            # 「缩放判定」阈值随框高放大（2026-10-30，修「近距离被当成开镜冻住」）：
            # 贴脸时框高本来就几百像素，准星一转，同一目标的框高因为透视/视差一帧就能
            # 变 10~30% —— 旧写法固定 10% 阈值把它判成"视野在缩放"，于是每帧重置
            # fov 稳定期，预算被按在 fov_hold_ratio(0.3)，表现就是"越贴脸越转不动"。
            # 框越大越宽容（最多 3 倍），小框（远处真开镜）判定不变。
            _zref = float(aim_cfg.get("fov_change_ref_px", 160.0))
            # fov_change_ref_px<=0 = 关掉这个自适应，回到固定阈值的老行为
            _zscale = 1.0 if _zref <= 0.0 else max(1.0, min(3.0, _bh / max(1.0, _zref)))
            _zthr = float(aim_cfg.get("fov_change_ratio", 0.10)) * _zscale
            _is_new_t = is_new_target(aim_lock_c, (_bcx, _bcy, _bh), aim_x, aim_y, aim_cfg)
            _fov_ms = float(aim_cfg.get("fov_settle_ms", 220))
            _fov_ratio = float(aim_cfg.get("fov_hold_ratio", 0.15))
            # ── 先判换人：真换人时该猛就猛，绝不能被"缩放稳定期"冻住首答 ──
            if _is_new_t:
                aim_new_until = now + aim_burst_s
                aim_k_samples = 0          # 换了目标：重新快速标定
                aim_fov_until = 0.0        # 换人不是缩放
                # 换人也把"部位粘滞 + 瞄准点平滑"清空：否则新目标先按旧目标的关键点
                # 位置做 EMA，首答会带一段旧目标的滞后（用户说的"第一定位不准"）
                aim_pose_part = None
                aim_pose_lost = 0
                aim_px_sm = None
                # 提前量、速度估计同样归零：别拿上一个目标的速度给新目标打提前量
                aim_vx = aim_vy = 0.0
                aim_lx = aim_ly = 0.0
                if aim_log:
                    print(f"[瞄准] 新目标 -> 首答脉冲 {aim_burst_s * 1000:.0f}ms（预算 {aim_budget_ratio_init:.2f}）")
            elif abs(_zr - 1.0) > _zthr:
                # 同一个目标的框在缩放 = 视野几何在变（开镜/收镜）→ 进稳定期
                aim_fov_until = now + _fov_ms / 1000.0
                if aim_log and now - last_aimdbg > 0.35:
                    last_aimdbg = now
                    print(f"[瞄准] 视野缩放 {_zr:.2f}x -> 稳定期 {_fov_ms:.0f}ms（预算限 {_fov_ratio:.2f}）")
            aim_prev_bh = _bh
            aim_lock_c = (_bcx, _bcy, _bh)
            _crop_cx, _crop_cy = _bcx, _bcy      # 下一帧裁剪窗口对准目标框中心
            # ── 远/近档判定（先判再取点，判定用"框中心离准星多远"，与用哪个部位无关）──
            _d_mode = max(abs(_bcx - aim_x), abs(_bcy - aim_y))
            if aim_split_on:
                _new_mode = aim_mode
                if aim_mode == "near" and _d_mode >= _ms_far + _ms_hyst:
                    _new_mode = "far"
                elif aim_mode == "far" and _d_mode <= _ms_far - _ms_hyst:
                    _new_mode = "near"
                if _new_mode != aim_mode:
                    aim_mode = _new_mode
                    # 换档必须清部位粘滞/瞄准点平滑：否则躯干的 EMA 会拖着头跑，首答偏
                    aim_pose_part = None
                    aim_pose_lost = 0
                    aim_px_sm = None
                    if aim_log:
                        print("[瞄准] 切到%s档（偏差 %.0fpx，阈值 %.0f±%.0f）→ 瞄%s"
                              % ("远" if aim_mode == "far" else "近", _d_mode,
                                 _ms_far, _ms_hyst,
                                 aim_split_far_part if aim_mode == "far" else aim_split_near_part))
            _far_mode = bool(aim_split_on and aim_mode == "far")
            # mode_split 关掉时完全回到旧的单一部位（pose_point）
            _part_pref = ((aim_split_far_part if _far_mode else aim_split_near_part)
                          if aim_split_on else pose_point)
            _h_ratio = aim_far_h if _far_mode else aim_near_h
            tx = (_x1 + _x2) / 2.0
            ty = _y1 + (_y2 - _y1) * _h_ratio
            # ── 有姿态关键点时：直接瞄"真·头胸" ──
            # 实测教训（2026-10-25）：单帧硬选点会"一次定位不准"——远景/背身/遮挡时
            # 鼻子、眼睛、肩膀随时丢，帧间还会在"鼻子"和"双肩中点"之间来回跳，
            # 于是首答落点一会头部一会胸口。改为：**逐帧粘住同一个部位** + 瞄准点平滑。
            # 逻辑抽成了模块级函数 pick_pose_point（可单测：见 _test_pose_pick.py）
            # ── ★2026-11-01 移植 kalman 命中盒分区（hitbox.enabled）★ ──
            # 用 17 关键点按框高重建 头/胸/腹，按 距离×可见性×优先级 打分选点，
            # 比固定 aim_height_ratio 更贴人；拿不到关键点时自动退回下面的 pose_point。
            _hb_used = False
            if _hitbox is not None:
                _kpi = None
                if kps_xy is not None and len(kps_xy):
                    _bd, _kpi = 1e18, None
                    _bcx_hb, _bcy_hb = (_x1 + _x2) / 2.0, (_y1 + _y2) / 2.0
                    for _i, _b in enumerate(boxes):
                        _cxb, _cyb = (_b[0] + _b[2]) / 2.0, (_b[1] + _b[3]) / 2.0
                        _dd = ((_cxb - _bcx_hb) ** 2 + (_cyb - _bcy_hb) ** 2) ** 0.5
                        if _dd < _bd:
                            _bd, _kpi = _dd, _i
                _kxy = kps_xy[_kpi] if (_kpi is not None and _kpi < len(kps_xy)) else None
                _kcf = (kps_cf[_kpi] if (_kpi is not None and kps_cf is not None
                                         and _kpi < len(kps_cf)) else None)
                _hp = _hitbox.pick((_x1, _y1, _x2, _y2), _kxy, _kcf, aim_x, aim_y, _hb_state)
                if _hp is not None:
                    tx, ty = _hp[0], _hp[1]
                    aim_pose_part = _hp[2]
                    aim_pose_lost = 0
                    aim_px_sm = (tx, ty)
                    _hb_used = True
            if not _hb_used:
                _pp = pick_pose_point(kps_xy, kps_cf, boxes, _x1, _y1, _x2, _y2,
                                      aim_cfg, _part_pref, aim_pose_part, aim_pose_lost,
                                      aim_px_sm)
                if _pp is not None and _pp[3] is not None:
                    aim_pose_part, aim_pose_lost, aim_px_sm, _pt = _pp
                    tx, ty = _pt
            # ── 速度前馈：用"目标自己"的历史位移估速度（存原始位置，避免反馈累积）──
            # 关键三条（抖动会直接变成"拉过头"）：
            #   ① 先去抖再求导：EMA 平滑目标位置，别拿逐帧差分当速度
            #      （30fps 下 6px 检测抖动 = 180px/s 假速度，被提前量一放大就是准星乱甩）
            #   ② 速度在 ≥150ms 的长窗口上测：同样的抖动只造成 ~40px/s 误差
            #   ③ 速度与加速度都限幅，噪声不许把速度积分到天上去
            # ★后坐力补偿：把按住开火期间累计的"该下压多少"叠到瞄准点上
            if _recoil is not None and (_rcl_acc[0] != 0.0 or _rcl_acc[1] != 0.0):
                tx += _rcl_acc[0]
                ty += _rcl_acc[1]
            _tx_raw, _ty_raw = tx, ty
            _alpha = float(aim_cfg.get("vel_smooth", 0.25))
            _vwin = float(aim_cfg.get("vel_window_ms", 150)) / 1000.0
            if aim_spos is None:
                aim_spos = [_tx_raw, _ty_raw]
            else:
                aim_spos[0] += (_tx_raw - aim_spos[0]) * _alpha
                aim_spos[1] += (_ty_raw - aim_spos[1]) * _alpha
            # ★2026-10-31 速度改源★ 用**锁定框中心**求速度，不用瞄准点。
            # 瞄准点会随部位（头/胸/鼻）切换瞬移 100~200px：即便 EMA，速度窗口里也会算出
            # 几百 px/s 的假速度 → 提前量把准星推到人外面、粉框外推也跟着飘
            # （用户："粉框还是经常脱离人体，导致鼠标跟随粉框乱飞"）。
            # 框中心不随部位切换变，是干净的速度源。vel_from_box=false 可退回旧行为。
            _bspos = (_bcx, _bcy) if aim_cfg.get("vel_from_box", True) else (_tx_raw, _ty_raw)
            if aim_bpos is None:
                aim_bpos = [_bspos[0], _bspos[1]]
            else:
                aim_bpos[0] += (_bspos[0] - aim_bpos[0]) * _alpha
                aim_bpos[1] += (_bspos[1] - aim_bpos[1]) * _alpha
            if aim_last_t is not None:
                _dtv = max(0.005, now - aim_last_t)
                if aim_last_tc is not None and (now - aim_last_vt) >= _vwin:
                    _nvx = (aim_bpos[0] - aim_last_tc[0]) / _dtv
                    _nvy = (aim_bpos[1] - aim_last_tc[1]) / _dtv
                    if _maxv > 0:
                        _nvx = max(-_maxv, min(_maxv, _nvx))
                        _nvy = max(-_maxv, min(_maxv, _nvy))
                    _dvmax = float(aim_cfg.get("vel_max_step_px_s", 220.0))
                    _dvx = _nvx - aim_vx
                    _dvy = _nvy - aim_vy
                    if abs(_dvx) > _dvmax:
                        _dvx = _dvmax if _dvx > 0 else -_dvmax
                    if abs(_dvy) > _dvmax:
                        _dvy = _dvmax if _dvy > 0 else -_dvmax
                    aim_vx += _dvx
                    aim_vy += _dvy
                    aim_last_vt = now
                    aim_last_tc = (aim_bpos[0], aim_bpos[1])
            aim_last_t = now
            # 速度死区：目标基本静止时（检测框抖动 1~2px 会算出假速度），速度清零
            if abs(aim_vx) < aim_vdead and abs(aim_vy) < aim_vdead:
                aim_vx = 0.0
                aim_vy = 0.0
            _mx_lead = float(aim_cfg.get("lead_max_px", 40))
            # 远档提前量打折（2026-10-30「远距离有时拉过头」）：远处框小，
            # 提前量多一点在屏幕上就是“跑到人头前面去”；远档只打躯干，不需要贴身提前量。
            _lead_k = (float((aim_cfg.get("mode_split") or {}).get("far_lead_scale", 0.5))
                       if _far_mode else 1.0)
            # ★2026-10-31★ 提前量再按**框宽**收一道：lead_max_px=90 对远处 100px 宽的框
            # 等于把准星推到整个人身宽以外（"鼠标跟着粉框乱飞"的一半原因）。
            _bw_a = max(1.0, _x2 - _x1)
            _lead_box_ratio = float(aim_cfg.get("lead_box_ratio", 0.35))
            _mx_lead = min(_mx_lead, _lead_box_ratio * _bw_a)
            # 首答脉冲期间不打提前量：刚出现的目标速度估计最不可靠（抖动/框跳都会算成假速度），
            # 提前量把瞄准点推到目标前方 → 就是用户说的"一次定位总是拉过头"。
            # 等首答脉冲结束、速度稳定了再加提前量。
            if aim_lead_s > 0.0 and (aim_vx != 0.0 or aim_vy != 0.0) and now >= aim_new_until:
                # 瞄准点前移到"他即将到达的位置"（打提前量）
                # 注意：提前量本身必须平滑 + 限速，直接"瞬时速度×系数"会被检测抖动带飞，
                # 表现为准星在敌人前面来回甩（用户说的"拉过头"）
                _tx_l = aim_vx * aim_lead_s * _lead_k
                _ty_l = aim_vy * aim_lead_s * _lead_k
                # 待打提前量的偏移量上限：既限绝对量，也限"不许超过目标速度本身对应的位移"
                _lim_l = min(_mx_lead * _lead_k, (abs(aim_vx) * aim_lead_s + 6.0) * _lead_k)
                if abs(_tx_l) > _lim_l:
                    _tx_l = _lim_l if _tx_l > 0 else -_lim_l
                _lim_y = min(_mx_lead * _lead_k, (abs(aim_vy) * aim_lead_s + 6.0) * _lead_k)
                if abs(_ty_l) > _lim_y:
                    _ty_l = _lim_y if _ty_l > 0 else -_lim_y
                _maxq = float(aim_cfg.get("lead_step_px", 6.0))    # 每检测帧提前量最多挪多少
                _dx_l = _tx_l - aim_lx
                _dy_l = _ty_l - aim_ly
                if abs(_dx_l) > _maxq:
                    _dx_l = _maxq if _dx_l > 0 else -_maxq
                if abs(_dy_l) > _maxq:
                    _dy_l = _maxq if _dy_l > 0 else -_maxq
                aim_lx += _dx_l
                aim_ly += _dy_l
                tx += aim_lx
                ty += aim_ly
            else:
                aim_lx *= 0.85
                aim_ly *= 0.85
            # ★2026-10-31★ 兜底：打提前量之后的瞄准点必须还在**锁定框**里（左右各留 8% 余量）。
            # 提前量/速度一旦估错，瞄准点会被推到人外面，表现出来就是"鼠标跟着粉框飞出去"。
            # 这是最后一道硬闸，与速度源、限幅无关——只要框在，瞄准点就不会跑到框外。
            if bool(aim_cfg.get("lead_clip_to_box", True)):
                _mrx = max(1.0, _x2 - _x1) * float(aim_cfg.get("lead_clip_margin_ratio", 0.0))
                _myr = max(1.0, _y2 - _y1) * float(aim_cfg.get("lead_clip_margin_ratio", 0.0))
                _tx0, _ty0 = tx, ty
                tx = max(_x1 - _mrx, min(_x2 + _mrx, tx))
                ty = max(_y1 - _myr, min(_y2 + _myr, ty))
                if abs(tx - _tx0) > 2.0 or abs(ty - _ty0) > 2.0:
                    _lead_clip_n += 1
                    if aim_log and now - _lead_clip_last > 2.0:
                        _lead_clip_last = now
                        print("[瞄准] 提前量把人推出锁定框 -> 已夹回（本帧 %+.0f,%+.0f px，累计 %d 次）"
                              % (tx - _tx0, ty - _ty0, _lead_clip_n))
            ex = tx - aim_x
            ey = ty - aim_y
            # ── 自适应标定（像素/计数）★修"第一定位不准"的关键★ ──
            # 物理关系：B = 目标方位(像素)、P = 准星、e = B - P，则
            #     Δe = ΔB - k × 本次发出的鼠标计数   →   k = (ΔB - Δe) / 计数
            # ΔB（目标自己的世界位移）靠"没发移动的那几帧"估出来，见下面标定样本段。
            # 旧写法等价于假设 ΔB=0（只在目标静止时学），实战里几乎学不到 → 首答偏。
            _tsx, _tsy = (aim_spos[0], aim_spos[1]) if aim_spos is not None else (tx, ty)
            # ── 本帧预算 ──
            # 平时：本周期最多消掉 budget_ratio 的偏差（防过冲）
            # **新目标刚出现时（first_burst_ms 内）：用 budget_ratio_initial(0.95)**
            #   —— 这是"第一定位不准 / 不能第一时间移动到人身上"的直接解药：
            #      旧写法首答只走 65%，300px 的偏差第一帧就还剩 105px，看起来根本没上身。
            _ad = max(abs(ex), abs(ey))
            if _ad > aim_max_dist:
                # 太远的目标不拉（否则准星平时到处乱飘）
                with aim_st["lock"]:
                    aim_st["valid"] = False
                if aim_log and now - last_aimdbg > 1.0:
                    last_aimdbg = now
                    print(f"[瞄准] 目标太远({_ad:.0f}px > {aim_max_dist:.0f}px)，不拉枪")
                continue
            _ratio = aim_budget_ratio
            if now < aim_new_until:
                _ratio = aim_budget_ratio_init
            # ── 未标定前不许用大预算（闭环增益可能 >1 直接振荡发散）──
            # k（像素/计数）还没测出来时，程序对"一次该发多少计数"是不确定的：
            # 若 k 实际是估计的 2 倍，0.95 的预算就变成闭环增益 1.9 → 准星来回撞、
            # 越撞越远（体感："拉过头"+"乱甩"）。所以前两个标定样本用保守预算。
            if now < aim_new_until and aim_k_samples < 2:
                _ratio = min(_ratio, 0.5)
            # ── 近距直取（2026-10-30，修「枪口贴到头附近时自瞄转得很慢」）──
            # 背景：偏差掉到 far_px(460) 以下后，预算按平方曲线一路降到 near_ratio，
            # **而且一直降到 0px** —— 最后几十像素每帧只肯走 near_ratio 那一份，
            # 贴得越近越钝：60px 要 5~8 个检测周期才贴上，体感就是"到敌人头边开始磨"。
            # 物理上恰恰相反：剩下的偏差越小，越应该一口气走完 —— 过冲风险由
            # "单调逼近（任何一轴都不越过目标）+ 剩余偏差实时扣减 + 死区"三层兜住。
            # 这里把 [0, near_boost_px] 这一段变成"越近越肯走"的反向曲线，
            # 在带口(near_boost_px)处取值仍是 near_ratio → 与外面的过渡曲线无缝衔接，
            # 没有换挡感。注意它在三道硬保护（fov/开镜/过冲）**之前**，
            # 真在缩放、真冲过头了，那三道照样压得住它。
            _far = float(aim_cfg.get("far_px", 400.0))
            _near_r = float(aim_cfg.get("near_ratio", 0.30))
            _nb = max(1.0, aim_near_boost_px)
            _near_band = bool(aim_near_boost_px > 0.0 and _ad < _nb)
            if _near_band:
                _tb = max(0.0, min(1.0, _ad / _nb))        # 0=贴脸, 1=带口
                # 曲线形状 (1-t²)²：带口处斜率=0（不会突然换挡），往里爬得很快 ——
                # 60px 处已到 ~0.85，真正贴脸时就是 near_boost_ratio。
                _ratio = max(_ratio, _near_r + (aim_near_boost_ratio - _near_r) * (1.0 - _tb * _tb) ** 2)
            # ── 视野缩放/开镜宽限：只允许"微量修正" ──
            # 缩放期间像素偏差被放大 z 倍，但它不代表准星真的偏了 → 宁可不动。
            # 稳定期过后立刻恢复正常预算，所以不牺牲"够快"。
            if now < aim_fov_until:
                _ratio = min(_ratio, float(aim_cfg.get("fov_hold_ratio", 0.15)))
            elif now < aim_ads_until and _ads_use:
                # 开镜宽限：像素偏差被缩放放大，先只做微量修正。
                # 但贴脸带（偏差≤near_boost_px）例外：那里的绝对误差本来就小，
                # 压到 0.45 + 限幅 80px 只会让"开镜锁"每次开镜都先磨 260ms ——
                # 用 near_ads_hold_ratio / near_ads_maxstep_px 放宽。
                _ar = float(aim_cfg.get("ads_hold_ratio", 0.45))
                if _near_band:
                    _ar = max(_ar, float(aim_cfg.get("near_ads_hold_ratio", 0.85)))
                _ratio = min(_ratio, _ar)
            # ── 过冲抑制：本帧偏差符号相对上一帧翻转 = 刚冲过目标 ──
            # 立刻降预算 + 缩短脉冲窗口，把"过了再回来"的振荡掐掉
            _sgn = 1 if ex > 0 else -1
            if aim_prev_sgn != 0 and _sgn != aim_prev_sgn and max(abs(ex), abs(ey)) > 8.0:
                aim_damp_until = now + 0.25
            aim_prev_sgn = _sgn
            if now < aim_damp_until:
                # 贴脸段用更宽的抑制比例：这里的符号翻转多半是"关键点/框抖了"，
                # 不是真的冲过头（真冲过头会被单调逼近直接钳在目标上）。
                # 旧写法一律压到 0.45，等于每抖一次就冻 250ms → 就是"越近越慢"。
                _damp_r = (float(aim_cfg.get("near_damp_ratio", 0.85)) if _near_band
                           else float(aim_cfg.get("damp_ratio", 0.45)))
                _ratio = min(_ratio, _damp_r)
            # ── 远冲近稳·连续版（2026-10-26）──
            # 原来是"150px 一刀切"（远 0.86 / 近 0.60），阶跃点会突然换挡。
            # 现在改成连续曲线：距离越近预算越小，far_px 及以上用满预算、
            # 到 near_boost_px 处降到 near_ratio，中间按平方曲线过渡（近处降得更快）。
            # 再往里（<near_boost_px）交给上面的"近距直取"，反向抬起。
            if _far > _nb + 1.0 and _nb <= _ad < _far:
                _t = (_ad - _nb) / (_far - _nb)         # 0=带口, 1=far_px
                # 线性过渡（原来是"按 ad/far 平方"，现在这段只是 [带口, far] 之间的一小截，
                # 再用平方会在中距（200~350px）额外压一脚，反而比旧版更钝）
                _curve = _near_r + (_ratio - _near_r) * _t
                _ratio = min(_ratio, _curve)
            _maxs = aim_maxstep
            if now < aim_fov_until:
                _maxs = min(_maxs, float(aim_cfg.get("fov_maxstep_px", 50)))
            elif now < aim_ads_until and _ads_use:
                # 同上：贴脸带放宽限幅，否则开镜头 260ms 每步最多 80px，贴脸也走不动
                _am = float(aim_cfg.get("ads_maxstep_px", 80))
                if _near_band:
                    _am = max(_am, float(aim_cfg.get("near_ads_maxstep_px", 160)))
                _maxs = min(_maxs, _am)
            _lim = min(_maxs, _ad * _ratio)
            with aim_st["lock"]:
                aim_st["ex"], aim_st["ey"] = ex, ey           # 观测偏差（给状态行/标定用）
                aim_st["tx"], aim_st["ty"] = tx, ty           # 目标绝对坐标（跟随线程据此算剩余偏差）
                aim_st["obs"] = (ex, ey)                      # 该帧真实观测偏差（对齐航位推算）
                aim_st["k"] = [_k_use[0], _k_use[1]]
                aim_st["gain"], aim_st["dead"] = aim_gain, aim_dead
                aim_st["noise"], aim_st["maxstep"] = aim_noise, aim_maxstep
                aim_st["tau"], aim_st["tau_floor"] = aim_tau, aim_tau_floor
                # 远档单步消比更粗（0.9=一步吃掉 90% 剩余），换来"甩过去就上身"；
                # 近档仍按 step_cap 摊成小步，贴脸才不会一帧跳过头。
                aim_st["step_cap"] = aim_far_step_cap if _far_mode else float(aim_cfg.get("step_cap", 0.18))
                # 绝对闸：贴脸段放宽到 abs_move_cap_near（默认 1.0=本周期允许走满剩余偏差），
                # 否则就算预算抬到 1.0，跟随线程也会在"已发 85% 观测偏差"处停下等下一帧。
                aim_st["abs_cap"] = aim_abs_cap_near if _near_band else aim_abs_cap
                # 每周期位移硬上限（像素）= min(abs_cap×偏差, burst_cap_box_ratio×框高)。
                # 与 k 无关，远处框小自然收得紧 —— 治「远距离拉过头」。
                _bcr = float(aim_cfg.get("burst_cap_box_ratio", 0.75))
                _cap_abs = (aim_abs_cap_near if _near_band else aim_abs_cap) * _ad
                aim_st["cap_px"] = min(_cap_abs, _bcr * _bh) if _bcr > 0.0 else 0.0
                aim_st["budget"] = _lim
                aim_st["sync_t"] = now      # 通知跟随线程：做一次对齐
                # ★2026-10-31★ 原来写 `now`（本帧 aim 计算时刻），绘制时 now-det_t≈0
                # → 外推等于没做，粉框永远慢一拍。改用**抓帧时刻**，才是真实延迟。
                aim_st["det_t"] = _boxes_t if _boxes_t else now
                aim_st["vx"], aim_st["vy"] = aim_vx, aim_vy
                aim_st["valid"] = True
            stat_aim += 1
            # ── 标定样本 ──
            # 1) 取出"上一帧到本帧之间真正发出去的计数"（用只增不减的 cc 做差）
            # 2) 估"目标自己的世界位移 ΔB"：只在**本帧几乎没发移动**时，画面位移≈世界位移
            #    （画面里的位移已经包含我们自己的移动，直接拿来用会把 k 越学越小！）
            with aim_st["lock"]:
                _cc = aim_st.get("cc") or [0.0, 0.0]
                _cc_now = (float(_cc[0]), float(_cc[1]))
            if aim_cc_last is None:
                aim_prev_sent = (0.0, 0.0)
            else:
                aim_prev_sent = (_cc_now[0] - aim_cc_last[0], _cc_now[1] - aim_cc_last[1])
            aim_cc_last = _cc_now
            _db = None
            if aim_prev_err is not None:
                # -Δe = -(e_now - e_prev)：没发移动时它就等于 ΔB
                _dbe = (aim_prev_err[0] - ex, aim_prev_err[1] - ey)
                if max(abs(aim_prev_sent[0]), abs(aim_prev_sent[1])) < 3.0:
                    _a = 0.3
                    aim_wm = (aim_wm[0] * (1 - _a) + _dbe[0] * _a,
                              aim_wm[1] * (1 - _a) + _dbe[1] * _a)
                    aim_wm_t = now
                # ΔB 估计 = 世界位移速率 × 距上次观测过了几个检测周期
                _nfr = max(1.0, round((now - aim_wm_t) / 0.033)) if aim_wm_t > 0 else 1.0
                _db = (aim_wm[0] * _nfr, aim_wm[1] * _nfr)
            _k_use, aim_k_samples = update_aim_k(
                _k_use, aim_k_samples, aim_prev_sent, aim_prev_err, _db, ex, ey, aim_adaptive)
            if _fire_use:
                aim_k_fire = _k_use
            elif _ads_use:
                aim_k_ads = _k_use
            else:
                aim_k = _k_use
            aim_prev_err = (ex, ey)
            aim_last_tcp = (tx, ty)      # 记住本帧瞄准点（丢目标时保持用）
            aim_lost_t = now             # 刷新"最近一次有锁定"的时刻
            # ★2026-10-31 四轮★ 过冲自检：实测位移 ÷ 计划位移。
            # 计划 = 上一周期跟随线程发出的计数 × k；实测 = 上一帧偏差 - 本帧偏差（同一目标）。
            # 比值 >1.05 说明"实际走得比计划远" = k 被低估/分组系数猜错 → 就是"总是拉过头"。
            if aim_cfg.get("overshoot_probe", True):
                with aim_st["lock"]:
                    _cmd0 = list(aim_st.get("cmd") or [0.0, 0.0])
                _pred_px = max(abs(_cmd0[0] * _k_use[0]), abs(_cmd0[1] * _k_use[1]))
                if aim_obs_prev is not None and _pred_px > 4.0:
                    _meas_px = max(abs(aim_obs_prev[0] - ex), abs(aim_obs_prev[1] - ey))
                    _ovr = _meas_px / _pred_px
                    aim_ovr_sm = (_ovr if aim_ovr_sm is None
                                  else aim_ovr_sm * 0.7 + _ovr * 0.3)
                aim_obs_prev = (ex, ey)
            if aim_log and now - last_aimdbg > 0.35:
                last_aimdbg = now
                _ovr_txt = (" 过冲比%.2f" % aim_ovr_sm) if aim_ovr_sm else ""
                print(f"[瞄准] 框({_x1:.0f},{_y1:.0f},{_x2:.0f},{_y2:.0f}) 锁({aim_pose_part or '框'}) "
                      f"偏差({ex:+.0f},{ey:+.0f})px "
                      f"系数({_k_use[0]:.2f},{_k_use[1]:.2f}) 本帧预算{_lim:.0f}px "
                      f"速度({aim_vx:+.0f},{aim_vy:+.0f})px/s 提前量({aim_lx:+.0f},{aim_ly:+.0f})px"
                      f"{_ovr_txt}"
                      + (" [开火]" if _fire_use else (" [开镜]" if _ads_use else "")))
        else:
            #★2026-10-30 自查「锁空白」★ 没锁定就把上一帧的瞄准残留全清掉。
            # 以前只把 valid 置 False，但 pose 部位/瞄准点 EMA/目标速度/提前量/上次瞄准点
            # 全都留着：一旦新目标出现，首答会带着上一个目标的滞后；而"保持态"还会
            # 继续花上一帧的预算往空白处走。这里的清空是"不许带着别人的记忆打架"。
            aim_pose_part = None
            aim_pose_lost = 0
            aim_px_sm = None
            aim_spos = None
            aim_bpos = None
            aim_vx = aim_vy = 0.0
            aim_lx = aim_ly = 0.0
            with aim_st["lock"]:
                aim_st["valid"] = False
                aim_st["hold"] = False
                aim_st["budget"] = 0.0        # 预算清零 = 跟随线程立刻停手
            # ── 目标短暂丢失（≤ hold_track_ms）时保持瞄准：准星不冻结，
            #    继续朝"上一帧的瞄准点"走完预算，避免"一帧不动、一帧猛拉"的抽动 ──
            _keep = (aim_on and allow_fire and aim_key_ok and aim_last_tcp is not None
                     and (now - aim_lost_t) <= aim_hold_s
                     and max(abs(aim_last_tcp[0] - aim_x), abs(aim_last_tcp[1] - aim_y)) <= aim_max_dist)
            if _keep:
                with aim_st["lock"]:
                    aim_st["tx"], aim_st["ty"] = aim_last_tcp[0], aim_last_tcp[1]
                    aim_st["valid"] = False
                    aim_st["hold"] = True
                    aim_st["budget"] = 0.0    # 保持=停在原地，不是继续往旧点拉
            elif aim_log and now - last_aimdbg > 1.5:
                last_aimdbg = now
                _why = []
                if not aim_on:
                    _why.append("F6 自瞄开关是关的")
                if not allow_fire:
                    _why.append("测试模式（不移动）")
                if lock_box is None:
                    _why.append("没有锁定到目标")
                if _why:
                    print("[瞄准] 未启动 ->", " + ".join(_why))
        # ── 开火调度（两种模式）──
        if fire_mode == "hold":
            # 按住模式：目标进红框 -> 立即锁定并倒计时（目标闪走也照常开火）-> 按住
            fire_key_ok = (not fire_req_aim) or is_key_down(aim_key_vk)
            if enabled and allow_fire and fire_key_ok and now >= hold_block_until:
                if (not holding) and hold_arm_at == 0.0 and hits:
                    if hold_enter_t == 0.0:
                        hold_enter_t = now
                    if skip_chance > 0 and rnd.random() < skip_chance:
                        stat_skip += 1
                        hold_arm_at = now + rnd.uniform(0.25, 0.8)
                    else:
                        hold_arm_at = now + rng_ms(react_pair, 0.0) / 1000.0
                if (not holding) and hold_arm_at and now >= hold_arm_at:
                    if key_event(tkey, True):
                        holding = True
                        hold_start_t = time.time()
                        stat_fire += 1
                        _lat = (time.time() - hold_enter_t) * 1000 if hold_enter_t else 0.0
                        hold_enter_t = 0.0
                        if hits:
                            print(f"[按住] {tkey} 按住开火（目标距准星 {hits[0][0]:.0f}px 高 {hits[0][3]:.0f}px | 进圈→按住 {_lat:.0f}ms）")
                        else:
                            print(f"[按住] {tkey} 按住开火（目标已闪走 -> 点一枪 | 进圈→按住 {_lat:.0f}ms）")
                    hold_arm_at = 0.0
            if holding:
                if max_hold_ms > 0 and (now - hold_start_t) * 1000 >= max_hold_ms:
                    key_event(tkey, False)
                    holding = False
                    release_at = 0.0
                    hold_block_until = now + rng_ms(hold_block_pair, 250.0) / 1000.0
                    print(f"[松开] 连续按住 {max_hold_ms:.0f}ms 已达上限，停顿 {(hold_block_until - now) * 1000:.0f}ms（拟人）")
                elif (not enabled) or (not allow_fire):
                    key_event(tkey, False)
                    holding = False
                    release_at = 0.0
                    print(f"[松开] {tkey} 已松开（功能关闭）")
                elif not hits:
                    if release_at == 0.0:
                        release_at = now + rng_ms(release_pair, 0.0) / 1000.0
                    elif now >= release_at:
                        key_event(tkey, False)
                        holding = False
                        release_at = 0.0
                        print(f"[松开] {tkey} 已松开（目标离开）")
                else:
                    release_at = 0.0
        elif enabled and allow_fire:
            if not have_pending and (now - last_fire) * 1000 >= next_cool:
                if skip_chance > 0 and rnd.random() < skip_chance:
                    stat_skip += 1
                    last_fire = now
                    next_cool = rng_ms(cool_pair, cfg.get("cooldown_ms", 150))
                    need_reaction = False
                else:
                    delay = rng_ms(react_pair, 0.0) if need_reaction else 0.0
                    if delay <= 0:
                        shots = int(round(rng_ms(burst_pair, cfg.get("burst_shots", 1)))) if rz_on else int(cfg.get("burst_shots", 1))
                        shots = max(1, shots)
                        hold = rng_ms(hold_pair, cfg.get("key_hold_ms", 30))
                        fire_q.put((hold, shots))
                        stat_fire += 1
                        last_fire = time.time()
                        next_cool = rng_ms(cool_pair, cfg.get("cooldown_ms", 150))
                        need_reaction = False
                        if cfg.get("log_detections", True):
                            d = hits[0][0] if hits else -1
                            print(f"[开火] 距离准星 {d:.0f}px 按住 {hold:.0f}ms x{shots}")
                    else:
                        pending_at = now + delay / 1000.0
                        have_pending = True

            if have_pending:
                if cancel_ms > 0 and (now - last_target_t) * 1000 > cancel_ms:
                    have_pending = False
                    stat_cancel += 1
                    print("[取消] 目标闪失，这次不开火")
                elif now >= pending_at:
                    have_pending = False
                    shots = int(round(rng_ms(burst_pair, cfg.get("burst_shots", 1)))) if rz_on else int(cfg.get("burst_shots", 1))
                    shots = max(1, shots)
                    hold = rng_ms(hold_pair, cfg.get("key_hold_ms", 30))
                    fire_q.put((hold, shots))
                    stat_fire += 1
                    last_fire = time.time()
                    next_cool = rng_ms(cool_pair, cfg.get("cooldown_ms", 150))
                    need_reaction = False
                    if cfg.get("log_detections", True):
                        d = hits[0][0] if hits else -1
                        lat = (time.time() - last_target_t) * 1000
                        print(f"[开火] 延迟后开火 距离准星 {d:.0f}px 按住 {hold:.0f}ms x{shots} (目标出现后 {lat:.0f}ms)")
        elif hits and not allow_fire and cfg.get("log_detections", True) and now - last_log > 0.6:
            last_log = now
            print(f"[锁定] 距离准星 {hits[0][0]:.0f}px 目标高 {hits[0][3]:.0f}px（测试模式不发键）")
        elif hits and not enabled and cfg.get("log_detections", True) and now - last_log > 0.6:
            last_log = now
            print(f"[锁定] 距离准星 {hits[0][0]:.0f}px 目标高 {hits[0][3]:.0f}px（未启用，按 F9）")

        _vis_n += 1
        # ★2026-10-31★ 锁定时不许抽帧显示：show_every=2 会让粉框一帧有一帧没（"粉框闪"）。
        _show_every = 1 if lock_box is not None else max(1, int(cfg.get("show_every", 2)))
        if show_window and (_vis_n % _show_every == 0):
            vis = frame.copy()
            if have_cv2:
                cv2.drawMarker(vis, (int(aim_x), int(aim_y)), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
                cv2.circle(vis, (int(aim_x), int(aim_y)), int(cfg.get("trigger_radius_px", 110)), (0, 255, 255), 1)
                # 准星参考十字（用它对比"屏幕中心是否就是游戏准星"）
                _cx, _cy = int(aim_x), int(aim_y)
                cv2.line(vis, (_cx - 16, _cy), (_cx + 16, _cy), (255, 255, 0), 1)
                cv2.line(vis, (_cx, _cy - 16), (_cx, _cy + 16), (255, 255, 0), 1)
                cv2.circle(vis, (_cx, _cy), 2, (0, 0, 255), -1)
                for box in boxes:
                    x1, y1, x2, y2 = [int(v) for v in box[:4]]
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 1)
                # 命中红框：锁定项已经由粉框表示，别再拿同坐标的红框把粉框盖掉
                for dist, bx, by, bh, bw, x1, y1, x2, y2 in hits:
                    if lock_box is not None and abs(x1 - lock_box[0]) < 1.5 \
                            and abs(y1 - lock_box[1]) < 1.5:
                        continue
                    cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
                if lock_box is not None:
                    # 锁定目标（粉框）——一眼看出程序咬住了谁（绿框=看到但没锁）
                    # ★速度外推★：检测只有 ~79fps，框本身慢一拍（目标 300px/s 就滞后 4~7px）。
                    # 按目标速度把框画到"此刻应在的位置"，肉眼才跟得上（用户说的"框不够快"）。
                    # ★2026-10-31 三处修正★
                    #  ① 基准改成"抓帧时刻"（见 aim_st["det_t"]），否则 dt≈0，外推形同虚设；
                    #  ② 位移必须限幅：v 的上限是 vel_max_px_s(2400)，不夹的话目标一抖
                    #     粉框能被甩出去一百多像素（看着像"锁到空气"）；
                    #  ③ 粉框改到最后画、加粗到 3px —— 以前先画粉框、再画同坐标的命中红框，
                    #     红框整条盖住粉框，锁定后根本看不见粉色。
                    _gx = _gy = 0.0
                    _dtu = 0.0
                    if aim_on and bool(aim_cfg.get("extrap_draw", True)):
                        with aim_st["lock"]:
                            _dtu = min(float(aim_cfg.get("extrap_max_ms", 60)) / 1000.0,
                                       max(0.0, time.time() - float(aim_st.get("det_t", 0.0))))
                            _vxu = float(aim_st.get("vx", 0.0))
                            _vyu = float(aim_st.get("vy", 0.0))
                        _bw_u = max(1.0, lock_box[2] - lock_box[0])
                        _bh_u = max(1.0, lock_box[3] - lock_box[1])
                        # ★2026-10-31 二改★ 外推收到 6% 框尺寸 + 绝对 12px 上限：
                        # 15%/37px 在贴脸大框上足够把粉框画到人外面（用户："粉框经常脱离人体"）。
                        # 外推只是让框"跟得上"，不该让框"离开人"。
                        _exr = float(aim_cfg.get("extrap_max_ratio", 0.06))
                        _exa = float(aim_cfg.get("extrap_max_px", 12.0))
                        _lim_x = min(_bw_u * _exr, _exa)
                        _lim_y = min(_bh_u * _exr, _exa)
                        _gx = max(-_lim_x, min(_lim_x, _vxu * _dtu))
                        _gy = max(-_lim_y, min(_lim_y, _vyu * _dtu))
                    _lx1, _ly1 = int(lock_box[0] + _gx), int(lock_box[1] + _gy)
                    _lx2, _ly2 = int(lock_box[2] + _gx), int(lock_box[3] + _gy)
                    _tcx = (lock_box[0] + lock_box[2]) / 2.0 + _gx
                    _tcy = lock_box[1] + (lock_box[3] - lock_box[1]) * aim_height + _gy
                    # 粉框 + 四角（角标画在框外 4px，框本身再被红框压住也还看得见）
                    cv2.rectangle(vis, (_lx1, _ly1), (_lx2, _ly2), (255, 0, 255), 2)
                    _lw = max(6, min(18, (_lx2 - _lx1) // 5))
                    for _cx1, _cy1, _sx, _sy in ((_lx1, _ly1, 1, 1), (_lx2, _ly1, -1, 1),
                                                (_lx1, _ly2, 1, -1), (_lx2, _ly2, -1, -1)):
                        cv2.line(vis, (_cx1, _cy1), (_cx1 + _sx * _lw, _cy1), (255, 0, 255), 3)
                        cv2.line(vis, (_cx1, _cy1), (_cx1, _cy1 + _sy * _lw), (255, 0, 255), 3)
                    cv2.line(vis, (_cx, _cy), (int(_tcx), int(_tcy)), (255, 0, 255), 1)
                    cv2.circle(vis, (int(_tcx), int(_tcy)), 3, (255, 0, 255), -1)
                    _lock_draw_n += 1
                    # 粉框自检标签：box / 外推量 / dt / 距准星
                    if aim_log:
                        _age_ms = (time.time() - _boxes_t) * 1000.0 if _boxes_t else -1.0
                        cv2.putText(vis, f"LOCK {_lx2 - _lx1}x{_ly2 - _ly1} "
                                         f"d+{_gx:+.0f},{_gy:+.0f} dt{_dtu * 1000:.0f}ms "
                                         f"age{_age_ms:.0f}ms",
                                    (max(2, _lx1), max(12, _ly1 - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 0, 255), 1)
                    # ── 粉框遥测：无窗口模式（6-实战-无窗口.bat）也能诊断 ──
                    if aim_log and now - _lock_dbg_last > 2.0:
                        _lock_dbg_last = now
                        print("[粉框] 框=%dx%d 外推=%+.0f,%+.0f dt=%.0fms 帧龄=%.0fms "
                              "画帧=%d 命中=%d"
                              % (_lx2 - _lx1, _ly2 - _ly1, _gx, _gy, _dtu * 1000,
                                 (time.time() - _boxes_t) * 1000.0 if _boxes_t else -1.0,
                                 _lock_draw_n, len(hits)))
                frames += 1
                fps = frames / max(1e-6, time.time() - t0)
                state = "FIRING" if (enabled and allow_fire) else "idle"
                cv2.putText(vis, f"FPS {fps:5.1f}  {state}  targets {len(hits)}  fire {stat_fire} skip {stat_skip}",
                            (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
                # AIM 状态行：直接看出"为什么不拉枪"
                _st_txt = "未锁定目标"
                if lock_box is not None:
                    with aim_st["lock"]:
                        _d_now = max(abs(aim_st["ex"]), abs(aim_st["ey"]))
                        _bg = aim_st["budget"]
                        _hold = bool(aim_st.get("hold"))
                        _mv = "在移动" if (_bg > 0 and _d_now > aim_st["dead"]) else "已停(对准/预算空)"
                        _sent = aim_st.get("sent", 0)
                    _part = {"head": "头", "chest": "胸", "nose": "鼻"}.get(aim_pose_part or "", "框")
                    _st_txt = (f"锁{_part} 偏差{_d_now:.0f}px 预算{_bg:.0f}px {_mv} "
                               f"心跳{aim_st.get('ticks', 0)} 已发{_sent}")
                elif aim_st.get("hold"):
                    _st_txt = "目标瞬丢·保持瞄准中"
                if not aim_on:
                    _st_txt = "F6 关了"
                elif not allow_fire:
                    _st_txt = "测试模式(不动)"
                cv2.putText(vis, f"AIM {'ON' if aim_on else 'OFF'} | {_st_txt}", (8, 66),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
                cv2.putText(vis, "F9 toggle / q quit", (8, 46),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
                if preview is None:
                    preview = PreviewThread("YOLO trigger")
                preview.push(vis)
                if preview.quit:
                    quit_flag = True
            else:
                # no-window 模式：什么都不显示（原来这里每帧调用 Image.show()，
                # 会弹出图片查看器并让循环卡到几 fps —— 这是"FPS 太低"的一个元凶）
                pass

        _pt_d = time.perf_counter()           # 本帧全部逻辑+绘制结束
        perf_acc[0] += _pt_b - _pt_a          # 抓屏（流水线下应 ≈0）
        perf_acc[1] += _pt_c - _pt_b          # 推理+后处理（真·模型耗时）
        perf_acc[2] += _pt_d - _pt_c          # 自瞄/开火逻辑 + 绘制/显示
        perf_n += 1
        if aim_log and now - perf_last > 3.0:
            perf_last = now
            _n = max(1, perf_n)
            print(f"[性能] 抓屏 {perf_acc[0] / _n * 1000:.1f}ms | "
                  f"推理 {perf_acc[1] / _n * 1000:.1f}ms | "
                  f"逻辑+绘制 {perf_acc[2] / _n * 1000:.1f}ms | "
                  f"合计 {(perf_acc[0] + perf_acc[1] + perf_acc[2]) / _n * 1000:.1f}ms "
                  f"({_n / 3.0:.1f} fps)")
            perf_acc = [0.0, 0.0, 0.0]
            perf_n = 0
        if args.seconds and (time.time() - t0) >= args.seconds:
            break

    if holding:
        try:
            key_event(tkey, False)
            print("[松开] 退出前释放开火键")
        except Exception:
            pass
    if preview is not None:
        preview.close()
        print("[信息] 显示线程：画出 %d 帧%s"
              % (preview.drawn, "" if preview.err is None else "，异常 " + preview.err))
    if frame_src is not None:
        frame_src.close()
        print("[信息] 裁剪精算：命中 %d 帧 / 回退全图 %d 次"
              % (_crop_hits, _crop_miss))
        print("[信息] 抓屏线程：有效帧 %d 张%s"
              % (frame_src.ticks, "" if frame_src.err is None else "，异常 " + frame_src.err))
    grabber.close()
    if show_window and have_cv2 and preview is None:
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
    if allow_fire:
        fire_q.put(None)
    print(f"[信息] 已退出（开火 {stat_fire} 次 / 随机跳过 {stat_skip} 次 / 闪失取消 {stat_cancel} 次）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[信息] 手动中断")