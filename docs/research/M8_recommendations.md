# M8 Recommendation system: research notes (2026-10-10)

Research done before building Build Plan M8 (CLAUDE.md rule 6). Published numbers below are the sources'
claims, not our measurements. In the code they appear only as **"projected (rule-of-thumb from <source>)"**
in `expected_impact`, never as results. Results come only from `validate_recommendation.py` runs.

## 1. Countermeasure catalogues for "expected impact"

| Source | What it gives | Used for |
|---|---|---|
| [FHWA CMF Clearinghouse](https://cmfclearinghouse.fhwa.dot.gov/resources_about.php) | Crash modification factors (CMF) with a 1-5 star quality rating. CMF 0.83 = 17 % fewer crashes. Speed-camera entries range from CMF 0.37 (4 stars, Scottsdale Loop 101 corridor, single-vehicle crashes) to 1.07 (1 star, Belgian fixed cameras, all injury crashes); the HSM value for automated speed enforcement is CMF 0.83 ("cannot be rated") | Speed-camera impact: 17 % (HSM CMF 0.83), with the wide range stated in `limitations` |
| [Transport for NSW, 2014 annual speed camera review](https://www.transport.nsw.gov.au/system/files/media/documents/2023/2014%20Annual%20review%20of%20speed%20cameras%20-%20no%20appendices.pdf) | Fixed cameras: 42 % fewer crashes at the sites (5 years before vs the latest 5 years) | Upper bound for the speed-camera projection |
| [ACT Hansard 2011 (UK case-study sites)](https://hansard.act.gov.au/hansard/7th-assembly/2011/HTML/week07/2958.htm) | Point-to-point cameras: 37-85 % fewer killed/serious-injury crashes | Alternative "average-speed enforcement" (text only) |
| FHWA STEP / [Proven Safety Countermeasures presentation](https://baltometro.org/sites/default/files/bmc_documents/committee/presentations/brss/BRSS220922pres_FHWA-Safety-Countermeasures.pdf) | Crosswalk visibility enhancements: crash reduction factor 23-48 % (pedestrian crashes) | Zebra-crossing recommendations |
| [FHWA-HRT-23-035 wrong-way driving countermeasures](https://highways.dot.gov/sites/fhwa.dot.gov/files/FHWA-HRT-23-035.pdf), [FHWA WWD workshop](https://www.fhwa.dot.gov/publications/research/safety/WrongWayDriving_WWD_Low-CostSafetyImprovementsWorkshop/WrongWayDriving.pdf) | More WRONG WAY / DO NOT ENTER signs and wrong-way arrows at ramps: statistically significant crash-risk reductions; we did not retrieve the numeric CMFs | Wrong-way rules: impact stated as "reduction, not quantified" |
| [iRAP methodology fact sheets](https://irap.org/?p=30531) | Road-attribute risk factors (delineation, traffic calming, medians); > 170 countermeasures ranked by casualty reduction and benefit-cost | Structure of a recommendation (problem → countermeasure → economic/priority ranking). Numeric factors not retrieved: not used |
| PRD §21.3 | "Signal installation → 70-80 % fewer red-light violations (signal-compliance benchmarks)" | Kept as the PRD's own rule-of-thumb, labelled as such |
| Nilsson power model, [Elvik 2009 table via FHWA](https://www.fhwa.dot.gov/publications/research/safety/17098/003.cfm) | Crashes scale with (v_after / v_before)^k: fatal 4.1, all injury 1.6 (rural roads / freeways). [Cameron & Elvik 2010](https://archive.acrs.org.au/article/nilssons-power-model-connecting-speed-and-road-trauma-does-it-apply-on-urban-roads): not directly applicable to urban arterials | Converts a **measured** mean-speed change from a CARLA run into a projected crash change (`sim_metrics.power_model`), labelled projected |

Note: in July 2026 FHWA removed speed safety cameras and four other items from its Proven Safety
Countermeasures list ([NPR, 2026-07-16](https://www.npr.org/2026/07/16/nx-s1-5893672); the evidence pages are
mirrored by [NACTO](https://nacto.org/latest/the-evidence-hasnt-changed-proven-safety-tools-continue-to-be-effective-best-practice/)).
The CMF Clearinghouse entries still exist; the catalogue cites the CMFs, not the list.

**Adopted:** every recommendation carries `expected_impact.source` (name, URL, value) and
`expected_impact.metric_note` ("source measures crashes, our counts are violations"). Where we have no
retrieved number the projection is `null` and says so. **Rejected:** inventing percentages for lane
markings, barriers, no-stopping enforcement or signal timing.

## 2. Hotspots: DBSCAN practice

- PRD §21.1: DBSCAN per type, eps 50 m, min 10 events; PRD "≥ 50 events" = a major hotspot.
- Literature tunes eps with a k-distance plot and finds city-specific values (e.g. [Amman, 700-3000 m](https://trid.trb.org/View/2714148));
  [GriDBSCAN](https://i-rep.emu.edu.tr/items/93bcd8f2-c0c0-43b4-843e-30eaac36533d) beat KDE on hit rate;
  [a Croatian study](https://doaj.org/article/8da8a40967b64e57b52c6a045c2f92bd) intersects DBSCAN with Getis-Ord Gi* / KDE;
  road-network distance (or snapping to the network) is preferred over planar distance; network KDE is the usual baseline.
- **Adopted:** planar DBSCAN in map metres (our coordinates are already metric) with configurable eps / min events;
  events carry their lane, so clusters are reported with their lanes and roads (a cheap form of snapping).
  Small datasets: below min events the type reports "no hotspot possible" and its events fall to an
  **"emerging pattern"** tier, grouped by road, with lower confidence and an explicit limitation.
- **Not adopted (yet):** network KDE / Gi*: needs weeks of data; we have one 10-minute flight.

## 3. Surrogate safety measures for validation

- [FHWA SSAM](https://www.fhwa.dot.gov/publications/research/safety/08051/02.cfm): conflict = TTC below
  1.5 s (default, from Hydén); also reports min TTC, PET, max deceleration, speed differential.
- PET thresholds are study-specific (one VISSIM/SSAM calibration: TTC 1.5-1.8 s, PET 4.7-5.3 s); recent CARLA work
  ([arXiv 2605.17229](https://arxiv.org/pdf/2605.17229)) uses its own thresholds (CurvTTC ≤ 2 s).
- **Adopted:** TTC < 1.5 s conflicts counted as episodes per vehicle pair, rear-end (follower heading, same path)
  and crossing (constant-velocity discs), with the minimum TTC per episode. Threshold configurable. PET not built.

## 4. Reproducible CARLA traffic

- [CARLA Traffic Manager docs](https://carla.readthedocs.io/en/latest/tuto_G_traffic_manager/) and the
  [0.9.11 release note](https://carla.org/2020/12/22/release-0.9.11/): determinism needs synchronous world +
  synchronous TM + `tm.set_random_device_seed(seed)` + Python `random.seed`; fixed `fixed_delta_seconds`.
- **Adopted** in `simulation/planning/validate_recommendation.py`: sync mode at 0.05 s, TM seed, hybrid physics off,
  seeded spawn order, fixed count, weather and duration; several seeds per variant to report the spread
  (Build Plan risk table). Determinism must still be checked by a repeat run (two baselines, same seed).
