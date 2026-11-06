# -*- coding: utf-8 -*-
"""生成「近距直取」的两个切换档（2026-10-30）

  config-近距直取.json          = 当前 config.json 快照（新基线）
  config-回退近距直取前.json    = 同上但把 5 个新开关关掉（回到 10-30 20:12 的手感）
  切换-近距直取.bat / 切换-回退近距直取前.bat  （GBK 编码，双击即用）

用法：python _make_近距直取档.py
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

OFF = {
    "near_boost_px": 0.0,        # 关掉贴脸带
    "follow_clamp": False,       # 恢复写反的钳位
    "near_damp_ratio": 0.45,     # 恢复"一律冻 250ms"
    "pose_smooth_big_px": 0.0,   # 关掉 EMA 自适应
    "fov_change_ref_px": 0.0,    # 关掉缩放阈值自适应
}

BAT = """@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
if not exist "{cfg}" (
  echo [FAIL] missing {cfg}
  pause
  exit /b 1
)
copy /Y "{cfg}" config.json >nul 2>&1
if errorlevel 1 (
  echo [FAIL] config.json 被占用，先关掉 yolo-trigger
  pause
  exit /b 1
)
echo [OK] {msg}
echo.
python -c "import json;a=json.load(open('config.json',encoding='utf-8'))['aim'];print('  near_boost_px',a.get('near_boost_px'),'| near_boost_ratio',a.get('near_boost_ratio'),'| abs_move_cap_near',a.get('abs_move_cap_near'),'| follow_clamp',a.get('follow_clamp'),'| near_damp_ratio',a.get('near_damp_ratio'))"
echo.
echo 重开 2-开始实战.bat 才生效
pause
"""


def write_gbk(name, text):
    with io.open(os.path.join(HERE, name), "w", encoding="gbk", newline="\r\n") as f:
        f.write(text)


def main():
    cfg = json.load(io.open(os.path.join(HERE, "config.json"), encoding="utf-8"))

    new = json.loads(json.dumps(cfg, ensure_ascii=False))
    io.open(os.path.join(HERE, "config-近距直取.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps(new, ensure_ascii=False, indent=2) + "\n")

    old = json.loads(json.dumps(cfg, ensure_ascii=False))
    old["aim"].update(OFF)
    io.open(os.path.join(HERE, "config-回退近距直取前.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps(old, ensure_ascii=False, indent=2) + "\n")

    write_gbk("切换-近距直取.bat", BAT.format(cfg="config-近距直取.json", msg="已切到【近距直取】：贴脸不再磨，一帧贴上"))
    write_gbk("切换-回退近距直取前.bat", BAT.format(cfg="config-回退近距直取前.json", msg="已切到【回退·近距直取前】：贴脸慢的手感回来了"))

    print("生成完毕：")
    for n in ("config-近距直取.json", "config-回退近距直取前.json",
              "切换-近距直取.bat", "切换-回退近距直取前.bat"):
        print("  ", n, os.path.getsize(os.path.join(HERE, n)), "B")


if __name__ == "__main__":
    main()