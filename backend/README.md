# backend/

FastAPI service (Build Plan **M5**, PRD Sections 13–14, 28). Not built yet.

- **API:** events, evidence, review (confirm / dismiss with a note), configuration and profiles, scenes / sites, sessions, statistics, exports (PDF, Excel, GeoJSON).
- **Storage:** PostGIS (events, tracks, zones, configs, recommendations, planner history), Redis (live vehicle state), an S3 store for clips, snapshots and reports (SeaweedFS on `http://localhost:9000`, used in place of MinIO; credentials in `infra/s3/s3.json`; use path-style addressing). All three come from the root `docker-compose.yml`.
- **Live:** WebSockets for vehicles (10 Hz) and new events.
- **Access:** JWT with the PRD roles OFFICER, OPERATOR, PLANNER, MAINTENANCE, ADMIN.

Contracts it must keep:
- Events: `schemas/event.schema.json` (what `ml/violation_engine` writes to `violations.json`).
- Profiles: `schemas/profile.schema.json`; seed profiles in `ml/violation_engine/configs/profiles/`.
- Conditions: `schemas/conditions.json`.
