# -*- coding: utf-8 -*-
"""yolo-trigger 2.5 运行目录清理（2026-10-30）

原则：白名单外一律移走（**软删除，移到 _待删-20261030/，可原样拖回**）。
白名单 = 启动/切档必需 + 2.5 这版的自查脚本 + 最近三份版本记录 + 模型 + 当前预设。

保留（KEEP）
  · 0~6 启动与诊断 bat、查看GPU余量.bat
  · main.py / config.json / pose_trt.py / build_pose_engine.py
  · yolo11n_pose_512_fp16.engine、yolo11n-pose.pt（跑起来必需，42MB）
  · 全部 config-*.json 与 切换-*.bat（切档工作流，单个 4~8KB）
  · README-当前版本.md、README.md、体检报告-20261028.md
  · VERSION-2.1-常驻锁人.txt / VERSION-2.2-视频取证.txt / VERSION-2.5-近距直取.txt
  · 2.5 自查：_check_分段.py / _check_近距直取.py / _test_近距直取.py / _test_跟随线程.py
  · 生成器：_patch_近距直取.py / _make_近距直取档.py / _clean_workspace_25.py

移走（DROP）
  · main.py 历史快照（main.py.bak-*, main.py-扫描版-*, main.py-常驻锁人-*）
  · config.backup-*、config-扫描版-*、config-体检前-*、config-原样开火版.json
  · VERSION-2.0*.txt、VERSION-2.1.txt、VERSION-2.2.txt、VERSION-2.4.txt
  · 一次性探针 _check_*.py（除保留的 4 个）、_bench_*.py
  · 图片/日志残留 _teamicon_check.png、_cleanup-*.log
  · 目录 __pycache__、_回退-*、_历史脚本、_历史预设

用法：
    python _clean_workspace_25.py                 # dry-run（默认目录 = 脚本所在目录）
    python _clean_workspace_25.py --dir C:\\yolo-trigger
    python _clean_workspace_25.py --dir X --apply # 真移走
"""
import argparse
import fnmatch
import os
import shutil
import time

KEEP_DIRS = set()

KEEP_EXACT = set("""
0-安装依赖.bat
1-测试识别.bat
2-开始实战.bat
3-控制台测试.bat
4-live-admin.bat
5-自瞄诊断.bat
6-实战-无窗口.bat
查看GPU余量.bat
main.py
config.json
pose_trt.py
build_pose_engine.py
yolo11n_pose_512_fp16.engine
yolo11n-pose.pt
README.md
README-当前版本.md
体检报告-20261028.md
VERSION-2.1-常驻锁人.txt
VERSION-2.2-视频取证.txt
VERSION-2.5-近距直取.txt
_check_分段.py
_check_近距直取.py
_test_近距直取.py
_test_跟随线程.py
_patch_近距直取.py
_make_近距直取档.py
_clean_workspace_25.py
.gitignore
config-近距直取.json
config-回退近距直取前.json
切换-近距直取.bat
切换-回退近距直取前.bat
""".split())

KEEP_GLOB = [
    "config-*.json",          # 各档预设（切档 bat 指向它们）
    "切换-*.bat",
]


def keep(name):
    if name in KEEP_EXACT:
        return True
    return any(fnmatch.fnmatch(name, g) for g in KEEP_GLOB)


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%.0f KB" % (n / 1024.0)


def collect(root):
    files = [f for f in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, f))]
    dirs = [d for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]
    keep_f, drop_f = [], []
    for f in files:
        (keep_f if keep(f) else drop_f).append(f)
    drop_d = [d for d in dirs if d not in KEEP_DIRS]
    return keep_f, drop_f, drop_d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--apply", action="store_true", help="真移走（移到 _待删-20261030/）")
    args = ap.parse_args()
    root = os.path.abspath(args.dir)

    keep_f, drop_f, drop_d = collect(root)
    sz_keep = sum(os.path.getsize(os.path.join(root, f)) for f in keep_f)
    sz_drop = sum(os.path.getsize(os.path.join(root, f)) for f in drop_f)
    sz_dir = 0
    for d in drop_d:
        for base, _, fs in os.walk(os.path.join(root, d)):
            sz_dir += sum(os.path.getsize(os.path.join(base, x)) for x in fs if os.path.isfile(os.path.join(base, x)))

    print("=" * 80)
    print("目录 %s" % root)
    print("保留 %d 个文件（%s）｜移走 %d 个文件（%s） + %d 个目录（%s）"
          % (len(keep_f), human(sz_keep), len(drop_f), human(sz_drop), len(drop_d), human(sz_dir)))
    print("=" * 80)

    def show(title, names, kind="file"):
        if not names:
            return
        print("\n[%s] %d 项" % (title, len(names)))
        for n in names:
            p = os.path.join(root, n)
            s = sz_dir if kind == "dir" else os.path.getsize(p)
            print("   %-46s %s" % (n, human(s)))

    cats = {
        "main.py 历史快照": [f for f in drop_f if f.startswith("main.py")],
        "config 备份/旧档": [f for f in drop_f if f.startswith("config.") or
                            f in ("config-扫描版-20261027.json", "config-体检前-20261028-1245.json",
                                  "config-原样开火版.json")],
        "旧版本记录": [f for f in drop_f if f.startswith("VERSION-")],
        "一次性探针/基准": [f for f in drop_f if f.startswith(("_check_", "_bench_", "_toggle_"))],
        "其它残留": [],
    }
    used = set()
    for v in cats.values():
        used.update(v)
    cats["其它残留"] = [f for f in drop_f if f not in used]
    print()
    print("【将被移走的文件】")
    for t, names in cats.items():
        show(t, names)
    show("目录", drop_d, kind="dir")

    if not args.apply:
        print("\n(dry-run，什么都没动；加 --apply 才真移走)")
        return

    trash = os.path.join(root, "_待删-20261030")
    os.makedirs(trash, exist_ok=True)
    log = []
    for f in drop_f:
        shutil.move(os.path.join(root, f), os.path.join(trash, f))
        log.append("MOV_FILE\t%s" % f)
    for d in drop_d:
        if d == os.path.basename(trash):
            continue
        shutil.move(os.path.join(root, d), os.path.join(trash, d))
        log.append("MOV_DIR\t%s" % d)
    with open(os.path.join(trash, "_moved.log"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(log) + "\n")
    print("\n[已移走] %d 文件 + %d 目录 → %s" % (len(drop_f), len(drop_d), trash))
    print("撤销：把 _待删-20261030 里的东西拖回根目录即可；确认无误后直接删掉该目录。")


if __name__ == "__main__":
    main()