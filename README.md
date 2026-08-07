# Autonomous Traffic Violation Detection and Urban Planning System

UAV aerial footage + Digital Twin platform for traffic violation detection and urban planning intelligence. Full spec in [docs/Product_Requirements_Document.md](docs/Product_Requirements_Document.md).

## Structure

| Folder | Contents |
|---|---|
| `ml/` | Detection (YOLOv8), tracking (DeepSORT), violation rule engine, geo-projection |
| `simulation/` | CARLA scripts for synthetic dataset generation and violation scenario scripting |
| `docs/` | Project documentation (PRD, etc.) |

Backend (FastAPI), frontend (React/Next.js + CesiumJS), and infra (Docker Compose) will be scaffolded later — current focus is the ML/detection pipeline.

## Getting Started

Setup instructions per component live in each folder's own README.
