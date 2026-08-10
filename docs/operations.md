# Operations

Local service boundaries:

- Router: `127.0.0.1:8765`
- Data Hub: `127.0.0.1:8766`
- Finance MCP: local stdio by default

Commands:

```powershell
scripts\start_hermes_opc.ps1
scripts\status_hermes_opc.ps1
scripts\health_hermes_opc.ps1
scripts\doctor_hermes_opc.ps1
scripts\stop_hermes_opc.ps1
```

Health checks database read-only access, migrations 0100–0111, environment-file existence,
provider booleans, disk, snapshot freshness, Data Hub reachability, fundamental coverage, WeCom
configuration, and the no-live boundary. Status values are `HEALTHY`, `DEGRADED`, `NOT_READY`,
and `FAILED`.

Doctor only prints advice. It never edits configuration, starts a backfill, deletes data, runs a
migration, or changes Git state.
