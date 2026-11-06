# -*- coding: utf-8 -*-
"""清理后完整性检查：每个 切换-*.bat 指向的 config 是否还在（2026-10-30）

用法：python _check_切换档完整性.py [--dir C:\\yolo-trigger]
退出码 0 = 全部指向存在的文件。
"""
import argparse
import io
import os
import re

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
a = ap.parse_args()
root = os.path.abspath(a.dir)

bad, ok = [], []
for f in sorted(os.listdir(root)):
    if not f.startswith("切换-") or not f.endswith(".bat"):
        continue
    txt = io.open(os.path.join(root, f), encoding="gbk", errors="replace").read()
    m = re.findall(r"copy /Y \"([^\"]+)\"", txt)
    if not m:
        bad.append((f, "解析不到 copy 行"))
        continue
    for t in m:
        if not os.path.isfile(os.path.join(root, t)):
            bad.append((f, t))
        else:
            ok.append((f, t))

print("目录 %s" % root)
print("  正常 %d 个切换档" % len(ok))
if bad:
    print("  ✗ 断链 %d 处：" % len(bad))
    for f, t in bad:
        print("     %-40s -> %s" % (f, t))
    raise SystemExit(1)
print("  全部指向存在的 config ✅")