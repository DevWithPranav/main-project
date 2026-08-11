# Product Requirements Document (PRD)
## Autonomous Aerial Surveillance Framework for Traffic Anomaly Detection and Digital Twin-Based Urban Planning

---

| Field | Detail |
|---|---|
| **Version** | 2.0.0 |
| **Date** | August 2026 |
| **Status** | Draft |
| **Document Type** | Project Requirements Document |
| **Classification** | Academic / Research |

---

# Table of Contents

1. [Document Meta](#1-document-meta)
2. [Executive Summary](#2-executive-summary)
3. [Problem Statement](#3-problem-statement)
4. [Proposed Solution](#4-proposed-solution)
5. [Objectives](#5-objectives)
6. [Objective-wise Solutions](#6-objective-wise-solutions)
7. [User Personas](#7-user-personas)
8. [Usage Scenarios](#8-usage-scenarios)
9. [Violation Types & Road Surface Anomaly Categories](#9-violation-types--road-surface-anomaly-categories)
10. [System Architecture](#10-system-architecture)
11. [Data Flow Diagrams](#11-data-flow-diagrams)
12. [Tech Stack](#12-tech-stack)
13. [Database Design](#13-database-design)
14. [API Design](#14-api-design)
15. [Edge Cases & Answers](#15-edge-cases--answers)
16. [Real World Issues & Mitigations](#16-real-world-issues--mitigations)
17. [CARLA Simulation Strategy](#17-carla-simulation-strategy)
18. [Digital Twin Design](#18-digital-twin-design)
19. [Dashboard Specifications](#19-dashboard-specifications)
20. [Automated Analysis & Reporting Architecture](#20-automated-analysis--reporting-architecture)
21. [Intelligent Urban Planning & Recommendation Engine](#21-intelligent-urban-planning--recommendation-engine)
22. [Limitations & Future Scope](#22-limitations--future-scope)
23. [References](#23-references)
24. [Glossary](#24-glossary)
25. [Assumptions & Constraints](#25-assumptions--constraints)
26. [System Modes of Operation](#26-system-modes-of-operation)
27. [Confidence & Accuracy Benchmarking Plan](#27-confidence--accuracy-benchmarking-plan)
28. [Security & Access Control](#28-security--access-control)
29. [Failure & Recovery Design](#29-failure--recovery-design)
30. [Compliance & Ethics Framework](#30-compliance--ethics-framework)
31. [Comparison With Existing Systems](#31-comparison-with-existing-systems)
32. [Innovation Highlights / Novelty Section](#32-innovation-highlights--novelty-section)
33. [Deployment Plan](#33-deployment-plan)
34. [Testing Plan](#34-testing-plan)
35. [Risk Register](#35-risk-register)

---

# 1. Document Meta

## 1.1 Project Title
**Autonomous Aerial Surveillance Framework for Traffic Anomaly Detection and Digital Twin-Based Urban Planning**

## 1.2 Authors

| Role | Name |
|---|---|
| Project Lead | Ghost |
| Backend Development | Ghost |
| Institution | GTech μLearn Platform |
| Document Version | 2.0.0 |

## 1.3 Revision History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | July 2026 | Initial draft (enforcement-centric framing) | Ghost |
| 1.0 | July 2026 | Full PRD complete (enforcement-centric framing) | Ghost |
| 2.0 | August 2026 | Full re-scope: repositioned as an autonomous surveillance and digital twin framework for urban planning; added road surface anomaly detection; replaced legal challan/enforcement pipeline with automated analysis & reporting; added intelligent urban planning and custom scenario simulation as first-class pillars | Ghost |

## 1.4 Document Purpose
This document defines the complete product requirements for the Autonomous Aerial Surveillance Framework. It is intended to guide the development team, project examiner, and any municipal or research stakeholder evaluating the system for pilot deployment. It covers all functional, non-functional, architectural, and operational requirements for a system that turns aerial footage into actionable urban intelligence — spanning traffic violation detection, road surface anomaly detection, digital twin visualization and simulation, automated reporting, and infrastructure planning support.

---

# 2. Executive Summary

Cities generate a constant stream of traffic and infrastructure problems — dangerous driving behaviour, deteriorating road surfaces, congestion, and unsafe intersections — that are almost never observed systematically. What exists today is fragmented: ground-level CCTV covers isolated points, road condition surveys happen once every few years on foot or by vehicle-mounted sensor, and city planners make infrastructure decisions from citizen complaints and manual studies rather than continuous, structured data.

This project proposes an **autonomous surveillance framework** that uses aerial footage — captured by UAV — to continuously monitor a road network for two distinct classes of problems: **traffic violations** (unsafe or illegal driving behaviour) and **road surface anomalies** (potholes, cracking, waterlogging, debris, and other physical degradation of the road surface). Both are detected using computer vision pipelines that convert raw aerial video into structured, geo-tagged events.

These events feed a **3D digital twin** of the monitored road network — a live virtual replica that gives traffic authorities and city planners a shared, spatially accurate view of what is happening on the ground. Beyond passive visualization, the digital twin doubles as a **simulation environment**: planners can construct custom traffic scenarios — a new signal, a lane reconfiguration, a one-way conversion — and observe how the system projects that change would affect violation and congestion patterns, before a single rupee is spent on physical infrastructure.

All detected events are compiled by an **automated analysis and reporting layer** into structured summaries, hotspot reports, and trend dashboards — eliminating the manual effort currently required to turn raw observations into a decision-ready document. An **intelligent urban planning engine** built on spatial clustering translates recurring patterns into concrete infrastructure recommendations (signal placement, resurfacing priority, lane redesign), closing the loop from raw footage to planning action.

The entire pipeline is developed, trained, and validated using CARLA, an open-source driving simulator, as a synthetic data generation environment, enabling complete testing of violation types, anomaly types, and edge cases before real UAV deployment.

The resulting platform is not an enforcement tool — it is a **governance and planning intelligence system**: it turns aerial footage into the structured, continuous, spatial understanding that cities need to plan smarter, keep roads safer, and manage infrastructure proactively rather than reactively.

---

# 3. Problem Statement

## 3.1 Cities Lack Continuous, Structured Visibility Into Their Own Road Networks

Traffic monitoring in most urban areas relies on static ground-level CCTV at a handful of high-risk intersections and periodic human inspection. Between those fixed points, the city is effectively blind — unsafe driving behaviour, road damage, and emerging congestion patterns go unobserved unless a citizen happens to complain or an incident forces attention. There is no continuous, wide-area, structured record of what is actually happening on the road network.

## 3.2 Ground-Level Systems Cover Points, Not Areas

Existing CCTV/ANPR infrastructure suffers from a fundamental design limitation — it monitors specific fixed points rather than continuous areas:

- Fixed field of view — cannot dynamically reposition to cover incidents outside a pre-set angle
- Occlusion — buildings, trees, and other vehicles create dead zones
- Single-plane perspective — no spatial trajectory data, no roof-level or top-down visibility
- No cross-zone intelligence — a camera at one junction has no awareness of what is happening at the next
- Static coverage — expanding coverage means physically installing new hardware

## 3.3 Road Surface Condition Is Monitored Reactively, Not Continuously

Road surface degradation — potholes, cracking, waterlogging, debris accumulation — is today identified almost entirely reactively: a citizen complaint, a vehicle damage claim, or an accident. Municipal bodies rarely have a continuously updated, geo-tagged inventory of road surface condition across their network. Maintenance budgets are allocated based on incomplete, anecdotal information rather than a structured severity-ranked backlog.

## 3.4 No System Converts Raw Observation Into Planning Intelligence

Even where violation or condition data is collected, it is rarely structured, aggregated, or delivered to city planners in a form that supports infrastructure decisions. Planners depend on periodic manual traffic studies and citizen complaints — both slow, expensive, and subjective. There is no existing system that continuously converts real-time aerial observation into structured spatial intelligence, automatically generated reports, and actionable planning recommendations.

## 3.5 Planners Cannot Test Infrastructure Changes Before Committing to Them

Today, a proposed infrastructure change — a new signal, a lane reconfiguration, a one-way conversion — is evaluated on intuition and historical precedent, not on a model of the specific road segment's actual violation and traffic pattern history. There is no tool that lets a planner draw a proposed change on a live model of the city and see a data-grounded projection of its effect before committing budget to it.

## 3.6 Summary of Identified Problems

| Problem | Impact |
|---|---|
| No continuous aerial-scale visibility into traffic behaviour | Violations and unsafe patterns between fixed camera points go undetected |
| Road surface condition monitored reactively | Maintenance is reactive, budgets misallocated, hazards persist longer than necessary |
| No structured planning intelligence layer | Infrastructure decisions made without empirical, location-specific data |
| No unified visualization environment for authorities and planners | Siloed operations, no shared situational awareness |
| No way to simulate infrastructure changes before implementation | Costly changes made on intuition rather than projected impact |
| Manual, slow conversion of raw observation into reports | Planning and response cycles are slow and labour-intensive |

---

# 4. Proposed Solution

## 4.1 System Overview

We propose an **Autonomous Aerial Surveillance and Digital Twin Framework** built on five integrated pillars:

- **Aerial Detection Engine** — a YOLOv8 + DeepSORT computer vision pipeline that detects and tracks vehicles from UAV aerial footage, identifying traffic violations in real time
- **Road Surface Anomaly Engine** — a parallel computer vision pipeline that scans the road surface for potholes, cracking, waterlogging, and debris, geo-tagging each defect with a severity score
- **Digital Twin Environment** — a 3D virtual replica of the monitored road network built on CesiumJS, serving as the shared visualization, monitoring, and scenario-simulation interface
- **Automated Analysis & Reporting Layer** — converts every detected event into structured, aggregated reports and trend dashboards without manual compilation
- **Intelligent Urban Planning Engine** — spatial clustering and rule-based recommendation logic that turns recurring violation and anomaly patterns into concrete infrastructure suggestions, testable via a what-if scenario simulator

## 4.2 How It Solves Each Problem

| Problem | Solution |
|---|---|
| No continuous aerial visibility | UAV provides dynamic, wide-area coverage not bound to fixed installation points |
| Road condition monitored reactively | Road Surface Anomaly Engine continuously scans and geo-tags defects with severity scoring |
| No planning intelligence layer | Urban Planning Engine with DBSCAN-powered hotspot analysis and rule-based recommendations |
| No unified visualization | Digital Twin serves as a shared live environment for monitoring authorities and planners alike |
| No way to test changes before implementing | What-if scenario simulator projects impact of proposed infrastructure changes on the digital twin |
| Manual reporting | Automated Analysis & Reporting layer compiles structured summaries, hotspot reports, and exportable documents automatically |

## 4.3 Why This Approach Over Alternatives

| Alternative Considered | Why Rejected |
|---|---|
| Fixed aerial cameras (balloons/poles) | No dynamic coverage, high installation cost, single fixed vantage point |
| Ground CCTV expansion only | Does not solve blind spots, no aerial perspective, cannot see road surface condition at scale |
| Periodic manual road condition surveys | Slow, expensive, produces a stale snapshot rather than continuous data |
| Fully unsupervised ML | Cannot reliably classify specific violation or anomaly types |
| Manual drone operator review | Not scalable, subject to human error, no real-time response, no structured output |

The chosen approach — dual computer-vision detection pipelines (behavioural + surface condition), digital twin visualization, automated reporting, and simulation-driven planning — is the only architecture that addresses all identified problems within a single coherent platform rather than as disconnected point solutions.

---

# 5. Objectives

## 5.1 Primary Objectives

**Objective 1 — Detect Violations in Traffic**
Develop a computer vision pipeline using YOLOv8 and DeepSORT to detect and continuously track vehicles from UAV aerial footage at altitudes of 80–120m, identifying ten categories of traffic violations in real time and converting each into a structured, geo-tagged event.

**Objective 2 — Create a Digital Twin System**
Construct a 3D virtual replica of the monitored road network using CesiumJS and OpenStreetMap data, with live vehicle markers, geo-fenced zones, and real-time overlays for both traffic violations and road surface anomalies — serving as the shared command and visualization environment.

**Objective 3 — Road Surface Anomaly Detection**
Implement a computer vision pipeline that scans aerial footage for road surface defects — potholes, cracking, waterlogging, and debris/obstruction — geo-tagging each with a severity score and maintaining a continuously updated condition inventory of the monitored network.

**Objective 4 — Intelligent Urban Planning**
Implement a spatial clustering and recommendation engine that analyses historical violation and anomaly data to generate actionable infrastructure recommendations, paired with a what-if scenario simulator that lets planners construct custom traffic scenarios and project their impact before implementation.

**Objective 5 — Automated Analysis and Reporting**
Build a reporting layer that automatically aggregates detection events into structured summaries, hotspot reports, and trend analyses — exportable in formats suitable for municipal and planning stakeholders — without manual compilation effort.

**Objective 6 — Improved Road Safety and Infrastructure Management**
Ensure the platform's outputs (violation intelligence, anomaly inventories, planning recommendations, and reports) are structured and delivered in a way that directly supports faster, better-informed road safety and infrastructure management decisions by the relevant authorities.

## 5.2 Supporting Technical Objectives

**Objective 7 — Geo-Spatial Coordinate Mapping**
Build a pixel-to-GPS projection system using drone telemetry (altitude, gimbal angle, GPS) to map every detected vehicle, violation, and road anomaly to an exact real-world coordinate.

**Objective 8 — CARLA-Based Simulation and Validation**
Build a complete simulation environment in CARLA for synthetic data generation, violation and anomaly scripting, and sim-to-real transfer validation before real UAV deployment.

**Objective 9 — Scalable Backend Infrastructure**
Develop a FastAPI-based backend with PostgreSQL/PostGIS, Redis, and WebSocket streaming to support all system components at production scale.

## 5.3 Success Criteria Per Objective

| Objective | Success Criteria |
|---|---|
| 1 — Violation detection | mAP ≥ 0.75 on aerial vehicle detection; tracking continuity ≥ 90%; all 10 violation types detectable with F1 ≥ 0.70 |
| 2 — Digital twin | Twin updates within 500ms of a real-world event; both violation and anomaly overlays functional |
| 3 — Road anomaly detection | Detection F1 ≥ 0.65 per anomaly category; severity scoring validated against manual inspection on a test set |
| 4 — Urban planning | Recommendations generated for any cluster with ≥ 50 historical events; what-if simulator produces a projection for any user-drawn scenario |
| 5 — Reporting | Structured report (PDF/Excel/GeoJSON) generated for any selected date range and zone within 10 seconds |
| 6 — Road safety/infra outcomes | Every detection event traceable end-to-end from raw footage to a report or recommendation an authority can act on |
| 7 — Geo-spatial mapping | GPS projection error ≤ 2m at 100m altitude |
| 8 — CARLA simulation | All violation and anomaly scripts executable in CARLA; dataset of 10,000+ frames generated |
| 9 — Backend infrastructure | API response time ≤ 200ms under 100 concurrent connections |

---

# 6. Objective-wise Solutions

## Objective 1 — Detect Violations in Traffic

**Technical Approach:**
- YOLOv8n/YOLOv8s model fine-tuned on aerial vehicle imagery
- DeepSORT tracker maintains unique Track IDs with appearance embeddings + Kalman filter trajectory prediction
- 30-frame rolling trajectory buffer maintained per Track ID
- Rule-based and trajectory-anomaly violation engine evaluates each buffer against the 10 defined violation types (Section 9)
- Input: RTSP stream from drone camera OR frame dump from CARLA simulation

## Objective 2 — Create a Digital Twin System

**Technical Approach:**
- CesiumJS as the 3D rendering engine with Cesium Ion terrain tiles
- OSM data loaded via the Cesium OSM Buildings layer
- Vehicle entities updated via WebSocket at 10Hz
- Violation and road-anomaly events persist on the twin as geo-tagged markers with metadata popups
- Zone polygons and anomaly severity heat layers stored as GeoJSON, rendered as CesiumJS entities
- Scenario-builder module allows planners to draw proposed changes directly on the twin

## Objective 3 — Road Surface Anomaly Detection

**Technical Approach:**
- YOLOv8-seg (segmentation variant) fine-tuned for pothole, crack, waterlogging, and debris classes using a pavement-distress dataset (e.g. RDD — Road Damage Dataset) adapted to aerial/oblique drone perspective
- Per-defect severity score computed from defect area (segmented pixel count × GSD) and defect class weighting
- Anomaly events deduplicated across passes using GPS proximity clustering so the same pothole is not re-logged every session
- Anomaly inventory maintained per road segment with a rolling condition score

## Objective 4 — Intelligent Urban Planning

**Technical Approach:**
- DBSCAN spatial clustering on both `violation_events` and `road_anomalies` tables in PostGIS
- Rule engine maps cluster characteristics (violation-type mix, anomaly density/severity) to recommendation types
- Recommendations stored as GeoJSON polygons with supporting metadata
- What-if scenario simulator: planner draws a proposed change on the twin; system identifies historically affected events and projects the estimated reduction/improvement

## Objective 5 — Automated Analysis and Reporting

**Technical Approach:**
- Scheduled and on-demand aggregation jobs (Celery) roll up events into summary statistics per zone, per type, per time window
- Report templates render aggregated data into PDF (executive summary + charts + maps), Excel (tabular), and GeoJSON (for GIS import) formats
- Trend dashboards computed via time-bucketed queries on PostGIS, rendered with Recharts/D3
- Reports are generated without any manual data compilation — a planner or authority selects a scope and receives a finished document

## Objective 6 — Improved Road Safety and Infrastructure Management

**Technical Approach:**
- Every violation and anomaly event is scored, geo-tagged, and routed to the relevant dashboard/report so authorities always have an actionable, prioritised view rather than raw footage
- Severity-based prioritisation (for both violation hotspots and anomaly condition scores) ensures the most safety-critical issues surface first
- Closed-loop tracking: recommendations and flagged anomalies carry a status field (pending → reviewed → actioned) so infrastructure management activity is auditable over time

## Objective 7 — Geo-Spatial Coordinate Mapping

**Technical Approach:**
- Homography matrix computed from drone altitude, gimbal pitch/roll/yaw, and GPS anchor points
- Pixel coordinates → Camera coordinates → World coordinates using the pinhole camera model
- `pyproj` library for coordinate reference system transformations
- Ground Sampling Distance (GSD) computed per frame:

```
GSD Formula:
GSD (m/pixel) = (H × SW) / (f × IW)
Where:
H  = drone altitude (metres)
SW = camera sensor width (mm)
f  = focal length (mm)
IW = image width (pixels)
```

## Objective 8 — CARLA-Based Simulation and Validation

**Technical Approach:**
- CARLA 0.9.15 running in synchronous mode at 20 FPS
- Spectator camera mounted at 100m altitude simulating the drone
- Python scripts per violation type and per anomaly type for controlled dataset generation
- Ground truth bounding boxes/segmentation masks + GPS coordinates auto-exported per frame
- YOLOv8 trained on CARLA data, evaluated on real drone footage

## Objective 9 — Scalable Backend Infrastructure

**Technical Approach:**
- FastAPI async endpoints with Pydantic validation
- PostgreSQL 16 + PostGIS 3.4 for persistent geo-spatial storage
- Redis 7 for live vehicle/session state with TTL
- WebSocket manager using FastAPI WebSockets + Redis Pub/Sub for fan-out
- MinIO S3-compatible storage for violation and anomaly snapshot images

---

# 7. User Personas

## Persona 1 — Traffic Monitoring Officer (Field)

| Attribute | Detail |
|---|---|
| **Name** | Officer Rajan |
| **Age** | 32 |
| **Role** | Field traffic monitoring and response |
| **Tech Comfort** | Basic smartphone, WhatsApp, maps |
| **Goal** | Know where violations and unsafe conditions are happening right now and respond quickly |
| **Pain Points** | Can't be everywhere, misses events in blind spots, no structured record to act on |
| **Needs from System** | Simple alert on phone/tablet, exact GPS location, vehicle description, photo evidence |
| **Dashboard** | Monitoring Dashboard — Live mode |

## Persona 2 — Traffic Control Room Supervisor

| Attribute | Detail |
|---|---|
| **Name** | Supervisor Meera |
| **Age** | 41 |
| **Role** | Central control room, coordinates field response |
| **Tech Comfort** | Comfortable with dashboards and CCTV systems |
| **Goal** | Maintain situational awareness and direct the right response to the right location |
| **Pain Points** | No real-time aerial view, hard to coordinate multiple field responders |
| **Needs from System** | Full twin view, live event feed, one-click dispatch |
| **Dashboard** | Monitoring Dashboard — Command mode |

## Persona 3 — City Planner / Urban Planning Engineer

| Attribute | Detail |
|---|---|
| **Name** | Engineer Priya |
| **Age** | 38 |
| **Role** | Urban traffic and infrastructure planning |
| **Tech Comfort** | GIS tools, Excel, government portal systems |
| **Goal** | Identify where to invest in signals, road markings, and zone changes, and test proposed changes before committing budget |
| **Pain Points** | No real violation/condition data to justify budget, studies are expensive, no way to test ideas before implementation |
| **Needs from System** | Historical heatmaps, trend charts, AI recommendations, a scenario simulator, exportable reports |
| **Dashboard** | Urban Planning Dashboard |

## Persona 4 — Municipal Maintenance Engineer

| Attribute | Detail |
|---|---|
| **Name** | Engineer Arjun |
| **Age** | 35 |
| **Role** | Road surface maintenance planning and crew dispatch |
| **Tech Comfort** | GIS tools, work-order systems |
| **Goal** | Know which road segments need repair most urgently and prioritise maintenance budget accordingly |
| **Pain Points** | Relies on citizen complaints and windshield surveys; no continuously updated condition inventory |
| **Needs from System** | Severity-ranked anomaly inventory, condition heatmap, exportable work-order list |
| **Dashboard** | Urban Planning Dashboard — Road Condition view |

## Persona 5 — System Administrator

| Attribute | Detail |
|---|---|
| **Name** | Admin Kiran |
| **Age** | 29 |
| **Role** | Platform technical management |
| **Tech Comfort** | High — developer background |
| **Goal** | Keep the system running, manage users and zones, onboard new drone sessions |
| **Pain Points** | No visibility into system health, hard to manage zones remotely |
| **Needs from System** | User management, zone editor, system health dashboard |
| **Dashboard** | Admin Panel |

## Persona 6 — Drone Operator

| Attribute | Detail |
|---|---|
| **Name** | Operator Sai |
| **Age** | 26 |
| **Role** | UAV flight and camera operation |
| **Tech Comfort** | Drone software, flight planning apps |
| **Goal** | Execute patrol routes, maintain feed quality, stay within DGCA limits |
| **Pain Points** | No feedback on what the system is detecting, unclear if feed is being processed |
| **Needs from System** | Feed status indicator, detection confidence display, altitude/zone compliance alerts |
| **Dashboard** | Drone Operator View (simplified) |

---

# 8. Usage Scenarios

## Scenario 1 — Officer Receives a Live Violation Alert and Responds

**Actor:** Officer Rajan
**Trigger:** Drone detects a vehicle parked in a no-parking zone

**Flow:**
1. Drone detects a stationary vehicle inside a no-parking polygon for > 30 seconds
2. Violation event created — GPS coordinate, timestamp, vehicle description, snapshot
3. Alert pushed via WebSocket to the Monitoring Dashboard
4. Alert appears on Rajan's tablet — map pin, photo, "White Sedan, no-parking zone, Junction 7"
5. Rajan taps "Navigate" — opens Google Maps with the violation GPS
6. Rajan arrives, verifies the situation, and marks the event "Actioned" with a note
7. Violation event status updates to "Resolved" on the digital twin and feeds into the weekly hotspot report

## Scenario 2 — Municipal Maintenance Engineer Reviews Road Condition Inventory

**Actor:** Engineer Arjun
**Trigger:** Monthly maintenance budget planning cycle

**Flow:**
1. Arjun opens the Urban Planning Dashboard, Road Condition view
2. Selects the monitored zone and a severity filter (High + Medium)
3. Twin renders a severity-coded overlay — red markers for high-severity potholes, orange for cracking, blue for waterlogging-prone segments
4. Arjun clicks a cluster of high-severity potholes on a arterial road — sees defect count, average severity, first-detected date, and recurrence trend
5. Arjun exports a prioritised work-order list (Excel) sorted by severity score for the maintenance crew

## Scenario 3 — City Planner Analyses a Monthly Hotspot Report

**Actor:** Engineer Priya
**Trigger:** Monthly planning review meeting

**Flow:**
1. Priya opens the Urban Planning Dashboard
2. Selects date range: last 30 days
3. Twin switches to heatmap mode — violation density overlaid on the 3D map
4. High-density cluster visible at MG Road / NH bypass intersection
5. Priya clicks the cluster — breakdown: 42% wrong-way, 31% red-light jumping
6. The recommendation panel shows: "Signal with countdown timer recommended at this intersection"
7. Priya opens the what-if simulator — adds a virtual signal, system estimates a 65% reduction in flagged violations at that cluster
8. Priya exports a PDF report with the heatmap, statistics, and recommendation for the municipal council

## Scenario 4 — Planner Builds a Custom Traffic Scenario in the Digital Twin

**Actor:** Engineer Priya
**Trigger:** Planning a road redesign for a one-way conversion

**Flow:**
1. Priya selects a road segment with a high wrong-way-driving violation history
2. Opens the Scenario Builder inside the digital twin
3. Draws the proposed one-way direction directly on the twin, plus a hypothetical lane closure
4. The simulator models the change against historical trajectory data: "Projected to eliminate 89% of wrong-way events on this segment; may increase congestion on the adjacent parallel road by an estimated 12%"
5. Priya saves the scenario and adds the projection to her planning report

## Scenario 5 — Drone Operator Runs a Patrol Session

**Actor:** Operator Sai
**Trigger:** Morning patrol shift begins

**Flow:**
1. Sai opens the Drone Operator View, logs in
2. Selects a patrol zone from the pre-defined zone map
3. System checks: zone active, system online
4. Sai launches the drone; the feed connects via RTSP
5. Detection pipeline activates — vehicle tracking and road-surface scanning begin in parallel
6. Operator view shows: "Detection active — 14 vehicles tracked, 3 anomalies logged this session"
7. Altitude monitor shows the current altitude: 97m (within DGCA limit — green)
8. Session ends — Sai lands the drone; the session auto-closes and data is archived

## Scenario 6 — Authority Requests an Automated Zone Report

**Actor:** Supervisor Meera
**Trigger:** Weekly command briefing

**Flow:**
1. Meera opens the reporting panel and selects "Zone 4, last 7 days"
2. She requests a combined report (violations + road anomalies)
3. The system compiles a structured PDF within seconds — top violation types, anomaly severity summary, trend chart vs. previous week
4. Meera forwards the report to the command briefing without any manual data entry

---

# 9. Violation Types & Road Surface Anomaly Categories

## 9.1 Traffic Violation Types

### Violation 1 — No-Parking Zone Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Zone polygon intersection + stationary state detection |
| **Trigger Condition** | Vehicle centroid inside no-parking GeoJSON polygon AND velocity < 2 km/h for > 30 seconds |
| **Edge Case 1** | Vehicle slows but doesn't fully stop — threshold: velocity < 2 km/h for a sustained period |
| **Edge Case 2** | Delivery vehicle brief stop — configurable grace period (default 30s, adjustable per zone) |
| **Confidence Threshold** | 90% — zone intersection is geometrically deterministic |

### Violation 2 — Wrong-Way Driving

| Attribute | Detail |
|---|---|
| **Detection Method** | Vehicle velocity vector vs. road direction vector comparison |
| **Trigger Condition** | Vehicle heading differs from the road's permitted direction by > 150° for > 5 consecutive frames |
| **Edge Case 1** | Vehicle reversing briefly — minimum displacement threshold before flagging |
| **Edge Case 2** | Legal turn temporarily appearing wrong-way — evaluated on exit heading, not mid-turn |
| **Confidence Threshold** | 85% |

### Violation 3 — Illegal U-Turn

| Attribute | Detail |
|---|---|
| **Detection Method** | Trajectory arc analysis in a restricted U-turn zone polygon |
| **Trigger Condition** | Vehicle enters the U-turn zone, heading changes > 160° within the zone boundary |
| **Edge Case 1** | Legal U-turn zones nearby — flagged only if the arc occurs inside the restricted polygon |
| **Edge Case 2** | Three-point turn — incremental heading change, filtered out by arc-smoothness metric |
| **Confidence Threshold** | 82% |

### Violation 4 — Red-Light Jumping

| Attribute | Detail |
|---|---|
| **Detection Method** | Signal state API + stop-line polygon crossing detection |
| **Trigger Condition** | Vehicle crosses the stop-line polygon while signal state = RED |
| **Edge Case 1** | Vehicle already crossing when the light turns red — evaluated by vehicle centroid position at the signal change moment |
| **Edge Case 2** | Emergency vehicles — vehicle-type classification excludes ambulance/fire truck |
| **Confidence Threshold** | 92% |

### Violation 5 — Speeding

| Attribute | Detail |
|---|---|
| **Detection Method** | Pixel displacement per frame × GSD × frame rate → speed in km/h |
| **Trigger Condition** | Computed speed exceeds the road segment's speed limit for > 10 consecutive frames |
| **Edge Case 1** | GPS drift causing apparent speed spike — Kalman filter smoothing applied before speed computation |
| **Edge Case 2** | Partial occlusion — speed computed only on frames with a full bounding box |
| **Confidence Threshold** | 85% |

### Violation 6 — Lane Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Lane boundary polygon + vehicle centroid tracking |
| **Trigger Condition** | Vehicle centroid crosses the lane boundary and sustains the crossing for > 3 seconds |
| **Edge Case 1** | Lane change vs. violation — brief crossing allowed; sustained crossing flagged |
| **Edge Case 2** | Dashed vs. solid marking — different thresholds stored per lane type |
| **Confidence Threshold** | 80% |

### Violation 7 — Zebra Crossing Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Vehicle stationary-state detection within the zebra crossing polygon |
| **Trigger Condition** | Vehicle stopped (velocity < 2 km/h) inside the crossing polygon for > 10 seconds |
| **Edge Case 1** | Traffic jam forcing a stop on the crossing — contextual check: if vehicles are stopped across the entire road, no violation is flagged |
| **Edge Case 2** | Motorcycle lane-splitting on the crossing — bounding box overlap threshold applied |
| **Confidence Threshold** | 85% |

### Violation 8 — Helmet-less Riding

| Attribute | Detail |
|---|---|
| **Detection Method** | Two-wheeler detection → rider head region crop → helmet classifier |
| **Trigger Condition** | Rider head region classified as no-helmet with confidence > 80% |
| **Edge Case 1** | Dark/night footage — flagged as "low confidence" |
| **Edge Case 2** | Cap resembling a helmet — classifier trained with hard negatives |
| **Confidence Threshold** | 80% |

### Violation 9 — Two-Wheeler Overloading

| Attribute | Detail |
|---|---|
| **Detection Method** | Two-wheeler detection → passenger count on the vehicle bounding box |
| **Trigger Condition** | Passenger count ≥ 3 on a two-wheeler |
| **Edge Case 1** | Child passenger — counted regardless, for safety |
| **Edge Case 2** | Rider with backpack resembling a second person — profile-shape classifier distinguishes |
| **Confidence Threshold** | 78% |

### Violation 10 — Illegal Stopping on Highway/Flyover

| Attribute | Detail |
|---|---|
| **Detection Method** | Zone polygon (highway/flyover) + stationary vehicle detection |
| **Trigger Condition** | Vehicle velocity < 5 km/h inside the highway/flyover polygon for > 20 seconds |
| **Edge Case 1** | Traffic jam on flyover — same contextual check as Violation 7 |
| **Edge Case 2** | Vehicle breakdown — flagged with a "possible breakdown" tag rather than a standard violation |
| **Confidence Threshold** | 88% |

## 9.2 Road Surface Anomaly Categories

### Anomaly 1 — Potholes

| Attribute | Detail |
|---|---|
| **Detection Method** | YOLOv8-seg instance segmentation of pothole regions |
| **Severity Scoring** | Function of segmented area (via GSD) and depth-proxy (shadow/edge contrast) — Low / Medium / High |
| **Edge Case 1** | Waterlogged pothole obscuring true boundary — flagged with reduced confidence, cross-checked with the waterlogging class |
| **Edge Case 2** | Shadow misclassified as a pothole — filtered using texture and edge-consistency checks |

### Anomaly 2 — Surface Cracking

| Attribute | Detail |
|---|---|
| **Detection Method** | Segmentation of linear/alligator crack patterns |
| **Severity Scoring** | Crack density (segmented length per road area) and pattern type (linear vs. alligator, the latter weighted higher) |
| **Edge Case 1** | Road markings misclassified as cracks — filtered via known lane-marking geometry mask |
| **Edge Case 2** | Tar/repair patches resembling cracks — trained as a hard-negative class |

### Anomaly 3 — Waterlogging / Flooding

| Attribute | Detail |
|---|---|
| **Detection Method** | Surface reflectance and colour-texture segmentation identifying standing water |
| **Severity Scoring** | Affected road area as a proportion of segment width; recurrence across sessions (chronic vs. one-off) |
| **Edge Case 1** | Wet road after rain without pooling — distinguished via reflectance uniformity threshold |
| **Edge Case 2** | Shadow from overpasses resembling water — filtered using time-of-day/sun-angle metadata |

### Anomaly 4 — Debris / Obstruction

| Attribute | Detail |
|---|---|
| **Detection Method** | Object detection for out-of-place objects on the road surface (fallen branches, construction material, abandoned objects) |
| **Severity Scoring** | Based on obstruction footprint relative to lane width and duration of persistence across sessions |
| **Edge Case 1** | Parked vehicle misclassified as debris — excluded via the vehicle detection class |
| **Edge Case 2** | Temporary construction material vs. hazardous debris — distinguished using a "scheduled works zone" flag on the zone metadata |

## 9.3 Confidence and Reporting Note

Unlike a legal-enforcement pipeline, events in this system do not require a fixed statutory confidence threshold before action — instead, every event carries its computed confidence/severity score into the digital twin and reports, and downstream dashboards allow authorities to filter by confidence level. This keeps the system honest about detection certainty without gating visibility behind a single hard cutoff.

---

# 10. System Architecture

## 10.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      INPUT LAYER                                │
│                                                                  │
│   CARLA Simulator          Real UAV Drone                       │
│   ┌─────────────┐          ┌──────────────┐                    │
│   │ Spectator   │          │ Camera Feed  │                    │
│   │ Camera      │          │ RTSP Stream  │                    │
│   │ @ 100m alt  │          │ MAVLink GPS  │                    │
│   └──────┬──────┘          └──────┬───────┘                    │
│          │ Frames + GT GPS        │ Frames + Telemetry         │
└──────────┼────────────────────────┼────────────────────────────┘
           │                        │
┌──────────▼────────────────────────▼────────────────────────────┐
│                   DETECTION PIPELINE                            │
│                                                                  │
│   YOLOv8 Vehicle Detection      YOLOv8-seg Road Surface         │
│              ↓                        Segmentation               │
│   DeepSORT Multi-Object Tracker             ↓                   │
│              ↓                   Anomaly Classifier +           │
│   Trajectory Buffer (30-frame)   Severity Scorer                │
│              ↓                        ↓                          │
│   Geo-Projection Engine (pixel → GPS via drone telemetry)      │
└──────────────────────────┬─────────────────────────────────────┘
                           │ Tracked vehicles + trajectories + anomalies
┌──────────────────────────▼─────────────────────────────────────┐
│              VIOLATION & ANOMALY ENGINE                         │
│                                                                  │
│   Violation Rule Engine        Anomaly Deduplication Engine     │
│   (10 violation types)         (GPS-proximity clustering)       │
│              ↓                        ↓                          │
│   Confidence / Severity Scorer (per event)                     │
│              ↓                                                   │
│   Structured Event (type, GPS, timestamp, score, snapshot)     │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                   BACKEND INFRASTRUCTURE                        │
│                                                                  │
│   FastAPI (async REST + WebSocket)                              │
│   ┌──────────────────┐  ┌──────────────┐  ┌────────────────┐  │
│   │ PostgreSQL       │  │ Redis        │  │ MinIO          │  │
│   │ + PostGIS        │  │ (live state) │  │ (snapshots)    │  │
│   │ (persistent)     │  │              │  │                │  │
│   └──────────────────┘  └──────────────┘  └────────────────┘  │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│           ANALYSIS, REPORTING & PLANNING LAYER                  │
│                                                                  │
│   DBSCAN Hotspot Clustering   Automated Report Generator        │
│   Recommendation Rule Engine  What-If Scenario Simulator        │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                   PRESENTATION LAYER                            │
│                                                                  │
│   ┌──────────────────────┐  ┌────────────────────────────────┐ │
│   │  Monitoring Dashboard│  │  Urban Planning Dashboard      │ │
│   │  (Live Ops)          │  │  (Historical + Planning +      │ │
│   │  React + CesiumJS    │  │   Scenario Simulation)         │ │
│   └──────────────────────┘  └────────────────────────────────┘ │
│                                                                  │
│                   DIGITAL TWIN (Shared Core)                   │
│                   CesiumJS 3D Virtual City                      │
└─────────────────────────────────────────────────────────────────┘
```

## 10.2 Layer-by-Layer Breakdown

### Input Layer
Accepts two input types: CARLA simulator frame dumps with ground-truth metadata (training/testing phase), and real UAV RTSP video with MAVLink telemetry (production phase). Both are normalised to the same frame + GPS telemetry format before entering the detection pipeline.

### Detection Pipeline Layer
Two parallel vision pipelines run per frame. The vehicle pipeline (YOLOv8 + DeepSORT) produces tracked trajectories for violation analysis. The road-surface pipeline (YOLOv8-seg) segments the road surface for anomaly classes, feeding a severity scorer. Both pipelines share the same geo-projection engine to convert pixel coordinates to GPS.

### Violation & Anomaly Engine Layer
Tracked trajectories are evaluated against the ten violation definitions (Section 9.1). Segmented anomalies are deduplicated against previously logged defects at the same location and scored for severity (Section 9.2). Both output structured events with a confidence/severity score.

### Backend Infrastructure Layer
FastAPI handles all API requests and WebSocket connections. PostgreSQL with PostGIS stores all persistent geo-spatial data. Redis maintains live vehicle/session state with TTL expiry. MinIO provides S3-compatible object storage for event snapshot images.

### Analysis, Reporting & Planning Layer
DBSCAN clustering identifies hotspots across both violation and anomaly data. The recommendation rule engine and what-if simulator (Section 21) turn clusters into planning suggestions. The automated report generator (Section 20) compiles structured, exportable documents on demand or on schedule.

### Presentation Layer
React/Next.js frontend with CesiumJS 3D twin embedded as a shared component. Role-based routing serves different overlay configurations and data endpoints per user type.

---

# 11. Data Flow Diagrams

## 11.1 End-to-End Violation Data Flow

```
Drone Camera Frame
       ↓
YOLOv8 Inference → [Bounding Boxes + Labels]
       ↓
DeepSORT Tracker → [Track ID + Appearance Embedding + Position]
       ↓
Geo-Projection → [Track ID + GPS Coordinate + Velocity vector]
       ↓
Trajectory Buffer → [30-frame history per Track ID]
       ↓
Violation Engine → [Violation Event or NULL]
       ↓
Confidence Scorer → [Score attached to event]
       ↓
┌──────────────────────────────────────┐
│         FastAPI Ingest Endpoint      │
└──────┬────────────────────┬──────────┘
       ↓                    ↓
   Redis (live)         PostgreSQL (persist)
   vehicle_positions    violation_events table
   active_violations    vehicle_tracks table
       ↓
   WebSocket Broadcast
       ↓
┌──────────────┐    ┌─────────────────────┐
│ Monitoring   │    │ Urban Planning      │
│ Dashboard    │    │ Dashboard           │
│ (live feed)  │    │ (analytics query)   │
└──────────────┘    └─────────────────────┘
```

## 11.2 Road Surface Anomaly Data Flow

```
Drone Camera Frame
       ↓
YOLOv8-seg Inference → [Segmented defect regions + class]
       ↓
Severity Scorer → [Area × GSD, class weighting → severity score]
       ↓
Geo-Projection → [Defect GPS centroid]
       ↓
Deduplication Check → PostGIS ST_DWithin against existing road_anomalies
       ↓
   New defect → INSERT into road_anomalies
   Existing defect → UPDATE recurrence_count, last_seen, severity trend
       ↓
   WebSocket Broadcast (new/updated anomaly)
       ↓
┌──────────────┐    ┌─────────────────────┐
│ Monitoring   │    │ Urban Planning      │
│ Dashboard    │    │ Dashboard           │
│ (twin pin)   │    │ (condition heatmap) │
└──────────────┘    └─────────────────────┘
```

## 11.3 Event Lifecycle Flow

```
[Detection] Violation or anomaly condition met
       ↓
[Pending] Condition sustained past threshold duration (violations) /
          deduplication check passed (anomalies)
       ↓
[Scored] Confidence / severity score computed
       ↓
[Flagged] Event stored, alert sent to relevant dashboard
       ↓
[Reviewed] Officer/planner/maintenance engineer reviews event
       ↓
[Actioned] Marked actioned with optional note (field response, work order issued, recommendation accepted)
       ↓
[Resolved] Event closed on the digital twin; retained in historical data for reporting and clustering
```

## 11.4 Automated Reporting Flow

```
Report request (on-demand from dashboard OR scheduled Celery job)
       ↓
Scope resolved: zone(s), date range, event type(s)
       ↓
PostGIS aggregation query: counts, trends, top hotspots, severity breakdown
       ↓
Template renderer: PDF (charts + map snapshot + summary) /
                    Excel (tabular) / GeoJSON (for GIS import)
       ↓
Report stored in MinIO + link returned to requester
       ↓
Delivered to dashboard for download, or emailed to configured recipients (scheduled reports)
```

## 11.5 Urban Planning Recommendation Flow

```
Historical violation_events + road_anomalies tables (PostGIS)
       ↓
DBSCAN spatial clustering (eps=50m, min_samples=10), run separately per event type
       ↓
Cluster identified → composition computed (violation-type mix or anomaly severity profile)
       ↓
Rule engine maps cluster profile to recommendation:
  - >40% red-light jumping at intersection → "Signal recommended"
  - >40% wrong-way on segment → "One-way conversion or barriers"
  - High-severity pothole cluster on arterial road → "Priority resurfacing"
  - Chronic waterlogging cluster → "Drainage improvement"
       ↓
Recommendation stored in recommendations table
       ↓
Rendered as a suggestion overlay on the Urban Planning twin
       ↓
Planner opens What-If Simulator → draws proposed change → system projects impact
       ↓
Export as PDF/Excel/GeoJSON report for planning submission
```

---

# 12. Tech Stack

## 12.1 Complete Tech Stack Per Layer

| Layer | Technology | Version | Justification |
|---|---|---|---|
| **Object Detection** | YOLOv8 (Ultralytics) | 8.x | Best speed/accuracy tradeoff for real-time aerial vehicle detection |
| **Object Tracking** | DeepSORT / ByteTrack | Latest | Maintains cross-frame Track IDs with appearance re-ID; handles occlusion |
| **Road Surface Segmentation** | YOLOv8-seg (Ultralytics) | 8.x | Instance segmentation for pothole/crack/waterlogging/debris regions with area-based severity scoring |
| **Anomaly Detection (trajectory)** | Isolation Forest | scikit-learn 1.x | Lightweight, no training data needed for trajectory anomalies |
| **Video Processing** | OpenCV | 4.x | Frame capture, preprocessing, homography transforms |
| **Geo-Projection** | pyproj, geopy | Latest | CRS transformations, geodesic distance calculations |
| **Backend API** | FastAPI | 0.110+ | Async support, native WebSocket, Pydantic validation |
| **Database** | PostgreSQL + PostGIS | 16 + 3.4 | Full geo-spatial query support (ST_DWithin, ST_Within, ST_Intersects) |
| **Live State** | Redis | 7.x | Sub-millisecond live vehicle position updates with TTL |
| **Object Storage** | MinIO | Latest | S3-compatible, self-hosted; violation and anomaly snapshot storage |
| **Async Tasks** | Celery + Redis | Latest | Async DBSCAN clustering, scheduled report generation |
| **Spatial Analysis** | GeoPandas, Shapely | Latest | Polygon operations, trajectory analysis, cluster geometry |
| **Clustering** | scikit-learn DBSCAN | 1.x | Spatial hotspot detection for violations and anomalies |
| **Simulation** | CARLA | 0.9.15 | Open-source driving simulator; drone simulation via spectator camera |
| **Digital Twin** | CesiumJS | Latest | 3D geo-accurate rendering; OSM building support; WebGL |
| **Frontend** | React + Next.js | 14.x | SSR for dashboard performance |
| **Charts** | Recharts + D3.js | Latest | Trend charts, condition breakdowns |
| **Report Generation** | WeasyPrint / ReportLab, openpyxl | Latest | PDF and Excel report rendering |
| **WebSockets** | FastAPI WebSockets | Native | Real-time twin updates |
| **Auth** | JWT (python-jose) | Latest | Role-based access control |

## 12.2 Why Key Tools Were Chosen Over Alternatives

| Tool Chosen | Alternative Considered | Why Chosen |
|---|---|---|
| YOLOv8 / YOLOv8-seg | ResNet-50 + separate segmentation head, EfficientDet | Single model family covers both detection and segmentation needs; faster inference, better small-object detection |
| DeepSORT | SORT, FairMOT | Better re-identification after occlusion via appearance embeddings |
| FastAPI | Django REST | Native async, WebSocket support, lighter weight for real-time data |
| CesiumJS | Mapbox GL JS, Kepler.gl | 3D geo-accurate terrain, free with OSM data, drone simulation capability |
| PostGIS | MongoDB + GeoJSON | Native geo-spatial indexing, ST_ function library |
| CARLA | AirSim, SUMO | Photorealistic rendering, Python API, vehicle physics, drone camera simulation |
| DBSCAN | k-means | Does not require pre-specifying cluster count; naturally handles noise/outliers in spatial event data |

---

# 13. Database Design

## 13.1 PostgreSQL + PostGIS Schema

### Table: zones
```sql
CREATE TABLE zones (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    zone_type       VARCHAR(50) NOT NULL, -- 'no_parking', 'intersection', 'lane', 'highway', 'zebra', 'school_zone'
    geometry        GEOMETRY(POLYGON, 4326) NOT NULL,
    speed_limit     INTEGER,             -- km/h, NULL if not applicable
    permitted_dir   FLOAT,               -- road direction angle in degrees, NULL if not applicable
    grace_period    INTEGER DEFAULT 30,  -- seconds before violation flagged
    is_active       BOOLEAN DEFAULT TRUE,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX zones_geometry_idx ON zones USING GIST(geometry);
```

### Table: violation_events
```sql
CREATE TABLE violation_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id        INTEGER NOT NULL,
    violation_type  VARCHAR(50) NOT NULL,
    session_id      UUID REFERENCES drone_sessions(id),
    location        GEOMETRY(POINT, 4326) NOT NULL,
    zone_id         UUID REFERENCES zones(id),
    confidence      FLOAT NOT NULL,
    status          VARCHAR(30) DEFAULT 'flagged', -- flagged, reviewed, actioned, resolved, dropped
    snapshot_url    TEXT,
    velocity        FLOAT,
    heading         FLOAT,
    flagged_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    reviewed_by     UUID REFERENCES users(id)
);
CREATE INDEX violation_events_location_idx ON violation_events USING GIST(location);
CREATE INDEX violation_events_type_idx ON violation_events(violation_type);
CREATE INDEX violation_events_time_idx ON violation_events(flagged_at);
```

### Table: road_anomalies
```sql
CREATE TABLE road_anomalies (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    anomaly_type       VARCHAR(50) NOT NULL, -- 'pothole', 'crack', 'waterlogging', 'debris'
    session_id         UUID REFERENCES drone_sessions(id),
    location           GEOMETRY(POINT, 4326) NOT NULL,
    zone_id            UUID REFERENCES zones(id),
    severity_score     FLOAT NOT NULL,       -- 0.0 - 1.0
    severity_band      VARCHAR(10),          -- 'low', 'medium', 'high'
    area_sq_m          FLOAT,
    status             VARCHAR(30) DEFAULT 'flagged', -- flagged, reviewed, work_order_issued, repaired
    snapshot_url        TEXT,
    first_detected_at  TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at       TIMESTAMPTZ DEFAULT NOW(),
    recurrence_count   INTEGER DEFAULT 1,
    reviewed_by        UUID REFERENCES users(id)
);
CREATE INDEX road_anomalies_location_idx ON road_anomalies USING GIST(location);
CREATE INDEX road_anomalies_type_idx ON road_anomalies(anomaly_type);
CREATE INDEX road_anomalies_severity_idx ON road_anomalies(severity_band);
```

### Table: vehicle_tracks
```sql
CREATE TABLE vehicle_tracks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id        INTEGER NOT NULL,
    session_id      UUID REFERENCES drone_sessions(id),
    trajectory      GEOMETRY(LINESTRING, 4326),
    start_time      TIMESTAMPTZ,
    end_time        TIMESTAMPTZ,
    vehicle_class   VARCHAR(30), -- car, motorcycle, truck, bus
    appearance_vec  FLOAT[],     -- DeepSORT embedding vector
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Table: recommendations
```sql
CREATE TABLE recommendations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id      INTEGER,
    source_type     VARCHAR(20), -- 'violation', 'anomaly'
    rec_type        VARCHAR(50), -- 'signal', 'speed_bump', 'signage', 'one_way', 'resurfacing', 'drainage'
    geometry        GEOMETRY(POLYGON, 4326),
    description     TEXT,
    supporting_data JSONB,
    projected_impact FLOAT, -- % estimated improvement from what-if model
    status          VARCHAR(30) DEFAULT 'pending', -- pending, reviewed, accepted, rejected
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Table: reports
```sql
CREATE TABLE reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_zone_ids  UUID[],
    date_from       DATE,
    date_to         DATE,
    report_type     VARCHAR(20), -- 'pdf', 'excel', 'geojson'
    file_url        TEXT,
    requested_by    UUID REFERENCES users(id),
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);
```

### Table: drone_sessions
```sql
CREATE TABLE drone_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id     UUID REFERENCES users(id),
    start_time      TIMESTAMPTZ DEFAULT NOW(),
    end_time        TIMESTAMPTZ,
    patrol_zone     GEOMETRY(POLYGON, 4326),
    max_altitude    FLOAT,
    status          VARCHAR(20) DEFAULT 'active' -- active, completed, aborted
);
```

### Table: users
```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(100) UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            VARCHAR(20) NOT NULL, -- OFFICER, OPERATOR, PLANNER, MAINTENANCE, ADMIN
    full_name       VARCHAR(200),
    badge_number    VARCHAR(50),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

## 13.2 Redis Key Structure

```
live:vehicle:{track_id}          → JSON {lat, lng, speed, heading, class, violation_status} TTL: 30s
live:session:{session_id}        → JSON {drone_lat, drone_lng, altitude, vehicle_count, anomaly_count} TTL: 60s
live:violations:active           → Sorted set of active violation event IDs by timestamp
live:anomalies:recent            → Sorted set of recently flagged anomaly IDs by timestamp
telemetry:{session_id}:latest    → JSON {lat, lng, alt, pitch, roll, yaw, timestamp} TTL: 10s
```

## 13.3 MinIO Bucket Structure

```
bucket: event-snapshots/
├── violations/{year}/{month}/{day}/{violation_event_id}/
│   ├── overview.jpg         ← full drone frame at violation moment
│   └── vehicle_crop.jpg     ← cropped bounding box of the violating vehicle
├── anomalies/{year}/{month}/{day}/{anomaly_id}/
│   └── defect_crop.jpg      ← cropped segmented defect region
bucket: reports/
├── {report_id}.pdf / .xlsx / .geojson
```

---

# 14. API Design

## 14.1 FastAPI REST Endpoints

### Authentication
| Method | Endpoint | Description |
|---|---|---|
| POST | /auth/login | Issue JWT token |
| POST | /auth/refresh | Refresh access token |
| POST | /auth/logout | Invalidate token |

### Violations
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /violations | List violations (filterable by type, zone, date, status) | OFFICER, PLANNER, ADMIN |
| GET | /violations/{id} | Get single violation event with full metadata | All |
| PATCH | /violations/{id} | Update status (reviewed/actioned/resolved), attach note | OFFICER, ADMIN |
| GET | /violations/stats | Aggregated stats (count by type, by zone, by date) | PLANNER, ADMIN |
| GET | /violations/heatmap | GeoJSON heatmap for a date range | PLANNER |

### Road Anomalies
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /anomalies | List road anomalies (filterable by type, severity, zone, status) | PLANNER, MAINTENANCE, ADMIN |
| GET | /anomalies/{id} | Get single anomaly with metadata and recurrence history | All |
| PATCH | /anomalies/{id} | Update status (reviewed/work_order_issued/repaired) | MAINTENANCE, ADMIN |
| GET | /anomalies/condition-inventory | Severity-ranked condition inventory export | MAINTENANCE, PLANNER |

### Zones
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /zones | List all zones as GeoJSON | All |
| POST | /zones | Create new zone polygon | PLANNER, ADMIN |
| PUT | /zones/{id} | Update zone geometry or metadata | PLANNER, ADMIN |
| DELETE | /zones/{id} | Deactivate zone | ADMIN |

### Sessions
| Method | Endpoint | Description | Role |
|---|---|---|---|
| POST | /sessions | Start drone session | OPERATOR |
| PATCH | /sessions/{id} | End session or update status | OPERATOR |
| GET | /sessions | List sessions | ADMIN |

### Recommendations & Scenarios
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /recommendations | List AI recommendations | PLANNER |
| PATCH | /recommendations/{id} | Accept or reject a recommendation | PLANNER |
| POST | /recommendations/generate | Trigger DBSCAN + recommendation run | PLANNER, ADMIN |
| POST | /scenarios/simulate | Submit a drawn scenario, receive a projected-impact response | PLANNER |

### Reports
| Method | Endpoint | Description | Role |
|---|---|---|---|
| POST | /reports/generate | Generate a report for a given scope/date range/type | OFFICER, PLANNER, MAINTENANCE, ADMIN |
| GET | /reports | List previously generated reports | All |
| GET | /reports/{id} | Download a report | All |

### Users (Admin)
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /users | List users | ADMIN |
| POST | /users | Create user | ADMIN |
| PATCH | /users/{id} | Update role or status | ADMIN |

## 14.2 WebSocket Events

| Event | Direction | Payload | Consumer |
|---|---|---|---|
| vehicle_update | Server → Client | {track_id, lat, lng, speed, heading, class} | Monitoring Dashboard |
| violation_alert | Server → Client | {event_id, type, lat, lng, snapshot_url, confidence} | Monitoring Dashboard |
| anomaly_alert | Server → Client | {anomaly_id, type, lat, lng, severity_band, snapshot_url} | Monitoring / Planning Dashboard |
| session_status | Server → Client | {session_id, vehicle_count, anomaly_count, drone_lat, drone_lng, altitude} | Operator View |
| track_lost | Server → Client | {track_id, last_known_lat, last_known_lng} | Monitoring Dashboard |
| recommendation_ready | Server → Client | {recommendation_id, type, location} | Planning Dashboard |

## 14.3 External API Integrations

| API | Purpose | Notes |
|---|---|---|
| **Traffic Signal Controller API** | Real-time signal state (RED/GREEN/YELLOW) | IoT-connected signals; fallback to visual detection |
| **MAVLink** | Drone telemetry (GPS, altitude, attitude) | Open protocol; DJI SDK as alternative |
| **Digital Sky Platform API** | No-fly zone boundaries for patrol planning | DGCA-published airspace data |

---

# 15. Edge Cases & Answers

**Q1: What happens if the drone loses track of a vehicle under a bridge or tree?**

A: DeepSORT's Kalman filter predicts the vehicle's position during occlusion using last known velocity and heading. If the vehicle re-emerges within the predicted range within a configurable time window (default 5 seconds), the same Track ID is restored. If it does not re-emerge, the track is marked "lost" and any in-progress violation evaluation is dropped for that track. Track loss is logged for post-session review.

**Q2: What happens if the drone loses signal mid-session?**

A: The detection pipeline writes events to a local buffer (Redis Streams) that persists independently of the WebSocket connection to the dashboard. When the drone reconnects, buffered events are flushed to PostgreSQL. The session is marked "degraded" during the disconnection window. If the drone does not reconnect within 60 seconds, the session is marked "aborted" and all events captured before disconnection are preserved.

**Q3: How does the system perform in night or low-light conditions?**

A: At night, YOLOv8 detection confidence drops significantly on standard RGB footage. The system flags any session where average detection confidence drops below 0.60 as "low-light degraded." Events detected during such sessions are tagged "low-light" and rendered with a confidence caveat on the dashboard rather than being suppressed outright — since this is a monitoring system, not an automated penalty pipeline, lower-confidence events remain visible but clearly labelled.

**Q4: How does the system handle rain, fog, or severe weather?**

A: Weather-induced image degradation reduces detection accuracy for both violations and road anomalies (e.g. rain can mimic waterlogging). The system monitors average YOLOv8 confidence per frame as a proxy for image quality. If the 30-frame rolling average confidence drops below 0.50, the session is flagged "weather degraded" and new events from that window are tagged accordingly rather than treated as high-confidence detections. The operator is alerted.

**Q5: What if the drone accidentally exceeds DGCA altitude limits?**

A: The drone telemetry stream includes real-time altitude. If altitude exceeds the configured limit (default 120m), the system sends an alert to the drone operator view and logs the overage. Detection continues but events during the overage window are tagged "altitude non-compliant" for operator awareness and regulatory audit. A hard warning at 110m gives the operator time to descend.

**Q6: How are false positives handled?**

A: Every flagged event carries its confidence/severity score into the dashboard. Reviewers (officers, planners, maintenance engineers) can dismiss an event with a reason, which is logged for model improvement feedback. The system tracks the dismissal rate per event type/category; if a category exceeds 20% dismissal rate, it is flagged for model retraining.

**Q7: How does the system handle the CARLA-to-real-world transfer gap?**

A: CARLA generates photorealistic but synthetic data. The domain gap is addressed through: (a) CARLA data augmentation — weather effects, lighting variation, motion blur applied during training; (b) fine-tuning on a small set of real drone footage before production deployment; (c) confidence calibration after real-world validation. Road surface anomaly detection additionally requires supplementing CARLA-generated road textures with a real-world pavement-distress dataset, since CARLA does not natively model surface degradation.

**Q8: What happens when vehicle density is very high (100+ vehicles in frame)?**

A: DeepSORT performance degrades at very high density due to ID switching. ByteTrack is available as an alternative tracker for high-density scenarios, and the system is configurable to switch tracker based on detected vehicle count per frame. Above 80 vehicles in frame, a density warning is logged and confidence thresholds are automatically raised.

**Q9: What if two drones are covering the same zone simultaneously?**

A: Each drone runs its own independent detection pipeline instance, identified by session_id. If two sessions have overlapping patrol zones, the system deduplicates events using a spatial and temporal check: if two events of the same type occur within 10 metres and 5 seconds of each other across two sessions, the higher-confidence event is retained and the other is marked "duplicate."

**Q10: How does the system avoid re-logging the same pothole every patrol session?**

A: Every newly detected road anomaly is checked against the existing `road_anomalies` table using a PostGIS `ST_DWithin` proximity query (default 3m radius) plus type match. A match updates `last_seen_at` and `recurrence_count` on the existing record rather than creating a duplicate; a rising recurrence count with stable/worsening severity is itself a useful signal for maintenance prioritisation.

**Q11: What if a vehicle enters the violation zone from outside the drone's field of view?**

A: Vehicles entering from outside the frame are assigned a new Track ID by DeepSORT when they become visible. For zone-based violations, the violation timer starts from the first visible frame inside the zone. For trajectory-based violations, detection may be delayed by up to 10 frames due to insufficient trajectory history — a known, documented limitation.

**Q12: How does the system distinguish a genuine road defect from a temporary condition (spilled liquid, shadow, tar patch)?**

A: The severity scorer weights recurrence across multiple independent sessions more heavily than a single-session detection. A defect seen only once and not observed on the next patrol pass is retained at low confidence but not surfaced as a priority item; a defect confirmed across two or more sessions is promoted to a standard-confidence entry in the condition inventory.

---

# 16. Real World Issues & Mitigations

## 16.1 DGCA Regulations Compliance

**Issue:** India's DGCA (Directorate General of Civil Aviation) regulates UAV operations under the Drone Rules 2021. Civilian drones are limited to 120m AGL and require operator certification, flight plan filing in controlled airspace, and no-fly zone adherence.

**Mitigation:**
- System enforces altitude monitoring with real-time alerts at 110m (warning) and 120m (hard limit flag)
- Patrol zone planner integrates no-fly zone data from the Digital Sky Platform API
- All sessions are logged with operator ID, altitude profile, and zone metadata for regulatory audit

## 16.2 Privacy Concerns — Aerial Surveillance

**Issue:** Continuous aerial surveillance of public roads raises privacy concerns under the DPDP Act 2023. Footage of individuals who are not part of a flagged event must be handled carefully.

**Mitigation:**
- Drone cameras are pointed at roads only — not residential windows or private property
- Non-flagged vehicle data is held in Redis with 30-second TTL and not persisted to long-term storage
- Only flagged-event images are stored in MinIO — general footage is not archived
- Facial recognition is explicitly not used at any point in the pipeline

## 16.3 Data Storage and Retention Policies

**Issue:** Long-term storage of surveillance data creates privacy and compliance obligations.

**Mitigation:**
- Live Redis state: 30-second TTL — no long-term storage of non-flagged vehicles
- Violation and anomaly events: retained for 2 years for trend analysis
- Drone session footage: not stored by default — only event snapshot crops are retained
- Reporting and recommendation data: aggregated — no individual vehicle linkage retained beyond the active session

## 16.4 Network Latency in Real-Time Streaming

**Issue:** RTSP drone feed + WebSocket twin updates must be delivered with low enough latency for real-time monitoring use.

**Mitigation:**
- Detection pipeline targets < 100ms inference time per frame on RTX 4060
- WebSocket updates pushed at 10Hz
- Redis Pub/Sub used for fan-out to multiple dashboard clients
- Local deployment (edge device or LAN server) recommended for production to eliminate internet latency

## 16.5 Model Bias Toward Certain Vehicle Types

**Issue:** YOLOv8 pretrained on COCO may perform better on car-type vehicles and worse on auto-rickshaws, two-wheelers, or heavy vehicles common in Indian traffic.

**Mitigation:**
- CARLA dataset augmented with Indian vehicle types using custom blueprints
- Additional fine-tuning on Indian traffic datasets (IDD — India Driving Dataset)
- Per-class detection accuracy tracked separately; classes below F1 0.65 flagged for retraining

## 16.6 GPS Accuracy in Urban Canyons

**Issue:** Urban environments with tall buildings cause GPS multipath errors, reducing position accuracy to 5–10m in some areas.

**Mitigation:**
- Drone RTK GPS recommended for production deployment (accuracy < 5cm)
- Kalman filter smoothing applied to trajectories to reduce position noise
- Zone polygons include a configurable buffer margin (default 2m) to account for GPS error

## 16.7 Seasonal and Recurring Nature of Road Surface Defects

**Issue:** Pothole formation and waterlogging are strongly seasonal (monsoon-driven), and defect severity can change rapidly between patrol sessions.

**Mitigation:**
- Condition inventory tracks severity trend over time per location, not just a point-in-time snapshot
- Patrol scheduling can be intensified during monsoon season via the drone session planner
- Reports can be scoped to compare pre/post-monsoon condition for budget planning

---

# 17. CARLA Simulation Strategy

## 17.1 Why CARLA

CARLA (Car Learning to Act) is an open-source autonomous driving simulator that provides photorealistic 3D urban environments, a rich Python API for scenario scripting, ground-truth sensor data, and a broad vehicle blueprint library. It eliminates the need for real drone footage during development and testing, enabling:

- Controlled, reproducible testing of all 10 violation types
- Automatic ground-truth label generation (no manual annotation)
- Testing of edge cases that cannot be safely staged in real traffic
- Generation of large-scale synthetic training datasets
- Validation of the detection pipeline before real UAV deployment

## 17.2 Simulation Setup

**Environment:** CARLA 0.9.15 in synchronous mode at 20 FPS
**Hardware:** HP Omen laptop, RTX 4060 — medium render quality, ray tracing OFF
**Map:** CARLA Town05 (multi-lane urban with intersections, flyovers, parking areas)

**Drone Simulation:**
```python
# Spectator camera mounted at 100m altitude with gimbal tilt control
spectator = world.get_spectator()
drone_transform = carla.Transform(
    carla.Location(x=50, y=50, z=100),
    carla.Rotation(pitch=-90, yaw=0, roll=0)  # nadir view
)
spectator.set_transform(drone_transform)

# RGB camera sensor attached to spectator position
camera_bp = world.get_blueprint_library().find('sensor.camera.rgb')
camera_bp.set_attribute('image_size_x', '1920')
camera_bp.set_attribute('image_size_y', '1080')
camera_bp.set_attribute('fov', '90')
```

## 17.3 Violation Scripting Plan

| Violation | Script Approach |
|---|---|
| No-parking | Spawn vehicle in no-parking polygon, apply full brake, hold 45 seconds |
| Wrong-way | Spawn vehicle facing opposite road direction, enable autopilot on reversed waypoints |
| Illegal U-turn | Spawn vehicle at restricted zone entry, script 180° arc waypoints inside zone |
| Red-light jumping | Set signal state to RED via CARLA API, spawn vehicle crossing stop line |
| Speeding | Apply target velocity above zone speed limit via set_target_velocity() |
| Lane violation | Script vehicle path crossing lane boundary polygon and sustaining crossing |
| Zebra crossing | Spawn vehicle stopped inside zebra crossing polygon |
| Helmet-less riding | Spawn motorcycle blueprint without helmet actor attached to rider |
| Overloading | Spawn motorcycle with 3 passenger actors attached |
| Highway stopping | Spawn vehicle on flyover segment, apply brake, hold |

## 17.4 Road Anomaly Simulation Approach

CARLA does not natively model road surface degradation, so anomaly training data uses a hybrid approach:

- **Synthetic texture overlay** — pothole, crack, and waterlogging texture patches procedurally composited onto CARLA road meshes at randomised locations before frame capture, with the composited region auto-exported as a ground-truth segmentation mask
- **Real-world dataset supplement** — public pavement-distress datasets (e.g. RDD — Road Damage Dataset) re-perspectived toward an aerial/oblique viewing angle via geometric augmentation to approximate drone viewpoint
- **Debris simulation** — CARLA static prop actors (traffic cones, fallen objects) placed on road segments to generate debris/obstruction training frames

## 17.5 Dataset Generation Pipeline

```
CARLA Session (synchronous mode)
       ↓
Per frame (every 5th frame = 4 FPS export):
  ├── RGB image saved → /dataset/images/{session_id}/{frame_id}.jpg
  ├── Semantic segmentation mask → /dataset/masks/{session_id}/{frame_id}.png
  ├── Ground truth bounding boxes (YOLO format) → /dataset/labels/{session_id}/{frame_id}.txt
  ├── Road anomaly segmentation mask (composited) → /dataset/anomaly_masks/{session_id}/{frame_id}.png
  ├── Vehicle GPS coordinates → /dataset/gps/{session_id}/{frame_id}.json
  └── Violation ground truth label → /dataset/violations/{session_id}/{frame_id}.json
       ↓
Dataset assembled: 10,000+ frames per violation type; 3,000+ frames per anomaly type
       ↓
Augmentation: weather, brightness, motion blur, compression artefacts
       ↓
YOLOv8 / YOLOv8-seg training on assembled dataset
       ↓
Model evaluation on held-out CARLA test set
       ↓
Transfer: fine-tune on small real drone footage / real pavement-distress dataset
```

## 17.6 Sim-to-Real Transfer Approach

- **Augmentation during training** — CARLA frames augmented with realistic degradation (rain overlay, motion blur, JPEG compression) to reduce domain gap
- **Fine-tuning** — base model trained on CARLA data is fine-tuned on a small set of manually labelled real drone frames and real pavement-distress images before production
- **Confidence calibration** — detection confidence and severity-scoring thresholds validated and adjusted on real footage before deployment

## 17.7 Validation Methodology

| Test Type | Approach |
|---|---|
| Unit validation | Each violation/anomaly script tested in isolation — detection rate measured |
| Edge case validation | Occlusion test, high-density test, defect-recurrence test scripted in CARLA |
| Integration validation | Full pipeline end-to-end on a scripted 30-minute CARLA session |
| Transfer validation | CARLA-trained model evaluated on real drone footage; mAP compared |

---

# 18. Digital Twin Design

## 18.1 What the Twin Mirrors

The digital twin is a continuously updated 3D virtual replica of the monitored road network. It mirrors:
- Every detected vehicle's real-time position, speed, and class
- All defined zone polygons as geo-fenced overlays
- All violation events with their exact GPS coordinates, timestamps, and snapshots
- All road surface anomalies with severity-coded markers
- Drone position and altitude
- Historical violation and anomaly density as heatmap layers (planning mode)
- AI recommendation polygons and saved what-if scenarios (planning mode)

## 18.2 Zone Polygon Management

Zone polygons are the foundational layer of the twin — they define where violation logic applies. Zones are:
- Drawn directly on the twin using a polygon drawing tool (planning dashboard)
- Stored as GeoJSON in PostGIS
- Colour-coded by zone type (red = no-parking, yellow = speed zone, blue = intersection)
- Editable and deletable without system restart
- Immediately active in the violation engine upon save
- Versioned — zone edit history maintained for audit

## 18.3 Live Vehicle Layer

Every vehicle tracked by DeepSORT appears on the twin as a 3D marker entity in CesiumJS, updated at 10Hz via WebSocket:

- **Green marker** — vehicle moving normally
- **Yellow marker** — vehicle in a suspicious state (approaching a violation threshold)
- **Red marker** — vehicle in an active violation
- Click on marker → popup showing Track ID, vehicle class, speed, heading, violation history in session

Live vehicle entities are ephemeral — they exist only while the vehicle is tracked. Non-flagged vehicle entities are removed from the twin display after 30 seconds of no update (matching Redis TTL).

## 18.4 Violation & Anomaly Event Layer

When an event is flagged:
- A pin is dropped at the exact GPS coordinate on the twin
- Pin colour/icon = event type and, for anomalies, severity band (low/medium/high)
- Click pin → popup with type, timestamp, confidence/severity score, description, snapshot image
- Pins persist on the twin and accumulate over sessions
- Pins filterable by type, date range, confidence/severity level, status

## 18.5 Historical Heatmap Layer (Planning Mode)

- Event GPS points aggregated into a heatmap tile layer, separately selectable for violations and road anomalies
- Colour scale from cool (low density) to hot (high density)
- Adjustable date range and type filters
- Rendered as a CesiumJS heatmap layer
- Updates on query — not real-time (batch computed)

## 18.6 Scenario Builder & What-If Overlay (Planning Mode)

- Planners draw proposed infrastructure changes directly on the twin — new signal position, one-way direction, lane closure, drainage improvement
- Each drawn scenario is stored and can be re-opened, edited, or shared
- Recommendation polygons from the clustering engine are rendered as semi-transparent overlays; click a recommendation to see the breakdown, projected impact, and proposed action
- Accepted/rejected recommendation status is reflected in polygon opacity

---

# 19. Dashboard Specifications

## 19.1 Monitoring Dashboard — Live Operations

**Purpose:** Real-time situational awareness and response coordination for field officers and control room supervisors

**Layout:**
- Left panel (30%): Live event feed — violations and anomalies as they are flagged, with type, GPS, description, confidence, timestamp
- Center (55%): CesiumJS digital twin — live vehicle markers, event pins, drone position
- Right panel (15%): Zone status — active zones, event count per zone in the current session

**Features:**

| Feature | Description |
|---|---|
| Live twin view | 3D map with all tracked vehicles moving in real-time at 10Hz |
| Live event feed | Real-time list of flagged violations and anomalies; click to jump the twin to that location |
| Vehicle spotlight | Click any vehicle marker → trajectory trail, speed, class, violation history |
| Field responder map | Positions of field officers (GPS from their mobile devices) |
| One-click dispatch | Assign a responder to an event with one tap |
| Drone status bar | Current altitude, session status, vehicle/anomaly count, feed quality |
| Event snapshot viewer | Photo evidence for each flagged event |
| Review / dismiss | Reviewer confirms or dismisses an AI-flagged event with a note |
| Session timeline | Scrub through the current session's events chronologically |

## 19.2 Urban Planning Dashboard — Historical Intelligence & Simulation

**Purpose:** Analysis of historical violation and road-condition data for infrastructure planning and policy decisions

**Layout:**
- Left panel (25%): Filter controls — date range, event types, zones, confidence/severity level
- Center (55%): CesiumJS twin in planning mode — heatmap layer, recommendation overlays, scenario builder
- Right panel (20%): Analytics charts — trend lines, type breakdown, top zones

**Features:**

| Feature | Description |
|---|---|
| Violation heatmap | Interactive density heatmap over any date range on the 3D twin |
| Road condition heatmap | Severity-coded overlay of road surface anomalies |
| Trend charts | Line charts — events over time per type; Recharts |
| Zone problem ranking | Ranked list of zones by total violation/anomaly count — sortable by type |
| AI recommendation panel | List of DBSCAN-generated recommendations with supporting data |
| What-if scenario simulator | Draw a proposed infrastructure change on the twin → system projects impact |
| Time-of-day analysis | Heatmap filtered by hour of day — identifies peak violation windows |
| Condition inventory export | Severity-ranked list of road anomalies for maintenance work orders |
| Month-over-month comparison | Twin layers toggling between months for visual pattern comparison |
| PDF/Excel/GeoJSON export | Export current view's statistics and recommendations |
| Zone editor | Draw, edit, delete zones directly on the twin |

---

# 20. Automated Analysis & Reporting Architecture

## 20.1 Architecture Overview

The automated analysis and reporting layer converts raw detection events into decision-ready output without manual compilation. It is the mechanism through which the platform delivers Objective 5 (Automated Analysis and Reporting) and directly supports Objective 6 (Improved Road Safety and Infrastructure Management) by ensuring every detected issue reaches the right stakeholder in a usable form.

```
EVENTS (violations + road anomalies)
      ↓ structured, geo-tagged, scored
AGGREGATION ENGINE (scheduled + on-demand)
      ↓ counts, trends, hotspot summaries, severity breakdowns
TEMPLATE RENDERER
      ↓ PDF / Excel / GeoJSON
DELIVERY
      ↓ dashboard download, scheduled email, GIS import
STAKEHOLDER (officer, planner, maintenance engineer, municipal authority)
```

## 20.2 Report Types

| Report Type | Contents | Primary Audience |
|---|---|---|
| Zone Summary Report | Event counts by type, trend vs. previous period, top hotspots | Control room, planners |
| Road Condition Report | Anomaly inventory sorted by severity, recurrence trend, suggested work order priority | Maintenance engineers |
| Planning Recommendation Report | AI recommendations, supporting cluster data, what-if projections | City planners, municipal council |
| Session Report | Per-drone-session summary — coverage area, events detected, confidence distribution | Drone operators, admins |

## 20.3 Scheduling & Delivery

- On-demand generation via `/reports/generate` — typically ready within 10 seconds for a standard zone/date-range scope
- Scheduled weekly/monthly reports configured per role via Celery Beat, delivered as a stored file with a notification
- All generated reports are retained and listable via `/reports`, forming an audit trail of what was reported and when

## 20.4 Data Integrity in Reporting

- Every figure in a generated report is traceable back to the underlying event records that produced it (no manual editing of aggregated numbers)
- Reports clearly label confidence/severity distributions rather than presenting a single blended number, so recipients can judge data reliability
- Low-confidence or degraded-session events are included but visually distinguished, never silently excluded

---

# 21. Intelligent Urban Planning & Recommendation Engine

## 21.1 DBSCAN Clustering Logic

DBSCAN (Density-Based Spatial Clustering of Applications with Noise) is applied to historical violation and road-anomaly GPS coordinates to identify spatial hotspots.

**Parameters:**
- `eps = 50` (metres) — maximum distance between two events to be considered the same cluster
- `min_samples = 10` — minimum events to form a cluster
- Events outside all clusters = noise (isolated incidents, not actionable patterns)

**Clustering is run:**
- On demand (planner triggers from dashboard)
- Automatically weekly via a Celery scheduled task
- Separately per event type (violation type or anomaly type) to identify type-specific patterns

## 21.2 Recommendation Rule Set

| Cluster Profile | Recommendation Type | Reasoning |
|---|---|---|
| >40% red-light jumping at intersection | Traffic signal installation | Crossing behaviour indicates a missing or inadequate signal |
| >40% speeding on a segment near school/hospital | Speed bump + advisory signage | High-vulnerability zone requires physical calming |
| >40% wrong-way driving on a road | One-way conversion + physical barriers | Persistent wrong-way suggests confusing road design |
| >50% lane violations on a multi-lane stretch | Lane marking refresh + rumble strips | Faded or inadequate lane markings |
| High-severity pothole cluster on an arterial road | Priority resurfacing | Safety-critical surface degradation on a high-traffic segment |
| Chronic waterlogging cluster (recurring across sessions) | Drainage improvement | Recurrence indicates a structural drainage problem, not a one-off event |
| High violation density across all types in a time window | Enforcement schedule adjustment | Deploy monitoring/response during peak violation hours |

## 21.3 What-If Scenario Simulator Design

The what-if simulator allows city planners to construct custom traffic scenarios and estimate their impact on violation and safety patterns — directly supporting the goal of enabling planners to "create custom traffic scenarios and simulate different conditions."

**Mechanism:**
- Planner draws a proposed change on the twin (new signal position, new one-way direction, new lane configuration, drainage change)
- System identifies all historical events that would be affected by this change
- A rule-based model estimates impact — e.g., adding a signal at an intersection → historical red-light-jumping events at that location → projected 70–80% reduction based on signal-compliance study benchmarks
- Projection displayed as "Before: 142 violations/month" → "Projected After: ~35 violations/month"
- Scenarios are saveable and shareable, so multiple proposed changes for the same segment can be compared side by side

**Limitation:** The projection is rule-based, not causal ML — it is an estimate for planning guidance, not a precise forecast. It is clearly labelled as a "projected estimate" in the UI.

## 21.4 Output Format for Planning & Government Use

Recommendations and scenario results are exportable as:
- **PDF report** — executive summary, heatmap image, top recommended actions, what-if projections, supporting statistics
- **GeoJSON export** — all recommendation polygons for import into government GIS systems
- **Excel export** — tabular violation/anomaly data per zone for spreadsheet analysis

---

# 22. Limitations & Future Scope

## 22.1 Current System Limitations

| Limitation | Impact | Mitigation Taken |
|---|---|---|
| Detection accuracy drops at night | Night monitoring not fully reliable | Low-light session flagging with visible confidence caveats |
| CARLA-to-real domain gap | Model may underperform on first real deployment | Fine-tuning + augmentation strategy |
| CARLA does not natively model road surface degradation | Anomaly training data requires synthetic overlay + real dataset supplement | Hybrid texture-overlay + real pavement-distress dataset approach |
| What-if simulator is rule-based, not causal | Projections are estimates, not precise | Clearly labelled as estimates in the UI |
| High vehicle density reduces tracker accuracy | More ID switches in dense traffic | ByteTrack fallback + raised confidence threshold |
| Single-zone CARLA training | Model may underperform in new geographies | Dataset diversity plan for production |
| No automated legal enforcement pipeline | System does not itself issue penalties | By design — scope is monitoring, analysis, and planning intelligence; enforcement action remains with human authorities |

## 22.2 Future Scope

- **Multi-drone coordination** — fleet management for city-wide coverage with conflict resolution
- **Edge deployment** — onboard drone inference eliminating latency from ground processing
- **Predictive maintenance** — ML model predicting which road segments are likely to develop high-severity defects before they appear, based on traffic load and existing condition trend
- **Predictive enforcement** — ML model predicting high-violation time-location combinations for proactive deployment
- **Night-mode with thermal camera** — full 24-hour operation capability
- **Causal what-if modelling** — replace rule-based projection with a trained causal ML model
- **Integration with smart traffic signals** — real-time signal timing adjustment based on violation patterns
- **Optional enforcement integration** — for jurisdictions that require it, an opt-in module could bridge flagged violations to existing ground-level ANPR/challan infrastructure, kept outside the core platform scope
- **Federated learning** — multiple city deployments contributing to shared model improvement without sharing raw data
- **Carbon footprint analysis layer** — idling vehicles in violation zones mapped to emissions estimates for environmental planning

---

# 23. References

1. Bochkovskiy, A., Wang, C. Y., & Liao, H. Y. M. (2020). YOLOv4: Optimal speed and accuracy of object detection. *arXiv:2004.10934*
2. Wojke, N., Bewley, A., & Paulus, D. (2017). Simple online and realtime tracking with a deep association metric. *ICIP 2017*
3. Dosovitskiy, A. et al. (2017). CARLA: An open urban driving simulator. *CoRL 2017*
4. DGCA India. (2021). Drone Rules 2021. Ministry of Civil Aviation, Government of India
5. Government of India. (2023). Digital Personal Data Protection Act 2023
6. Arya, D. et al. (2020). Global Road Damage Detection: State-of-the-art solutions. *IEEE BigData 2020* (RDD dataset)
7. Ester, M. et al. (1996). A density-based algorithm for discovering clusters in large spatial databases with noise. *KDD 1996* (DBSCAN original paper)
8. Indian Institute of Technology. India Driving Dataset (IDD). *idd.insaan.iiit.ac.in*
9. Cesium. CesiumJS Documentation. *cesium.com/docs*
10. Ultralytics. YOLOv8 Documentation. *docs.ultralytics.com*

---

# 24. Glossary

| Term | Definition |
|---|---|
| **AGL** | Above Ground Level — drone altitude measured from the ground directly below the drone |
| **BVLOS** | Beyond Visual Line of Sight — drone operation where the operator cannot see the drone directly |
| **CARLA** | Car Learning to Act — open-source photorealistic autonomous driving simulator |
| **DBSCAN** | Density-Based Spatial Clustering of Applications with Noise — clustering algorithm that groups geographically close data points |
| **DeepSORT** | Deep Simple Online and Realtime Tracking — multi-object tracking algorithm using appearance embeddings and Kalman filter |
| **DGCA** | Directorate General of Civil Aviation — India's aviation regulatory authority |
| **Digital Twin** | A virtual 3D replica of a physical environment that mirrors real-world state in real time |
| **DPDP Act** | Digital Personal Data Protection Act 2023 — India's data privacy legislation |
| **GeoJSON** | Open standard format for encoding geographic data structures |
| **Geo-fencing** | Using GPS coordinates to define a virtual geographic boundary |
| **GSD** | Ground Sampling Distance — real-world distance represented by one pixel in an aerial image |
| **Homography** | Mathematical transformation mapping points from one plane (image) to another (ground) |
| **JWT** | JSON Web Token — compact, self-contained token for authentication and authorisation |
| **Kalman Filter** | Mathematical algorithm for estimating the state of a dynamic system from noisy measurements |
| **MAVLink** | Micro Air Vehicle Link — lightweight open-source communication protocol for drones |
| **MinIO** | S3-compatible open-source object storage server |
| **mAP** | Mean Average Precision — standard metric for evaluating object detection model accuracy |
| **Nadir** | Camera orientation pointing straight down — directly below the drone |
| **OSM** | OpenStreetMap — free, open-source global map data |
| **PostGIS** | Spatial extension for PostgreSQL enabling geo-spatial queries |
| **RBAC** | Role-Based Access Control — restricting system access based on user roles |
| **RDD** | Road Damage Dataset — public benchmark dataset of pavement distress imagery |
| **ReID** | Re-Identification — matching the same object across different camera views |
| **RTSP** | Real Time Streaming Protocol — standard for streaming video over networks |
| **RTK GPS** | Real-Time Kinematic GPS — high-accuracy GPS with centimetre-level precision |
| **Severity Score** | Computed numeric score (0.0–1.0) representing the estimated seriousness of a detected road surface anomaly |
| **Sim-to-Real** | Transfer learning approach where a model trained on simulation data is adapted for real-world use |
| **Track ID** | Unique persistent identifier assigned to a detected vehicle throughout a tracking session |
| **UAV** | Unmanned Aerial Vehicle — drone |
| **What-If Simulator** | Digital twin module allowing planners to model a proposed infrastructure change and view a projected impact |
| **WebSocket** | Full-duplex communication protocol over a single TCP connection — enables real-time server-to-client data push |
| **YOLOv8** | You Only Look Once version 8 — state-of-the-art real-time object detection model |
| **YOLOv8-seg** | Segmentation variant of YOLOv8 used for pixel-level road surface anomaly detection |

---

# 25. Assumptions & Constraints

## 25.1 Assumptions

| # | Assumption |
|---|---|
| A1 | The drone has a stable GPS signal providing position accuracy within ±5m throughout the session |
| A2 | Traffic signal state is accessible via API for IoT-connected signals; visual detection used as fallback |
| A3 | Drone camera can be tilted via gimbal for angled views when helmet detection or road surface inspection requires it |
| A4 | Monitored road zone has adequate mobile or WiFi network connectivity for real-time data streaming |
| A5 | Drone operator is DGCA-certified and complies with all operational requirements |
| A6 | The digital twin is operated on a device with sufficient GPU for CesiumJS 3D rendering |
| A7 | Zone polygons are pre-configured by an admin/planner before violation detection begins |
| A8 | Detected violations and anomalies are acted upon by the relevant human authority — the system does not assume automatic enforcement or automatic repair dispatch |

## 25.2 Constraints

| # | Constraint | Impact |
|---|---|---|
| C1 | DGCA altitude limit: 120m AGL maximum | Detail available for road-surface-level defects and helmet visibility is limited by resolution at this altitude |
| C2 | DGCA no-fly zones (airports, military, restricted areas) | Patrol zones must exclude these areas |
| C3 | RTX 4060 laptop GPU available for development | CARLA render quality limited to medium; processing pipeline optimised for this hardware |
| C4 | No facial recognition permitted | Rider identification limited to helmet presence/absence, not face |
| C5 | DPDP Act 2023 compliance required | Non-flagged footage must not be stored long-term |
| C6 | CARLA does not natively simulate road surface degradation | Anomaly training requires synthetic texture overlay and real-dataset supplementation |

---

# 26. System Modes of Operation

## Mode 1 — Simulation Mode (CARLA)

**Description:** Full pipeline operates on CARLA simulator input. No real drone involved.
**Use Case:** Development, testing, dataset generation, violation/anomaly scripting, edge case validation.
**Data Source:** CARLA Python API — frames + ground truth GPS.
**Indicator:** Dashboard banner shows "SIMULATION MODE — CARLA DATA."

## Mode 2 — Live Drone Mode

**Description:** Full pipeline operates on real UAV RTSP stream with MAVLink telemetry.
**Use Case:** Production monitoring operations.
**Data Source:** Real drone camera + GPS telemetry.
**Requirements:** Active drone session, DGCA-compliant altitude.
**Indicator:** Dashboard banner shows "LIVE SESSION — [session_id]."

## Mode 3 — Playback / Review Mode

**Description:** System re-processes previously recorded drone footage or replays a past session on the digital twin.
**Use Case:** Reviewing a completed session, analysing a specific past incident, model evaluation.
**Data Source:** Archived session footage + event logs.
**Note:** Events cannot be newly created in playback mode — events already in the database are reviewed only.
**Indicator:** Dashboard shows a scrubber timeline and "PLAYBACK MODE" banner.

## Mode 4 — Planning Mode

**Description:** Urban Planning Dashboard operates independently of any live session, querying only historical data.
**Use Case:** Infrastructure and maintenance planning analysis outside of active monitoring operations.
**Data Source:** PostgreSQL historical violation_events, road_anomalies, recommendations.
**No live feed:** Digital twin shows static heatmaps, no moving vehicles.
**Indicator:** No session banner; the Planning Dashboard is the default view for PLANNER/MAINTENANCE roles.

## Mode 5 — Offline Mode

**Description:** Network connection lost during an active live session.
**Behaviour:** Detection pipeline continues. Events written to a local Redis Streams buffer. WebSocket reconnection attempted every 10 seconds. Upon reconnect, the buffered events are flushed to PostgreSQL. Dashboard shows a "Connection lost — reconnecting" banner.
**Data Loss Risk:** If Redis is also unavailable, in-flight events may be lost. Mitigated by Redis persistence (AOF mode enabled).

---

# 27. Confidence & Accuracy Benchmarking Plan

## 27.1 Object Detection Metrics

| Metric | Description | Target |
|---|---|---|
| **mAP@0.5** | Mean Average Precision at IoU 0.5 — standard detection accuracy | ≥ 0.75 |
| **mAP@0.5:0.95** | mAP across IoU thresholds 0.5–0.95 — stricter accuracy | ≥ 0.55 |
| **Per-class AP** | AP per vehicle class (car, motorcycle, truck, bus) | ≥ 0.70 per class |
| **Inference time** | Time per frame on RTX 4060 | ≤ 100ms (≥ 10 FPS) |

## 27.2 Tracking Metrics

| Metric | Description | Target |
|---|---|---|
| **MOTA** | Multiple Object Tracking Accuracy | ≥ 0.65 |
| **ID Switch Rate** | Frequency of Track ID reassignment to the wrong vehicle | ≤ 5% |
| **Track Continuity** | % of frames where the correct Track ID is maintained per vehicle | ≥ 90% |

## 27.3 Violation Detection Metrics

Measured per violation type across a CARLA test set of 100 scripted scenarios per violation:

| Metric | Description | Target |
|---|---|---|
| **Precision** | Of all flagged events, how many were actual violations | ≥ 0.85 |
| **Recall** | Of all actual violations, how many were detected | ≥ 0.75 |
| **F1 Score** | Harmonic mean of precision and recall | ≥ 0.70 per violation type |
| **False Positive Rate** | % of flagged events that were not violations | ≤ 15% |
| **False Negative Rate** | % of real violations that were missed | ≤ 25% |

## 27.4 Road Surface Anomaly Detection Metrics

Measured per anomaly category across a mixed CARLA-synthetic and real pavement-distress test set:

| Metric | Description | Target |
|---|---|---|
| **Segmentation IoU** | Intersection-over-union of predicted vs. ground-truth defect region | ≥ 0.55 |
| **Precision** | Of all flagged anomalies, how many were genuine defects | ≥ 0.80 |
| **Recall** | Of all genuine defects, how many were detected | ≥ 0.70 |
| **F1 Score** | Harmonic mean of precision and recall | ≥ 0.65 per anomaly category |
| **Severity Scoring Correlation** | Correlation between computed severity score and manual inspector rating on a validation subset | Spearman ρ ≥ 0.6 |

## 27.5 System Performance Metrics

| Metric | Description | Target |
|---|---|---|
| **End-to-end latency** | Time from event occurrence to dashboard alert | ≤ 3 seconds |
| **Twin update frequency** | Vehicle position update rate on the digital twin | 10 Hz |
| **API response time** | REST endpoint response under 100 concurrent users | ≤ 200ms |
| **WebSocket message delay** | Delay between event creation and client receipt | ≤ 500ms |
| **Report generation time** | Time to produce a standard zone/date-range report | ≤ 10 seconds |

## 27.6 Geo-Projection Accuracy

| Metric | Description | Target |
|---|---|---|
| **GPS projection error** | Average error between projected GPS and ground truth GPS | ≤ 2m at 100m altitude |
| **Zone intersection accuracy** | Correct zone membership determination | ≥ 98% |

---

# 28. Security & Access Control

## 28.1 Role-Based Access Control (RBAC)

| Feature | OFFICER | OPERATOR | PLANNER | MAINTENANCE | ADMIN |
|---|---|---|---|---|---|
| View Monitoring Dashboard | ✓ | ✓ | ✗ | ✗ | ✓ |
| View Urban Planning Dashboard | ✗ | ✗ | ✓ | ✓ (Road Condition view) | ✓ |
| Review / dismiss events | ✓ | ✗ | ✗ | ✗ | ✓ |
| Start / end drone sessions | ✗ | ✓ | ✗ | ✗ | ✓ |
| Create / edit zones | ✗ | ✗ | ✓ | ✗ | ✓ |
| Update anomaly status (work order/repaired) | ✗ | ✗ | ✗ | ✓ | ✓ |
| Trigger recommendation generation | ✗ | ✗ | ✓ | ✗ | ✓ |
| Run what-if scenario simulator | ✗ | ✗ | ✓ | ✗ | ✓ |
| Generate reports | ✓ | ✗ | ✓ | ✓ | ✓ |
| Manage users | ✗ | ✗ | ✗ | ✗ | ✓ |
| View system health | ✗ | ✓ | ✗ | ✗ | ✓ |

## 28.2 API Authentication

- All REST endpoints protected by a JWT bearer token
- Tokens issued at `/auth/login` with 8-hour expiry for field/shift roles
- Refresh tokens with 30-day expiry stored in HTTP-only cookies
- Role claim embedded in the JWT payload — role checked at endpoint level
- Failed authentication attempts logged with IP and timestamp

## 28.3 Drone Feed Security

- RTSP stream from the drone transmitted over WPA3-encrypted WiFi or a 4G LTE private APN
- Stream authenticated by session token — unauthenticated streams rejected
- MAVLink telemetry encrypted via MAVLink 2 signing

## 28.4 Data Access Audit Log

All actions on violation and anomaly events are logged:
```
{
  user_id, role, action, resource_id, timestamp, ip_address, result
}
```
Audit logs are immutable — append-only, no delete permission for any role including ADMIN.

## 28.5 Event Data Access

- Event snapshots (images) stored in MinIO with presigned URL access — URLs expire in 1 hour
- No direct public URL access to snapshot images
- Officers and maintenance engineers can only view events from their own jurisdictional zone (zone-scoped query)

---

# 29. Failure & Recovery Design

## 29.1 Detection Pipeline Crash

**Failure:** YOLOv8/YOLOv8-seg inference process crashes mid-session.
**Detection:** Watchdog process monitors the inference subprocess; no-heartbeat timeout of 5 seconds.
**Recovery:** Watchdog restarts the inference subprocess automatically. Session continues from the current frame. Gap in detection (during restart) logged as "pipeline gap: {start} to {end}" in the session record.
**Data Impact:** Events occurring during the restart window (~2–5 seconds) may be missed. Logged as a known gap.

## 29.2 Database Connection Lost

**Failure:** PostgreSQL connection dropped during an active session.
**Detection:** FastAPI SQLAlchemy connection pool raises OperationalError.
**Recovery:** Events routed to Redis Streams as a temporary buffer. Connection retry with exponential backoff (1s, 2s, 4s, 8s, max 30s). On reconnect, the Redis Streams buffer is flushed to PostgreSQL in order. Dashboard displays a "Database degraded — buffering events" banner.
**Data Impact:** Up to 30 seconds of events buffered in Redis. If Redis also fails, in-flight events may be lost (extremely rare dual-failure).

## 29.3 WebSocket Connection Drop

**Failure:** WebSocket connection between FastAPI and the dashboard client drops.
**Detection:** Client-side WebSocket onclose event; server-side connection manager removes the client.
**Recovery:** Client attempts reconnection every 3 seconds with exponential backoff up to 30 seconds. On reconnect, the client requests a "catch-up" payload — the server sends the last 60 seconds of events and current vehicle positions.
**Data Impact:** Dashboard events missed during disconnection are recovered on reconnect.

## 29.4 Drone Loses GPS

**Failure:** Drone GPS signal lost — altitude and position data unavailable.
**Detection:** MAVLink telemetry reports GPS fix quality = 0.
**Recovery:** Last known GPS position held and displayed on the twin with a "GPS lost" indicator. Geo-projection halted — pixel-to-GPS conversion suspended. Alert sent to the operator view.
**Data Impact:** Events during GPS loss have reduced geo-accuracy and are tagged "GPS-degraded."

## 29.5 Redis Failure

**Failure:** Redis instance crashes.
**Detection:** Redis connection pool raises ConnectionError.
**Recovery:** Live vehicle state temporarily unavailable — the digital twin pauses moving vehicles (shows last known positions). Events are written directly to PostgreSQL (slower but safe). WebSocket fan-out switches to direct broadcast (reduced performance). Redis restart attempted automatically. Alert sent to admin.
**Data Impact:** Live twin may show stale vehicle positions for up to 60 seconds during Redis restart.

---

# 30. Compliance & Ethics Framework

## 30.1 DGCA Drone Regulations

The system is designed to comply with India's Drone Rules 2021:

- **Altitude enforcement:** System monitors real-time altitude and alerts the operator at 110m, flags at 120m
- **No-fly zones:** Patrol zone setup integrates Digital Sky Platform no-fly zone boundaries
- **Operator certification:** System requires the drone operator's DGCA certification number at session creation
- **Flight logging:** All sessions logged with GPS track, altitude profile, operator ID — available for regulatory audit

## 30.2 IT Act 2000 and DPDP Act 2023

- **Data minimisation:** Only flagged-event data is stored. Non-flagged vehicle data is held in Redis with a 30-second TTL and not archived.
- **Purpose limitation:** Collected data is used only for traffic monitoring, road condition assessment, and urban planning — not shared with commercial entities.
- **Retention limits:** Event data retained for 2 years maximum.
- **Access control:** Data accessible only to authorised personnel via RBAC.
- **No biometric data:** Facial recognition is explicitly not implemented at any point in the pipeline.

## 30.3 Facial Recognition Avoidance Policy

This system explicitly does not implement facial recognition for any purpose. Vehicle occupant identification is limited to:
- Helmet presence/absence (binary classification — not identity)
- Passenger count on two-wheelers (count — not identity)

No facial embedding, biometric template, or identity inference from face is performed. This is a design principle, documented to protect against future scope creep.

## 30.4 Surveillance Scope Boundaries

The drone camera is directed at road surfaces and traffic only. The patrol zone planner includes a buffer constraint that prevents zone boundaries from being drawn over residential areas, private property, or non-road spaces. Camera tilt is constrained to road-facing angles only — tilt commands that would direct the camera toward windows or private spaces are rejected by the operator interface.

## 30.5 Human-in-the-Loop Principle

While this platform does not issue legal penalties itself, every flagged violation and anomaly is presented for human review before being marked actioned or resolved. This is both a data-quality safeguard and an ethics measure — the system surfaces evidence and prioritisation, but the judgement and any consequent action always rests with a human authority.

---

# 31. Comparison With Existing Systems

## 31.1 Feature Comparison Table

| Feature | Our System | Dubai RTA Drone Enforcement | Hyderabad ITMS | Standard Fixed CCTV | Manual Road Condition Survey |
|---|---|---|---|---|---|
| Aerial detection | ✓ | ✓ | ✗ | ✗ | ✗ |
| Dynamic coverage area | ✓ | ✓ | ✗ | ✗ | Partial |
| 10 violation types | ✓ | ~3-4 | ~5-6 | ~2-3 | N/A |
| Road surface anomaly detection | ✓ | ✗ | ✗ | ✗ | ✓ (manual, infrequent) |
| Digital twin environment | ✓ | ✗ | Partial | ✗ | ✗ |
| City planning intelligence layer | ✓ | ✗ | ✗ | ✗ | ✗ |
| Automated report generation | ✓ | ✗ | Partial | ✗ | ✗ |
| What-if infrastructure simulator | ✓ | ✗ | ✗ | ✗ | ✗ |
| CARLA simulation validation | ✓ | ✗ | ✗ | ✗ | ✗ |
| Open-source stack | ✓ | ✗ (proprietary) | ✗ (proprietary) | Partial | N/A |
| Continuous, structured data | ✓ | ✓ | Partial | Partial | ✗ |

## 31.2 Academic Literature Comparison

Existing academic work on aerial traffic monitoring and pavement-distress detection (surveyed 2020–2025) shares common limitations that this project addresses:

- Most papers treat traffic violation detection and road surface condition monitoring as entirely separate research problems, never unified in one aerial platform
- Few combine aerial detection with a digital twin and a planner-facing simulation tool
- None combine violation detection, road anomaly detection, urban planning recommendations, and automated reporting in a single coherent platform
- Sim-to-real approaches using CARLA for aerial traffic and surface-condition datasets are rare at the BTech level

---

# 32. Innovation Highlights / Novelty Section

This section explicitly identifies the novel contributions of this project distinct from existing work.

## Innovation 1 — Unified Aerial Detection of Behavioural and Physical Road Conditions

**What it is:** A single aerial pipeline that simultaneously detects traffic violations (behavioural) and road surface anomalies (physical/structural) from the same drone footage stream.

**Why it's novel:** Existing systems treat traffic enforcement and pavement-condition monitoring as separate domains with separate hardware and separate teams. Unifying both detection tasks on one aerial platform and one digital twin is a system-level contribution.

## Innovation 2 — CARLA Sim-to-Real Pipeline Extended to Road Surface Anomalies

**What it is:** Use of the CARLA autonomous driving simulator, extended with synthetic texture-overlay compositing, as a synthetic dataset factory for both traffic violation types and road surface defect types from a simulated drone perspective.

**Why it's novel:** CARLA is widely used for ground-level autonomous driving research but rarely for aerial traffic monitoring, and essentially never for road-surface-defect simulation, since it has no native support for pavement degradation. Extending it via synthetic overlay compositing to generate anomaly training data is a novel application of an existing tool.

## Innovation 3 — Unified Governance Digital Twin for Monitoring + Planning + Maintenance

**What it is:** A single digital twin environment serving field/control-room monitoring, city planning, and municipal maintenance stakeholders with role-differentiated views of the same underlying spatial data.

**Why it's novel:** Existing traffic digital twin implementations are typically either pure operational tools (monitoring only) or pure analytical tools (planning only), and rarely extend to road maintenance stakeholders at all. Unifying all three in one platform is an architectural contribution.

## Innovation 4 — Automated Analysis and Reporting Closing the Observation-to-Action Loop

**What it is:** A reporting layer that automatically compiles raw detection events into structured, decision-ready reports — zone summaries, condition inventories, planning recommendations — without manual data compilation.

**Why it's novel:** Existing systems collect data but rarely close the loop into an automatically generated, stakeholder-ready document. This closes the gap between "data exists" and "a decision-maker has something actionable."

## Innovation 5 — What-If Infrastructure Simulator on a Live Digital Twin

**What it is:** An interactive simulator allowing city planners to construct custom traffic scenarios directly on the digital twin and see projected safety/violation impact before implementation, grounded in the platform's own historical detection data.

**Why it's novel:** Projecting enforcement/safety outcomes from a proposed infrastructure change using the same platform's historical trajectory and violation data — rather than a generic traffic model — is not documented in existing traffic management platforms at this scale.

---

# 33. Deployment Plan

## 33.1 Pilot Deployment Recommendation

For real-world deployment, a phased pilot is recommended:

**Phase 0 — Simulation Validation (Current)**
Complete CARLA testing, dataset generation, and pipeline validation for both violation and road-anomaly detection. No real-world component.

**Phase 1 — Single Zone Pilot**
Select a single zone (1km² area) with both traffic and road-condition concerns. Deploy one drone operator. Run the system for 30 days. Collect accuracy benchmarks and false positive rates for both detection pipelines.

**Phase 2 — Multi-Zone Expansion**
Expand to 3–5 zones. Add Urban Planning Dashboard users (planners and maintenance engineers). Generate first planning recommendations and condition inventories from the 30-day dataset.

**Phase 3 — City-Wide Rollout**
Full city coverage with drone fleet coordination. Scheduled reporting integrated into municipal workflows. Government MoU for data sharing and continued patrol operations.

## 33.2 Hardware Requirements for Production

| Component | Specification |
|---|---|
| **Detection server** | GPU server with NVIDIA A100 or RTX 4090; 64GB RAM; Ubuntu 22.04 |
| **Database server** | 16-core CPU; 64GB RAM; 4TB SSD (event images); PostgreSQL + PostGIS |
| **Redis server** | 8-core CPU; 32GB RAM; Redis 7 with AOF persistence |
| **Object storage** | MinIO cluster or AWS S3 equivalent; minimum 10TB for 2-year retention |
| **Drone** | DJI Matrice 300 RTK or equivalent with RTK GPS and gimbal-mounted camera |
| **Ground network** | 4G LTE private APN or dedicated WiFi mesh for drone-to-server stream |

## 33.3 Government/Municipal Onboarding Steps

1. MoU between deploying agency and DGCA for drone operations authorisation
2. Data-sharing agreement with the municipal traffic and public-works departments
3. Designation of the receiving authority for automated reports and recommendations
4. Officer training on the Monitoring Dashboard (2-day programme recommended)
5. Planner and maintenance engineer training on the Urban Planning Dashboard (1-day programme recommended)

## 33.4 Cost Estimate — Pilot Deployment

| Component | Estimated Cost (INR) |
|---|---|
| Drone (DJI Matrice 300 RTK) | ₹8,00,000 – ₹12,00,000 |
| Detection GPU server | ₹3,00,000 – ₹5,00,000 |
| Database + Redis server | ₹1,50,000 – ₹2,50,000 |
| Software development (one-time) | Open-source stack — no licensing cost |
| Cloud storage (1 year) | ₹60,000 – ₹1,20,000 |
| Operator training | ₹50,000 |
| **Total pilot estimate** | **₹13,60,000 – ₹21,20,000** |

Compared to a single fixed CCTV installation (typically ₹3,00,000–₹5,00,000 per camera, coverage limited to one point) plus a separate periodic manual road-condition survey contract, this system provides dynamic, continuous, full-zone coverage of both traffic behaviour and road condition at significantly better cost efficiency per square kilometre.

---

# 34. Testing Plan

## 34.1 Unit Tests

| Component | Test Cases |
|---|---|
| YOLOv8 inference | Vehicle detection on known test images; confidence threshold validation |
| YOLOv8-seg inference | Defect segmentation on known test images; IoU validation |
| DeepSORT tracker | Track ID persistence across 30-frame sequences; occlusion recovery test |
| Geo-projection | Known pixel coordinates → expected GPS within ±2m |
| Violation engine (each type) | Scripted input trajectories → expected violation output per type |
| Severity scorer | Known defect areas/classes → expected severity band output |
| Confidence scorer | Edge case inputs (all pass, all fail, mixed) → expected score outputs |
| Deduplication engine | Repeated detections at same GPS → single record with incrementing recurrence_count |
| FastAPI endpoints | Each endpoint with valid and invalid inputs; auth token validation |
| RBAC | Each role attempting each endpoint — access granted/denied correctly |

## 34.2 Integration Tests

| Test | Description |
|---|---|
| Violation pipeline integration | CARLA frame → YOLOv8 → DeepSORT → violation engine → database → WebSocket → dashboard |
| Anomaly pipeline integration | CARLA frame → YOLOv8-seg → severity scorer → deduplication → database → WebSocket → dashboard |
| Twin sync | Event created in database → appears on the CesiumJS twin within 500ms |
| DBSCAN recommendation | 50+ events seeded → recommendation generated → appears on the planning dashboard |
| Report generation | Zone + date range selected → report generated with traceable aggregation → downloadable within 10 seconds |
| What-if simulator | Scenario drawn → historical events matched → projection returned |

## 34.3 CARLA Simulation Test Cases

One test script per violation/anomaly type, run 100 times each with variation:

| Test ID | Type | Variation |
|---|---|---|
| SIM-V01 | No-parking | Vehicle colour variation, day/night, partial occlusion |
| SIM-V02 | Wrong-way | Different road widths, single vs multi-lane |
| SIM-V03 | Illegal U-turn | Near legal U-turn zone, tight vs wide arc |
| SIM-V04 | Red-light jumping | Different signal timings, close vs distant crossing |
| SIM-V05 | Speeding | Different speed exceedances (10% over, 50% over) |
| SIM-V06 | Lane violation | Dashed vs solid, brief vs sustained crossing |
| SIM-V07 | Zebra crossing | Traffic jam context vs clear traffic |
| SIM-V08 | Helmet-less | Different rider sizes, different drone tilt angles |
| SIM-V09 | Overloading | 3 vs 4 passengers, different rider sizes |
| SIM-V10 | Highway stopping | Individual stop vs jam context |
| SIM-A01 | Pothole | Size variation, waterlogged vs dry |
| SIM-A02 | Cracking | Linear vs alligator pattern |
| SIM-A03 | Waterlogging | Partial vs full-width coverage |
| SIM-A04 | Debris | Small vs large obstruction, near vs far from lane centre |
| SIM-EDGE01 | Track loss | Vehicle passes under bridge during violation |
| SIM-EDGE02 | High density | 80+ vehicles in frame simultaneously |
| SIM-EDGE03 | Recurrence | Same defect location scanned across 3 simulated sessions |

## 34.4 Performance Tests

| Test | Method | Target |
|---|---|---|
| API load test | Locust — 100 concurrent users; all endpoints | P95 response ≤ 200ms |
| WebSocket load | 50 concurrent WebSocket clients; event broadcast | Message delivery ≤ 500ms |
| Detection throughput | 1920×1080 frame at 20 FPS sustained | ≥ 10 FPS inference |
| Database query | Combined heatmap query over 100,000 events | Response ≤ 2 seconds |
| Report generation load | 20 concurrent report requests | All complete within 15 seconds |

## 34.5 User Acceptance Testing

| User | Test Scenario | Pass Criteria |
|---|---|---|
| Mock Monitoring Officer | Receive alert, navigate to location, review and mark actioned | All steps completable within 2 minutes on tablet |
| Mock City Planner | Generate heatmap, read AI recommendation, run what-if, export report | All steps completable without training beyond the user guide |
| Mock Maintenance Engineer | Filter condition inventory by severity, export work-order list | Steps completable without assistance |
| Mock Drone Operator | Start session, see detection active, receive altitude warning, end session | Session lifecycle completable without assistance |

---

# 35. Risk Register

| ID | Risk | Category | Likelihood | Impact | Mitigation |
|---|---|---|---|---|---|
| R01 | YOLOv8 mAP below target on real drone footage after CARLA training | Technical | Medium | High | Fine-tuning on real footage; augmentation during training; IDD dataset supplement |
| R02 | DeepSORT ID switch rate too high in dense traffic | Technical | Medium | High | ByteTrack as fallback tracker; raised confidence threshold at high density |
| R03 | CARLA performance insufficient on RTX 4060 laptop for dataset generation | Technical | Low | Medium | Medium render quality; reduce traffic density; use CARLA headless mode |
| R04 | Road anomaly segmentation accuracy low due to CARLA's lack of native surface-degradation modelling | Technical | High | High | Synthetic texture-overlay compositing + real pavement-distress dataset fine-tuning |
| R05 | GPS projection error exceeds 2m target | Technical | Low | Medium | RTK GPS for production; Kalman smoothing; zone buffer margin |
| R06 | DGCA regulations change mid-project | Operational | Low | High | System architecture supports any altitude limit; configurable threshold |
| R07 | Severity scoring for road anomalies not well correlated with manual inspection | Technical | Medium | Medium | Validation against a manually inspected subset; iterative weighting adjustment |
| R08 | False positive rate exceeds 15% for one or more violation/anomaly types | Technical | Medium | Medium | Per-type thresholds adjusted; additional training data for underperforming types |
| R09 | DPDP Act compliance breach due to unintended data retention | Legal | Low | High | TTL enforcement on Redis; no long-term storage of non-flagged vehicle data |
| R10 | System performance degradation under high concurrent users | Technical | Low | Medium | Load testing before deployment; Redis Pub/Sub for fan-out reduces DB load |
| R11 | Team capacity insufficient for full scope across both detection domains | Project | Medium | Medium | Prioritise: violation detection + digital twin + monitoring dashboard as core deliverables; road anomaly detection as second-phase milestone |
| R12 | Weather events (monsoon) degrading drone footage quality during testing | Operational | High (India) | Low | Flagging mechanism implemented; test in good conditions; document limitation |
| R13 | CesiumJS rendering performance poor on low-spec dashboard devices | Technical | Medium | Low | Fallback to a 2D map mode for low-spec clients; configurable render quality |
| R14 | Stakeholders expect automated enforcement despite the monitoring-only scope | Project | Medium | Medium | Scope explicitly documented (Section 22.1, 30.5); communicated at project kickoff and in all reports |

---

*End of Document*

---

**Document Version:** 2.0.0
**Total Sections:** 35
**Status:** Complete — Ready for Project Submission
