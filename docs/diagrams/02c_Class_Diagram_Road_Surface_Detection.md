# Class Diagram — Pillar 2: Road Surface Detection
## Autonomous Aerial Surveillance Framework for Traffic Anomaly Detection and Digital Twin-Based Urban Planning

> Part of the [Class Diagram set](02_Class_Diagram.md) · Companion to [Product_Requirements_Document.md](../Product_Requirements_Document.md)
> Uses Mermaid's native `classDiagram` syntax. Classes marked `<<shared>>` are defined in full in the [master overview](02_Class_Diagram.md).

---

## Scope

The complete class model behind turning a raw aerial frame into a severity-ranked, maintenance-ready `RoadAnomaly` record — segmentation (`RoadSurfaceSegmenter`, using **YOLOv26l-seg**), classification, severity scoring, and deduplication across patrol sessions.

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

    class RoadSurfaceSegmenter {
        +String model = "YOLOv26l-seg"
        +segment(frame) DefectMask[]
    }

    class DefectMask {
        +Polygon maskRegion
        +Float pixelArea
        +Float confidence
    }

    class AnomalyClassifier {
        +List~String~ classes = "pothole, crack, waterlogging, debris"
        +classify(mask) String
    }

    class SeverityScorer {
        +Map classWeights
        +score(mask, area, gsd) Float
    }

    class DeduplicationEngine {
        +Float proximityRadius = 3.0
        +isDuplicate(location, type) Boolean
        +findExisting(location, type) RoadAnomaly
    }

    class RoadAnomaly {
        +UUID id
        +String anomalyType
        +Point location
        +Float severityScore
        +String severityBand
        +Float areaSqM
        +String status
        +String snapshotUrl
        +DateTime firstDetectedAt
        +DateTime lastSeenAt
        +Integer recurrenceCount
        +flag()
        +updateRecurrence()
        +markWorkOrderIssued()
        +markRepaired()
    }

    DroneSession "1" --> "*" RoadSurfaceSegmenter : streams frames to
    RoadSurfaceSegmenter "1" --> "*" DefectMask : produces
    DefectMask "1" --> "1" AnomalyClassifier : classified by
    DefectMask "1" --> "1" GeoProjector : centroid projected via
    AnomalyClassifier "1" --> "1" SeverityScorer : type feeds
    SeverityScorer "1" --> "1" DeduplicationEngine : scored defect checked by
    Zone "1" --> "*" DeduplicationEngine : scopes
    DeduplicationEngine "1" --> "*" RoadAnomaly : creates or updates
    RoadAnomaly "1" ..> AlertService : triggers (new defect)
    User "1" --> "*" RoadAnomaly : reviews / updates status
```

---

## Class Details

| Class | Purpose |
|---|---|
| `RoadSurfaceSegmenter` | Wraps YOLOv26l-seg — produces a pixel-level defect mask per frame |
| `DefectMask` | One segmented region in a single frame: shape, area, confidence |
| `AnomalyClassifier` | Classifies a defect mask into one of the four anomaly categories (PRD Section 9.2) |
| `SeverityScorer` | Computes a severity score from defect area (via Ground Sampling Distance) and class weighting |
| `DeduplicationEngine` | Checks a scored, geo-projected defect against the existing inventory using GPS proximity, so the same pothole isn't re-logged every patrol pass |
| `RoadAnomaly` | The persisted, reviewable record — tracks severity trend and recurrence over time, drives the maintenance work-order flow |

---

## Related Diagrams

| Diagram | Relationship |
|---|---|
| [Master Class Diagram](02_Class_Diagram.md) | Shared classes (`Zone`, `DroneSession`, `GeoProjector`, `AlertService`, `User`) referenced here in full |
| [Digital Twin Class Diagram](02b_Class_Diagram_Digital_Twin.md) | `RoadAnomaly` is visualized there |
| [Road Surface Detection Use Case Diagram](01c_Use_Case_Diagram_Road_Surface_Detection.md) | Behavioural counterpart to this structural view |
