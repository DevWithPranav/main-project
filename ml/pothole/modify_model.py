"""Build Plan M9 / decision #2: splice DSConv + SimAM (+ GELU) into YOLO26-seg (pothole track Phase 2).

Follows arXiv 2505.04207 (YOLOv8n-seg + DSConv/SimAM/GELU on PothRGBD), mapped onto YOLO26:
    DSConv  replaces every top-level 3x3 Conv of backbone and neck except the stem (layer 0).
            In YOLO26l-seg those are the six stride-2 downsampling convs (layers 1, 3, 5, 7, 17, 20).
            The paper also skips the first conv ("only edges and colour transitions").
    SimAM   after every C3k2 block of backbone and neck except the first one (layers 4..22);
            the paper does the same with C2f ("early layers have no discriminative features yet").
    GELU    replaces SiLU in every Conv (paper: all Conv blocks). Optional (--no-gelu) because it
            moves the pretrained features further than the other two changes.

Surgery keeps layer indices, `f`/`i` routing and output shapes, so the stock Segment26 head and loss
work unchanged. Pretrained weights are kept: a DSConv takes over its Conv's kernel and BatchNorm,
its offsets start at zero and its kernel is rescaled for the initial modulation mask, so a DSConv
equals the pretrained Conv exactly before training. SimAM (no parameters) and GELU do change the
features, so the modified network starts away from the stock output and needs the fine-tune;
--check prints the relative output gap with and without GELU.

Ultralytics rebuilds the model from YAML inside the trainer, which would undo the surgery, so
training goes through make_trainer(), a SegmentationTrainer whose get_model() applies it. Checkpoints
pickle the modified modules as `modules.DSConv` etc.: load them after `import modify_model` (it puts
ml/pothole on sys.path), as evaluate.py does.

Usage:
    python ml/pothole/modify_model.py --check                      # forward/backward on GPU or CPU
    python ml/pothole/modify_model.py --check --weights yolo26l-seg.pt --imgsz 640 --batch 2
"""

import argparse
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from modules import DSConv, SimAM  # noqa: E402

from ultralytics.models.yolo.segment import SegmentationTrainer  # noqa: E402
from ultralytics.nn.modules import C3k2, Conv  # noqa: E402
from ultralytics.nn.tasks import SegmentationModel  # noqa: E402

DEFAULT_MODS = {"dsconv": True, "simam": True, "gelu": True}
MASK_BIAS_INIT = 3.0  # initial modulation sigmoid(3) = 0.95, compensated in the kernel (_dsconv_from)


class C3k2SimAM(nn.Module):
    """A C3k2 followed by SimAM; carries the ultralytics routing attributes (f, i, type, np)."""

    def __init__(self, block: nn.Module, e_lambda: float = 1e-4):
        super().__init__()
        self.block = block
        self.simam = SimAM(e_lambda)
        for a in ("f", "i", "type", "np"):
            if hasattr(block, a):
                setattr(self, a, getattr(block, a))

    def forward(self, x):
        return self.simam(self.block(x))


def _dsconv_from(conv: Conv, act: nn.Module) -> DSConv:
    c = conv.conv
    ds = DSConv(c.in_channels, c.out_channels, k=c.kernel_size[0], s=c.stride[0], p=c.padding[0],
                g=c.groups, d=c.dilation[0], act=act)
    ds = ds.to(c.weight.device, c.weight.dtype)
    with torch.no_grad():
        # the kernel is divided by the initial mask value so DSConv == Conv exactly at the start
        # (zero offsets), while the mask keeps a usable gradient (sigmoid'(3) = 0.045)
        ds.weight.copy_(c.weight / torch.sigmoid(torch.tensor(MASK_BIAS_INIT)))
        ds.bn.load_state_dict(conv.bn.state_dict())
        ds.mask_conv.bias.fill_(MASK_BIAS_INIT)
    ds.bn.eps, ds.bn.momentum = conv.bn.eps, conv.bn.momentum  # ultralytics sets eps=1e-3, momentum=0.03
    for a in ("f", "i", "type", "np"):
        if hasattr(conv, a):
            setattr(ds, a, getattr(conv, a))
    return ds


def apply_mods(model: SegmentationModel, mods: dict | None = None) -> SegmentationModel:
    """In-place surgery on a (stock) YOLO26-seg DetectionModel; returns it. Idempotence is guarded."""
    mods = {**DEFAULT_MODS, **(mods or {})}
    if getattr(model, "m9_mods", None):
        return model
    layers = model.model
    head = len(layers) - 1  # Segment26 head stays stock
    if mods["gelu"]:
        for m in layers.modules():
            if isinstance(m, Conv):
                m.act = nn.GELU()
    act = nn.GELU() if mods["gelu"] else nn.SiLU()
    first_c3k2 = next(i for i, m in enumerate(layers) if isinstance(m, C3k2))
    for i in range(1, head):
        m = layers[i]
        if mods["dsconv"] and type(m) is Conv and m.conv.kernel_size[0] > 1:
            layers[i] = _dsconv_from(m, act)
        elif mods["simam"] and isinstance(m, C3k2) and i != first_c3k2:
            layers[i] = C3k2SimAM(m)
    model.m9_mods = mods
    if isinstance(getattr(model, "yaml", None), dict):
        model.yaml["m9_mods"] = mods  # saved in checkpoints, tells evaluate.py what it is loading
    return model


def build_modified(weights: str | Path | nn.Module | None, cfg: str | dict = "yolo26l-seg.yaml", nc: int | None = None,
                   mods: dict | None = None, verbose: bool = False) -> SegmentationModel:
    """Stock model from cfg (+ stock weights), then the surgery. A modified checkpoint (m9_mods set)
    is loaded after the surgery so its DSConv/SimAM weights land in the right places."""
    if isinstance(weights, (str, Path)):
        from ultralytics.nn.tasks import load_checkpoint
        weights, _ = load_checkpoint(str(weights))
    if isinstance(weights, nn.Module) and isinstance(getattr(weights, "yaml", None), dict) and cfg == "yolo26l-seg.yaml":
        cfg = {k: v for k, v in weights.yaml.items() if k != "m9_mods"}
    if nc is None:
        nc = cfg.get("nc", 1) if isinstance(cfg, dict) else 1
    model = SegmentationModel(cfg, nc=nc, verbose=verbose)
    modified_ckpt = isinstance(weights, nn.Module) and getattr(weights, "m9_mods", None)
    if isinstance(weights, nn.Module) and not modified_ckpt:
        model.load(weights, verbose=verbose)
    apply_mods(model, modified_ckpt or mods)
    if modified_ckpt:
        sd = weights.float().state_dict()
        missing, unexpected = model.load_state_dict(sd, strict=False)
        if missing or unexpected:
            print(f"[modify_model] load: {len(missing)} missing, {len(unexpected)} unexpected keys")
    return model


def make_trainer(mods: dict | None = None):
    """SegmentationTrainer subclass whose model is the modified one; pass as model.train(trainer=...)."""
    mods = {**DEFAULT_MODS, **(mods or {})}

    class ModifiedSegTrainer(SegmentationTrainer):
        def get_model(self, cfg=None, weights=None, verbose=True):
            from ultralytics.utils import RANK
            model = SegmentationModel(cfg, nc=self.data["nc"], ch=self.data["channels"], verbose=verbose and RANK == -1)
            model = self.set_model_names_for_load(model) if hasattr(self, "set_model_names_for_load") else model
            if weights is not None and getattr(weights, "m9_mods", None):
                apply_mods(model, weights.m9_mods)
                model.load_state_dict(weights.float().state_dict(), strict=False)
            else:
                if weights is not None:
                    model.load(weights)
                apply_mods(model, mods)
            if verbose and RANK == -1:
                n_ds = sum(isinstance(m, DSConv) for m in model.model)
                n_sa = sum(isinstance(m, C3k2SimAM) for m in model.model)
                print(f"[modify_model] mods={mods}: {n_ds} DSConv, {n_sa} SimAM, "
                      f"{sum(p.numel() for p in model.parameters()) / 1e6:.2f} M params")
            return model

    ModifiedSegTrainer.__name__ = "ModifiedSegTrainer"
    return ModifiedSegTrainer


def _tensors(o):
    if torch.is_tensor(o):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _tensors(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _tensors(v)


def check(weights: str, imgsz: int, batch: int, device: str, mods: dict) -> dict:
    """Forward/backward pass on random input; stock-vs-modified output gap before training."""
    from ultralytics.nn.tasks import load_checkpoint
    dev = torch.device(device if (device == "cpu" or torch.cuda.is_available()) else "cpu")
    stock, _ = load_checkpoint(weights)
    stock = stock.float().to(dev).eval()
    x = torch.rand(batch, 3, imgsz, imgsz, device=dev)

    def first(o):  # eval output: (decoded predictions, raw dict/list); the decoded tensor comes first
        while isinstance(o, (list, tuple)):
            o = o[0]
        return o

    gaps = {}
    with torch.no_grad():
        a = first(stock(x))
        # surgery without GELU must reproduce the stock output closely (weights carried over)
        for name, m in (("no_gelu", {**mods, "gelu": False}), ("requested", mods)):
            mm = build_modified(weights, mods=m).to(dev).eval()
            b = first(mm(x))
            gaps[name] = float((a - b).abs().max() / (a.abs().max() + 1e-9)) if a.shape == b.shape else float("nan")
    model = build_modified(weights, mods=mods).to(dev)
    model.train()
    for p in model.parameters():
        p.requires_grad_(True)
    if dev.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    with torch.autocast(dev.type, enabled=dev.type == "cuda"):
        out = model(x)
    loss = sum(t.float().pow(2).mean() for t in _tensors(out))
    loss.backward()
    if dev.type == "cuda":
        torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    ds = [m for m in model.model if isinstance(m, DSConv)]
    res = {
        "device": str(dev), "imgsz": imgsz, "batch": batch, "mods": mods,
        "n_dsconv": len(ds), "n_simam": sum(isinstance(m, C3k2SimAM) for m in model.model),
        "params_m_stock": sum(p.numel() for p in stock.parameters()) / 1e6,
        "params_m_modified": sum(p.numel() for p in model.parameters()) / 1e6,
        "rel_out_diff_vs_stock_no_gelu": round(gaps["no_gelu"], 4),
        "rel_out_diff_vs_stock_requested": round(gaps["requested"], 4),
        "offset_grad_nonzero": all(m.offset_conv.weight.grad is not None and m.offset_conv.weight.grad.abs().sum() > 0 for m in ds),
        "fwd_bwd_s": round(dt, 3),
        "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if dev.type == "cuda" else None,
    }
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="forward/backward smoke check")
    ap.add_argument("--weights", default="yolo26l-seg.pt")
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--device", default="0")
    ap.add_argument("--no-gelu", action="store_true")
    ap.add_argument("--no-dsconv", action="store_true")
    ap.add_argument("--no-simam", action="store_true")
    a = ap.parse_args()
    mods = {"dsconv": not a.no_dsconv, "simam": not a.no_simam, "gelu": not a.no_gelu}
    if a.check:
        dev = "cpu" if a.device == "cpu" else f"cuda:{a.device}"
        for k, v in check(a.weights, a.imgsz, a.batch, dev, mods).items():
            print(f"{k:28s} {v}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
