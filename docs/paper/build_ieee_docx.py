from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

OUT = Path(__file__).with_name("aerial_surveillance_ieee_paper.docx")

# Content is intentionally limited to claims and measurements recorded in the
# project tracker through 2026-09-27. The paper distinguishes implemented work
# from architecture that remains planned.
TITLE = "An Evaluation-Driven Aerial Surveillance Framework for Traffic Monitoring and Urban Planning"
AUTHOR = "Afif Showfeer"
ABSTRACT = (
    "Urban traffic and road-condition monitoring remains spatially fragmented: fixed cameras observe selected locations, while aerial video can cover wider areas but introduces small objects, camera motion, occlusion, and identity ambiguity. This paper reports the implemented perception and evaluation components of an aerial surveillance framework intended to support traffic analysis and future urban-planning workflows. The vehicle pipeline combines a VisDrone2019-DET-fine-tuned YOLO26l detector, class-agnostic non-maximum suppression (NMS), online multi-object tracking, camera-motion-aware scene mapping, offline tracklet stitching, and track-level post-processing. On VisDrone2019-DET validation, the mean AP at IoU 0.5 is 0.585 over the four vehicle classes used by the project (car, van, truck, bus), compared with 0.457 over all ten dataset classes. On a 1,987-frame CARLA Town10HD clip with 40 annotated vehicles, the current BoT-SORT pipeline obtains HOTA 0.630, IDF1 0.859, and MOTA 0.829; a TrackTrack configuration obtains HOTA 0.631 and IDF1 0.874, exposing a small identity-versus-recall trade-off rather than a decisive winner. A separate YOLO26l-seg pothole baseline on PothRGBD achieves mask mAP@50 of 0.927. These results establish a measurable perception prototype, not a completed violation-detection or digital-twin system. Ground truth is simulator-generated, real-footage identity metrics are not yet available, and the backend, digital twin, violation rules, and aerial pothole transfer remain future work."
)
KEYWORDS = "UAV surveillance, aerial vehicle detection, multi-object tracking, CARLA, digital twin, road-surface anomaly detection"

SECTIONS = [
    ("I. INTRODUCTION", [
        ("p", "Urban road networks are observed through a patchwork of fixed cameras, periodic surveys, and incident reports. Fixed cameras provide persistent views but cover limited locations and are vulnerable to occlusion; manual road inspections are intermittent and expensive. A UAV can move its field of view across roads and intersections, potentially providing a broader record of traffic and pavement conditions. Turning that footage into planning evidence is not straightforward: vehicles occupy few pixels, the camera moves independently of traffic, and apparent image displacement mixes object motion with camera motion."),
        ("p", "This project develops an aerial video analysis pipeline as the perception foundation for a broader urban surveillance and planning framework. The intended system combines vehicle detection and tracking with road-surface anomaly detection, geospatial event storage, and a future three-dimensional digital twin. Its purpose is monitoring and decision support; automated citation or enforcement is outside the stated system scope. The work reported here focuses on components implemented and measured by September 2026, rather than presenting the complete intended platform as operational."),
        ("p", "The contribution is an evaluation-driven vehicle pipeline that includes class-agnostic deduplication, configurable online trackers, scene-map stabilization, offline tracklet stitching, confidence-weighted class consolidation, track filtering, and short-gap interpolation. The project also provides an imported CARLA ground-truth clip and a repeatable experiment runner that scores pipeline stages. A pothole segmentation baseline is reported as a separate perception branch. The principal empirical finding is that detector-output deduplication materially reduced track fragmentation on the tested recordings, while tracker rankings on the current CARLA clip remain close and require confirmation on a more challenging real-footage ground truth."),
        ("p", "The remainder of the paper reviews related work, describes the methodology and implementation, specifies the experimental protocol, presents detection and tracking results with their limitations, and concludes with the next validation steps."),
    ]),
    ("II. RELATED WORK", [
        ("h", "Aerial detection and tracking"),
        ("p", "VisDrone established a benchmark for drone-view detection and tracking, with strong variation in object scale, density, viewpoint, and occlusion [1], [2]. These properties motivate the use of aerial data for detector training but also expose a domain gap between benchmark imagery and simulator or operational footage. This project fine-tunes an Ultralytics YOLO26l model on VisDrone2019-DET and evaluates detections separately from identity association. The reported model is a project baseline, not a claim that a particular detector family is optimal."),
        ("p", "Modern tracking-by-detection systems associate detector boxes across frames. ByteTrack uses lower-confidence boxes to recover existing tracks [4]; BoT-SORT combines motion association, camera-motion compensation, and optional appearance cues [5]. Such methods can only associate objects supplied by the detector. In this project, duplicate cross-class boxes from the detector were a larger measured source of fragmentation than tracker choice, motivating class-agnostic NMS before association. TrackTrack is included as an alternate tracker configuration in the measured comparison; its result is treated as clip-specific evidence rather than a general ranking."),
        ("h", "Evaluation and camera motion"),
        ("p", "Tracking evaluation must distinguish correct identity continuity from simply producing fewer identifiers. HOTA jointly summarizes detection and association quality and is designed to balance the two [6]; IDF1 and MOTA provide complementary identity and event-error views. The project therefore scores detection precision and recall, IDF1, MOTA, HOTA, ID switches, and false-positive tracks against imported annotations. For moving cameras, global motion compensation alone provides frame-to-frame alignment but not a stable scene coordinate system. A keyframe-chained homography is implemented to express detections in a persistent map plane, following a Stabilo-based registration approach; its current evaluation is limited to the CARLA clip."),
        ("h", "Simulation and road-condition analysis"),
        ("p", "CARLA provides a controllable driving simulation for generating repeatable scenes and sensor data [7]. In this project, CARLA is used for aerial data capture and for an annotated 1080p validation clip. The clip enables controlled pipeline scoring but does not reproduce all real-UAV effects. Road distress detection is a distinct research problem; segmentation can represent irregular defect boundaries more directly than boxes. The implemented pothole model is an unmodified YOLO26l-seg baseline on PothRGBD. DSConv and SimAM are documented as possible architecture components [8], [9], but have not been integrated into a trained model. Neither the pothole result nor the simulator tracking result establishes transfer to real aerial pavement imagery."),
    ]),
    ("III. PROPOSED METHODOLOGY", [
        ("p", "The implemented vehicle workflow is: video or frame-folder input; YOLO26l inference; optional class and size filters; class-agnostic NMS; online tracking; optional scene-map estimation; offline stitching; track-level class voting and filtering; short-gap filling; and metric computation. The processing stages preserve intermediate outputs so that detection, association, stitching, and post-processing can be assessed separately. The target operating classes are car, bus, and truck; until the planned retraining is complete, the model's van label is mapped to car for scoring."),
        ("h", "Detection and duplicate suppression"),
        ("p", "The detector was fine-tuned on all ten VisDrone2019-DET categories at 640-pixel input size. For the vehicle pipeline, detections are restricted to car, van, truck, and bus. Because the YOLO26 end-to-end head can emit overlapping boxes for one physical vehicle under different classes, class-agnostic NMS is applied before tracking. The suppression rule retains the higher-confidence box when intersection-over-union (IoU) exceeds 0.5. Optional per-class confidence gates and a running box-size filter are available, but remain disabled by default because a CARLA ground-truth run found the tested bus/truck gates slightly reduced recall and overall scores."),
        ("h", "Online association and scene mapping"),
        ("p", "The current reference configuration uses BoT-SORT with sparse optical-flow global motion compensation. A TrackTrack configuration is also evaluated under the same detector and confidence thresholds. A framewise homography maps image locations into a scene coordinate plane: Stabilo SIFT registration is computed against a current keyframe with vehicle regions masked, and keyframe transforms are chained to the initial map frame. Frames with insufficient registration inliers reuse the prior transform and trigger a new keyframe. These map coordinates support analysis under camera movement; they are not georeferenced GPS coordinates and are not used to claim metric speed."),
        ("h", "Offline trajectory repair"),
        ("p", "Tracklet stitching considers a track ending and another beginning within a 90-frame gap. Candidate links are gated by camera-motion-compensated position and recent vehicle velocity, box-size ratio, class compatibility (car and van may match), HSV colour-histogram distance, and a combined cost threshold. A greedy one-predecessor/one-successor assignment allows chains while limiting ambiguous merges. Post-processing then assigns a confidence-weighted majority class per track, records the per-frame label as raw_class, removes tracks shorter than one second or below the configured mean-confidence threshold, and linearly interpolates gaps up to one second. Interpolated rows are marked and carry zero detection confidence. Each stage has separate CSV outputs to preserve auditability."),
        ("h", "Parallel pavement branch and target architecture"),
        ("p", "The implemented pavement branch is a stock YOLO26l-seg pothole model trained on RGB images. Its output is not yet connected to vehicle trajectories or a geospatial event store. The planned system architecture places vehicle detections and pavement masks alongside scene analysis, optional adaptive tiling, camera-motion estimation, event logic, and future digital-twin services. This paper evaluates only the perception and selected trajectory-processing stages; adaptive tiling, violation rules, live services, and twin visualization are not claimed as implemented."),
    ]),
    ("IV. SYSTEM IMPLEMENTATION", [
        ("p", "The vehicle detector is Ultralytics YOLO26l, fine-tuned using the full VisDrone2019-DET training split. The current pipeline is implemented in Python and OpenCV within the Ultralytics tracking stack. Processing tools support recorded CARLA frame folders and ordinary video files; run configuration is saved with weights, tracker YAML, input size, confidence settings, software version, source, and run statistics. The experiment runner launches each configuration in a fresh subprocess, runs tracking and post-processing, scores each stage, and appends results to a regression table. This avoids state contamination observed in earlier multi-run experiments."),
        ("p", "Trajectory CSVs record frame index, timestamp, track identifier, class, bounding-box centre and dimensions, confidence, and optional scene-map coordinates. A run can retain raw, stitched, and final outputs, together with a track summary and link-audit data. Ground-truth import supports CVAT MOT 1.1, CVAT video XML, and the project CSV format, with checks for invalid frames, duplicate frame/ID pairs, out-of-frame boxes, class changes, gaps, and empty frames."),
        ("p", "The CARLA recorder captures RGB frames and timing metadata; an optional instance-segmentation and depth-camera workflow supports automatic labels for detector training. That labelling tool is separate from the tracking benchmark presented here. The pothole branch uses YOLO26l-seg and the project's PothRGBD split. No shared API, PostGIS storage, Redis live state, CesiumJS frontend, reporting service, or urban-planning recommender is implemented in the current prototype. Pixel-to-GPS projection is likewise not established for real drone telemetry."),
    ]),
    ("V. EXPERIMENTAL SETUP", [
        ("h", "Datasets and protocol"),
        ("p", "Vehicle detection is evaluated on the 548-image VisDrone2019-DET validation split. The four in-scope classes are car, van, truck, and bus; scope-adjusted mean AP is the unweighted mean over those classes. Tracking is evaluated on CARLA Town10HD flight 20260926_215405: 1,987 frames at 1920 x 1080, approximately 86 s and 23.04 frames/s, with 40 vehicle identities and 5,259 annotated boxes. Labels cover frames 661-1986; other empty portions were visually checked and retained in the source clip. This is simulator-derived ground truth, not the real-footage one-minute benchmark originally intended."),
        ("p", "The main tracker comparison uses 640-pixel inference, class-agnostic NMS, shared detector and association confidence levels, and the final stage after stitching, class voting, one-second/0.3-confidence filtering, and one-second gap filling. A single fresh process is used per configuration. Results are deterministic for the saved configurations. The detector-size study varies input size on the same CARLA clip; the tracker comparison includes BoT-SORT and TrackTrack. HOTA is computed using TrackEval's HOTA implementation [6]."),
        ("h", "Pothole protocol and metrics"),
        ("p", "The pothole baseline uses 1,000 PothRGBD RGB-D images partitioned into 800 training, 100 validation, and 100 test images; depth is not used. The listed metrics are best-epoch validation values. Detection metrics include per-class AP at IoU 0.5 and averaged AP across IoU 0.5:0.95. Tracking metrics include IDF1, MOTA, HOTA, ID switches, and box-level precision/recall. Because the CARLA sequence is relatively easy and has no snow, the tracking figures should be interpreted as an initial reproducible baseline rather than evidence of general UAV performance."),
    ]),
    ("VI. RESULTS AND DISCUSSION", [
        ("h", "Vehicle detection"),
        ("p", "The fine-tuned detector reaches overall mAP@50 of 0.457 and mAP@50:0.95 of 0.273 across the ten VisDrone categories. Restricting the mean to the project's four target vehicle classes yields 0.585 and 0.413, respectively. Car is strongest (0.832 mAP@50); truck (0.426) and van (0.481) are weakest, while bus reaches 0.600. Inference is reported at 11.8 ms per image, approximately 85 frames/s, on an RTX 4060 Laptop GPU. The scope-adjusted score is the relevant vehicle metric, but it remains below the PRD's 0.75 target. The target is ambitious for aerial footage and is not met by the current model."),
        ("p", "The results suggest that detector training and scale are more immediate improvement targets than a larger backbone. The model was trained at 640 pixels on ten classes, although six categories are outside the current vehicle scope; truck and bus are also much less frequent than cars. A planned retraining uses three classes (car including vans, bus, truck), higher resolution and additional non-snow CARLA/UAV data. It has not been completed, so expected gains are not included as results."),
        ("h", "Tracking comparison on CARLA"),
        ("p", "Table I summarizes the final pipeline scores. BoT-SORT has the higher MOTA and recall; TrackTrack has the higher IDF1, fewer identity switches, and no false-positive tracks under the project's definition. HOTA differs by only 0.001. Thus the CARLA clip does not establish a decisive tracker winner. The detector precision/recall and identity measures show the trade-off: TrackTrack returns fewer detections after processing but slightly improves identity consistency. Both configurations are close to one final identity per annotated vehicle (41 or 39 predicted IDs against 40 ground-truth IDs)."),
        ("table", [
            ["Tracker", "HOTA", "IDF1", "MOTA", "IDSW", "P/R"],
            ["BoT-SORT", "0.630", "0.859", "0.829", "3", "0.924/0.902"],
            ["TrackTrack", "0.631", "0.874", "0.817", "2", "0.931/0.882"],
        ]),
        ("caption", "TABLE I. FINAL TRACKING RESULTS ON CARLA GT (40 VEHICLES)"),
        ("p", "The baseline trajectory has 41 final identifiers and one false-positive track; TrackTrack has 39 identifiers and none. A false-positive track is one for which fewer than half of its predicted boxes match a ground-truth box. Gap filling increased MOTA by approximately 1.6-2.3 points and recall by 1.7-2.3 points in the measured baseline runs without lowering precision. On this clip, the one-second duration filter removes two real vehicles that are visible for only 9-10 frames, demonstrating that short-track removal trades recall for cleaner track inventories."),
        ("h", "Why deduplication and resolution matter"),
        ("p", "The tracker improvement work identified overlapping detections as a major fragmentation source. In a roundabout drone video, class-agnostic NMS reduced a short-clip count from 113 to 45 unique IDs and reduced tracks of at most ten frames from 60 to 6. On the full 8,749-frame roundabout video, raw unique IDs fell from 3,730 to 714 before post-processing; after stitching, 695 remained. These are counts, not ground-truth identity scores, and should not be read as accuracy claims. Visual spot-checks showed stable identities over short windows and for some parked vehicles, but real-footage ground truth is still needed."),
        ("p", "Detector input-size effects depend on object scale. On the 720p roundabout clip, increasing inference size from 640 to 1280 yielded about 16% more visually verified vehicle boxes. Conversely, on the CARLA clip, 640 outperformed 1280 and 1920: the final baseline IDF1 decreased from 0.852 to 0.802 and 0.716, respectively, while false-positive tracks rose from 7 to 45 and 62 before post-processing. Larger input is therefore not a universal improvement; inference scale must be selected for the apparent size distribution and validated on target footage."),
        ("h", "Scene map and pothole baseline"),
        ("p", "On the CARLA clip, the keyframe-chained scene map kept 24 stopped or queued vehicles within 0.05-0.16 vehicle lengths of their median map position over their visibility. Apparent motion among stopped vehicles remained around 0.2-0.3 vehicle lengths/s, and late-clip scale drift reached 1.4-2.0x for some trajectories. Speed logic must therefore smooth positions and cannot treat current map coordinates as calibrated metres. The implementation has not yet been tested on the second planned clip with sudden camera motion and pan-away/return sequences."),
        ("p", "The stock YOLO26l-seg pothole model reports box precision/recall of 0.936/0.838, box mAP@50 of 0.923, and box mAP@50:0.95 of 0.605. Mask precision/recall are 0.956/0.856, mask mAP@50 is 0.927, and mask mAP@50:0.95 is 0.635. The baseline is promising for the single pothole class on the dataset split, but it does not validate cracks, waterlogging, debris, or transfer to aerial views. The planned DSConv/SimAM model-surgery and training stage remains incomplete."),
        ("h", "Limitations"),
        ("p", "The principal limitation is evaluation coverage. Tracking metrics come from one CARLA sequence without snow and with comparatively large, clear vehicles. The project still lacks a ground-truth-scored real drone clip with sudden camera motion, parked-car returns, and difficult weather. VisDrone mAP is below the stated 0.75 target; the pothole evaluation is dataset-specific and ground-level. Track-level filters can remove real short-lived or low-confidence vehicles. Scene-map scale drift prevents interpreting map displacement as physical speed. No violation rule has been scored, and the larger backend, digital twin, GPS mapping, reporting, and urban-planning components are designed but unimplemented."),
    ]),
    ("VII. CONCLUSION AND FUTURE WORK", [
        ("p", "This paper presented the implemented and measured portion of an aerial surveillance prototype for traffic monitoring and future urban-planning support. A VisDrone-fine-tuned YOLO26l model achieves 0.585 scope-adjusted mAP@50 on car, van, truck, and bus. A CARLA-based tracking benchmark establishes an initial baseline: BoT-SORT and TrackTrack are nearly tied in HOTA, with a modest identity-versus-recall trade-off. Class-agnostic NMS substantially reduced observed fragmentation in real-video experiments, while track stitching, class consolidation, and gap filling provide auditable offline refinement. A separate stock segmentation model provides a strong PothRGBD pothole baseline."),
        ("p", "The results do not yet demonstrate reliable violation detection or an operational digital twin. Immediate work is to score the size filter and alternative detector on ground truth, test scene-map linking on a second camera-motion clip, and select tracker thresholds from both simulator and real footage. The vehicle detector retraining is planned after pipeline improvements and dataset preparation. In parallel, the project must implement and validate trajectory-based violation rules, real-telemetry geospatial projection, aerial pavement transfer, and the backend and CesiumJS twin. Future reports should preserve the separation between measured system behavior and unimplemented design, and should avoid using unique-ID counts as a substitute for identity ground truth."),
    ]),
]

REFERENCES = [
    "P. Zhu, L. Wen, X. Bian, H. Ling, and Q. Hu, \"Vision Meets Drones: A Challenge,\" in Proc. European Conference on Computer Vision Workshops, 2018.",
    "P. Zhu et al., \"Detection and Tracking Meet Drones Challenge,\" IEEE Transactions on Pattern Analysis and Machine Intelligence, vol. 44, no. 11, pp. 7380-7399, 2022.",
    "Ultralytics, \"Ultralytics YOLO,\" software documentation and repository, 2023. [Online]. Available: https://github.com/ultralytics/ultralytics",
    "Y. Zhang et al., \"ByteTrack: Multi-Object Tracking by Associating Every Detection Box,\" in Proc. European Conference on Computer Vision, 2022, pp. 1-21.",
    "N. Aharon, R. Orfaig, and B.-Z. Bobrovsky, \"BoT-SORT: Robust Associations Multi-Pedestrian Tracking,\" arXiv:2206.14651, 2022.",
    "J. Luiten et al., \"HOTA: A Higher Order Metric for Evaluating Multi-Object Tracking,\" International Journal of Computer Vision, vol. 129, pp. 548-578, 2021.",
    "A. Dosovitskiy et al., \"CARLA: An Open Urban Driving Simulator,\" in Proc. Conference on Robot Learning, 2017, pp. 1-16.",
    "Y. Qi et al., \"Dynamic Snake Convolution Based on Topological Geometric Constraints for Tubular Structure Segmentation,\" in Proc. IEEE/CVF International Conference on Computer Vision, 2023.",
    "L. Yang, R.-Y. Zhang, L. Li, and X. Xie, \"SimAM: A Simple, Parameter-Free Attention Module for Convolutional Neural Networks,\" in Proc. International Conference on Machine Learning, 2021, pp. 11863-11874.",
]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
XML = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("w", W)
ET.register_namespace("r", R)
ET.register_namespace("", PKG)

def tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"

def el(parent: ET.Element, name: str, attrs: dict | None = None) -> ET.Element:
    return ET.SubElement(parent, tag(W, name), {tag(W, k): str(v) for k, v in (attrs or {}).items()})

def run(p: ET.Element, text: str, *, bold: bool = False, italic: bool = False, size: int | None = None) -> None:
    r = el(p, "r")
    rpr = el(r, "rPr")
    el(rpr, "rFonts", {"ascii": "Times New Roman", "hAnsi": "Times New Roman", "cs": "Times New Roman"})
    if bold:
        el(rpr, "b")
    if italic:
        el(rpr, "i")
    if size:
        el(rpr, "sz", {"val": size})
        el(rpr, "szCs", {"val": size})
    t = el(r, "t")
    if text.startswith(" ") or text.endswith(" "):
        t.set(tag(XML, "space"), "preserve")
    t.text = text

def paragraph(parent: ET.Element, text: str = "", *, style: str | None = None, align: str | None = None,
              bold: bool = False, italic: bool = False, size: int | None = None,
              left: int | None = None, first: int | None = None, after: int | None = None,
              before: int | None = None, keep: bool = False) -> ET.Element:
    p = el(parent, "p")
    ppr = el(p, "pPr")
    if style:
        el(ppr, "pStyle", {"val": style})
    if align:
        el(ppr, "jc", {"val": align})
    if left is not None or first is not None:
        el(ppr, "ind", {**({"left": left} if left is not None else {}), **({"firstLine": first} if first is not None else {})})
    if after is not None or before is not None:
        el(ppr, "spacing", {**({"after": after} if after is not None else {}), **({"before": before} if before is not None else {})})
    if keep:
        el(ppr, "keepNext")
    if text:
        run(p, text, bold=bold, italic=italic, size=size)
    return p

def section_properties(cols: int, continuous: bool = False) -> ET.Element:
    sp = ET.Element(tag(W, "sectPr"))
    if continuous:
        el(sp, "type", {"val": "continuous"})
    el(sp, "pgSz", {"w": 12240, "h": 15840})
    el(sp, "pgMar", {"top": 980, "right": 900, "bottom": 980, "left": 900, "header": 400, "footer": 450, "gutter": 0})
    el(sp, "cols", {"num": cols, "space": 360, "equalWidth": 1})
    return sp

def make_table(parent: ET.Element, rows: list[list[str]]) -> None:
    tbl = el(parent, "tbl")
    pr = el(tbl, "tblPr")
    el(pr, "tblW", {"w": 5000, "type": "pct"})
    borders = el(pr, "tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = el(borders, edge, {"val": "single", "sz": 4, "space": 0, "color": "808080"})
    layout = el(pr, "tblLayout", {"type": "autofit"})
    for row_index, values in enumerate(rows):
        tr = el(tbl, "tr")
        if row_index == 0:
            trpr = el(tr, "trPr")
            el(trpr, "tblHeader")
        for value in values:
            tc = el(tr, "tc")
            tcpr = el(tc, "tcPr")
            el(tcpr, "tcW", {"w": 900, "type": "auto"})
            if row_index == 0:
                shd = el(tcpr, "shd", {"fill": "D9E2F3", "val": "clear"})
            p = paragraph(tc, value, size=15, bold=row_index == 0, after=20)
            el(p.find(tag(W, "pPr")), "keepLines")

def add_styles() -> bytes:
    styles = ET.Element(tag(W, "styles"))
    normal = el(styles, "style", {"type": "paragraph", "default": 1, "styleId": "Normal"})
    el(normal, "name", {"val": "Normal"})
    rpr = el(normal, "rPr")
    el(rpr, "rFonts", {"ascii": "Times New Roman", "hAnsi": "Times New Roman", "cs": "Times New Roman"})
    el(rpr, "sz", {"val": 20})
    el(rpr, "szCs", {"val": 20})
    ppr = el(normal, "pPr")
    el(ppr, "spacing", {"after": 40, "line": 225, "lineRule": "exact"})
    for sid, name, size, bold, align in [
        ("Title", "Title", 34, True, "center"),
        ("Author", "Author", 20, False, "center"),
        ("H1", "Heading 1", 20, True, "center"),
        ("H2", "Heading 2", 20, True, "left"),
        ("Small", "Small", 17, False, "both"),
    ]:
        st = el(styles, "style", {"type": "paragraph", "styleId": sid})
        el(st, "name", {"val": name})
        if sid in ("H1", "H2"):
            el(st, "basedOn", {"val": "Normal"})
            el(st, "next", {"val": "Normal"})
            el(st, "keepNext")
        sr = el(st, "rPr")
        el(sr, "rFonts", {"ascii": "Times New Roman", "hAnsi": "Times New Roman"})
        el(sr, "sz", {"val": size})
        if bold:
            el(sr, "b")
        if sid == "H2":
            el(sr, "i")
        if sid == "Title":
            el(sr, "color", {"val": "000000"})
        sp = el(st, "pPr")
        el(sp, "jc", {"val": align})
        if sid == "H1":
            el(sp, "spacing", {"before": 80, "after": 50, "keepNext": 1})
        elif sid == "H2":
            el(sp, "spacing", {"before": 60, "after": 30, "keepNext": 1})
    return ET.tostring(styles, encoding="utf-8", xml_declaration=True)

def build_document() -> bytes:
    doc = ET.Element(tag(W, "document"))
    body = el(doc, "body")
    paragraph(body, TITLE, style="Title", after=100)
    paragraph(body, AUTHOR, style="Author", after=20)
    paragraph(body, "Autonomous Aerial Surveillance Framework", style="Author", size=18, after=100)
    p = paragraph(body, "Abstract—" + ABSTRACT, size=18, after=60)
    paragraph(body, "Index Terms—" + KEYWORDS + ".", size=18, after=100)
    # This continuous section break switches from the full-width title block to two columns.
    sep = el(body, "p")
    ppr = el(sep, "pPr")
    ppr.append(section_properties(1, continuous=True))
    for heading, blocks in SECTIONS:
        paragraph(body, heading, style="H1", keep=True, before=90, after=50)
        for kind, content in blocks:
            if kind == "p":
                paragraph(body, content, style="Normal", align="both", first=180, after=35)
            elif kind == "h":
                paragraph(body, content, style="H2", keep=True, before=60, after=25)
            elif kind == "caption":
                paragraph(body, content, align="center", bold=True, size=16, before=20, after=20, keep=True)
            elif kind == "table":
                make_table(body, content)
    paragraph(body, "REFERENCES", style="H1", before=80, after=50)
    for idx, ref in enumerate(REFERENCES, 1):
        paragraph(body, f"[{idx}] {ref}", align="both", left=250, first=-250, size=16, after=20)
    body.append(section_properties(2))
    return ET.tostring(doc, encoding="utf-8", xml_declaration=True)

def write_docx() -> None:
    content_types = ET.Element(tag(CT, "Types"))
    ET.SubElement(content_types, tag(CT, "Default"), {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(content_types, tag(CT, "Default"), {"Extension": "xml", "ContentType": "application/xml"})
    ET.SubElement(content_types, tag(CT, "Override"), {"PartName": "/word/document.xml", "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"})
    ET.SubElement(content_types, tag(CT, "Override"), {"PartName": "/word/styles.xml", "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"})
    rels = ET.Element(tag(PKG, "Relationships"))
    ET.SubElement(rels, tag(PKG, "Relationship"), {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "Target": "word/document.xml"})
    doc_rels = ET.Element(tag(PKG, "Relationships"))
    ET.SubElement(doc_rels, tag(PKG, "Relationship"), {"Id": "rId1", "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", "Target": "styles.xml"})
    with ZipFile(OUT, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ET.tostring(content_types, encoding="utf-8", xml_declaration=True))
        zf.writestr("_rels/.rels", ET.tostring(rels, encoding="utf-8", xml_declaration=True))
        zf.writestr("word/_rels/document.xml.rels", ET.tostring(doc_rels, encoding="utf-8", xml_declaration=True))
        zf.writestr("word/document.xml", build_document())
        zf.writestr("word/styles.xml", add_styles())
    # Focused package validation: required parts parse and both column sections exist.
    with ZipFile(OUT) as zf:
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml", "word/_rels/document.xml.rels"}
        assert required.issubset(set(zf.namelist()))
        xml = ET.fromstring(zf.read("word/document.xml"))
        sections = xml.findall(f".//{tag(W, 'sectPr')}")
        assert len(sections) == 2, f"Expected title and two-column sections, got {len(sections)}"
        assert sections[0].find(tag(W, "cols")).get(tag(W, "num")) == "1"
        assert sections[1].find(tag(W, "cols")).get(tag(W, "num")) == "2"
        ET.fromstring(zf.read("word/styles.xml"))
    print(f"Created and validated {OUT} ({OUT.stat().st_size:,} bytes)")
    print(f"Sections: {len(SECTIONS)}; references: {len(REFERENCES)}; layout: letter, IEEE-style two columns")

if __name__ == "__main__":
    write_docx()
