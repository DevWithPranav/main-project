# frontend/: Configuration Dashboard (Build Plan M6)

Vite + React 19 + TypeScript, Mantine (UI + charts), TanStack Query, React Router. Talks to the
backend through the contract in `backend/API.md` (Vite proxies `/api` and the WebSocket to :8000).

```powershell
cd frontend
npm install            # once
npm run dev            # http://localhost:5173, needs the backend on :8000
npm run dev:mock       # no backend: in-browser mock API with generated data
npm run build          # type-check + production build into dist/
npm test               # vitest
```

Log in with a dev user (`officer`, `operator`, `planner`, `maintenance`, `admin`; password = name + `123`).
The UI hides actions a role can't do; the backend enforces them.

| Page | What |
|---|---|
| Live monitoring | Vehicles on the lane map (green ok / amber checking / red flagged), event feed, system health. **Live** = WS `/api/ws/live`; **Replay** = an imported session's trajectories with events at their flag time |
| Violations | Filters (session, kind, type, condition, status, review; kept in the URL), table, detail drawer with evidence clip, values, review (confirm/dismiss + note) |
| Statistics | Counts by type / condition / status / hour, event density on the map, hotspot ranking; exports PDF/XLSX/CSV/GeoJSON with the same filters |
| Sessions | Imported flights; import a processed flight |
| Configuration | Profiles: violations on/off + thresholds, conditions, model/modules/road JSON; validated against `schemas/profile.schema.json` before saving a new version with a note; history. Road configuration: lane map coloured by road features, lane attributes on click |
| Recommendations | M8 recommendations with every field, planner decision (accept/reject/modify + rationale), planner history |

Code: `src/api/` (typed client, mock), `src/lib/` (filters, formatting, permissions, profiles, stats), `src/components/` (LaneMap SVG, EventDrawer, FilterBar), `src/pages/`.
