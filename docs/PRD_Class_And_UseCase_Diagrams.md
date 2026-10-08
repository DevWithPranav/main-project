# Class Diagram & Use Case Diagram — Autonomous Traffic Violation Detection & Urban Planning System

Derived from [Product_Requirements_Document.md](Product_Requirements_Document.md) (System Architecture §10, Database Design §13, API Design §14, User Personas §7, Usage Scenarios §8, Objective-wise Solutions §6). These describe the full target platform the PRD specifies — a superset of what's currently implemented under `ml/`/`simulation/` (see [DFD_Diagrams.md](DFD_Diagrams.md) for the as-built pipeline).

---

## Class Diagram

Domain entities are drawn from the PostGIS schema (§13.1); pipeline/engine classes are drawn from the architecture layers (§10) and objective-wise technical approaches (§6). Dashboards map to the role-based presentation layer (§10.2, §12).

```mermaid
classDiagram
    class User {
        +UUID id
        +String username
        +String passwordHash
        +String role
        +String fullName
        +String badgeNumber
        +Boolean isActive
        +authenticate(password) Boolean
        +hasRole(role) Boolean
    }

    class DroneSession {
        +UUID id
        +UUID operatorId
        +DateTime startTime
        +DateTime endTime
        +Polygon patrolZone
        +Float maxAltitude
        +String status
        +start() void
        +end() void
        +checkAltitudeCompliance(altitude) Boolean
    }

    class Zone {
        +UUID id
        +String name
        +String zoneType
        +Polygon geometry
        +Integer speedLimit
        +Float permittedDir
        +Integer gracePeriod
        +Boolean isActive
        +containsPoint(point) Boolean
    }

    class VehicleTrack {
        +UUID id
        +Integer trackId
        +UUID sessionId
        +LineString trajectory
        +DateTime startTime
        +DateTime endTime
        +String vehicleClass
        +Float[] appearanceVec
        +addPosition(point, timestamp) void
        +computeVelocity() Float
        +computeHeading() Float
    }

    class ViolationEvent {
        +UUID id
        +Integer trackId
        +String violationType
        +UUID sessionId
        +Point location
        +UUID zoneId
        +Float confidence
        +String status
        +String snapshotUrl
        +Float velocity
        +Float heading
        +String plateNumber
        +UUID challanId
        +DateTime flaggedAt
        +DateTime resolvedAt
        +UUID officerId
        +flag() void
        +resolve(officerId) void
        +linkPlate(plateNumber) void
    }

    class Camera {
        +UUID id
        +String name
        +Point location
        +Boolean hasAnpr
        +String apiEndpoint
        +Integer coverageRadius
        +Boolean isActive
        +trigger(eventId, expectedArrival) void
    }

    class Recommendation {
        +UUID id
        +Integer clusterId
        +String recType
        +Polygon geometry
        +String description
        +JSON supportingData
        +Float projectedReduction
        +String status
        +accept() void
        +reject() void
    }

    class Challan {
        +UUID id
        +UUID violationEventId
        +String plateNumber
        +Float amount
        +String status
        +generateDraft() void
        +submit(officerId) void
    }

    class DetectionEngine {
        +Model yoloModel
        +detect(frame) List~Detection~
    }

    class TrackerEngine {
        +track(detections) List~VehicleTrack~
        +predictOcclusion(track) Point
    }

    class GeoProjectionEngine {
        +computeGSD(altitude, sensorWidth, focalLength, imageWidth) Float
        +pixelToGPS(pixel, telemetry) Point
    }

    class ViolationRuleEngine {
        +evaluate(track, zone) ViolationEvent
    }

    class AnomalyEngine {
        +evaluate(track) ViolationEvent
    }

    class ConfidenceScorer {
        +threshold Float
        +score(event) Float
        +passesThreshold(event) Boolean
    }

    class IdentityThreadingEngine {
        +disambiguate(track, cameraCapture) Float
        +predictArrival(track, camera) DateTime
    }

    class RecommendationEngine {
        +epsMeters Float
        +minSamples Integer
        +cluster(events) List~Recommendation~
    }

    class DigitalTwin {
        +updateVehicle(track) void
        +renderZone(zone) void
        +renderViolation(event) void
        +renderRecommendation(rec) void
    }

    class Dashboard {
        <<abstract>>
        +user User
        +render() void
    }
    class PoliceDashboard
    class CityPlannerDashboard
    class AdminPanel
    class DroneOperatorView

    Dashboard <|-- PoliceDashboard
    Dashboard <|-- CityPlannerDashboard
    Dashboard <|-- AdminPanel
    Dashboard <|-- DroneOperatorView

    User "1" --> "0..*" DroneSession : operates
    User "1" --> "0..*" ViolationEvent : reviews (officer)
    DroneSession "1" --> "0..*" VehicleTrack : produces
    VehicleTrack "1" --> "0..*" ViolationEvent : triggers
    Zone "1" --> "0..*" ViolationEvent : contains
    Camera "1" --> "0..*" ViolationEvent : identifies
    ViolationEvent "1" --> "0..1" Challan : generates
    RecommendationEngine --> Recommendation : produces
    DigitalTwin --> Zone : renders
    DigitalTwin --> ViolationEvent : renders
    DigitalTwin --> VehicleTrack : renders

    DetectionEngine --> TrackerEngine : detections
    TrackerEngine --> GeoProjectionEngine : tracks
    GeoProjectionEngine --> ViolationRuleEngine : geo-tracks
    GeoProjectionEngine --> AnomalyEngine : geo-tracks
    ViolationRuleEngine --> ConfidenceScorer : candidate event
    AnomalyEngine --> ConfidenceScorer : candidate event
    ConfidenceScorer --> ViolationEvent : flags
    ViolationEvent --> IdentityThreadingEngine : requests ID
    IdentityThreadingEngine --> Camera : triggers
    IdentityThreadingEngine --> Challan : enables draft

    PoliceDashboard --> DigitalTwin : embeds
    CityPlannerDashboard --> DigitalTwin : embeds
    CityPlannerDashboard --> RecommendationEngine : requests
```

---

## Use Case Diagram

Actors from §7 (User Personas); use cases synthesized from §8 (Usage Scenarios) and the role column of §14.1 (API Design).

```mermaid
flowchart LR
    Officer([Traffic Police Officer])
    ControlRoom([Control Room Operator])
    Planner([City Planner])
    Admin([System Administrator])
    DroneOp([Drone Operator])
    ANPR([Ground CCTV / ANPR System])
    Vahan([Vahan API\nGovernment])

    subgraph SYS["Autonomous Traffic Violation Detection & Governance Platform"]
        direction TB
        UC1(["View Live Violation Alerts"])
        UC2(["Navigate to Violation Location"])
        UC3(["Confirm Violation On-Site"])
        UC4(["Review & Submit Challan"])
        UC5(["Dispatch Officer to Violation"])
        UC6(["View Command Mode\nFull Twin"])
        UC7(["View Historical Heatmap"])
        UC8(["Analyze Violation Clusters"])
        UC9(["View AI Recommendations"])
        UC10(["Run What-If Simulation"])
        UC11(["Export Planning Report"])
        UC12(["Launch Drone Patrol Session"])
        UC13(["Monitor Feed &\nDetection Status"])
        UC14(["Monitor Altitude\nCompliance"])
        UC15(["Manage Users"])
        UC16(["Manage Zones"])
        UC17(["Manage Camera Registry"])
        UC18(["Monitor System Health"])
        UC19(["Generate Challan Draft"])
        UC20(["Trigger Ground Camera\nCapture"])
        UC21(["Issue Challan"])
    end

    Officer --> UC1
    Officer --> UC2
    Officer --> UC3
    Officer --> UC4

    ControlRoom --> UC6
    ControlRoom --> UC1
    ControlRoom --> UC5

    Planner --> UC7
    Planner --> UC8
    Planner --> UC9
    Planner --> UC10
    Planner --> UC11

    Admin --> UC15
    Admin --> UC16
    Admin --> UC17
    Admin --> UC18

    DroneOp --> UC12
    DroneOp --> UC13
    DroneOp --> UC14

    UC4 -.include.-> UC19
    UC19 -.include.-> UC20
    UC20 -.include.-> ANPR
    UC4 -.include.-> UC21
    UC21 -.include.-> Vahan
    UC9 -.include.-> UC8
```

---

## Notes

- **Class diagram** entities (`User`, `DroneSession`, `Zone`, `VehicleTrack`, `ViolationEvent`, `Camera`, `Recommendation`) map 1:1 to the PostgreSQL/PostGIS tables in PRD §13.1; `Challan` is implied by `violation_events.challan_id` and the challan lifecycle in §11.2 but has no dedicated table in the current schema draft, so it's modeled here as its own class for clarity. Engine classes (`DetectionEngine` → `TrackerEngine` → `GeoProjectionEngine` → `ViolationRuleEngine`/`AnomalyEngine` → `ConfidenceScorer` → `IdentityThreadingEngine`) mirror the pipeline layers in §10.1/§10.2 and the per-objective technical approaches in §6.
- **Use case diagram** actors are the five personas in §7. `UC19`–`UC21` (challan draft generation, ground camera trigger, challan issuance) are modeled as system-internal use cases included from `UC4 Review & Submit Challan`, per the violation event lifecycle in §11.2 and the external integrations in §14.3 (`ANPR` = Ground CCTV Trigger API, `Vahan` = Vahan API).
- This PRD describes a considerably larger platform (dual dashboards, digital twin, hybrid enforcement, DBSCAN planning engine, full backend/DB stack) than what's currently implemented — see [First_Phase_Plan.md](First_Phase_Plan.md) and [DFD_Diagrams.md](DFD_Diagrams.md) for the as-built Phase 1 scope (detection + tracking + violation-flagging on aerial footage, no backend/dashboard/digital twin yet).
