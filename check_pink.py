"""「粉框功能异常」核查（2026-10-31）

粉框 = 锁定框（main.py 画框颜色 255,0,255）。它出问题的形态有四种，本脚本逐条钉住：

  ① 看不见 / 变成红框   —— 粉框先画、同坐标的命中红框后画，把粉框整条盖掉
  ② 一帧有一帧没有     —— `show_every=2` 把整个 vis 抽帧，锁定后粉框闪一半
  ③ 永远慢一拍          —— 外推基准写成 aim 时刻（now - det_t ≈ 0），等于没外推
  ④ 甩到人旁边/墙上     —— 外推量没限幅，速度尖峰（上限 2400px/s）能甩出 140+px

运行：python _check_粉框.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "main.py"), encoding="utf-8").read()
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

OK = True


def case(name, cond, detail=""):
    global OK
    OK &= bool(cond)
    print("  %s %-40s %s" % ("✅" if cond else "❌", name, detail))
    return bool(cond)


print("=" * 78)
print("A. 绘制顺序：粉框必须最后画（否则被命中红框盖掉）")
print("=" * 78)
_i_green = SRC.find('for box in boxes:\n                    x1, y1, x2, y2 = [int(v) for v in box[:4]]')
_i_red = SRC.find("命中红框：锁定项已经由粉框表示")
_i_pink = SRC.find("# 锁定目标（粉框）")
case("绿框在命中红框之前", 0 <= _i_green < _i_red, "idx %d < %d" % (_i_green, _i_red))
case("命中红框在粉框之前（粉框最后画）", 0 <= _i_red < _i_pink, "idx %d < %d" % (_i_red, _i_pink))
case("同坐标红框跳过（不覆盖粉框）",
     "abs(x1 - lock_box[0]) < 1.5" in SRC and "continue" in SRC[_i_red:_i_red + 400])
case("粉框加粗到 3px 角标",
     "255, 0, 255), 3)" in SRC and "_lw = max(6, min(18" in SRC)

print()
print("=" * 78)
print("B. 抽帧：锁定时必须每帧画（不再 show_every=2 闪一半）")
print("=" * 78)
case("锁定强制 show_every=1",
     '_show_every = 1 if lock_box is not None else' in SRC,
     "show_every 配置=%s" % CFG.get("show_every"))

print()
print("=" * 78)
print("C. 外推基准：必须用抓帧时刻，不是 aim 计算时刻")
print("=" * 78)
case("_frame_t 在推理前打时间戳", "_frame_t = time.time()" in SRC)
case("boxes 带自己的时刻 _boxes_t", "_boxes_t = _frame_t" in SRC)
case("aim_st['det_t'] 用 _boxes_t",
     'aim_st["det_t"] = _boxes_t if _boxes_t else now' in SRC)
case("旧写法（det_t = now）已消失",
     'aim_st["det_t"] = now       # 本次观测时刻' not in SRC)

print()
print("=" * 78)
print("D. 外推限幅：位移夹在框尺寸的 extrap_max_ratio 以内")
print("=" * 78)
_ratio = float(CFG["aim"].get("extrap_max_ratio", 0.06))
_exabs = float(CFG["aim"].get("extrap_max_px", 12.0))
_vmax = float(CFG["aim"].get("vel_max_px_s", 2400))
_dtmax = float(CFG["aim"].get("extrap_max_ms", 60)) / 1000.0
case("代码里有限幅 _lim_x/_lim_y",
     "_lim_x = min(_bw_u * _exr, _exa)" in SRC)
case("配置有 extrap_max_ratio", "extrap_max_ratio" in CFG["aim"], "= %.2f" % _ratio)
case("配置有 extrap_max_px（绝对上限）", "extrap_max_px" in CFG["aim"], "= %.0fpx" % _exabs)


def draw_offset(v_px_s, dt_ms, bw, bh, ratio, clamp=True):
    dt = min(dt_ms / 1000.0, _dtmax)
    g = v_px_s * dt
    if clamp:
        lim = min(bw * ratio, _exabs)
        g = max(-lim, min(lim, g))
    return g


print()
print("  目标 100x250 的框、检测→绘制延迟 dt，看粉框心离开真值多少像素")
print("  %-10s %-8s %-14s %-14s %-10s" % ("目标速度", "dt", "旧(无限幅)", "新(限幅6%)", "改善"))
for v in (300, 600, 900, 2400):
    for dtms in (8, 16, 33, 60):
        old = draw_offset(v, dtms, 100, 250, _ratio, clamp=False)
        new = draw_offset(v, dtms, 100, 250, _ratio, clamp=True)
        print("  %-10s %-8s %-14s %-14s %-10s"
              % ("%dpx/s" % v, "%dms" % dtms,
                 "%+.1fpx" % old, "%+.1fpx" % new,
                 "→%+.0fpx" % (abs(old) - abs(new))))

_worst_old = max(abs(draw_offset(v, 60, 100, 250, _ratio, clamp=False)) for v in (2400,))
_worst_new = max(abs(draw_offset(v, 60, 100, 250, _ratio, clamp=True)) for v in (2400,))
case("最坏情况不再甩出一个框宽以上", _worst_old > 100 and _worst_new <= 15.0,
     "旧 %+.0fpx → 新 %+.0fpx（框宽 100）" % (_worst_old, _worst_new))

print()
print("=" * 78)
print("E. 外推的目标不是「追平延迟」，而是「框始终罩得住人」")
print("=" * 78)
bw, bh = 100, 250
for v in (300, 600, 900):
    for dtms in (8, 16, 33):
        # 延迟 dt 时框心落后 v*dt；外推 g 之后，画出来的框心与真值差 lag-g。
        # 判据：这个差必须还在框半宽以内 —— 否则就是用户说的「粉框脱离人体」。
        lag = v * dtms / 1000.0
        g = draw_offset(v, dtms, bw, bh, _ratio, clamp=True)
        resid = lag - g
        case("v=%dpx/s dt=%dms 画出的框仍罩住人（差 %.1fpx）" % (v, dtms, resid),
             abs(resid) <= bw * 0.5, "外推 %+.1fpx / 滞后 %.1fpx" % (g, lag))

print()
print("=" * 78)
print("F. 粉框遥测（无窗口模式也能诊断）")
print("=" * 78)
case("有无窗口遥测打印", '[粉框] 框=%dx%d 外推=' in SRC)
case("有帧龄 age 显示", "age{_age_ms:.0f}ms" in SRC)
case("_boxes_t / _lock_draw_n 已初始化",
     "_boxes_t = 0.0" in SRC and "_lock_draw_n = 0" in SRC)

print()
print("全部通过 ✅" if OK else "有失败项 ❌")
sys.exit(0 if OK else 1)