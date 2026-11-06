# -*- coding: utf-8 -*-
"""跟随线程 × 近距直取 集成测试（2026-10-30）

不打游戏、不动鼠标：把 main.mouse_move 换成记录器，真跑 start_aim_follow 那个线程，
按"检测帧喂预算 → 1ms tick 花预算"的真实节奏走一遍，验证：

  1) 贴脸带（偏差 60px、预算=偏差、abs_cap=0.95）一个周期就该走到 ~95%，且**不越过目标**
  2) 带外（abs_cap=0.85）同样的偏差一个周期也只走 85%，说明新旧口径确实不同
  3) 走完一个周期后线程必须停下（不许继续发像素，否则就是"抽"）

用法：python _test_跟随线程.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as M  # noqa: E402

SENT = []          # 记录每次 mouse_move 的 (dx, dy)
SENT_LOCK = threading.Lock()


def fake_mouse_move(dx, dy):
    with SENT_LOCK:
        SENT.append((dx, dy))
    return True


def make_st(abs_cap, clamp=True):
    st = {
        "lock": threading.Lock(), "rnd": None, "ex": 0.0, "ey": 0.0,
        "valid": False, "budget": 0.0, "k": [1.0, 1.0], "gain": 1.0,
        "dead": 2.0, "noise": 0.0, "maxstep": 520.0,
        "tau": 0.02, "tau_floor": 0.1, "sync_t": 0.0,
        "cmd": [0.0, 0.0], "cc": [0.0, 0.0], "step_cap": 0.75,
        "abs_cap": abs_cap, "hold": False, "jump_cap_px": 150.0,
        "follow_clamp": clamp,
        "tx": 0.0, "ty": 0.0, "obs": None,     # 线程启动瞬间就会读，先给上
    }
    return st


def run_cycle(st, err_px, budget, cx0=0.0):
    """模拟一个检测周期：喂目标偏差 + 预算，等 25ms，读回实际发出的像素。"""
    with SENT_LOCK:
        SENT.clear()
    with st["lock"]:
        st["tx"], st["ty"] = cx0 + err_px, 0.0      # 目标绝对坐标（准星在 0）
        st["obs"] = (err_px, 0.0)
        st["budget"] = budget
        st["cmd"] = [0.0, 0.0]
        st["sync_t"] = time.time()
        st["valid"] = True
    time.sleep(0.025)
    with SENT_LOCK:
        total = sum(dx for dx, _ in SENT)
        n = len(SENT)
    return total, n


def main():
    M.mouse_move = fake_mouse_move            # 绝不动真鼠标
    st = make_st(abs_cap=0.95)
    M.start_aim_follow(st, 1)
    time.sleep(0.02)                          # 线程起来

    print("=" * 74)
    print("一、贴脸带：偏差 60px，预算=60px（near_boost_ratio=1.0），abs_cap=0.95")
    print("=" * 74)
    # 先来一帧"对齐"（obs 让它知道准星在哪）
    run_cycle(st, 60.0, 60.0)
    moved, n = run_cycle(st, 60.0, 60.0, cx0=0.0)
    print("  本周期发出 %5.1fpx（%2d 次）→ 残余偏差 %.1fpx" % (moved, n, 60.0 - moved))
    assert 0.0 < moved <= 60.0, "不许越过目标（单调逼近）"
    assert moved >= 60.0 * 0.9, "贴脸带该走到 ≥90%（旧口径只有 55%~85%）"

    print()
    print("=" * 74)
    print("二、旧口径对照：偏差 60px、预算=60×near_ratio(0.55)=33px、abs_cap=0.85")
    print("=" * 74)
    st2 = make_st(abs_cap=0.85)
    M.start_aim_follow(st2, 1)
    time.sleep(0.02)
    run_cycle(st2, 60.0, 33.0)
    moved2, n2 = run_cycle(st2, 60.0, 33.0)
    print("  本周期发出 %5.1fpx（%2d 次）→ 残余偏差 %.1fpx" % (moved2, n2, 60.0 - moved2))
    # 绝对闸是"每步发出**前**检查"，所以最后一个整步可能略微超过闸值（≤1 步）
    assert moved2 <= 60.0 * 0.75, "旧口径下这一周期只该走到预算那一份"
    assert moved2 <= moved + 1e-6, "旧口径不该比新口径走得更多"

    print()
    print("=" * 74)
    print("三、follow_clamp=false（旧的反写钳位）一周期只发一步 —— 这就是「磨」的机械原因")
    print("=" * 74)
    st3 = make_st(abs_cap=0.95, clamp=False)
    M.start_aim_follow(st3, 1)
    time.sleep(0.02)
    run_cycle(st3, 60.0, 60.0)
    moved3, n3 = run_cycle(st3, 60.0, 60.0)
    print("  本周期发出 %5.1fpx（%2d 次）→ 残余偏差 %.1fpx" % (moved3, n3, 60.0 - moved3))
    assert moved3 < moved, "旧钳位应该明显比新钳位走得少"
    assert n3 <= 1, "旧钳位每周期只发一步"

    print()
    print("=" * 74)
    print("四、预算花完后必须停手（不许自己接着发）")
    print("=" * 74)
    with SENT_LOCK:
        SENT.clear()
    time.sleep(0.03)
    with SENT_LOCK:
        leftover = len(SENT)
    print("  预算=0 后 30ms 内又发了 %d 次（应为 0）" % leftover)
    assert leftover == 0, "预算花完还在发像素 = 准星会抽"

    print()
    print("全部通过 ✅（本测试没有触碰真实鼠标）")


if __name__ == "__main__":
    main()