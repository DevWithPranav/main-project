# Research Notes

Web research done before building each milestone (rule: `AGENTS/rules.md` §7). For each topic: what was found, what we adopt, and what we reject. Numbers quoted from papers are **their claims**, not our measurements; nothing here is adopted as a result until it is measured on our data and logged in `docs/main_project_tracker.md`.

---

## 2026-10-09: first pass (M0 infra, M1–M8)

### Object store (M0)
- MinIO no longer publishes free images: `minio/minio` on Docker Hub and `quay.io/minio/minio` both refused the pull (checked 2026-10-09).
- **Adopted:** SeaweedFS (Apache-2.0, S3 API), verified in `docker-compose.yml`.

### Road features in the lane map (M1)
- **Ramp detection without lane tags.** Foretellix's OpenSCENARIO domain classifies highway entries and exits from *the topology and geometry of road intersections and interchanges, as well as speed limits* ([Foretellix highway route elements](https://docs.nvidia.foretellix.com/osc_dom/route_elements/oscdomain_route_elements_highway.html)). ASAM OSI defines on-ramps (rural/urban road → motorway), off-ramps, and motorway-to-motorway connectors as lane classifications ([ASAM OSI Lane Classification](https://www.asam.net/static_downloads/ASAM_OSI_reference-documentation_v3.5.0/structosi3_1_1Lane_1_1Classification.html)). OSM's `motorway_link` is the same idea ([OSM wiki](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dmotorway_link)).
  - **Adopted:** use OpenDRIVE's ramp lane types (`onRamp` / `offRamp` / `entry` / `exit`) when a map has them. Town04/05 don't, so derive ramps from topology (non-highway lanes linked to highway lanes through junctions) plus the speed limit. Record `ramp` as `on` / `off` / `link`, following OSI.
- **Outcome (measured on our maps):** topology + speed works once a ramp must reach a highway lane of *another* road through a junction; road class has to follow the road, not the posted limit (OSI/OSM also treat motorway as a road class); OpenDRIVE `<bridge>` tags are unreliable in Town05 (ground-level stretches tagged), so height decides. Details in the tracker row of 2026-10-09 (M1).
- **OpenDRIVE → lanelet conversion** (CommonRoad Scenario Designer, [docs](https://commonroad-scenario-designer.readthedocs.io/en/latest/_modules/opendrive/opendrive_parser/elements/junction/)). **Rejected for now:** our exporter already reads OpenDRIVE through CARLA's own parser, which is the ground truth for the simulator. Revisit if we need lanelet-based rule monitors (M2).

### Rules: lane change, unsafe lane change, criticality (M2, M8)
- Formal traffic rules in temporal logic, evaluated on recorded traffic: TUM interstate rules ([Maierhofer et al. 2020](https://portal.fis.tum.de/en/publications/formalization-of-interstate-traffic-rules-in-temporal-logic/), already the basis of our engine); freeway lane-change rules in Metric Temporal Logic, calibrated on naturalistic data ([TRID record](https://trid.trb.org/View/2736802)); lane change as an ordered sequence of *safe gap → signal → move* ([Digital Highway Code, arXiv:2209.14036](https://arxiv.org/pdf/2209.14036)); Traffic Scenario Logic on OpenDRIVE networks ([AAAI-25](https://ojs.aaai.org/index.php/AAAI/article/view/33667/35822)); LLM-assisted rule→MTL translation ([TR2MTL](https://publica.fraunhofer.de/handle/publica/472933)).
  - **Adopted for A3/A8:** separate the *spatial* condition (gap / time-to-collision to the vehicle in the target lane) from the *temporal* one (lane change in progress), as in the timed-automata work. Signals (indicators) can't be seen from 60 m, so A8 uses gap + TTC only.
- **Criticality measures:** [CommonRoad-CriMe](https://cps.pages.gitlab.lrz.de/commonroad/commonroad-criticality-measures/) (`pip install commonroad-crime`, IEEE IV 2023; TTC, time-to-react and many more). FHWA's [SSAM v3](https://rosap.ntl.bts.gov/view/dot/35919) computes min TTC / PET from simulated trajectories.
  - **To evaluate in M2/M8:** CommonRoad-CriMe for conflict indicators in the CARLA before/after runs. It needs scenarios in CommonRoad format, so check the conversion cost first; otherwise implement TTC/PET ourselves using its definitions.
- **TTC/PET details from drone data:** [SinD 2.0](https://arxiv.org/pdf/2607.16943) computes PET only when the closest conflict points are < 2.5 m apart, and keeps a TTC only when the closing speed is > 0.5 m/s and it stays valid ≥ 3 consecutive frames (preprint, verify). [CitySim](https://arxiv.org/pdf/2208.11036) uses min TTC for merge/diverge and cut-in events, and min PET for intersection conflicts; [UAV PET vs signal timing](https://arxiv.org/pdf/2210.05044); [drone traffic metrics at intersections](https://arxiv.org/pdf/2411.02349).
  - **Adopted as starting values** for A8 and the M8 conflict metrics, re-expressed in seconds (≥ 3 frames → ≥ 0.1 s at 30 fps), then tuned on staged acts.

### Pedestrians from 50–70 m (M3)
- Most VisDrone objects are tiny: 54 % of instances < 32×32 px, 22 % < 16×16 ([LAF-YOLOv10](https://arxiv.org/pdf/2609.14560)); 68 % < 32×32 per [DroneScan-YOLO](https://arxiv.org/pdf/2604.13278). Gains reported from a P2 (high-resolution) head + attention fusion + Soft-NMS ([SOD-YOLO](https://papers.cool/arxiv/2507.12727)), from small-object layers on YOLOv11 ([MSDF-YOLO, SAE 2025](https://saemobilus.sae.org/downloads/papers/2025-99-0449/Full%20Text%20PDF)), and from SAHI sliced inference ([thesis](https://norma.ncirl.ie/8780/)). A YOLO26 VisDrone model card also recommends SAHI or a 1024 px model ([HF](https://huggingface.co/steven0226/uav-traffic-vision)).
  - **Plan for M3:** measure pedestrian recall on CARLA walkers with the `full_train` model at 1280 px, then with SAHI slices restricted to crosswalk areas (cheap, as crossings are known from the map). Only retrain with a P2 head if both fall short.

### 3D twin (M7)
- No ready OpenDRIVE → CesiumJS pipeline exists. Options found: [libOpenDRIVE](https://github.com/dacuotecuo/libopendrive) (C++, compiles to WebAssembly with JS bindings, generates 3D road meshes) → three.js [GLTFExporter](https://threejs.org/docs/examples/en/exporters/GLTFExporter.html) → Cesium; or OpenDRIVE → CityGML 3.0 → Cesium, as TUM's Digital Twin Munich did ([GISRUK 2023](https://mediatum.ub.tum.de/doc/1712095/1712095.pdf)); [OpenTwinMap](https://arxiv.org/pdf/2511.21925) builds CARLA-compatible twins from OSM + LiDAR; [GDAL XODR driver](https://github.com/DLR-TS/gdal/blob/libopendrive-pr/doc/source/drivers/vector/xodr.rst) wraps libOpenDRIVE.
  - **Plan for M7:** first draw lanes from our lane-map JSON as Cesium corridors with the M1 road heights (no new toolchain, same data the engine uses); add libOpenDRIVE meshes for surfaces and junctions after that.

### Reproducible CARLA runs (M8)
- Traffic Manager deterministic mode since 0.9.11: world and TM both synchronous, fixed `fixed_delta_seconds` (tutorial: 0.05), `traffic_manager.set_random_device_seed(seed)`, and seed Python's `random` too; send commands in batches ([CARLA 0.9.11 release](https://carla.org/2020/12/22/release-0.9.11/), [TM tutorial](https://carla.readthedocs.io/en/latest/tuto_G_traffic_manager/), [TM docs 0.9.12](https://carla.readthedocs.io/en/0.9.12/adv_traffic_manager/)).
  - **Adopted for M8:** `traffic_flow.py` gets `--seed` with exactly that setup. Hybrid physics is **not** used in validation runs: no source confirms it keeps determinism.
