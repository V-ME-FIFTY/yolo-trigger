# -*- coding: utf-8 -*-
"""pose 模型的 TensorRT 推理后端（自写，绕开 ultralytics）
   流程：letterbox -> TRT(F P16) -> 解码 17 关键点 -> NMS -> 原图坐标
   输出与 ultralytics 对齐：boxes(xyxy) + keypoints[N,17,3](x,y,conf)
"""
import os

import numpy as np
import torch
import tensorrt as trt


class PoseTRT:
    def __init__(self, engine_path, imgsz=512, conf=0.35, iou=0.5, max_det=20):
        self.logger = trt.Logger(trt.Logger.ERROR)
        with open(engine_path, "rb") as f:
            self.engine = trt.Runtime(self.logger).deserialize_cuda_engine(f.read())
        if self.engine is None:
            raise RuntimeError("engine 反序列化失败: " + engine_path)
        self.ctx = self.engine.create_execution_context()
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.iou = float(iou)
        self.max_det = int(max_det)

        names = [self.engine.get_tensor_name(i) for i in range(self.engine.num_io_tensors)]
        self.in_name = next(n for n in names
                            if self.engine.get_tensor_mode(n) == trt.TensorIOMode.INPUT)
        self.out_names = [n for n in names
                          if self.engine.get_tensor_mode(n) == trt.TensorIOMode.OUTPUT]
        self.in_half = self.engine.get_tensor_dtype(self.in_name) == trt.DataType.HALF
        self.dt = torch.float16 if self.in_half else torch.float32

        in_shape = tuple(self.engine.get_tensor_shape(self.in_name))
        self.in_shape = (1, 3, self.imgsz, self.imgsz) if in_shape[0] < 0 or in_shape[2] < 0 \
            else in_shape
        self.buf_in = torch.empty(self.in_shape, dtype=self.dt, device="cuda")
        self.buf_out = {}
        for n in self.out_names:
            sh = tuple(self.engine.get_tensor_shape(n))
            dt = torch.float16 if self.engine.get_tensor_dtype(n) == trt.DataType.HALF else torch.float32
            self.buf_out[n] = torch.empty(sh, dtype=dt, device="cuda")
        self.ctx.set_tensor_address(self.in_name, self.buf_in.data_ptr())
        for n in self.out_names:
            self.ctx.set_tensor_address(n, self.buf_out[n].data_ptr())
        # 高优先级 CUDA 流：游戏把 GPU 跑满时（实测推理 4.1ms → 15.8ms），
# 让我们的 kernel 能优先被调度，抢回一部分时间。POSE_HIGH_PRIORITY=0 可关闭。
        _hp = os.environ.get("POSE_HIGH_PRIORITY", "1") != "0"
        try:
            self._cs = torch.cuda.Stream(priority=-1 if _hp else 0)
        except Exception:
            self._cs = torch.cuda.current_stream()
        self.stream = self._cs.cuda_stream
        # letterbox 用的整块画布只分配一次（512²×3 ≈ 786KB），避免每帧 np.full 重新分配+填充
        self._canvas = np.full((self.imgsz, self.imgsz, 3), 114, dtype=np.uint8)
        self._canvas_box = None
        self._warmup()

    def _warmup(self):
        z = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        self.infer(z)

    # ── 前处理 ──
    def _letterbox(self, img):
        h, w = img.shape[:2]
        r = min(self.imgsz / h, self.imgsz / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        if nh != h or nw != w:
            import cv2
            resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        else:
            resized = img
        top, left = (self.imgsz - nh) // 2, (self.imgsz - nw) // 2
        # 画布复用：只有填充区域变化时才整块刷回 114（画面尺寸固定时 = 零开销）
        box = (top, left, nh, nw)
        if self._canvas_box != box:
            self._canvas[:] = 114
            self._canvas_box = box
        canvas = self._canvas
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, r, left, top

    # ── 推理 ──
    def infer(self, frame_bgr):
        # ── 前处理尽量放 GPU：只把 uint8 画布传过去（786KB），
        #    归一化/通道转换/转置都在 GPU 上做，省掉 CPU 上的大数组运算（原来 5~10ms）──
        # dxcam 给的就是连续 BGR：只有真不连续/通道数不对时才拷贝（原来每帧白拷 1.9MB）
        if (not frame_bgr.flags["C_CONTIGUOUS"]) or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            frame_bgr = np.ascontiguousarray(frame_bgr[:, :, :3])
        canvas, r, left, top = self._letterbox(frame_bgr)
        # 前处理/拷贝必须也在同一条（高优先级）流上，否则顺序与优先级都不对
        with torch.cuda.stream(self._cs):
            t = torch.from_numpy(canvas).to("cuda", non_blocking=True)  # H,W,3 uint8
            t = t.permute(2, 0, 1).unsqueeze(0)                         # 1,3,H,W
            # 一步转成推理精度再归一化：省掉 .float() 与 .half() 的两次转换
            t = t.flip(1).to(self.dt).div_(255.0)                       # BGR->RGB + 0~1
            self.buf_in.copy_(t)
            self.ctx.execute_async_v3(self.stream)
        self._cs.synchronize()          # 只等自己这条流（比全设备同步更省）
        raw = self.buf_out[self.out_names[0]]                           # 仍在 GPU 上
        return self._post_gpu(raw, r, left, top)

    def infer_crop(self, frame_bgr, x0, y0, side):
        """只推理 frame 里目标周围的一块 side×side 小图，坐标仍返回**全图**坐标。

        为什么：锁定目标后没必要每帧都跑整幅 800×800（实测实战负载下 62ms/帧），
        在目标周围裁 imgsz×imgsz 的小图只需 12.5ms（5 倍快），而且源像素 1:1 进网络
        （不再是 800 压到 512 的 0.64 倍）→ 关键点更准，锁头更稳。

        映射很纯粹：小图坐标 + (x0, y0) = 全图坐标。
        """
        h, w = frame_bgr.shape[:2]
        side = int(side)
        x0 = int(max(0, min(w - side, x0)))
        y0 = int(max(0, min(h - side, y0)))
        crop = frame_bgr[y0:y0 + side, x0:x0 + side]
        boxes, kps, conf = self.infer(crop)
        if len(boxes):
            boxes = boxes.copy()
            boxes[:, [0, 2]] += x0
            boxes[:, [1, 3]] += y0
            if kps is not None and len(kps):
                kps = kps.copy()
                kps[:, :, 0] += x0
                kps[:, :, 1] += y0
        return boxes, kps, conf, (x0, y0, side)

    def _post_gpu(self, raw, r, left, top):
        """后处理：先在 GPU 上按置信度筛掉绝大多数锚点，只把有效行拷回 CPU"""
        o = raw[0]                                                      # [56, N] on GPU(f16)
        if o.shape[0] < o.shape[1]:   # [56, 5376] -> [5376, 56]
            o = o.transpose(0, 1)                                       # -> [N, 56]
        keep = o[:, 4] > self.conf       # 直接在 fp16 上比较，省掉整块 .float()（300K 元素）
        if not bool(keep.any()):
            return (np.zeros((0, 4), np.float32), np.zeros((0, 17, 3), np.float32),
                    np.zeros((0,), np.float32))
        sel = o[keep].float().cpu().numpy()                             # 只把有效行转/拷回来
        return self._post_np(sel, r, left, top)

    # ── 后处理 ──
    def _post_np(self, pred, r, left, top):
        """pred 已经是 [N, 56] 的 numpy（GPU 侧筛过）"""
        conf = pred[:, 4]
        cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / r
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / r

        kpts = pred[:, 5:].reshape(-1, 17, 3).copy()
        kpts[:, :, 0] = (kpts[:, :, 0] - left) / r
        kpts[:, :, 1] = (kpts[:, :, 1] - top) / r

        idx = self._nms(boxes, conf)
        return boxes[idx], kpts[idx], conf[idx]

    def _nms(self, boxes, scores):
        order = scores.argsort()[::-1]
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        keep = []
        while order.size > 0 and len(keep) < self.max_det:
            i = order[0]
            keep.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(x1[i], x1[rest])
            yy1 = np.maximum(y1[i], y1[rest])
            xx2 = np.minimum(x2[i], x2[rest])
            yy2 = np.minimum(y2[i], y2[rest])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
            order = rest[iou <= self.iou]
        return np.array(keep, dtype=np.int64)