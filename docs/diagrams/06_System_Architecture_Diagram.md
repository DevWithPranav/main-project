# Full System Architecture Diagram
## Autonomous Aerial Surveillance Framework for Traffic Anomaly Detection and Digital Twin-Based Urban Planning

> Companion diagram set for [Product_Requirements_Document.md](../Product_Requirements_Document.md).
> Consolidates the whole tech stack (PRD [Section 12](../Product_Requirements_Document.md#12-tech-stack)) into one picture: aerial acquisition → AI/ML processing → violation & anomaly detection → backend → analysis/planning → digital twin & dashboards.

---

## Scope

Unlike the other diagrams in this set, this one is intentionally the "full picture" — it's meant to be the single diagram that shows the complete technical path from a raw drone frame to a planner making a decision. To keep it readable despite covering more ground, it's organized into **six layers**, each its own subgraph, with data flowing top to bottom.

---

## Architecture Diagram

```mermaid
flowchart TD
    subgraph L1[Layer 1 - Aerial Data Acquisition]
        DRONE[Autonomous Drone / CARLA<br/>RTSP + MAVLink Telemetry]
        SIG[Traffic Signal<br/>Controller Feed]
    end

    subgraph L2[Layer 2 - AI / ML Detection Pipeline]
        DET[YOLOv26l<br/>Vehicle Detection]
        TRK[DeepSORT<br/>Tracking]
        SEG[YOLOv26l-seg<br/>Road Surface Segmentation]
        GEO[Geo-Projection Engine<br/>Pixel to GPS]
    end

    subgraph L3[Layer 3 - Violation and Anomaly Engine]
        VRULE[Violation Rule Engine<br/>+ Confidence Scorer]
        ARULE[Anomaly Classifier<br/>+ Severity Scorer + Dedup]
    end

    subgraph L4[Layer 4 - Backend Infrastructure]
        API[FastAPI<br/>REST + WebSocket]
        DB[(PostgreSQL + PostGIS)]
        CACHE[(Redis - live state)]
        STORE[(MinIO - snapshots & reports)]
    end

    subgraph L5[Layer 5 - Analysis, Reporting and Planning]
        CLUSTER[DBSCAN<br/>Hotspot Clustering]
        REC[Recommendation<br/>Engine]
        SIM[What-If<br/>Scenario Simulator]
        RPT[Report<br/>Generator]
    end

    subgraph L6[Layer 6 - Digital Twin and Dashboards]
        TWIN[CesiumJS<br/>Digital Twin]
        MDASH[Monitoring<br/>Dashboard]
        PDASH[Urban Planning<br/>Dashboard]
    end

    MON[Monitoring Authority]
    PLN[Planner & Maintenance Team]
    OPS[System Operations]

    DRONE --> DET
    DRONE --> SEG
    SIG --> VRULE
    DET --> TRK --> GEO --> VRULE
    SEG --> GEO
    GEO --> ARULE

    VRULE --> API
    ARULE --> API
    API --> DB
    API --> CACHE
    API --> STORE

    DB --> CLUSTER --> REC
    DB --> SIM
    DB --> RPT

    CACHE --> TWIN
    DB --> TWIN
    REC --> TWIN
    TWIN --> MDASH
    TWIN --> PDASH

    MDASH <--> MON
    PDASH <--> PLN
    OPS <--> API
```

---

## Layer-by-Layer Summary

| Layer | Components | Role |
|---|---|---|
| 1 — Aerial Data Acquisition | Autonomous Drone / CARLA, Traffic Signal Feed | Supplies raw frames, telemetry, and signal state |
| 2 — AI / ML Detection Pipeline | YOLOv26l, DeepSORT, YOLOv26l-seg, Geo-Projection Engine | Turns raw frames into tracked vehicles and segmented road defects with real-world GPS coordinates |
| 3 — Violation & Anomaly Engine | Violation Rule Engine, Anomaly Classifier | Converts tracked/segmented output into scored, structured events |
| 4 — Backend Infrastructure | FastAPI, PostgreSQL + PostGIS, Redis, MinIO | Persists, caches, and serves all system data over REST/WebSocket |
| 5 — Analysis, Reporting & Planning | DBSCAN, Recommendation Engine, What-If Simulator, Report Generator | Turns historical data into hotspots, recommendations, projections, and reports |
| 6 — Digital Twin & Dashboards | CesiumJS Twin, Monitoring Dashboard, Urban Planning Dashboard | Where every pillar's output is actually seen and acted on |

---

## Diagram Index

| # | Diagram | File | Status |
|---|---|---|---|
| 1 | Use Case Diagram set (4 files) | `01_Use_Case_Diagram*.md` | ✅ Done |
| 2 | Class Diagram set (4 files) | `02_Class_Diagram*.md` | ✅ Done |
| 3 | DFD Level 0 — Context Diagram | `03_DFD_Level_0.md` | ✅ Done |
| 4 | DFD Level 1 — Major Processes | `04_DFD_Level_1.md` | ✅ Done |
| 5 | DFD Level 2 — Subprocess Detail | `05_DFD_Level_2.md` | ✅ Done |
| 6 | Full System Architecture Diagram | `06_System_Architecture_Diagram.md` | ✅ Done |
