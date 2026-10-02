"""Optional detection filters applied before tracking (Improvement Plan item A4).

Both are off by default until they have been scored on the ground-truth clip.

- Per-class confidence gates: rare classes need a higher score (bus/truck >= 0.4).
  The global detector conf stays low (0.1) so weak car boxes can still continue tracks.
- Size sanity filter: drop boxes far outside the scene's typical vehicle size
  (< 0.3x or > 4x the running median of sqrt(w*h)). A drone at a fixed altitude sees
  vehicles at a fairly constant size, so a tiny or huge box is usually a snow/roof blob
  or a merged group. The median is taken over the last SIZE_WINDOW kept boxes and only
  used once SIZE_MIN_SAMPLES boxes have been seen, so altitude changes are followed.

Registered as an on_predict_postprocess_end callback *before* dedupe_boxes and the
tracker: a gated bus box then no longer suppresses the car box of the same vehicle.

Caveat: TrackTrack re-injects boxes that its own loose NMS pass finds and the tight
pass doesn't (dets_del), taken from the raw predictions — a gated box above
track_high_thresh can come back that way.
"""

from collections import deque

import numpy as np
import torch

CLASS_MIN_CONF = {"bus": 0.4, "truck": 0.4}
SIZE_MIN_RATIO, SIZE_MAX_RATIO = 0.3, 4.0
SIZE_WINDOW = 2000
SIZE_MIN_SAMPLES = 50


class DetectionFilter:
    def __init__(self, class_gates: bool = False, size_filter: bool = False):
        self.class_gates = class_gates
        self.size_filter = size_filter
        self.sizes: deque[float] = deque(maxlen=SIZE_WINDOW)
        self.stats = {"boxes_in": 0, "dropped_class_gate": 0, "dropped_size": 0}

    def config(self) -> dict:
        return {
            "class_gates": CLASS_MIN_CONF if self.class_gates else None,
            "size_filter": {"min_ratio": SIZE_MIN_RATIO, "max_ratio": SIZE_MAX_RATIO, "window": SIZE_WINDOW,
                            "min_samples": SIZE_MIN_SAMPLES} if self.size_filter else None,
        }

    def __call__(self, predictor) -> None:
        for i, r in enumerate(predictor.results):
            if r.boxes is None or len(r.boxes) == 0:
                continue
            boxes = r.boxes
            self.stats["boxes_in"] += len(boxes)
            keep = torch.ones(len(boxes), dtype=torch.bool, device=boxes.conf.device)

            if self.class_gates:
                cls = boxes.cls.int().tolist()
                min_conf = torch.tensor([CLASS_MIN_CONF.get(r.names[c], 0.0) for c in cls], device=boxes.conf.device)
                gated = boxes.conf < min_conf
                self.stats["dropped_class_gate"] += int(gated.sum())
                keep &= ~gated

            if self.size_filter:
                wh = boxes.xywh[:, 2:4]
                size = (wh[:, 0] * wh[:, 1]).clamp(min=0).sqrt()
                if len(self.sizes) >= SIZE_MIN_SAMPLES:
                    median = float(np.median(self.sizes))
                    odd = (size < SIZE_MIN_RATIO * median) | (size > SIZE_MAX_RATIO * median)
                    self.stats["dropped_size"] += int((odd & keep).sum())
                    keep &= ~odd
                self.sizes.extend(size[keep].tolist())

            if not keep.all():
                predictor.results[i] = r[keep.nonzero().flatten()]
