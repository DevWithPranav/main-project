# schemas/

Shared contracts between the violation engine, the backend and the dashboards (Build Plan M0).

| File | What |
|---|---|
| `event.schema.json` | One violation or road-surface anomaly event. `kind` defaults to `violation`, so older `violations.json` files still validate |
| `profile.schema.json` | A configuration profile: types and conditions on/off, thresholds, model, modules, road configuration |
| `scene.schema.json` | A lane map with its road features (Build Plan M1): what the engine, the 3D twin and the backend read |
| `conditions.json` | The 34 conditions of `docs/Expected_Output.md` Section 4.2, their engine type, status, and how an event is matched to one |

Check files (main `venv`):

```powershell
python ml/violation_engine/schemas.py events <violations.json> [...]
python ml/violation_engine/profiles.py check ml/violation_engine/configs/profiles/*.json
```

Tests: `ml/violation_engine/tests/test_schemas.py`. When you change a schema, bump its `version` and update the engine and backend in the same change.
