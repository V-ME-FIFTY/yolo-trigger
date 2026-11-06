# -*- coding: utf-8 -*-
"""手动调 k（像素/计数）。改完必须重启 main.py 生效。"""
import json, io, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
c = json.load(io.open("config.json", encoding="utf-8"))
cur = float(c["kmatrix"]["prior"][0])
print("当前 k = %.2f   (发计数 = 像素 / k)" % cur)
print("  过冲/冲过头 -> k 太小 -> 加大   |   不到位/停在前头 -> k 太大 -> 减小")
s = input("新 k（回车不改，+0.2 / -0.2 也行）: ").strip()
if s:
    try:
        new = cur + float(s) if s[0] in "+-" else float(s)
    except ValueError:
        print("输入无效，未修改"); raise SystemExit(1)
    new = max(0.30, min(3.00, new))
    c["kmatrix"]["prior"] = [round(new, 3), round(new, 3)]
    json.dump(c, io.open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已设为 k = %.2f，重启 main.py 生效" % new)
