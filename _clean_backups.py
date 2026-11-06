# -*- coding: utf-8 -*-
"""清理 C:\\yolo-trigger 的历史备份（保留当前 + 基线 + 最近一份）。

规则：
  main.py 备份：保留 2115-测试版（基线）、204423-锁死不换人前（最近）、只画锁定框-已回退（配套）
  config 备份 ：保留 2115-测试版（基线）、200650-回退前（最近）
  config 预设 ：保留被 bat 引用的 3 个 + 当前模式对应 + 验证过的历史修复档
运行：python _clean_backups.py [--apply]
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APPLY = "--apply" in sys.argv

KEEP_MAIN = {
    "main.py.bak-20261031-2115-测试版",          # 基线
    "main.py.bak-20261101-204423-锁死不换人前",  # 最近一份
    "main.py.只画锁定框-已回退",                  # 与 config 配套的回退产物
}
KEEP_PRESET = {
    "config-极速.json", "config-平衡.json", "config-稳重.json",   # 切换-bat 引用
    "config-锁死不换人.json",                                     # 当前 lock.never_switch 对应
    "config-粉框贴基准.json", "config-锁定支撑闸.json", "config-不拉过头.json",  # 验证过的修复档
    "config-只画锁定框-已回退.json",
}
KEEP_BACKUP = {
    "config.backup-20261031-2115-测试版.json",   # 基线
    "config.backup-20261101-200650-回退前.json", # 最近一份
}
NEVER_TOUCH = {"main.py", "config.json", "k_matrix.json"}


def plan():
    del_main, del_cfg = [], []
    for p in glob.glob(os.path.join(HERE, "main.py.*")):
        n = os.path.basename(p)
        if n in KEEP_MAIN:
            continue
        del_main.append(p)
    for p in glob.glob(os.path.join(HERE, "config*.json")):
        n = os.path.basename(p)
        if n in KEEP_PRESET or n in KEEP_BACKUP or n in NEVER_TOUCH:
            continue
        del_cfg.append(p)
    return sorted(del_main), sorted(del_cfg)


def main():
    dm, dc = plan()
    total = 0
    print("=== 将删除 %d 个 main.py 备份 ===" % len(dm))
    for p in dm:
        sz = os.path.getsize(p)
        total += sz
        print("   %7.1fKB  %s" % (sz / 1024, os.path.basename(p)))
    print("\n=== 将删除 %d 个 config 文件 ===" % len(dc))
    for p in dc:
        sz = os.path.getsize(p)
        total += sz
        print("   %7.1fKB  %s" % (sz / 1024, os.path.basename(p)))
    print("\n合计释放 %.1f MB" % (total / 1048576))
    print("\n=== 保留 ===")
    for p in sorted(glob.glob(os.path.join(HERE, "main.py*")) + glob.glob(os.path.join(HERE, "config*.json"))):
        n = os.path.basename(p)
        if n not in [os.path.basename(x) for x in dm + dc]:
            print("   KEEP  %s" % n)
    if not APPLY:
        print("\n[dry-run] 加 --apply 才真删")
        return
    log = os.path.join(HERE, "_deleted-%s.log" % __import__("time").strftime("%Y%m%d-%H%M%S"))
    with open(log, "w", encoding="utf-8") as f:
        for p in dm + dc:
            f.write(p + "\n")
            os.remove(p)
    print("\n已删除，清单写入 %s" % os.path.basename(log))


if __name__ == "__main__":
    main()