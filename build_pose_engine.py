# -*- coding: utf-8 -*-
"""导出并编译指定输入尺寸的 pose engine（ONNX -> TensorRT FP16）。

用法：python build_pose_engine.py 416
产出：yolo11n_pose_416_fp16.onnx / yolo11n_pose_416_fp16.engine
不改动现有 512 的 engine，安全可回退。
"""
import os
import sys
import time

IMGSZ = int(sys.argv[1]) if len(sys.argv) > 1 else 416
PT = r"C:\yolo-trigger\yolo11n-pose.pt"
ROOT = r"C:\yolo-trigger"
ONNX = os.path.join(ROOT, "yolo11n_pose_%d_fp16.onnx" % IMGSZ)
ENG = os.path.join(ROOT, "yolo11n_pose_%d_fp16.engine" % IMGSZ)

# ── 1) ONNX ──
if not os.path.exists(ONNX):
    t0 = time.time()
    from ultralytics import YOLO
    m = YOLO(PT)
    out = m.export(format="onnx", imgsz=IMGSZ, half=True, simplify=True, opset=12, device=0)
    print("onnx 导出 ->", out, "用时 %.1fs" % (time.time() - t0))
    if os.path.abspath(out) != os.path.abspath(ONNX):
        os.replace(out, ONNX)
else:
    print("onnx 已存在，跳过导出：", ONNX)

# ── 2) TensorRT ──
import tensorrt as trt

logger = trt.Logger(trt.Logger.WARNING)
builder = trt.Builder(logger)
# TRT 10+ 默认就是 explicit batch，EXPLICIT_BATCH 标志在 11.x 已移除 → 传 0
flags = 0
network = builder.create_network(flags)
parser = trt.OnnxParser(network, logger)
with open(ONNX, "rb") as f:
    if not parser.parse(f.read()):
        for i in range(parser.num_errors):
            print("解析错误:", parser.get_error(i))
        raise SystemExit("ONNX 解析失败")

config = builder.create_builder_config()
# TRT 11 起 BuilderFlag.FP16 被移除（精度由 ONNX 自带的 fp16 权重决定），
# 这里做存在性判断，新旧版本都能用。
if hasattr(trt.BuilderFlag, "FP16"):
    config.set_flag(trt.BuilderFlag.FP16)
else:
    print("TRT 无 FP16 flag（11.x），依赖 ONNX 的 fp16 权重")
if hasattr(config, "set_memory_pool_limit") and hasattr(trt.MemoryPoolType, "WORKSPACE"):
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 2 << 30)

print("编译 engine（imgsz=%d, FP16）…" % IMGSZ)
t0 = time.time()
plan = builder.build_serialized_network(network, config)
if plan is None:
    raise SystemExit("build_serialized_network 返回 None")
with open(ENG, "wb") as f:
    f.write(plan)
print("engine ->", ENG, "%.1f MB，用时 %.1fs" % (os.path.getsize(ENG) / 1e6, time.time() - t0))