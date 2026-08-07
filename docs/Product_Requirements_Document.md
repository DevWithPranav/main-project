# Product Requirements Document (PRD)
## Autonomous Traffic Violation Detection and Urban Planning System Using UAV Aerial Footage and Digital Twin Technology

---

| Field | Detail |
|---|---|
| **Version** | 1.0.0 |
| **Date** | July 2026 |
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
9. [All 10 Violation Types](#9-all-10-violation-types)
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
20. [Hybrid Enforcement Architecture](#20-hybrid-enforcement-architecture)
21. [AI Planning Recommendation Engine](#21-ai-planning-recommendation-engine)
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
**Autonomous Traffic Violation Detection and Urban Planning System Using UAV Aerial Footage and Digital Twin Technology**

## 1.2 Authors

| Role | Name |
|---|---|
| Project Lead | Ghost |
| Backend Development | Ghost |
| Institution | GTech μLearn Platform |
| Document Version | 1.0.0 |

## 1.3 Revision History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | July 2026 | Initial draft | Ghost |
| 1.0 | July 2026 | Full PRD complete | Ghost |

## 1.4 Document Purpose
This document defines the complete product requirements for the Autonomous Traffic Violation Detection and Urban Planning System. It is intended to guide the development team, project examiner, and any government stakeholder evaluating the system for pilot deployment. It covers all functional, non-functional, architectural, legal, and operational requirements.

---

# 2. Executive Summary

Traffic violation detection in Indian urban areas suffers from three critical gaps: limited spatial coverage of ground-level infrastructure, inability to provide city planners with actionable violation intelligence, and lack of a legally defensible vehicle identification mechanism for aerial enforcement.

This project proposes a UAV-based autonomous traffic monitoring system that uses computer vision to detect ten categories of traffic violations from aerial drone footage. Each detected vehicle is continuously tracked using a unique identity thread that bridges aerial detection to ground-level identification, solving the vehicle ambiguity problem that makes aerial enforcement legally indefensible in existing systems.

The system is anchored by a 3D Digital Twin — a virtual replica of the monitored road network — that serves as a shared command environment for two distinct user groups: traffic enforcement officers who use it for real-time operational awareness, and city planners who use it to analyse historical violation patterns and plan infrastructure improvements.

The entire system is developed, trained, and validated using CARLA, an open-source autonomous driving simulator, as a synthetic data generation environment. This sim-to-real transfer methodology enables complete testing of all violation types and edge cases before real UAV deployment, representing a significant academic contribution at the BTech level.

The resulting platform is not merely a detection tool — it is a governance system that transforms raw drone footage into actionable urban intelligence.

---

# 3. Problem Statement

## 3.1 Current Traffic Enforcement Gaps

Traffic enforcement in Indian cities relies predominantly on two mechanisms: static ground-level CCTV cameras deployed at known high-risk intersections, and physical traffic police personnel. Both approaches share a fundamental limitation — they cover only the points where infrastructure has been installed or officers have been deployed. Violations occurring between camera positions, in side streets, parking zones, or during off-peak hours go largely undetected.

This infrastructure-dependency creates systematic enforcement blind spots across entire urban zones. Cities cannot afford to install cameras at every road segment, and human officer deployment is constrained by shift timings, manpower shortages, and physical fatigue.

## 3.2 Ground-Level System Limitations

Existing ground-level CCTV and ANPR systems face inherent limitations that reduce their effectiveness:

- Fixed field of view — cameras cannot dynamically respond to incidents outside their pre-set angle
- Occlusion — vehicles, trees, and infrastructure block sightlines creating dead zones
- Single-plane perspective — cannot detect roof-level violations or provide spatial trajectory data
- No cross-zone intelligence — systems at one intersection have no awareness of patterns at another
- Static zone definitions — violation zones cannot be updated without physical reinstallation

## 3.3 Urban Planning Intelligence Gap

Traffic violation data collected by existing systems is rarely structured, aggregated, or made available to city planners in a form that enables infrastructure decisions. Planners typically rely on periodic manual traffic studies and citizen complaints to identify problem areas — both of which are slow, expensive, and subjective.

There is no existing system that continuously converts real-time violation event data into structured spatial intelligence for urban planning decisions. As a result, signal placement, road marking, lane design, and enforcement scheduling decisions are made without empirical, location-specific violation pattern data.

## 3.4 Legal Enforcement Challenges

The introduction of aerial surveillance for traffic enforcement creates a new legal challenge: violation events detected by a drone must be linked to a specific vehicle owner to issue a legally valid penalty notice (challan) under the Motor Vehicles Act. This requires a vehicle's registration number, which is printed on its number plate.

At standard UAV operating altitudes of 80–120m (the DGCA civilian limit for most operations in India), number plates are not reliably readable from aerial footage due to resolution limitations, perspective distortion, motion blur, and the nadir (straight-down) camera angle that shows vehicle rooftops rather than front or rear plates.

## 3.5 The Vehicle Ambiguity Problem

Even when ground-level cameras are triggered to capture plate information, a second challenge emerges: in dense urban traffic, multiple visually similar or identical vehicles (same colour, same model) may be present near the violation location at the same time. Without a continuous, mathematically verifiable identity chain from the aerial violation event to the ground-level plate capture, there is no reliable mechanism to determine which vehicle committed the violation. Issuing a challan to the wrong vehicle owner constitutes an unlawful penalty and creates legal liability for the enforcement authority.

## 3.6 Summary of Identified Problems

| Problem | Impact |
|---|---|
| Limited coverage of ground infrastructure | Systematic enforcement blind spots |
| No aerial perspective in existing systems | Spatial violation patterns undetected |
| No planning intelligence layer | Infrastructure decisions made without data |
| Number plate unreadable from aerial altitude | Aerial detection not legally enforceable alone |
| Vehicle ambiguity at ground level | Risk of wrongful penalty issuance |
| No unified platform for enforcement + planning | Siloed operations, no shared situational awareness |

---

# 4. Proposed Solution

## 4.1 System Overview

We propose an **Autonomous Traffic Violation Detection and Governance Platform** built on four integrated pillars:

- **UAV Detection Engine** — YOLOv8 + DeepSORT pipeline that detects and continuously tracks vehicles from drone aerial footage, identifying ten violation types in real time
- **Geo-Spatial Projection Layer** — Converts pixel-level detections into GPS coordinates using drone telemetry, enabling location-accurate violation event pinning
- **Digital Twin Environment** — A 3D virtual replica of the monitored road network built on CesiumJS, serving as the shared command and planning interface
- **Hybrid Enforcement Architecture** — Bridges aerial detection to ground-level CCTV/ANPR for legally valid vehicle identification and challan generation

## 4.2 How It Solves Each Problem

| Problem | Solution |
|---|---|
| Limited coverage | UAV provides dynamic, wide-area aerial coverage not bound to fixed positions |
| No aerial perspective | YOLOv8 + DeepSORT provides spatial trajectory data across the full drone field of view |
| No planning intelligence | City Planner dashboard with DBSCAN-powered hotspot analysis and AI recommendations |
| Number plate unreadable from air | Hybrid architecture triggers ground CCTV for plate capture; Super-Resolution ANPR as secondary |
| Vehicle ambiguity | Confidence-weighted identity threading engine with mandatory 95%+ threshold before flagging |
| No unified platform | Digital Twin serves as shared environment for both enforcement and planning users |

## 4.3 Why This Approach Over Alternatives

| Alternative Considered | Why Rejected |
|---|---|
| Fixed aerial cameras (balloons/poles) | No dynamic coverage, high installation cost |
| Ground CCTV expansion only | Does not solve blind spots, no aerial perspective |
| Fully unsupervised ML | Cannot reliably classify specific violation types |
| Supervised-only classification | Requires massive labelled datasets per violation type |
| Manual drone operator review | Not scalable, human error, no real-time response |

The chosen approach — semi-supervised detection with rule-based violation logic, hybrid enforcement, and digital twin governance — is the only architecture that addresses all six identified problems simultaneously within the constraints of Indian regulatory and legal frameworks.

---

# 5. Objectives

## 5.1 Primary Objectives

**Objective 1 — UAV-Based Aerial Violation Detection**
Develop a computer vision pipeline using YOLOv8 and DeepSORT to detect and continuously track vehicles from UAV aerial footage at altitudes of 80–120m, identifying ten categories of traffic violations in real time.

**Objective 2 — Ten-Violation Detection Coverage**
Implement detection logic for: no-parking zone violation, wrong-way driving, illegal U-turn, red-light jumping, speeding, lane violation, zebra crossing violation, helmet-less riding, two-wheeler overloading, and illegal stopping on restricted roads.

**Objective 3 — Geo-Spatial Coordinate Mapping**
Build a pixel-to-GPS projection system using drone telemetry (altitude, gimbal angle, GPS) to map every detected vehicle and violation event to an exact real-world coordinate.

**Objective 4 — Vehicle Identity Threading and Ambiguity Resolution**
Design a confidence-weighted multi-modal disambiguation engine that maintains a continuous identity thread from violation detection to ground-level vehicle identification, ensuring no challan is issued below a 95% confidence threshold.

**Objective 5 — Digital Twin Environment**
Construct a 3D virtual replica of the monitored road network using CesiumJS and OpenStreetMap data, with live vehicle markers, geo-fenced violation zones, and real-time violation event overlays.

**Objective 6 — Dual-Dashboard Governance Platform**
Develop two separate role-based dashboards — one for traffic enforcement officers and one for city planners — sharing the same digital twin core.

**Objective 7 — AI-Powered Urban Planning Recommendations**
Implement a spatial clustering engine using DBSCAN on historical violation data to generate actionable infrastructure recommendations.

**Objective 8 — CARLA-Based Simulation and Validation**
Build a complete simulation environment in CARLA for synthetic data generation, violation scripting, and sim-to-real transfer validation before real UAV deployment.

**Objective 9 — Hybrid Enforcement Architecture**
Integrate aerial detection with ground-level CCTV and ANPR infrastructure for legally valid vehicle identification and challan generation.

**Objective 10 — Scalable Backend Infrastructure**
Develop a FastAPI-based backend with PostgreSQL/PostGIS, Redis, and WebSocket streaming to support all system components at production scale.

## 5.2 Success Criteria Per Objective

| Objective | Success Criteria |
|---|---|
| 1 | mAP ≥ 0.75 on aerial vehicle detection, tracking continuity ≥ 90% across frames |
| 2 | All 10 violations detectable in CARLA simulation with F1 ≥ 0.70 per violation |
| 3 | GPS projection error ≤ 2m at 100m altitude |
| 4 | Zero wrongful identity assignments in controlled test suite |
| 5 | Digital twin updates within 500ms of real-world event |
| 6 | Both dashboards functional with role-based access control |
| 7 | Recommendations generated for any zone with ≥ 50 historical violations |
| 8 | All 10 violation scripts executable in CARLA; dataset of 10,000+ frames generated |
| 9 | End-to-end challan draft generated within 30 seconds of violation detection |
| 10 | API response time ≤ 200ms under 100 concurrent connections |

---

# 6. Objective-wise Solutions

## Objective 1 — UAV-Based Aerial Violation Detection

**Technical Approach:**
- YOLOv8n/YOLOv8s model (nano or small variant for real-time performance) fine-tuned on aerial vehicle imagery
- DeepSORT tracker maintains unique Track IDs with appearance embeddings + Kalman filter trajectory prediction
- Frame buffer of 30 frames maintained per Track ID for trajectory analysis
- Input: RTSP stream from drone camera OR frame dump from CARLA simulation

## Objective 2 — Ten-Violation Detection Coverage

**Technical Approach:**
- Rule-based violation engine evaluating trajectory data per Track ID
- Each violation has its own detection module with specific trigger conditions
- Violations categorised as: Zone-based (1,6,7,10), Trajectory-based (2,3,5), State-based (4,8,9)
- Detailed logic per violation documented in Section 9

## Objective 3 — Geo-Spatial Coordinate Mapping

**Technical Approach:**
- Homography matrix computed from drone altitude, gimbal pitch/roll/yaw, and GPS anchor points
- Pixel coordinates → Camera coordinates → World coordinates using pinhole camera model
- `pyproj` library for coordinate reference system transformations
- Ground sampling distance (GSD) computed per frame: GSD = (Altitude × Sensor Width) / (Focal Length × Image Width)

```
GSD Formula:
GSD (m/pixel) = (H × SW) / (f × IW)
Where:
H  = drone altitude (metres)
SW = camera sensor width (mm)
f  = focal length (mm)
IW = image width (pixels)
```

## Objective 4 — Vehicle Identity Threading

**Technical Approach:**
- Continuous Track ID from DeepSORT maintained from violation frame to ground camera trigger
- Four-filter disambiguation: direction match, arrival time window, appearance embedding, continuous track
- Confidence score computed as weighted sum of four filter results
- Cases below 95% confidence dropped with "ambiguous — unresolved" status

## Objective 5 — Digital Twin Environment

**Technical Approach:**
- CesiumJS as 3D rendering engine with Cesium Ion terrain tiles
- OSM data loaded via Cesium OSM Buildings layer
- Vehicle entities updated via WebSocket at 10Hz
- Violation event entities persist on twin with metadata popup
- Zone polygons stored as GeoJSON, rendered as CesiumJS polygon entities

## Objective 6 — Dual-Dashboard Governance Platform

**Technical Approach:**
- React + Next.js frontend with role-based routing
- JWT-authenticated sessions with role claim (OFFICER / PLANNER / ADMIN)
- Shared CesiumJS twin component, different overlay layers per role
- FastAPI serves different data endpoints per role scope

## Objective 7 — AI Planning Recommendations

**Technical Approach:**
- DBSCAN spatial clustering on PostGIS violation_events table
- Rule engine maps cluster characteristics to recommendation types
- Recommendations stored as GeoJSON polygons with metadata
- Rendered as suggestion overlay layer on City Planner twin view

## Objective 8 — CARLA Simulation

**Technical Approach:**
- CARLA 0.9.15 running synchronous mode at 20 FPS
- Spectator camera mounted at 100m altitude simulating drone
- Python scripts per violation type for controlled dataset generation
- Ground truth bounding boxes + GPS coordinates auto-exported per frame
- YOLOv8 trained on CARLA data, evaluated on real drone footage

## Objective 9 — Hybrid Enforcement Architecture

**Technical Approach:**
- Ground camera registry table in PostGIS with GPS positions
- Nearest-camera finder using PostGIS ST_DWithin query
- Trigger API sends violation timestamp + vehicle description to ground camera
- ANPR result linked back to violation event by event_id
- Challan draft generated as structured JSON, presented to officer for approval

## Objective 10 — Scalable Backend Infrastructure

**Technical Approach:**
- FastAPI async endpoints with Pydantic validation
- PostgreSQL 16 + PostGIS 3.4 for persistent geo-spatial storage
- Redis 7 for live vehicle state with 30-second TTL
- WebSocket manager using FastAPI WebSockets + Redis Pub/Sub for fan-out
- MinIO S3-compatible storage for violation snapshot images

---

# 7. User Personas

## Persona 1 — Traffic Police Officer (Field Enforcement)

| Attribute | Detail |
|---|---|
| **Name** | Constable Rajan |
| **Age** | 32 |
| **Role** | Field traffic enforcement officer |
| **Tech Comfort** | Basic smartphone, WhatsApp, maps |
| **Goal** | Know where violations are happening right now and respond fast |
| **Pain Points** | Can't be everywhere, misses violations in blind spots, paperwork is slow |
| **Needs from System** | Simple alert on phone/tablet, exact GPS location, vehicle description, photo evidence |
| **Dashboard** | Police Dashboard — Live mode |

## Persona 2 — Traffic Control Room Operator

| Attribute | Detail |
|---|---|
| **Name** | Supervisor Meera |
| **Age** | 41 |
| **Role** | Central control room, coordinates all field officers |
| **Tech Comfort** | Comfortable with dashboards and CCTV systems |
| **Goal** | Dispatch the right officer to the right location instantly |
| **Pain Points** | No real-time aerial view, hard to coordinate multiple officers |
| **Needs from System** | Full twin view, officer positions, live violation feed, one-click dispatch |
| **Dashboard** | Police Dashboard — Command mode |

## Persona 3 — City Planner / Municipal Engineer

| Attribute | Detail |
|---|---|
| **Name** | Engineer Priya |
| **Age** | 38 |
| **Role** | Urban traffic infrastructure planning |
| **Tech Comfort** | GIS tools, Excel, government portal systems |
| **Goal** | Identify where to invest in new signals, road markings, and zone changes |
| **Pain Points** | No real violation data to justify infrastructure budget, studies are expensive |
| **Needs from System** | Historical heatmaps, trend charts, AI recommendations, exportable reports |
| **Dashboard** | City Planner Dashboard |

## Persona 4 — System Administrator

| Attribute | Detail |
|---|---|
| **Name** | Admin Kiran |
| **Age** | 29 |
| **Role** | Platform technical management |
| **Tech Comfort** | High — developer background |
| **Goal** | Keep system running, manage users and zones, onboard new cameras |
| **Pain Points** | No visibility into system health, hard to manage zones remotely |
| **Needs from System** | User management, zone editor, camera registry, system health dashboard |
| **Dashboard** | Admin Panel |

## Persona 5 — Drone Operator

| Attribute | Detail |
|---|---|
| **Name** | Operator Sai |
| **Age** | 26 |
| **Role** | UAV flight and camera operation |
| **Tech Comfort** | Drone software, flight planning apps |
| **Goal** | Execute patrol routes, maintain feed quality, stay within DGCA limits |
| **Pain Points** | No feedback on what system is detecting, unclear if feed is being processed |
| **Needs from System** | Feed status indicator, detection confidence display, altitude/zone compliance alerts |
| **Dashboard** | Drone Operator View (simplified) |

---

# 8. Usage Scenarios

## Scenario 1 — Officer Receives Live Violation Alert and Responds

**Actor:** Constable Rajan  
**Trigger:** Drone detects a vehicle parked in a no-parking zone

**Flow:**
1. Drone detects stationary vehicle in no-parking polygon for >30 seconds
2. Violation event created — GPS coordinate, timestamp, vehicle description, snapshot
3. Alert pushed via WebSocket to Police Dashboard
4. Alert appears on Rajan's tablet — map pin, photo, "White Sedan, no-parking zone, Junction 7"
5. Rajan taps "Navigate" — opens Google Maps with violation GPS
6. Ground camera triggered — plate captured within predicted time window
7. Rajan arrives, confirms vehicle physically — taps "Confirm" in app
8. Challan draft generated — Rajan reviews and submits
9. Violation event marked "Resolved" on digital twin

## Scenario 2 — City Planner Analyses Monthly Hotspot Report

**Actor:** Engineer Priya  
**Trigger:** Monthly planning review meeting

**Flow:**
1. Priya opens City Planner Dashboard
2. Selects date range: last 30 days
3. Twin switches to heatmap mode — violation density overlaid on 3D map
4. High-density cluster visible at MG Road / NH bypass intersection
5. Priya clicks cluster — breakdown: 42% wrong-way, 31% red-light jumping
6. AI recommendation panel shows: "Signal with countdown timer recommended at this intersection"
7. Priya opens what-if simulator — adds virtual signal, system estimates 65% reduction in flagged violations at that cluster
8. Priya exports PDF report with heatmap, stats, and recommendation for municipal council

## Scenario 3 — Drone Operator Sets Up a Patrol Session

**Actor:** Operator Sai  
**Trigger:** Morning patrol shift begins

**Flow:**
1. Sai opens Drone Operator View, logs in
2. Selects patrol zone from pre-defined zone map
3. System checks: zone active, cameras registered nearby, system online
4. Sai launches drone, feed connects to system via RTSP
5. Detection pipeline activates — vehicle tracking begins
6. Operator view shows: "Detection active — 14 vehicles tracked"
7. Altitude monitor shows current altitude: 97m (within DGCA limit — green)
8. Session ends — Sai lands drone, session auto-closes, data archived

## Scenario 4 — Planner Uses What-If Simulator

**Actor:** Engineer Priya  
**Trigger:** Planning a road redesign for a one-way conversion

**Flow:**
1. Priya selects a road segment with high wrong-way driving violations
2. Opens What-If Simulator
3. Draws proposed one-way direction on the digital twin
4. Simulator models: "Based on historical trajectory data, this change is projected to eliminate 89% of wrong-way events on this segment"
5. Priya adds the result to her planning report

## Scenario 5 — Admin Registers a New Ground CCTV Camera

**Actor:** Admin Kiran  
**Trigger:** City installs 3 new CCTV cameras at a junction

**Flow:**
1. Kiran logs into Admin Panel
2. Opens Camera Registry section
3. Enters camera GPS coordinates, camera ID, ANPR capability (yes/no), API endpoint
4. Camera appears on digital twin as a camera icon
5. System immediately considers new camera in nearest-camera queries for violation events in that area

---

# 9. All 10 Violation Types

## Violation 1 — No-Parking Zone Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Zone polygon intersection + stationary state detection |
| **Trigger Condition** | Vehicle centroid inside no-parking GeoJSON polygon AND velocity < 2 km/h for > 30 seconds |
| **Camera Tilt** | Nadir (straight down) sufficient — vehicle visible from roof |
| **Edge Case 1** | Vehicle slows but doesn't fully stop — threshold: velocity < 2 km/h for sustained period |
| **Edge Case 2** | Delivery vehicle brief stop — configurable grace period (default 30s, adjustable per zone) |
| **Confidence Threshold** | 90% — zone intersection is geometrically deterministic |

## Violation 2 — Wrong-Way Driving

| Attribute | Detail |
|---|---|
| **Detection Method** | Vehicle velocity vector vs road direction vector comparison |
| **Trigger Condition** | Vehicle heading angle differs from road permitted direction by > 150° for > 5 consecutive frames |
| **Road Direction Data** | Stored per road segment in PostGIS with permitted direction angle |
| **Edge Case 1** | Vehicle reversing briefly — minimum displacement threshold before flagging |
| **Edge Case 2** | Vehicle making a legal turn that temporarily appears wrong-way — evaluated on exit heading, not mid-turn |
| **Confidence Threshold** | 85% |

## Violation 3 — Illegal U-Turn

| Attribute | Detail |
|---|---|
| **Detection Method** | Trajectory arc analysis in restricted U-turn zone polygon |
| **Trigger Condition** | Vehicle enters U-turn zone, heading changes > 160° within zone boundary |
| **Edge Case 1** | Legal U-turn zones nearby — violation only flagged if arc occurs inside restricted polygon |
| **Edge Case 2** | Three-point turn — heading change is incremental, not continuous arc; filtered out by arc smoothness metric |
| **Confidence Threshold** | 82% |

## Violation 4 — Red-Light Jumping

| Attribute | Detail |
|---|---|
| **Detection Method** | Signal state API + stop line polygon crossing detection |
| **Trigger Condition** | Vehicle crosses stop line polygon while signal state = RED |
| **Signal State Source** | Traffic signal controller API (IoT-connected signals) OR visual signal detection from drone frame |
| **Edge Case 1** | Vehicle already crossing when light turns red — evaluated by vehicle centroid position at signal change moment |
| **Edge Case 2** | Emergency vehicles — vehicle type classification; ambulance/fire truck excluded |
| **Confidence Threshold** | 92% |

## Violation 5 — Speeding

| Attribute | Detail |
|---|---|
| **Detection Method** | Pixel displacement per frame × Ground Sampling Distance (GSD) × frame rate → speed in km/h |
| **Trigger Condition** | Computed speed > road speed limit for that segment for > 10 consecutive frames |
| **Speed Limit Data** | Stored per road segment in PostGIS |
| **Edge Case 1** | GPS drift causes apparent speed spike — Kalman filter smoothing applied to trajectory before speed computation |
| **Edge Case 2** | Vehicle partially occluded — speed computed only on frames with full bounding box visibility |
| **Confidence Threshold** | 85% |

## Violation 6 — Lane Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Lane boundary polygon + vehicle centroid tracking |
| **Trigger Condition** | Vehicle centroid crosses lane boundary polygon and sustains crossing for > 3 seconds |
| **Edge Case 1** | Lane change vs violation — brief crossing allowed; sustained crossing flagged |
| **Edge Case 2** | Dashed vs solid lane marking — different thresholds per lane type stored in zone metadata |
| **Confidence Threshold** | 80% |

## Violation 7 — Zebra Crossing Violation

| Attribute | Detail |
|---|---|
| **Detection Method** | Vehicle stationary state detection within zebra crossing polygon |
| **Trigger Condition** | Vehicle stopped (velocity < 2 km/h) inside zebra crossing polygon for > 10 seconds |
| **Edge Case 1** | Traffic jam forces vehicle to stop on crossing — contextual evaluation: if vehicles stopped across entire road, no violation flagged |
| **Edge Case 2** | Motorcycle lane splitting on crossing — bounding box overlap threshold applied |
| **Confidence Threshold** | 85% |

## Violation 8 — Helmet-less Riding

| Attribute | Detail |
|---|---|
| **Detection Method** | Two-wheeler detection → rider head region crop → helmet classifier |
| **Trigger Condition** | Two-wheeler classified, rider head region shows no helmet with confidence > 80% |
| **Camera Tilt** | Requires ~30–45° tilt to see rider head; drone tilt triggered on two-wheeler detection |
| **Edge Case 1** | Dark/night footage — infrared or enhanced exposure required; flagged as "low confidence" |
| **Edge Case 2** | Rider wearing cap that partially resembles helmet — helmet classifier trained with hard negatives |
| **Confidence Threshold** | 80% |

## Violation 9 — Two-Wheeler Overloading

| Attribute | Detail |
|---|---|
| **Detection Method** | Two-wheeler detection → passenger count on vehicle bounding box |
| **Trigger Condition** | Passenger count ≥ 3 on a two-wheeler |
| **Camera Tilt** | Requires side/angled view for passenger counting |
| **Edge Case 1** | Child passenger — partial height detection; counted regardless for safety |
| **Edge Case 2** | Large rider with backpack appearing as second person — profile shape classifier to distinguish |
| **Confidence Threshold** | 78% |

## Violation 10 — Illegal Stopping on Highway/Flyover

| Attribute | Detail |
|---|---|
| **Detection Method** | Zone polygon (highway/flyover) + stationary vehicle detection |
| **Trigger Condition** | Vehicle velocity < 5 km/h inside highway/flyover polygon for > 20 seconds (shorter grace period than no-parking) |
| **Edge Case 1** | Traffic jam on flyover — same contextual check as Violation 7; flow-wide stop not flagged as individual violation |
| **Edge Case 2** | Vehicle breakdown — flag with "possible breakdown" tag; dispatch support rather than penalty |
| **Confidence Threshold** | 88% |

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
│   YOLOv8 Object Detection (vehicles, riders, passengers)       │
│              ↓                                                   │
│   DeepSORT Multi-Object Tracker (Track ID assignment)          │
│              ↓                                                   │
│   Trajectory Buffer (30-frame rolling window per Track ID)     │
│              ↓                                                   │
│   Geo-Projection Engine (pixel → GPS via drone telemetry)      │
└──────────────────────────┬─────────────────────────────────────┘
                           │ Tracked vehicles + GPS trajectories
┌──────────────────────────▼─────────────────────────────────────┐
│                   VIOLATION ENGINE                              │
│                                                                  │
│   Rule Engine          Anomaly Engine                           │
│   (V1,V3,V4,V6,V7,    (V2,V5 — trajectory                     │
│    V8,V9,V10)          anomaly detection)                      │
│              ↓                                                   │
│   Confidence Scorer (4-factor weighted score per event)        │
│              ↓                                                   │
│   Violation Event (Track ID, GPS, type, timestamp, score)      │
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
│                   PRESENTATION LAYER                            │
│                                                                  │
│   ┌──────────────────────┐  ┌────────────────────────────────┐ │
│   │  Police Dashboard    │  │  City Planner Dashboard        │ │
│   │  (Live Ops)          │  │  (Historical + Planning)       │ │
│   │  React + CesiumJS    │  │  React + CesiumJS + D3/Recharts│ │
│   └──────────────────────┘  └────────────────────────────────┘ │
│                                                                  │
│                   DIGITAL TWIN (Shared Core)                   │
│                   CesiumJS 3D Virtual City                      │
└─────────────────────────────────────────────────────────────────┘
```

## 10.2 Layer-by-Layer Breakdown

### Input Layer
Accepts two input types: CARLA simulator frame dumps with ground truth metadata (training/testing phase), and real UAV RTSP video stream with MAVLink telemetry (production phase). Both are normalised to the same frame + GPS telemetry format before entering the detection pipeline.

### Detection Pipeline Layer
YOLOv8 runs inference per frame, outputting bounding boxes, class labels, and confidence scores for all detected objects. DeepSORT receives these detections and maintains cross-frame identity using appearance embeddings and Kalman filter motion prediction. A 30-frame trajectory buffer accumulates position and velocity history per Track ID for violation analysis.

### Violation Engine Layer
Two parallel sub-engines process trajectory data. The rule engine applies deterministic geometric and state-based conditions for violations with clear spatial definitions. The anomaly engine applies statistical trajectory analysis for violations defined by unusual movement patterns. Both engines output violation events that are passed through the confidence scorer before storage.

### Backend Infrastructure Layer
FastAPI handles all API requests and WebSocket connections. PostgreSQL with PostGIS extension stores all persistent geo-spatial data. Redis maintains live vehicle state with TTL expiry. MinIO provides S3-compatible object storage for violation snapshot images.

### Presentation Layer
React/Next.js frontend with CesiumJS 3D twin embedded as a shared component. Role-based routing serves different overlay configurations and data endpoints per user type.

---

# 11. Data Flow Diagrams

## 11.1 End-to-End Data Flow

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
Confidence Scorer → [Score ≥ 95%? → Flag | < 95%? → Drop]
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
│ Police Dash  │    │ City Planner Dash   │
│ (live feed)  │    │ (analytics query)   │
└──────────────┘    └─────────────────────┘
```

## 11.2 Violation Event Lifecycle Flow

```
[Detection] Vehicle enters violation condition
       ↓
[Pending] Violation condition sustained past threshold duration
       ↓
[Scored] Confidence score computed (≥ 95%?)
       ↓
   YES → [Flagged] Event stored, alert sent, ground camera triggered
       ↓
[Identified] Ground camera captures plate → linked to event
       ↓
[Draft] Challan draft generated → sent to officer for review
       ↓
[Confirmed] Officer approves → challan issued via Vahan API
       ↓
[Resolved] Event marked resolved on digital twin

   NO (< 95%) → [Dropped] Event logged as "ambiguous — unresolved"
                           No alert, no challan
```

## 11.3 Vehicle Identity Threading Flow

```
Violation detected — Track ID #42 assigned
           ↓
Appearance embedding captured (colour, shape, texture)
Velocity vector recorded (speed + heading)
GPS coordinate at violation moment stored
           ↓
Trajectory prediction: "Track #42 will reach Camera G-07 in ~11 seconds"
           ↓
Camera G-07 triggered — captures all vehicles in frame at T+11s
           ↓
Four-filter disambiguation:
  ✓ Heading 047° ± 15°?
  ✓ Arrival at T+8s to T+14s?
  ✓ Appearance embedding cosine similarity > 0.85?
  ✓ No track gap between violation and camera?
           ↓
All four pass → Confidence score computed
           ↓
Score ≥ 95% → Vehicle #42 identified → plate linked
Score < 95% → Case dropped

```

## 11.4 Ground Camera Trigger Flow

```
Violation Event (GPS: 8.5241°N, 76.9366°E, heading: 047°)
           ↓
PostGIS Query: SELECT * FROM cameras WHERE ST_DWithin(location, violation_point, 200)
           ↓
Nearest camera found: G-07 (150m away, heading 047° path)
           ↓
Time-to-camera estimated: distance / vehicle_speed
           ↓
Trigger API call to G-07: { event_id, expected_arrival, vehicle_description }
           ↓
G-07 captures burst of frames at expected window
           ↓
ANPR runs on captured frames → plate extracted
           ↓
PATCH /violations/{event_id} → link plate to event
```

## 11.5 City Planner Recommendation Flow

```
Historical violation_events table (PostGIS)
           ↓
DBSCAN spatial clustering (eps=50m, min_samples=10)
           ↓
Cluster identified → violation type distribution computed
           ↓
Rule engine maps cluster profile to recommendation:
  - >40% red-light jumping at intersection → "Signal recommended"
  - >40% wrong-way on segment → "One-way conversion or barriers"
  - >40% speeding near institution → "Speed bump / zone redesign"
  - >40% no-parking, no sign visible → "Signage installation"
           ↓
Recommendation stored in recommendations table
           ↓
Rendered as suggestion polygon on City Planner twin
           ↓
What-if simulator models projected violation reduction
           ↓
Export as PDF report for government submission
```

---

# 12. Tech Stack

## 12.1 Complete Tech Stack Per Layer

| Layer | Technology | Version | Justification |
|---|---|---|---|
| **Object Detection** | YOLOv8 (Ultralytics) | 8.x | Best speed/accuracy tradeoff for real-time aerial detection; pretrained on COCO with vehicle classes |
| **Object Tracking** | DeepSORT / ByteTrack | Latest | Maintains cross-frame Track IDs with appearance re-ID; handles occlusion |
| **Anomaly Detection** | Isolation Forest | scikit-learn 1.x | Lightweight, no training data needed for trajectory anomalies |
| **Video Processing** | OpenCV | 4.x | Frame capture, preprocessing, homography transforms |
| **Geo-Projection** | pyproj, geopy | Latest | CRS transformations, geodesic distance calculations |
| **Backend API** | FastAPI | 0.110+ | Async support, WebSocket native, Pydantic validation; matches Ghost's stack |
| **Database** | PostgreSQL + PostGIS | 16 + 3.4 | Full geo-spatial query support (ST_DWithin, ST_Within, ST_Intersects) |
| **Live State** | Redis | 7.x | Sub-millisecond live vehicle position updates with TTL |
| **Object Storage** | MinIO | Latest | S3-compatible, self-hosted, free; violation snapshot storage |
| **Async Tasks** | Celery + Redis | Latest | Async DBSCAN clustering, report generation, challan processing |
| **Spatial Analysis** | GeoPandas, Shapely | Latest | Polygon operations, trajectory analysis, cluster geometry |
| **Clustering** | scikit-learn DBSCAN | 1.x | Spatial violation hotspot detection |
| **Super Resolution** | Real-ESRGAN | Latest | License plate upscaling for aerial ANPR |
| **ANPR** | PaddleOCR | Latest | Best support for Indian number plate formats |
| **Simulation** | CARLA | 0.9.15 | Open-source autonomous driving simulator; drone simulation via spectator camera |
| **Digital Twin** | CesiumJS | Latest | 3D geo-accurate rendering; OSM building support; WebGL |
| **Frontend** | React + Next.js | 14.x | Matches Ghost's stack; SSR for dashboard performance |
| **Charts** | Recharts + D3.js | Latest | Violation trend charts, heatmaps |
| **WebSockets** | FastAPI WebSockets | Native | Real-time twin updates |
| **Auth** | JWT (python-jose) | Latest | Role-based access control |

## 12.2 Why Key Tools Were Chosen Over Alternatives

| Tool Chosen | Alternative Considered | Why Chosen |
|---|---|---|
| YOLOv8 | ResNet-50 (existing), EfficientDet | Faster inference, better small-object detection, built-in tracking integration |
| DeepSORT | SORT, FairMOT | Better re-identification after occlusion via appearance embeddings |
| FastAPI | Django REST | Native async, WebSocket support, lighter weight for real-time data |
| CesiumJS | Mapbox GL JS, Kepler.gl | 3D geo-accurate terrain, free with OSM data, drone simulation capability |
| PostGIS | MongoDB + GeoJSON | Native geo-spatial indexing, ST_ function library, mature for urban data |
| CARLA | AirSim, SUMO | Photorealistic rendering, Python API, vehicle physics, drone camera simulation |
| PaddleOCR | OpenALPR, Tesseract | Best accuracy on Indian number plate formats; free and open-source |

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
    status          VARCHAR(30) DEFAULT 'flagged', -- flagged, identified, draft, confirmed, resolved, dropped
    snapshot_url    TEXT,
    velocity        FLOAT,
    heading         FLOAT,
    plate_number    VARCHAR(20),
    challan_id      UUID,
    flagged_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    officer_id      UUID REFERENCES users(id)
);
CREATE INDEX violation_events_location_idx ON violation_events USING GIST(location);
CREATE INDEX violation_events_type_idx ON violation_events(violation_type);
CREATE INDEX violation_events_time_idx ON violation_events(flagged_at);
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

### Table: cameras
```sql
CREATE TABLE cameras (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    location        GEOMETRY(POINT, 4326) NOT NULL,
    has_anpr        BOOLEAN DEFAULT FALSE,
    api_endpoint    TEXT,
    coverage_radius INTEGER DEFAULT 50, -- metres
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX cameras_location_idx ON cameras USING GIST(location);
```

### Table: recommendations
```sql
CREATE TABLE recommendations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id      INTEGER,
    rec_type        VARCHAR(50), -- 'signal', 'speed_bump', 'signage', 'one_way', 'enforcement'
    geometry        GEOMETRY(POLYGON, 4326),
    description     TEXT,
    supporting_data JSONB,
    projected_reduction FLOAT, -- % estimated reduction from what-if model
    status          VARCHAR(30) DEFAULT 'pending', -- pending, reviewed, accepted, rejected
    created_at      TIMESTAMPTZ DEFAULT NOW()
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
    role            VARCHAR(20) NOT NULL, -- OFFICER, OPERATOR, PLANNER, ADMIN
    full_name       VARCHAR(200),
    badge_number    VARCHAR(50),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

## 13.2 Redis Key Structure

```
live:vehicle:{track_id}          → JSON {lat, lng, speed, heading, class, violation_status} TTL: 30s
live:session:{session_id}        → JSON {drone_lat, drone_lng, altitude, vehicle_count} TTL: 60s
live:violations:active           → Sorted set of active violation event IDs by timestamp
telemetry:{session_id}:latest    → JSON {lat, lng, alt, pitch, roll, yaw, timestamp} TTL: 10s
camera:trigger:{camera_id}       → JSON {event_id, expected_time, vehicle_desc} TTL: 60s
```

## 13.3 MinIO Bucket Structure

```
bucket: violation-snapshots/
├── {year}/{month}/{day}/
│   └── {violation_event_id}/
│       ├── overview.jpg         ← full drone frame at violation moment
│       ├── vehicle_crop.jpg     ← cropped bounding box of violating vehicle
│       └── plate_crop.jpg       ← cropped plate region (if available)
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
| PATCH | /violations/{id} | Update status, link plate, assign officer | OFFICER, ADMIN |
| GET | /violations/stats | Aggregated stats (count by type, by zone, by date) | PLANNER, ADMIN |
| GET | /violations/heatmap | GeoJSON heatmap for date range | PLANNER |

### Zones
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /zones | List all zones as GeoJSON | All |
| POST | /zones | Create new zone polygon | PLANNER, ADMIN |
| PUT | /zones/{id} | Update zone geometry or metadata | PLANNER, ADMIN |
| DELETE | /zones/{id} | Deactivate zone | ADMIN |

### Cameras
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /cameras | List all registered cameras | All |
| POST | /cameras | Register new camera | ADMIN |
| POST | /cameras/{id}/trigger | Trigger camera capture (internal) | System |
| PUT | /cameras/{id} | Update camera details | ADMIN |

### Sessions
| Method | Endpoint | Description | Role |
|---|---|---|---|
| POST | /sessions | Start drone session | OPERATOR |
| PATCH | /sessions/{id} | End session or update status | OPERATOR |
| GET | /sessions | List sessions | ADMIN |

### Recommendations
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /recommendations | List AI recommendations | PLANNER |
| PATCH | /recommendations/{id} | Accept or reject recommendation | PLANNER |
| POST | /recommendations/generate | Trigger DBSCAN + recommendation run | PLANNER, ADMIN |

### Users (Admin)
| Method | Endpoint | Description | Role |
|---|---|---|---|
| GET | /users | List users | ADMIN |
| POST | /users | Create user | ADMIN |
| PATCH | /users/{id} | Update role or status | ADMIN |

## 14.2 WebSocket Events

| Event | Direction | Payload | Consumer |
|---|---|---|---|
| vehicle_update | Server → Client | {track_id, lat, lng, speed, heading, class} | Police Dashboard |
| violation_alert | Server → Client | {event_id, type, lat, lng, snapshot_url, confidence} | Police Dashboard |
| session_status | Server → Client | {session_id, vehicle_count, drone_lat, drone_lng, altitude} | Operator View |
| track_lost | Server → Client | {track_id, last_known_lat, last_known_lng} | Police Dashboard |
| recommendation_ready | Server → Client | {recommendation_id, type, location} | Planner Dashboard |

## 14.3 External API Integrations

| API | Purpose | Notes |
|---|---|---|
| **Vahan API** | Vehicle owner lookup by plate number | Government API — requires authorised integration |
| **Traffic Signal Controller API** | Real-time signal state (RED/GREEN/YELLOW) | IoT-connected signals; fallback to visual detection |
| **Ground CCTV Trigger API** | Send capture trigger to ANPR cameras | Per-camera REST endpoint |
| **MAVLink** | Drone telemetry (GPS, altitude, attitude) | Open protocol; DJI SDK as alternative |

---

# 15. Edge Cases & Answers

**Q1: Two visually identical vehicles near violation location — which one gets the challan?**

A: The confidence-weighted four-filter disambiguation engine is applied. Only if all four checks — heading match, arrival time window, appearance embedding similarity, and continuous track — pass with a combined confidence ≥ 95% is a vehicle identified. If two identical vehicles both pass the filters (extremely unlikely given the time window is typically 3–5 seconds wide), the case is marked "ambiguous — unresolved" and no challan is issued. The system always errs toward inaction rather than risk a wrongful penalty.

**Q2: What happens if the drone loses track of the vehicle under a bridge or tree?**

A: DeepSORT's Kalman filter predicts the vehicle's position during occlusion using last known velocity and heading. If the vehicle re-emerges within the predicted position range within a configurable time window (default 5 seconds), the same Track ID is restored. If it does not re-emerge within the window, the track is marked "lost" and the violation case is dropped — no challan issued. Track loss is logged for post-session review.

**Q3: What happens if the drone loses signal mid-session?**

A: The detection pipeline writes violation events to a local buffer (Redis Streams) that persists independently of the WebSocket connection to the dashboard. When the drone reconnects, buffered events are flushed to PostgreSQL. The session is marked "degraded" during the disconnection window. If the drone does not reconnect within 60 seconds, the session is marked "aborted" and all events captured before disconnection are preserved.

**Q4: How does the system perform in night or low-light conditions?**

A: At night, YOLOv8 detection confidence drops significantly on standard RGB footage. The system flags any session where average detection confidence drops below 0.60 as "low-light degraded" and applies a reduced violation confidence threshold (minimum 98% instead of 95%) to compensate. For full night operation, an IR or thermal camera attachment is recommended, which requires a separate model fine-tuned on thermal imagery. Night detections are tagged "low-light" in the event record.

**Q5: How does the system handle rain, fog, or severe weather?**

A: Weather-induced image degradation reduces detection accuracy. The system monitors average YOLOv8 confidence scores per frame as a proxy for image quality. If the 30-frame rolling average confidence drops below 0.50, the session is flagged "weather degraded" and violation detection is paused. No violations are flagged during degraded sessions to prevent false positives. The operator is alerted to land the drone.

**Q6: What if the drone accidentally exceeds DGCA altitude limits?**

A: The drone telemetry stream includes real-time altitude. If altitude exceeds the configured limit (default 120m), the system sends an alert to the drone operator view and logs the overage. Violation detection continues but the event is tagged "altitude non-compliant" — such events would not be legally admissible and are flagged for operator awareness. A hard warning at 110m gives the operator time to descend before reaching the limit.

**Q7: How are false positive violations handled?**

A: Every flagged violation goes through the confidence scorer before generating an alert. Events below 95% confidence are dropped before reaching officers. Events that reach officer review can be manually dismissed. Dismissed events are logged with dismissal reason for model improvement feedback. The system tracks false positive rate per violation type; if a type exceeds 20% dismissal rate, it is flagged for model retraining.

**Q8: How does the system handle the CARLA-to-real-world transfer gap?**

A: CARLA generates photorealistic but synthetic data. Domain gap (difference between synthetic and real footage) is addressed through: (a) CARLA data augmentation — weather effects, lighting variation, motion blur applied during training; (b) fine-tuning — YOLOv8 model trained on CARLA data is fine-tuned on a small set of real drone footage before production deployment; (c) confidence calibration — detection confidence thresholds may need adjustment after real-world validation.

**Q9: What happens when vehicle density is very high (100+ vehicles in frame)?**

A: DeepSORT performance degrades at very high vehicle density due to ID switching caused by close proximity between vehicles. ByteTrack is available as an alternative tracker that handles high-density scenarios better. The system is configurable to switch tracker based on detected vehicle count per frame. Above 80 vehicles in frame, a density warning is logged and violation confidence thresholds are automatically raised by 5% to account for reduced tracking reliability.

**Q10: What if two drones are covering the same zone simultaneously?**

A: Each drone runs its own independent detection pipeline instance, identified by session_id. If two sessions are active with overlapping patrol zones, the system deduplicates violation events using a spatial and temporal deduplication check: if two violation events of the same type occur within 10 metres and 5 seconds of each other across two sessions, the higher-confidence event is retained and the other is marked "duplicate." Officers are notified of the duplication.

**Q11: What if the nearest ground camera is offline or unavailable?**

A: The camera registry stores health status per camera, updated via periodic ping checks. If the nearest camera is offline, the system searches for the next nearest active camera within range. If no active camera is within range, the violation event is stored with status "unidentified — no camera coverage" and no challan is generated. The event is retained for statistical purposes. The coverage gap is logged for admin review.

**Q12: What if a vehicle enters the violation zone from outside the drone's field of view?**

A: Vehicles entering from outside the frame are assigned a new Track ID by DeepSORT when they become visible. For zone-based violations (no-parking, illegal stopping), the violation timer starts from the first frame the vehicle is visible inside the zone, regardless of when it entered. For trajectory-based violations (wrong-way, U-turn), these require a minimum trajectory length to detect — vehicles entering mid-violation may not have sufficient trajectory history, so detection may be delayed by up to 10 frames. This is a known limitation documented in the system.

**Q13: What if confidence score is just below the 95% threshold?**

A: The event is stored in the database with status "low-confidence" rather than dropped entirely. It is not sent as an alert to officers and does not generate a challan. The event is visible to admins for audit purposes. If the same vehicle commits the same violation again in the same session and the new event scores ≥ 95%, both events are linked and the high-confidence event proceeds normally.

---

# 16. Real World Issues & Mitigations

## 16.1 DGCA Regulations Compliance

**Issue:** India's DGCA (Directorate General of Civil Aviation) regulates UAV operations under the Drone Rules 2021. Civilian drones are limited to 120m AGL (above ground level) and require operator certification, flight plan filing in controlled airspace, and no-fly zone adherence.

**Mitigation:**
- System enforces altitude monitoring with real-time alerts at 110m (warning) and 120m (hard limit flag)
- Patrol zone planner integrates no-fly zone data from the Digital Sky Platform API
- All sessions are logged with operator ID, altitude profile, and zone metadata for regulatory audit
- BVLOS (Beyond Visual Line of Sight) operations require additional DGCA approval — system flags patrol zones that exceed VLOS range

## 16.2 Indian Legal Enforceability of AI Evidence

**Issue:** AI-generated evidence is not automatically admissible in Indian courts. The Motor Vehicles Act Section 136A allows electronic surveillance evidence but requires human officer confirmation and chain-of-custody documentation.

**Mitigation:**
- No challan is issued without officer review and approval — human is always in the loop
- Full event audit trail stored: detection timestamp, confidence score, snapshot, plate capture, officer ID, approval timestamp
- Challan draft is presented as supporting evidence, not standalone automated penalty
- Legal framework alignment documented in system design for government stakeholder review

## 16.3 Privacy Concerns — Aerial Surveillance

**Issue:** Continuous aerial surveillance of public roads raises privacy concerns under the DPDP Act 2023. Footage of individuals who are not committing violations must be handled carefully.

**Mitigation:**
- Drone cameras are pointed at roads only — not residential windows or private property
- Non-violating vehicle data is held in Redis with 30-second TTL and not persisted to long-term storage
- Only violating vehicle images are stored in MinIO — general crowd footage is not archived
- Facial recognition is explicitly not used at any point in the pipeline
- Privacy policy document published for any government pilot deployment

## 16.4 Data Storage and Retention Policies

**Issue:** Long-term storage of surveillance data creates privacy and compliance obligations.

**Mitigation:**
- Live Redis state: 30-second TTL — no long-term storage of non-violating vehicles
- Violation events: retained for 2 years (standard Indian traffic records retention period)
- Drone session footage: not stored by default — only violation snapshot crops are retained
- Recommendation analytics data: anonymised and aggregated — no individual vehicle linkage

## 16.5 Network Latency in Real-Time Streaming

**Issue:** RTSP drone feed + WebSocket twin updates must be delivered with low enough latency for real-time enforcement use.

**Mitigation:**
- Detection pipeline targets < 100ms inference time per frame on RTX 4060
- WebSocket updates pushed at 10Hz — 100ms update interval acceptable for enforcement
- Redis Pub/Sub used for fan-out to multiple dashboard clients — no per-client database query
- Local deployment (edge device or LAN server) recommended for production to eliminate internet latency

## 16.6 Model Bias Toward Certain Vehicle Types

**Issue:** YOLOv8 pretrained on COCO may perform better on car-type vehicles and worse on auto-rickshaws, two-wheelers, or heavy vehicles common in Indian traffic.

**Mitigation:**
- CARLA dataset augmented with Indian vehicle types using custom blueprints
- Additional fine-tuning on Indian traffic datasets (IDD — India Driving Dataset) available on public repositories
- Per-class detection accuracy tracked separately in benchmarking; vehicle classes below F1 0.65 flagged for retraining

## 16.7 GPS Accuracy in Urban Canyons

**Issue:** Urban environments with tall buildings cause GPS multipath errors, reducing position accuracy to 5–10m in some areas.

**Mitigation:**
- Drone RTK (Real-Time Kinematic) GPS recommended for production deployment (accuracy < 5cm)
- For standard GPS: Kalman filter smoothing applied to trajectory reduces position noise
- Violation zone polygons include a configurable buffer margin (default 2m) to account for GPS error
- GPS accuracy estimate included in confidence score calculation for zone-intersection violations

---

# 17. CARLA Simulation Strategy

## 17.1 Why CARLA

CARLA (Car Learning to Act) is an open-source autonomous driving simulator that provides photorealistic 3D urban environments, a rich Python API for scenario scripting, ground-truth sensor data, and a broad vehicle blueprint library. It eliminates the need for real drone footage during development and testing phases, enabling:

- Controlled, reproducible testing of all 10 violation types
- Automatic ground truth label generation (no manual annotation)
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

## 17.4 Dataset Generation Pipeline

```
CARLA Session (synchronous mode)
       ↓
Per frame (every 5th frame = 4 FPS export):
  ├── RGB image saved → /dataset/images/{session_id}/{frame_id}.jpg
  ├── Semantic segmentation mask → /dataset/masks/{session_id}/{frame_id}.png
  ├── Ground truth bounding boxes (YOLO format) → /dataset/labels/{session_id}/{frame_id}.txt
  ├── Vehicle GPS coordinates → /dataset/gps/{session_id}/{frame_id}.json
  └── Violation ground truth label → /dataset/violations/{session_id}/{frame_id}.json
       ↓
Dataset assembled: 10,000+ frames per violation type
       ↓
Augmentation: weather, brightness, motion blur, compression artefacts
       ↓
YOLOv8 training on assembled dataset
       ↓
Model evaluation on held-out CARLA test set
       ↓
Transfer: fine-tune on small real drone footage set
```

## 17.5 Sim-to-Real Transfer Approach

The domain gap between CARLA synthetic data and real drone footage is addressed through three strategies:

- **Augmentation during training** — CARLA frames augmented with realistic degradation (rain overlay, motion blur, JPEG compression) to reduce domain gap
- **Fine-tuning** — base model trained on CARLA data is fine-tuned on 500–1000 manually labelled real drone frames before production
- **Confidence calibration** — detection confidence thresholds validated and adjusted on real footage before deployment

## 17.6 Validation Methodology

| Test Type | Approach |
|---|---|
| Unit validation | Each violation script tested in isolation — detection rate measured |
| Edge case validation | Identical vehicle ambiguity test, occlusion test, high-density test scripted in CARLA |
| Integration validation | Full pipeline end-to-end on scripted 30-minute CARLA session |
| Transfer validation | CARLA-trained model evaluated on real drone footage; mAP compared |

---

# 18. Digital Twin Design

## 18.1 What the Twin Mirrors

The digital twin is a continuously updated 3D virtual replica of the monitored road network. It mirrors:
- Every detected vehicle's real-time position, speed, and class
- All defined violation zone polygons as geo-fenced overlays
- All violation events with their exact GPS coordinates, timestamps, and snapshots
- Ground camera positions and status
- Drone position and altitude
- Historical violation density as a heatmap layer (planner mode only)
- AI recommendation polygons (planner mode only)

## 18.2 Zone Polygon Management

Zone polygons are the foundational layer of the twin — they define where violations can occur. Zones are:
- Drawn directly on the twin using a polygon drawing tool (city planner dashboard)
- Stored as GeoJSON in PostGIS
- Colour-coded by zone type (red = no-parking, yellow = speed zone, blue = intersection)
- Editable and deleteable without system restart
- Immediately active in the violation engine upon save (no pipeline restart required)
- Versioned — zone edit history maintained for audit

## 18.3 Live Vehicle Layer

Every vehicle tracked by DeepSORT appears on the twin as a 3D marker entity in CesiumJS, updated at 10Hz via WebSocket:

- **Green marker** — vehicle moving normally
- **Yellow marker** — vehicle in suspicious state (approaching violation threshold)
- **Red marker** — vehicle in active violation
- Click on marker → popup showing Track ID, vehicle class, speed, heading, violation history in session

Live vehicle entities are ephemeral — they exist only while the vehicle is tracked. Non-violating vehicle entities are removed from twin display after 30 seconds of no update (matching Redis TTL).

## 18.4 Violation Event Layer

When a violation is flagged:
- A pin is dropped at the exact GPS coordinate on the twin
- Pin colour = violation type colour code
- Click pin → popup with type, timestamp, confidence score, vehicle description, snapshot image
- Pins persist permanently on the twin and accumulate over sessions
- Pins filterable by type, date range, confidence level

## 18.5 Historical Heatmap Layer (Planner Mode)

- Violation event GPS points aggregated into a heatmap tile layer
- Colour scale from cool (low density) to hot (high density)
- Adjustable date range filter
- Adjustable violation type filter
- Rendered as CesiumJS heatmap via custom shader or Mapbox heatmap layer
- Updates on query — not real-time (batch computed)

## 18.6 AI Recommendation Overlay (Planner Mode)

- DBSCAN cluster boundaries rendered as semi-transparent polygon entities
- Recommendation icon displayed at cluster centroid (signal icon, road icon, etc.)
- Click recommendation → panel shows: violation breakdown, projected reduction, proposed action
- Accepted/rejected status reflected in polygon opacity

---

# 19. Dashboard Specifications

## 19.1 Police Dashboard — Live Enforcement

**Purpose:** Real-time operational awareness and violation response for enforcement officers and control room supervisors

**Layout:**
- Left panel (30%): Violation alert feed — live list of new violations with type, GPS, vehicle description, confidence, timestamp
- Center (55%): CesiumJS digital twin — live vehicle markers, violation pins, drone position
- Right panel (15%): Zone status — current active zones, violation count per zone in current session

**Features:**

| Feature | Description |
|---|---|
| Live twin view | 3D map with all tracked vehicles moving in real-time at 10Hz |
| Violation alert feed | Real-time list of flagged violations; click to jump twin view to location |
| Vehicle spotlight | Click any vehicle marker → trajectory trail, speed, class, violation history |
| Officer deployment map | Positions of all field officers (GPS from their mobile devices) |
| One-click dispatch | Assign officer to violation event with one tap |
| Drone status bar | Current altitude, session status, vehicle count, feed quality |
| Violation snapshot viewer | Photo evidence for each flagged event |
| Confirm / dismiss | Officer reviews AI-flagged event and confirms or dismisses |
| Challan draft viewer | Review and approve challan before submission |
| Session timeline | Scrub through current session's events chronologically |

## 19.2 City Planner Dashboard — Historical Intelligence

**Purpose:** Analysis of historical violation data for urban infrastructure planning and policy decisions

**Layout:**
- Left panel (25%): Filter controls — date range, violation types, zones, confidence level
- Center (55%): CesiumJS twin in planning mode — heatmap layer, recommendation overlays
- Right panel (20%): Analytics charts — trend lines, type breakdown, top zones

**Features:**

| Feature | Description |
|---|---|
| Violation heatmap | Interactive density heatmap over any date range on 3D twin |
| Violation trend charts | Line charts — violations over time per type; recharts |
| Zone problem ranking | Ranked list of zones by total violation count — sortable by type |
| AI recommendation panel | List of DBSCAN-generated recommendations with supporting data |
| What-if simulator | Draw proposed infrastructure change on twin → system projects violation reduction |
| Time-of-day analysis | Heatmap filtered by hour of day — identifies peak violation windows |
| Vehicle class breakdown | Pie chart of violations by vehicle type |
| Month-over-month comparison | Twin layers toggling between months for visual pattern comparison |
| PDF/Excel export | Export current view's stats and recommendations for government submission |
| Zone editor | Draw, edit, delete violation zones directly on twin |

---

# 20. Hybrid Enforcement Architecture

## 20.1 Architecture Overview

The hybrid enforcement architecture bridges the aerial detection capability of the drone with the ground-level identification capability of existing CCTV/ANPR infrastructure to produce a legally defensible enforcement chain.

```
DRONE (Detection)
      ↓ violation event + vehicle trajectory + predicted path
SYSTEM (Orchestration)
      ↓ trigger signal + time window + vehicle description
GROUND CAMERA (Identification)
      ↓ plate number + capture timestamp
SYSTEM (Linking)
      ↓ linked violation event + plate
OFFICER (Review)
      ↓ confirmed challan
VAHAN API (Registry)
      ↓ vehicle owner details
CHALLAN ISSUED
```

## 20.2 Ground Camera Trigger Flow

1. Violation event created with vehicle Track ID, GPS, velocity vector, and heading
2. Trajectory prediction computes where vehicle will be in next 5–30 seconds
3. PostGIS query finds nearest active ANPR-capable camera within 300m on the vehicle's projected path
4. Trigger API call sent to camera: `{ event_id, expected_arrival_time, vehicle_class, color, heading }`
5. Camera captures burst of 10 frames in the expected arrival window
6. ANPR (PaddleOCR) runs on captured frames — best-confidence plate extracted
7. Plate + camera capture timestamp POSTed to `/violations/{event_id}` endpoint
8. System links plate to violation event — status updated to "identified"

## 20.3 ANPR Integration

PaddleOCR is configured specifically for Indian number plate formats:
- Standard white plate (private vehicles): `XX 00 XX 0000` format
- Yellow plate (commercial): same format with yellow background
- BH series (new national format): `00BH 0000 XX` format
- High Security Registration Plates (HSRP) with hologram — may require pre-processing

Confidence threshold for plate acceptance: > 0.80. Below threshold, plate is flagged as "partial read" and officer must manually verify.

## 20.4 Vahan Database Link

Once plate is confirmed by officer, system queries the Vahan national vehicle registry API:
- Input: registration number
- Output: vehicle owner name, address, insurance status, fitness certificate status
- Challan draft pre-filled with owner details
- Vahan integration requires government-authorised API credentials for production deployment

## 20.5 Challan Generation Workflow

```
Violation confirmed (≥ 95% confidence + officer approval)
         ↓
Plate identified → Vahan API query → owner details
         ↓
Challan draft generated:
  {
    violation_type: "No-Parking Zone",
    location: "MG Road Junction 7",
    timestamp: "2026-07-31 10:23:44",
    vehicle_plate: "KL 01 AB 1234",
    owner_name: "...",
    fine_amount: ₹500,
    evidence: [snapshot_url, plate_capture_url],
    officer_id: "...",
    event_id: "..."
  }
         ↓
Officer reviews draft → taps "Submit"
         ↓
Challan issued via government e-challan system
         ↓
SMS notification to vehicle owner (via registered mobile in Vahan)
         ↓
Violation event status → "resolved"
```

## 20.6 Human Officer Approval Gate

No challan is ever issued automatically without a human officer reviewing and approving the draft. This is both a legal requirement under Indian law and a system design principle. The officer approval step is mandatory and cannot be bypassed by any system role including ADMIN.

---

# 21. AI Planning Recommendation Engine

## 21.1 DBSCAN Clustering Logic

DBSCAN (Density-Based Spatial Clustering of Applications with Noise) is applied to historical violation event GPS coordinates to identify spatial hotspots.

**Parameters:**
- `eps = 50` (metres) — maximum distance between two events to be considered same cluster
- `min_samples = 10` — minimum violation events to form a cluster
- Events outside all clusters = noise (isolated incidents, not actionable patterns)

**Clustering is run:**
- On demand (planner triggers from dashboard)
- Automatically weekly via Celery scheduled task
- Separately per violation type to identify type-specific patterns

## 21.2 Recommendation Rule Set

| Cluster Profile | Recommendation Type | Reasoning |
|---|---|---|
| >40% red-light jumping at intersection | Traffic signal installation | Crossing behaviour indicates missing or inadequate signal |
| >40% speeding on segment near school/hospital | Speed bump + advisory signage | High-vulnerability zone requires physical calming |
| >40% wrong-way driving on road | One-way conversion + physical barriers | Persistent wrong-way suggests confusing road design |
| >40% no-parking in zone with no visible signage | No-parking signage installation | Violation may be due to unawareness |
| >50% lane violations on multi-lane stretch | Lane marking refresh + rumble strips | Faded or inadequate lane markings |
| High violation density across all types in time window | Enforcement schedule adjustment | Deploy officers during peak violation hours |
| >40% helmet-less riding cluster | Enforcement checkpoint recommendation | High compliance failure area |

## 21.3 What-If Simulator Design

The what-if simulator allows city planners to model proposed infrastructure changes and estimate their impact on violation rates.

**Mechanism:**
- Planner draws a proposed change on the twin (new signal position, new one-way direction, new zone boundary)
- System identifies all historical violation events that would be affected by this change
- Rule-based model estimates reduction: e.g., adding signal at intersection → historical red-light jumping events at that location → projected 70–80% reduction based on signal-compliance studies
- Projection displayed as "Before: 142 violations/month" → "Projected After: ~35 violations/month"

**Limitation:** Projection is rule-based, not causal ML — it is an estimate for planning guidance, not a precise forecast. Clearly labelled as "projected estimate" in UI.

## 21.4 Output Format for Government Use

Recommendations are exportable as:
- **PDF report** — executive summary, heatmap image, top 10 recommended actions, what-if projections, supporting violation statistics
- **GeoJSON export** — all recommendation polygons for import into government GIS systems
- **Excel export** — tabular violation data per zone for spreadsheet analysis

---

# 22. Limitations & Future Scope

## 22.1 Current System Limitations

| Limitation | Impact | Mitigation Taken |
|---|---|---|
| Number plate unreadable at high altitude | Cannot independently identify vehicle | Hybrid ground camera architecture |
| Detection accuracy drops at night | Night enforcement not fully reliable | Low-light session flagging + raised threshold |
| CARLA-to-real domain gap | Model may underperform on first real deployment | Fine-tuning + augmentation strategy |
| What-if simulator is rule-based, not causal | Projections are estimates, not precise | Clearly labelled as estimates in UI |
| High vehicle density reduces tracker accuracy | More ID switches in dense traffic | ByteTrack fallback + raised confidence threshold |
| Vahan API requires government authorisation | Cannot verify owner in academic prototype | Simulated in prototype; noted for production |
| Single-zone CARLA training | Model may underperform in new geographies | Dataset diversity plan for production |

## 22.2 Future Scope

- **Multi-drone coordination** — fleet management for city-wide coverage with conflict resolution
- **Edge deployment** — onboard drone inference eliminating latency from ground processing
- **Predictive enforcement** — ML model predicting high-violation time-location combinations for proactive deployment
- **Night-mode with thermal camera** — full 24-hour operation capability
- **Causal what-if modelling** — replace rule-based projection with trained causal ML model
- **Integration with smart traffic signals** — real-time signal timing adjustment based on violation patterns
- **Federated learning** — multiple city deployments contributing to shared model improvement without sharing raw data
- **Carbon footprint analysis layer** — idling vehicles in violation zones mapped to emissions estimates for environmental planning

---

# 23. References

1. Bochkovskiy, A., Wang, C. Y., & Liao, H. Y. M. (2020). YOLOv4: Optimal speed and accuracy of object detection. *arXiv:2004.10934*
2. Wojke, N., Bewley, A., & Paulus, D. (2017). Simple online and realtime tracking with a deep association metric. *ICIP 2017*
3. Dosovitskiy, A. et al. (2017). CARLA: An open urban driving simulator. *CoRL 2017*
4. DGCA India. (2021). Drone Rules 2021. Ministry of Civil Aviation, Government of India
5. Government of India. (2023). Digital Personal Data Protection Act 2023
6. Ministry of Road Transport and Highways. (2019). Motor Vehicles (Amendment) Act 2019 — Section 136A
7. Ester, M. et al. (1996). A density-based algorithm for discovering clusters in large spatial databases with noise. *KDD 1996* (DBSCAN original paper)
8. Wang, X. et al. (2021). Real-ESRGAN: Training real-world blind super-resolution with pure synthetic data. *ICCV 2021*
9. Indian Institute of Technology. India Driving Dataset (IDD). *idd.insaan.iiit.ac.in*
10. National Informatics Centre. Vahan — National Vehicle Registry. *vahan.parivahan.gov.in*
11. Cesium. CesiumJS Documentation. *cesium.com/docs*
12. Ultralytics. YOLOv8 Documentation. *docs.ultralytics.com*

---

# 24. Glossary

| Term | Definition |
|---|---|
| **ANPR** | Automatic Number Plate Recognition — automated system that reads vehicle registration plates from camera footage |
| **AGL** | Above Ground Level — drone altitude measured from the ground directly below the drone |
| **BVLOS** | Beyond Visual Line of Sight — drone operation where the operator cannot see the drone directly |
| **CARLA** | Car Learning to Act — open-source photorealistic autonomous driving simulator |
| **Challan** | Official traffic violation penalty notice issued to a vehicle owner in India |
| **CCTV** | Closed-Circuit Television — fixed surveillance cameras |
| **DBSCAN** | Density-Based Spatial Clustering of Applications with Noise — clustering algorithm that groups geographically close data points |
| **DeepSORT** | Deep Simple Online and Realtime Tracking — multi-object tracking algorithm using appearance embeddings and Kalman filter |
| **DGCA** | Directorate General of Civil Aviation — India's aviation regulatory authority |
| **Digital Twin** | A virtual 3D replica of a physical environment that mirrors real-world state in real time |
| **DPDP Act** | Digital Personal Data Protection Act 2023 — India's data privacy legislation |
| **GeoJSON** | Open standard format for encoding geographic data structures |
| **Geo-fencing** | Using GPS coordinates to define a virtual geographic boundary |
| **GSD** | Ground Sampling Distance — real-world distance represented by one pixel in an aerial image |
| **Homography** | Mathematical transformation mapping points from one plane (image) to another (ground) |
| **IoT** | Internet of Things — network of connected physical devices |
| **JWT** | JSON Web Token — compact, self-contained token for authentication and authorisation |
| **Kalman Filter** | Mathematical algorithm for estimating the state of a dynamic system from noisy measurements |
| **MAVLink** | Micro Air Vehicle Link — lightweight open-source communication protocol for drones |
| **MinIO** | S3-compatible open-source object storage server |
| **mAP** | Mean Average Precision — standard metric for evaluating object detection model accuracy |
| **Nadir** | Camera orientation pointing straight down — directly below the drone |
| **OSM** | OpenStreetMap — free, open-source global map data |
| **PostGIS** | Spatial extension for PostgreSQL enabling geo-spatial queries |
| **RBAC** | Role-Based Access Control — restricting system access based on user roles |
| **ReID** | Re-Identification — matching the same object or person across different camera views |
| **RTSP** | Real Time Streaming Protocol — standard for streaming video over networks |
| **RTK GPS** | Real-Time Kinematic GPS — high-accuracy GPS with centimetre-level precision |
| **Sim-to-Real** | Transfer learning approach where a model trained on simulation data is adapted for real-world use |
| **Track ID** | Unique persistent identifier assigned to a detected vehicle throughout a tracking session |
| **UAV** | Unmanned Aerial Vehicle — drone |
| **Vahan** | India's national vehicle registration database managed by the Ministry of Road Transport |
| **WebSocket** | Full-duplex communication protocol over a single TCP connection — enables real-time server-to-client data push |
| **YOLOv8** | You Only Look Once version 8 — state-of-the-art real-time object detection model |

---

# 25. Assumptions & Constraints

## 25.1 Assumptions

| # | Assumption |
|---|---|
| A1 | The drone has a stable GPS signal providing position accuracy within ±5m throughout the session |
| A2 | Ground CCTV cameras exist at major intersections in the monitored zone |
| A3 | Traffic signal state is accessible via API for IoT-connected signals; visual detection used as fallback |
| A4 | Drone camera can be tilted via gimbal for angled views when helmet or plate capture is needed |
| A5 | Indian number plates are in standard formats as defined by MoRTH — state code + district code + number |
| A6 | Monitored road zone has adequate mobile or WiFi network connectivity for real-time data streaming |
| A7 | Drone operator is DGCA-certified and complies with all operational requirements |
| A8 | The digital twin is operated on a device with sufficient GPU for CesiumJS 3D rendering |
| A9 | Zone polygons are pre-configured by an admin before violation detection begins |
| A10 | Vahan API access is available for production deployment with appropriate government authorisation |

## 25.2 Constraints

| # | Constraint | Impact |
|---|---|---|
| C1 | DGCA altitude limit: 120m AGL maximum | Number plate not readable from nadir view; hybrid architecture required |
| C2 | DGCA no-fly zones (airports, military, restricted areas) | Patrol zones must exclude these areas |
| C3 | RTX 4060 laptop GPU available for development | CARLA render quality limited to medium; processing pipeline optimised for this hardware |
| C4 | Indian number plate formats only — regional scripts on plates in some states | OCR model must handle Devanagari and other scripts in state codes |
| C5 | No facial recognition permitted | Rider identification limited to helmet presence/absence, not face |
| C6 | Violation events require human officer approval before challan — cannot be fully automated | Adds latency between detection and enforcement |
| C7 | DPDP Act 2023 compliance required | Non-violating footage must not be stored long-term |

---

# 26. System Modes of Operation

## Mode 1 — Simulation Mode (CARLA)

**Description:** Full pipeline operates on CARLA simulator input. No real drone or real cameras involved.  
**Use Case:** Development, testing, dataset generation, violation scripting, edge case validation.  
**Data Source:** CARLA Python API — frames + ground truth GPS.  
**Difference from Live:** Detection confidence calibration may differ; all identifications are ground-truth (no ambiguity).  
**Indicator:** Dashboard banner shows "SIMULATION MODE — CARLA DATA."

## Mode 2 — Live Drone Mode

**Description:** Full pipeline operates on real UAV RTSP stream with MAVLink telemetry.  
**Use Case:** Production enforcement operations.  
**Data Source:** Real drone camera + GPS telemetry + ground CCTV triggers.  
**Requirements:** Active drone session, DGCA-compliant altitude, active ground cameras in range.  
**Indicator:** Dashboard banner shows "LIVE SESSION — [session_id]."

## Mode 3 — Playback / Review Mode

**Description:** System re-processes previously recorded drone footage or replays a past session on the digital twin.  
**Use Case:** Officer reviewing a completed session, planner analysing a specific past incident, model evaluation.  
**Data Source:** Archived session footage + violation event logs.  
**Note:** Violations cannot be newly issued in playback mode — events already in database are reviewed only.  
**Indicator:** Dashboard shows scrubber timeline and "PLAYBACK MODE" banner.

## Mode 4 — Planning Mode

**Description:** City planner dashboard operates independently of any live session, querying only historical data.  
**Use Case:** Infrastructure planning analysis outside of active enforcement operations.  
**Data Source:** PostgreSQL historical violation_events, recommendations.  
**No live feed:** Digital twin shows static violation heatmap, no moving vehicles.  
**Indicator:** No session banner; planner dashboard is the default view for PLANNER role.

## Mode 5 — Offline Mode

**Description:** Network connection lost during active live session.  
**Behaviour:** Detection pipeline continues. Events written to local Redis Streams buffer. WebSocket reconnection attempted every 10 seconds. Upon reconnect, buffered events flushed to PostgreSQL. Dashboard shows "Connection lost — reconnecting" banner.  
**Data Loss Risk:** If Redis is also unavailable (local Redis crash), in-flight events may be lost. Mitigated by Redis persistence (AOF mode enabled).

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
| **MOTA** | Multiple Object Tracking Accuracy — combined measure of ID switches, false positives, misses | ≥ 0.65 |
| **ID Switch Rate** | Frequency of Track ID reassignment to wrong vehicle | ≤ 5% |
| **Track Continuity** | % of frames where correct Track ID maintained per vehicle | ≥ 90% |

## 27.3 Violation Detection Metrics

Measured per violation type across CARLA test set of 100 scripted scenarios per violation:

| Metric | Description | Target |
|---|---|---|
| **Precision** | Of all flagged events, how many were actual violations | ≥ 0.85 |
| **Recall** | Of all actual violations, how many were detected | ≥ 0.75 |
| **F1 Score** | Harmonic mean of precision and recall | ≥ 0.70 per violation type |
| **False Positive Rate** | % of flagged events that were not violations | ≤ 15% |
| **False Negative Rate** | % of real violations that were missed | ≤ 25% |

## 27.4 Identity Threading Accuracy

| Metric | Description | Target |
|---|---|---|
| **Correct Identity Rate** | % of violation events correctly linked to the right vehicle at ground camera | ≥ 95% |
| **Wrongful Identity Rate** | % of events where wrong vehicle was identified | 0% (mandatory) |
| **Dropout Rate** | % of events dropped due to confidence below threshold | Acceptable up to 30% — better to drop than wrongfully identify |

## 27.5 System Performance Metrics

| Metric | Description | Target |
|---|---|---|
| **End-to-end latency** | Time from violation occurrence to dashboard alert | ≤ 3 seconds |
| **Twin update frequency** | Vehicle position update rate on digital twin | 10 Hz |
| **API response time** | REST endpoint response under 100 concurrent users | ≤ 200ms |
| **WebSocket message delay** | Delay between event creation and client receipt | ≤ 500ms |

## 27.6 Geo-Projection Accuracy

| Metric | Description | Target |
|---|---|---|
| **GPS projection error** | Average error between projected GPS and ground truth GPS | ≤ 2m at 100m altitude |
| **Zone intersection accuracy** | Correct zone membership determination | ≥ 98% |

---

# 28. Security & Access Control

## 28.1 Role-Based Access Control (RBAC)

| Feature | OFFICER | OPERATOR | PLANNER | ADMIN |
|---|---|---|---|---|
| View police live dashboard | ✓ | ✓ | ✗ | ✓ |
| View city planner dashboard | ✗ | ✗ | ✓ | ✓ |
| Confirm / dismiss violations | ✓ | ✗ | ✗ | ✓ |
| Approve and submit challans | ✓ | ✗ | ✗ | ✓ |
| Start / end drone sessions | ✗ | ✓ | ✗ | ✓ |
| Create / edit zones | ✗ | ✗ | ✓ | ✓ |
| Register / edit cameras | ✗ | ✗ | ✗ | ✓ |
| Manage users | ✗ | ✗ | ✗ | ✓ |
| Trigger recommendation generation | ✗ | ✗ | ✓ | ✓ |
| Export reports | ✗ | ✗ | ✓ | ✓ |
| View system health | ✗ | ✓ | ✗ | ✓ |

## 28.2 API Authentication

- All REST endpoints protected by JWT bearer token
- Tokens issued at `/auth/login` with 8-hour expiry for officers (shift-aligned)
- Refresh tokens with 30-day expiry stored in HTTP-only cookies
- Role claim embedded in JWT payload — role checked at endpoint level
- Failed authentication attempts logged with IP and timestamp

## 28.3 Drone Feed Security

- RTSP stream from drone transmitted over WPA3-encrypted WiFi or 4G LTE private APN
- Stream authenticated by session token — unauthenticated streams rejected
- MAVLink telemetry encrypted via MAVLink 2 signing

## 28.4 Data Access Audit Log

All actions on violation events and challans are logged:
```
{
  user_id, role, action, resource_id, timestamp, ip_address, result
}
```
Audit logs are immutable — append-only, no delete permission for any role including ADMIN.

## 28.5 Violation Data Access

- Violation snapshots (images) stored in MinIO with presigned URL access — URLs expire in 1 hour
- No direct public URL access to violation images
- Officers can only view violation events from their own jurisdictional zone (zone-scoped query)

---

# 29. Failure & Recovery Design

## 29.1 Detection Pipeline Crash

**Failure:** YOLOv8 inference process crashes mid-session.  
**Detection:** Watchdog process monitors inference subprocess; no-heartbeat timeout of 5 seconds.  
**Recovery:** Watchdog restarts inference subprocess automatically. Session continues from current frame. Violation engine resumes. Gap in detection (during restart) logged as "pipeline gap: {start} to {end}" in session record.  
**Data Impact:** Violations occurring during restart window (~2–5 seconds) may be missed. Logged as known gap.

## 29.2 Database Connection Lost

**Failure:** PostgreSQL connection dropped during active session.  
**Detection:** FastAPI SQLAlchemy connection pool raises OperationalError.  
**Recovery:** Events routed to Redis Streams as temporary buffer. Connection retry with exponential backoff (1s, 2s, 4s, 8s, max 30s). On reconnect, Redis Streams buffer flushed to PostgreSQL in order. Dashboard displays "Database degraded — buffering events" banner.  
**Data Impact:** Up to 30 seconds of events buffered in Redis. If Redis also fails, in-flight events may be lost (extremely rare dual-failure).

## 29.3 WebSocket Connection Drop

**Failure:** WebSocket connection between FastAPI and dashboard client drops.  
**Detection:** Client-side WebSocket onclose event; server-side connection manager removes client.  
**Recovery:** Client attempts reconnection every 3 seconds with exponential backoff up to 30 seconds. On reconnect, client requests "catch-up" payload — server sends last 60 seconds of events and current vehicle positions. Dashboard displays "Reconnecting..." overlay.  
**Data Impact:** Dashboard events missed during disconnection are recovered on reconnect.

## 29.4 Drone Loses GPS

**Failure:** Drone GPS signal lost — altitude and position data unavailable.  
**Detection:** MAVLink telemetry reports GPS fix quality = 0.  
**Recovery:** Last known GPS position held and displayed on twin with "GPS lost" indicator. Geo-projection halted — pixel-to-GPS conversion suspended. Zone-based violations continue using last valid geo-projection matrix (assumes drone hasn't moved significantly). Trajectory-based violations paused. Alert sent to operator view: "GPS signal lost — violation detection degraded."  
**Data Impact:** Violations during GPS loss have reduced geo-accuracy and are tagged "GPS-degraded."

## 29.5 Ground Camera Offline

**Failure:** Triggered ground camera does not respond within expected time window.  
**Detection:** Trigger API call returns timeout or error after 15-second wait.  
**Recovery:** System searches next nearest active camera within range. If no alternative camera available, violation event status set to "unidentified — no camera coverage." Event retained for statistics. Officer notified of coverage gap.  
**Data Impact:** Violation detected but cannot be enforced without plate identification. Counted in violation statistics but no challan generated.

## 29.6 Redis Failure

**Failure:** Redis instance crashes.  
**Detection:** Redis connection pool raises ConnectionError.  
**Recovery:** Live vehicle state temporarily unavailable — digital twin pauses moving vehicles (shows last known positions). Violation events written directly to PostgreSQL (slower but safe). WebSocket fan-out switches to direct broadcast (reduced performance). Redis restart attempted automatically. Alert sent to admin.  
**Data Impact:** Live twin may show stale vehicle positions for up to 60 seconds during Redis restart.

---

# 30. Compliance & Ethics Framework

## 30.1 DGCA Drone Regulations

The system is designed to comply with India's Drone Rules 2021:

- **Altitude enforcement:** System monitors real-time altitude and alerts operator at 110m, flags at 120m
- **No-fly zones:** Patrol zone setup integrates Digital Sky Platform no-fly zone boundaries
- **Operator certification:** System requires drone operator DGCA certification number at session creation
- **Flight logging:** All sessions logged with GPS track, altitude profile, operator ID — available for regulatory audit
- **Remote ID:** System logs drone remote identification data as required by DGCA for drone operations above 250g

## 30.2 IT Act 2000 and DPDP Act 2023

The Digital Personal Data Protection Act 2023 imposes obligations on organisations collecting personal data:

- **Data minimisation:** Only violation-related data is stored. Non-violating vehicle data held in Redis with 30-second TTL and not archived.
- **Purpose limitation:** Collected data used only for traffic enforcement and urban planning — not shared with commercial entities.
- **Retention limits:** Violation event data retained for 2 years maximum, consistent with traffic records standard.
- **Access control:** Data accessible only to authorised enforcement and planning personnel via RBAC.
- **No biometric data:** Facial recognition is explicitly not implemented at any point in the pipeline.

## 30.3 Facial Recognition Avoidance Policy

This system explicitly does not implement facial recognition for any purpose. Vehicle occupant identification is limited to:
- Helmet presence/absence (binary classification — not identity)
- Passenger count on two-wheelers (count — not identity)

No facial embedding, biometric template, or identity inference from face is performed. This is a design principle, not merely an implementation choice, and is documented to protect against future scope creep.

## 30.4 Surveillance Scope Boundaries

The drone camera is directed at road surfaces and traffic only. The system's patrol zone planner includes a buffer constraint that prevents zone boundaries from being drawn over residential areas, private property, or non-road spaces. Camera tilt is constrained to road-facing angles only — tilt commands that would direct the camera toward windows or private spaces are rejected by the operator interface.

## 30.5 Wrongful Enforcement Protection

The 95% confidence threshold and mandatory officer approval gate are both ethics measures, not merely technical ones. They exist to protect vehicle owners from wrongful penalties and to ensure human accountability in every enforcement action. These thresholds are documented as policy requirements — they cannot be lowered without a formal change management process.

---

# 31. Comparison With Existing Systems

## 31.1 Feature Comparison Table

| Feature | Our System | Dubai RTA Drone Enforcement | Hyderabad ITMS | Standard Fixed CCTV |
|---|---|---|---|---|
| Aerial detection | ✓ | ✓ | ✗ | ✗ |
| Dynamic coverage area | ✓ | ✓ | ✗ | ✗ |
| 10 violation types | ✓ | ~3-4 | ~5-6 | ~2-3 |
| Digital twin environment | ✓ | ✗ | Partial | ✗ |
| City planning intelligence layer | ✓ | ✗ | ✗ | ✗ |
| Vehicle ambiguity resolution | ✓ (novel) | Not documented | ✗ | N/A |
| AI-generated planning recommendations | ✓ | ✗ | ✗ | ✗ |
| What-if infrastructure simulator | ✓ | ✗ | ✗ | ✗ |
| CARLA simulation validation | ✓ | ✗ | ✗ | ✗ |
| Open-source stack | ✓ | ✗ (proprietary) | ✗ (proprietary) | Partial |
| Indian legal framework alignment | ✓ | N/A | ✓ | ✓ |
| Dual-role governance dashboards | ✓ | ✗ | ✗ | ✗ |

## 31.2 Academic Literature Comparison

Existing academic work on aerial traffic violation detection (surveyed 2020–2025) shares common limitations that this project addresses:

- Most papers focus on detection accuracy only — no end-to-end enforcement architecture
- None address the vehicle ambiguity problem with a confidence-scoring framework
- None combine aerial detection with urban planning intelligence in a single platform
- Sim-to-real approaches using CARLA for aerial traffic datasets are rare at the BTech level

---

# 32. Innovation Highlights / Novelty Section

This section explicitly identifies the novel contributions of this project distinct from existing work.

## Innovation 1 — Confidence-Weighted Vehicle Identity Threading

**What it is:** A four-factor disambiguation engine that maintains a continuous identity thread between aerial violation detection and ground-level vehicle identification, with a mandatory 95% confidence gate before any enforcement action.

**Why it's novel:** No existing aerial enforcement system publicly documents a mathematically defined disambiguation mechanism. Most systems either do not address the ambiguity problem or resolve it informally. This system makes it a first-class architectural component with a formal confidence model.

## Innovation 2 — CARLA Sim-to-Real Pipeline for Aerial Traffic Violation Detection

**What it is:** Use of CARLA autonomous driving simulator as a synthetic aerial dataset factory, generating labelled training data for 10 violation types from a simulated drone perspective.

**Why it's novel:** CARLA is widely used for ground-level autonomous driving research but rarely for aerial traffic enforcement. Applying it to simulate drone perspective, generate aerial violation datasets, and validate an enforcement pipeline represents a novel application of an existing tool.

## Innovation 3 — Unified Governance Digital Twin for Enforcement + Planning

**What it is:** A single digital twin environment serving two distinct user groups (enforcement officers and city planners) with different operational views of the same underlying spatial data.

**Why it's novel:** Existing traffic digital twin implementations are either pure operational tools (enforcement only) or pure analytical tools (planning only). Unifying both functions in a single platform with role-differentiated views is an architectural contribution.

## Innovation 4 — AI-Powered Infrastructure Recommendation Engine on Historical Violation Data

**What it is:** DBSCAN spatial clustering on historical violation events, mapped through a rule engine to generate specific infrastructure recommendations (signal placement, road redesign, enforcement scheduling), visualised on the digital twin and exportable for government use.

**Why it's novel:** Existing traffic systems collect violation data but do not systematically convert it into urban planning recommendations. Closing this loop — from detection to planning action — is a system-level contribution.

## Innovation 5 — What-If Infrastructure Simulator

**What it is:** Interactive simulator allowing city planners to model proposed infrastructure changes on the digital twin and see projected violation reduction estimates before implementation.

**Why it's novel:** Projection of enforcement outcome from infrastructure change using historical trajectory data is not documented in existing traffic management platforms at this scale.

---

# 33. Deployment Plan

## 33.1 Pilot Deployment Recommendation

For real-world deployment, a phased pilot is recommended:

**Phase 0 — Simulation Validation (Current)**  
Complete CARLA testing, dataset generation, and pipeline validation. No real-world component.

**Phase 1 — Single Zone Pilot**  
Select a single high-violation zone (1km² area). Deploy one drone operator. Connect 2–3 existing CCTV cameras. Run system for 30 days. Collect accuracy benchmarks and false positive rates.

**Phase 2 — Multi-Zone Expansion**  
Expand to 3–5 zones. Add city planner dashboard users. Generate first planning recommendations from 30-day dataset.

**Phase 3 — City-Wide Rollout**  
Full city coverage with drone fleet coordination. Integration with Vahan API for live challan issuance. Government MoU for data sharing.

## 33.2 Hardware Requirements for Production

| Component | Specification |
|---|---|
| **Detection server** | GPU server with NVIDIA A100 or RTX 4090; 64GB RAM; Ubuntu 22.04 |
| **Database server** | 16-core CPU; 64GB RAM; 4TB SSD (violation images); PostgreSQL + PostGIS |
| **Redis server** | 8-core CPU; 32GB RAM; Redis 7 with AOF persistence |
| **Object storage** | MinIO cluster or AWS S3 equivalent; minimum 10TB for 2-year retention |
| **Drone** | DJI Matrice 300 RTX or equivalent with RTK GPS and gimbal-mounted camera |
| **Ground network** | 4G LTE private APN or dedicated WiFi mesh for drone-to-server stream |

## 33.3 Government Onboarding Steps

1. MoU between deploying agency and DGCA for drone operations authorisation
2. Integration agreement with existing CCTV/ANPR camera operators
3. Vahan API access authorisation from NIC/MoRTH
4. Designation of legal authority for AI-assisted challan issuance under MV Act Section 136A
5. Officer training on Police Dashboard (3-day programme recommended)
6. City Planner training on Planning Dashboard (1-day programme recommended)

## 33.4 Cost Estimate — Pilot Deployment

| Component | Estimated Cost (INR) |
|---|---|
| Drone (DJI Matrice 300 RTX) | ₹8,00,000 – ₹12,00,000 |
| Detection GPU server | ₹3,00,000 – ₹5,00,000 |
| Database + Redis server | ₹1,50,000 – ₹2,50,000 |
| Software development (one-time) | Open-source stack — no licensing cost |
| Cloud storage (1 year) | ₹60,000 – ₹1,20,000 |
| Operator training | ₹50,000 |
| **Total pilot estimate** | **₹13,60,000 – ₹21,20,000** |

Compared to a single fixed CCTV installation with ANPR (typically ₹3,00,000–₹5,00,000 per camera with software, coverage limited to one point), this system provides dynamic full-zone coverage at significantly better cost efficiency per square kilometre.

---

# 34. Testing Plan

## 34.1 Unit Tests

| Component | Test Cases |
|---|---|
| YOLOv8 inference | Vehicle detection on known test images; confidence threshold validation |
| DeepSORT tracker | Track ID persistence across 30-frame sequences; occlusion recovery test |
| Geo-projection | Known pixel coordinates → expected GPS within ±2m |
| Violation engine (each type) | Scripted input trajectories → expected violation output per type |
| Confidence scorer | Edge case inputs (all pass, all fail, mixed) → expected score outputs |
| FastAPI endpoints | Each endpoint with valid and invalid inputs; auth token validation |
| RBAC | Each role attempting each endpoint — access granted/denied correctly |

## 34.2 Integration Tests

| Test | Description |
|---|---|
| Pipeline integration | CARLA frame → YOLOv8 → DeepSORT → violation engine → database → WebSocket → dashboard |
| Camera trigger flow | Violation event → nearest camera query → trigger API → ANPR → plate link |
| Challan generation | Plate linked → Vahan query (simulated) → challan draft → officer approval |
| Twin sync | Violation event created in database → appears on CesiumJS twin within 500ms |
| DBSCAN recommendation | 50+ violation events seeded → recommendation generated → appears on planner dashboard |

## 34.3 CARLA Simulation Test Cases

One test script per violation type, run 100 times each with variation:

| Test ID | Violation | Variation |
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
| SIM-EDGE01 | Ambiguity | Two identical vehicles near violation |
| SIM-EDGE02 | Track loss | Vehicle passes under bridge during violation |
| SIM-EDGE03 | High density | 80+ vehicles in frame simultaneously |

## 34.4 Performance Tests

| Test | Method | Target |
|---|---|---|
| API load test | Locust — 100 concurrent users; all endpoints | P95 response ≤ 200ms |
| WebSocket load | 50 concurrent WebSocket clients; violation broadcast | Message delivery ≤ 500ms |
| Detection throughput | 1920×1080 frame at 20 FPS sustained | ≥ 10 FPS inference |
| Database query | Violation heatmap query over 100,000 events | Response ≤ 2 seconds |

## 34.5 User Acceptance Testing

| User | Test Scenario | Pass Criteria |
|---|---|---|
| Mock Police Officer | Receive alert, navigate to location, confirm violation, submit challan | All steps completable within 2 minutes on tablet |
| Mock City Planner | Generate heatmap, read AI recommendation, run what-if, export report | All steps completable without training beyond user guide |
| Mock Drone Operator | Start session, see detection active, receive altitude warning, end session | Session lifecycle completable without assistance |

---

# 35. Risk Register

| ID | Risk | Category | Likelihood | Impact | Mitigation |
|---|---|---|---|---|---|
| R01 | YOLOv8 mAP below target on real drone footage after CARLA training | Technical | Medium | High | Fine-tuning on real footage; augmentation during training; IDD dataset supplement |
| R02 | DeepSORT ID switch rate too high in dense traffic | Technical | Medium | High | ByteTrack as fallback tracker; raised confidence threshold at high density |
| R03 | CARLA performance insufficient on RTX 4060 laptop for dataset generation | Technical | Low | Medium | Medium render quality; reduce traffic density; use CARLA headless mode |
| R04 | Ground camera ANPR accuracy below 80% for Indian plates | Technical | Medium | Medium | PaddleOCR fine-tuned on Indian plates; Super-Resolution pre-processing |
| R05 | GPS projection error exceeds 2m target | Technical | Low | Medium | RTK GPS for production; Kalman smoothing; zone buffer margin |
| R06 | DGCA regulations change mid-project | Operational | Low | High | System architecture supports any altitude limit; configurable threshold |
| R07 | Ground cameras not available or not ANPR-capable in pilot zone | Operational | Medium | High | Document as limitation; Super-Resolution ANPR from drone as partial fallback |
| R08 | Vahan API access not obtainable for academic prototype | Operational | High | Medium | Vahan API simulated in prototype; real access required only for production |
| R09 | False positive rate exceeds 15% for one or more violation types | Technical | Medium | Medium | Per-type thresholds adjusted; additional training data for underperforming types |
| R10 | Wrongful vehicle identification causes wrongful challan | Legal | Low | Critical | 95% threshold gate + mandatory officer approval is absolute defence; never automate |
| R11 | DPDP Act compliance breach due to unintended data retention | Legal | Low | High | TTL enforcement on Redis; no long-term storage of non-violating vehicle data |
| R12 | System performance degradation under high concurrent users | Technical | Low | Medium | Load testing before deployment; Redis Pub/Sub for fan-out reduces DB load |
| R13 | Team capacity insufficient for all 35 sections of scope | Project | Medium | Medium | Prioritise: detection pipeline + digital twin + police dashboard as core deliverables |
| R14 | Weather events (monsoon) degrading drone footage quality during testing | Operational | High (India) | Low | Flagging mechanism implemented; test in good conditions; document limitation |
| R15 | CesiumJS rendering performance poor on low-spec dashboard devices | Technical | Medium | Low | Fallback to Mapbox 2D mode for low-spec clients; configurable render quality |

---

*End of Document*

---

**Document Version:** 1.0.0  
**Total Sections:** 35  
**Status:** Complete — Ready for Project Submission
