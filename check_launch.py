"""启动冒烟（2026-10-31）：跑真实 main.py 12 秒，看有没有 Traceback

为什么单独做这个：k 矩阵接入时把 `_bh` 写在了"锁定分支"之前，导致**一启动就
UnboundLocalError**；而我当时只看日志前 6 行（都是正常的 [信息] 行），没看退出码与尾部，
于是把"一启动就崩"当成"能跑"交付了。这个脚本把这条纪律固化下来：
  ① 退出码必须是 timeout(124) —— 说明它一直在跑，而不是自己退了
  ② 输出里不许出现 Traceback
  ③ 必须打印到 [性能] 行 —— 说明推理循环真的在转

运行：python check_launch.py        （在含 main.py 的目录里跑，或指定路径）
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_main():
    for cand in (os.path.join(HERE, "main.py"),
                 r"C:\yolo-trigger\main.py",
                 r"C:\Workzone\yolo-trigger-kalman\main.py"):
        if os.path.exists(cand):
            return cand
    return None


main_py = find_main()
if main_py is None:
    print("❌ 找不到 main.py")
    sys.exit(1)
workdir = os.path.dirname(main_py)
print("=" * 78)
print("启动冒烟：%s" % main_py)
print("=" * 78)

env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
try:
    r = subprocess.run([sys.executable, "-u", "main.py", "--no-window"],
                       cwd=workdir, env=env, capture_output=True, text=True,
                       timeout=12, encoding="utf-8", errors="replace")
    out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
except subprocess.TimeoutExpired as e:
    out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + \
          ((e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""))
    code = 124

ok = True


def case(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print("  %s %-44s %s" % ("✅" if cond else "❌", name, detail))


tb = "Traceback (most recent call last)" in out
perf = len(re.findall(r"\[性能\]", out))
case("没有 Traceback", not tb)
case("跑满 12 秒没自己退出（退出码 124）", code == 124, "code=%s" % code)
case("推理循环在转（有 [性能] 行）", perf >= 2, "%d 行（GPU 被游戏占用时会变少）" % perf)

if tb:
    print("\n---- 崩溃现场 ----")
    idx = out.find("Traceback")
    print(out[idx:idx + 1200])
if code != 124 and not tb:
    print("\n---- 最后 15 行 ----")
    print("\n".join(out.splitlines()[-15:]))

print()
print("全部通过 ✅" if ok else "有失败项 ❌")
sys.exit(0 if ok else 1)