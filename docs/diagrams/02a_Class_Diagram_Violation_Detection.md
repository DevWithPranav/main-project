# Class Diagram — Pillar 1: Traffic Anomaly & Violation Detection
## Autonomous Aerial Surveillance Framework for Traffic Anomaly Detection and Digital Twin-Based Urban Planning

> Part of the [Class Diagram set](02_Class_Diagram.md) · Companion to [Product_Requirements_Document.md](../Product_Requirements_Document.md)
> Uses Mermaid's native `classDiagram` syntax. Classes marked `<<shared>>` are defined in full in the [master overview](02_Class_Diagram.md).

---

## Scope

The complete class model behind turning a raw aerial frame into a reviewed `ViolationEvent` — detection (`VehicleDetector`, using **YOLOv26l**), tracking (`VehicleTracker`, DeepSORT), rule evaluation, and confidence scoring.

---

## Class Diagram

```mermaid
classDiagram
    class DroneSession {
        <<shared - see master>>
    }
    class Zone {
        <<shared - see master>>
    }
    class GeoProjector {
        <<shared - see master>>
    }
    class AlertService {
        <<shared - see master>>
    }
    class User {
        <<shared - see master>>
    }

    class VehicleDetector {
        +String model = "YOLOv26l"
        +Float confidenceThreshold
        +detect(frame) Detection[]
    }

    class Detection {
        +BoundingBox box
        +String vehicleClass
        +Float confidence
    }

    class VehicleTracker {
        +String trackerType = "DeepSORT"
        +track(detections) TrackedVehicle[]
    }

    class TrackedVehicle {
        +Integer trackId
        +LineString trajectory
        +String vehicleClass
        +Float[] appearanceVector
        +Float speed
        +Float heading
        +updatePosition(point)
        +predictNext() Point
    }

    class ViolationRuleEngine {
        +RuleSet rules
        +evaluate(track, zone) ViolationCandidate
    }

    class ViolationCandidate {
        +String violationType
        +TrackedVehicle track
        +Zone zone
    }

    class ConfidenceScorer {
        +Map weightFactors
        +score(candidate) Float
    }

    class ViolationEvent {
        +UUID id
        +Integer trackId
        +String violationType
        +Point location
        +Float confidence
        +String status
        +String snapshotUrl
        +Float velocity
        +Float heading
        +DateTime flaggedAt
        +DateTime resolvedAt
        +flag()
        +review(user, note)
        +dispatch()
        +resolve()
    }

    DroneSession "1" --> "*" VehicleDetector : streams frames to
    VehicleDetector "1" --> "*" Detection : produces
    Detection "*" --> "1" VehicleTracker : consumed by
    VehicleTracker "1" --> "*" TrackedVehicle : maintains
    TrackedVehicle "1" --> "1" GeoProjector : position projected via
    TrackedVehicle "1" --> "1" ViolationRuleEngine : evaluated by
    Zone "1" --> "*" ViolationRuleEngine : supplies rules to
    ViolationRuleEngine "1" --> "*" ViolationCandidate : produces
    ViolationCandidate "1" --> "1" ConfidenceScorer : scored by
    ConfidenceScorer "1" --> "*" ViolationEvent : produces
    ViolationEvent "1" ..> AlertService : triggers
    User "1" --> "*" ViolationEvent : reviews
```

---

## Class Details

| Class | Purpose |
|---|---|
| `VehicleDetector` | Wraps YOLOv26l — runs inference on each incoming frame, returns raw detections |
| `Detection` | One detected object in a single frame: bounding box, class, confidence |
| `VehicleTracker` | Wraps DeepSORT — assigns and maintains a Track ID across frames using appearance + Kalman filter prediction |
| `TrackedVehicle` | A vehicle's running state across frames: trajectory, class, speed, heading, appearance embedding |
| `ViolationRuleEngine` | Evaluates a tracked vehicle's trajectory against the 10 violation rules (PRD Section 9.1), scoped by the zone it's in |
| `ViolationCandidate` | An unscored match against a violation rule, pending confidence scoring |
| `ConfidenceScorer` | Computes the final confidence score for a candidate |
| `ViolationEvent` | The persisted, reviewable record — everything downstream (alerts, twin, reports) reads from here |

---

## Related Diagrams

| Diagram | Relationship |
|---|---|
| [Master Class Diagram](02_Class_Diagram.md) | Shared classes (`Zone`, `DroneSession`, `GeoProjector`, `AlertService`, `User`) referenced here in full |
| [Digital Twin Class Diagram](02b_Class_Diagram_Digital_Twin.md) | `ViolationEvent` is visualized there |
| [Violation Detection Use Case Diagram](01a_Use_Case_Diagram_Violation_Detection.md) | Behavioural counterpart to this structural view |
