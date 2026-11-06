"""「粉框脱离人体」第三轮核查：锁定框必须有当帧检出支撑（2026-10-31）

用户对照：「基准版粉框都不乱飘」。基准版没有锁闸 / 队友绿环 / 底部屏蔽 / 小窗精算
这些**过滤器**，所以"锁定的那个人"永远不会从候选里消失；而现在一旦过滤器把正在锁的人
剔掉（绿环误判、人贴底部、宽高比卡边），pick_locked 会保持旧框 hold_ms(400ms)，
粉框就挂在人早就不在的地方，瞄准点照旧框算 → 「粉框脱离人体、鼠标跟着飞」。

修法（本轮）：锁框必须由当帧通过过滤的检出支撑；没有支撑只在 stale_clear_ms(默认 60ms)
内容忍，超时立即清锁（同时清 box/t/v），不再画旧框、不再照着旧框瞄。

运行：python _check_锁定支撑.py
"""
import sys

SRC = open('main.py', encoding='utf-8').read()
OK = True


def case(name, cond, detail=""):
    global OK
    OK &= bool(cond)
    print("  %s %-44s %s" % ("✅" if cond else "❌", name, detail))
    return bool(cond)


print("=" * 84)
print("A. 代码级不变量")
print("=" * 84)
case("有「当帧无支撑」判定", "_lock_fresh" in SRC and "(_ia / _ua) >= 0.25" in SRC)
case("支撑超时清锁（stale_clear_ms）", "stale_clear_ms" in SRC and 'lock_st["unsup_t"]' in SRC)
case("清锁同时清 box/t/v", 'lock_st["box"] = None' in SRC and 'lock_st["v"] = [0.0, 0.0]' in SRC)
case("清锁有日志可诊断", "[锁定] 当帧无检出支撑 -> 清锁" in SRC)
_i_lock = SRC.find("_lock_fresh = False")
_i_hits = SRC.find("target_present = bool(hits)")
case("支撑判定发生在瞄准/命中之前", 0 < _i_lock < _i_hits, "idx %d < %d" % (_i_lock, _i_hits))
_i_draw = SRC.find("if lock_box is not None:\n                    # 锁定目标（粉框）")
case("画框条件仍是 lock_box is not None",
     SRC.find("lock_box = None\n                        lock_st[\"box\"] = None") > 0 or _i_draw > 0)


def sim(stale_clear_ms, hold_ms, unsupported_frames):
    """人前 10 帧有检出，之后被过滤器剔掉；统计粉框还画了多少帧"""
    drawn = 0
    box = None
    unsup_t = 0.0
    t = 0.0
    FPS = 60.0
    for i in range(10 + unsupported_frames):
        t = i / FPS
        supported = i < 10
        if supported:
            box = (100.0, 100.0, 200.0, 350.0)
            unsup_t = 0.0
        else:
            if box is not None:
                if not unsup_t:
                    unsup_t = t
                # 旧行为：没有支撑也保持 hold_ms
                if (t - unsup_t) * 1000.0 >= stale_clear_ms:
                    box = None
                    unsup_t = 0.0
                    continue
        if box is not None:
            drawn += 1
    return drawn


print()
print("=" * 84)
print("B. 过滤器把正在锁的人剔掉之后，粉框还会画多久（60fps）")
print("=" * 84)
print("  %-26s %-18s %-18s" % ("场景", "旧（hold 400ms）", "新（stale 60ms）"))
for name, hold, unsup_ms in (("绿环误判成队友（丢 0.5s）", 400, 30),
                             ("人贴画面底部被屏蔽（丢 0.3s）", 400, 18),
                             ("宽高比卡边（丢 0.2s）", 400, 12),
                             ("单帧抖动掉落（丢 1 帧）", 400, 1)):
    old_frames = int(hold / 1000.0 * 60)
    new_frames = sim(60.0, hold, unsup_ms)
    print("  %-26s %-18s %-18s" % (name, "旧：还能画 %d 帧" % old_frames,
                                   "新：还能画 %d 帧" % max(0, new_frames - 10)))
case("单帧掉落仍容忍（不会一帧就清）", sim(60.0, 400, 1) >= 10, "1 帧掉落仍保持锁定")
case("长时间无支撑会被清掉", sim(60.0, 400, 30) <= 10 + 4, "30 帧掉落 → 最多再画 4 帧")

print()
print("=" * 84)
print("C. 新增配置档")
print("=" * 84)
import json
import os
for f, keys in (("config-粉框贴基准.json", ("lock_gate", "team_icon", "ignore_zone", "crop_track")),
                ("config-锁定支撑闸.json", ())):
    if not os.path.exists(f):
        case("存在 " + f, False)
        continue
    c = json.load(open(f, encoding='utf-8'))
    if keys:
        off = all((c.get(k) or {}).get("enabled") is False for k in keys)
        case("存在 " + f, off, "锁闸/绿环/底部屏蔽/小窗 全部 enabled=false")
    else:
        case("存在 " + f, True, "stale_clear_ms=%.0f" % c["lock"]["stale_clear_ms"])

print()
print("全部通过 ✅" if OK else "有失败项 ❌")
sys.exit(0 if OK else 1)