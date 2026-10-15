"""TrackTrack with buffered IoU association (C-BIoU, Yang et al., WACV 2023) for fast vehicles at the live frame rate.

TrackTrack matches a track to a detection only when their boxes overlap (IoU > 0.1, cost = 1 - HIoU x IoU below
match_thresh), and a new track is matched to the next frame's box with no motion prediction at all; it needs
min_track_len further matches before it is drawn. The live pipeline processes ~8 of the ~30 frames it receives
(latest-frame-wins), so a car moves 2-4 m (half to one car length) between processed frames: above ~70 km/h a new
car's boxes no longer overlap enough and it is never confirmed (recorded Town04 highway, 2026-10-15: 3-4 % of the cars
at 70-144 km/h boxed, against 59 % below 70).

Buffered IoU compares the boxes enlarged around their centres by `iou_buffer` x their width / height on each side,
so a box that moved about its own length still overlaps its track. Track-aware initialisation (tai_thr) keeps the
true boxes: a car next to a tracked one can still start its own track. iou_buffer 0 (the default) is stock TrackTrack.

Registered as tracker_type "tracktrack" (a config without iou_buffer runs unchanged) on import.
"""

import numpy as np
from ultralytics.trackers import track as _track
from ultralytics.trackers.track_tracker import TRACKTRACK, _angle_distance, _confidence_distance, _cosine_distance
from ultralytics.utils.metrics import bbox_ioa


def _buffered(boxes: np.ndarray, b: float) -> np.ndarray:
    w, h = boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]
    return np.stack([boxes[:, 0] - b * w, boxes[:, 1] - b * h, boxes[:, 2] + b * w, boxes[:, 3] + b * h], axis=1)


def hmiou_distance(tracks_a, tracks_b, buffer: float = 0.0):
    """(iou_sim, 1 - HIoU x IoU) as TrackTrack's _hmiou_distance, on boxes enlarged by `buffer`."""
    n, m = len(tracks_a), len(tracks_b)
    if n == 0 or m == 0:
        return np.zeros((n, m), dtype=np.float32), np.ones((n, m), dtype=np.float32)
    a = np.ascontiguousarray([t.xyxy for t in tracks_a], dtype=np.float32)
    b = np.ascontiguousarray([t.xyxy for t in tracks_b], dtype=np.float32)
    if buffer > 0:
        a, b = _buffered(a, buffer), _buffered(b, buffer)
    iou_sim = bbox_ioa(a, b, iou=True)
    h_over = np.minimum(a[:, 3:4], b[:, 3:4].T) - np.maximum(a[:, 1:2], b[:, 1:2].T)
    h_union = np.maximum(a[:, 3:4], b[:, 3:4].T) - np.minimum(a[:, 1:2], b[:, 1:2].T)
    h_iou = np.clip(h_over / (h_union + 1e-9), 0, 1)
    return iou_sim, 1.0 - h_iou * iou_sim


class BufferedTrackTrack(TRACKTRACK):
    def __init__(self, args, *a, **k):
        super().__init__(args, *a, **k)
        self.iou_buffer = float(getattr(args, "iou_buffer", 0.0) or 0.0)

    def _cost_matrix(self, tracks, dets):
        """TrackTrack's multi-cue cost with the IoU terms on buffered boxes."""
        iou_sim, hmiou_dist = hmiou_distance(tracks, dets, self.iou_buffer)
        if self.encoder is not None:
            cos = _cosine_distance(tracks, dets)
            cost = np.where(np.isnan(cos), hmiou_dist, self.iou_weight * hmiou_dist + self.reid_weight * cos)
        else:
            cost = hmiou_dist
        cost += self.conf_weight * _confidence_distance(tracks, dets)
        if iou_sim.size > 0:
            supported = iou_sim > 0.10
            pairs = np.nonzero(supported)
            if len(pairs[0]):
                cost[pairs] += self.angle_weight * _angle_distance(tracks, dets, self.frame_id, pairs)
            cost[~supported] = 1.0
        return np.clip(cost, 0, 1)


_track.TRACKER_MAP["tracktrack"] = BufferedTrackTrack
