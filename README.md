# PBS MCP Server

MCP-Server fuer den Proxmox Backup Server (PBS): Status und Diagnose lesend, dazu Verify/GC starten und (gesperrt, nur mit Freigabe) Snapshots loeschen. Gleicher Aufbau wie zammad-mcp: laeuft ausschliesslich als Docker-Container (Streamable HTTP) in der Cloud hinter Reverse-Proxy, ein lokaler Betrieb (stdio/uvx) ist nicht vorgesehen, Schutz per statischem Bearer-Token (`MCP_AUTH_TOKEN`), Image per GitHub Actions nach ghcr.io.

Schreibende Aktionen sind gesichert: `MCP_READONLY=true` sperrt sie alle, Verify/GC starten braucht `confirm=true` pro Aufruf (ohne confirm nur Vorschau), Loeschen geht nur mit `MCP_ALLOW_DELETE=true` am Server und `confirm=true` pro Aufruf (ohne confirm nur Vorschau). Neue Backups erzeugt der PBS nicht selbst, das loest der Proxmox VE aus.

## Tools

Lesend:

| Tool | Beschreibung |
|------|--------------|
| `get_version` | Version des laufenden MCP-Servers (Redeploy-Kontrolle) |
| `server_version` | Verbindungstest, PBS-Version |
| `datastore_list` | Datastores mit Belegung, GC-Status (ohne Belegungshistorie) |
| `namespace_list` | Namespaces eines Datastores |
| `group_list` | Backup-Gruppen (vm/ct/host) mit letztem Backup |
| `snapshot_list` | Snapshots inkl. Verify-Status (Filter: ns, backup_type, backup_id) |
| `verify_failed_list` | Alle Snapshots mit Verify "failed", gruppiert nach vm/ID |
| `verify_job_list` | Verify-Jobs mit letztem/naechstem Lauf |
| `task_list` | Tasks (Filter: Fehler, laufend, Typ, Store, Zeitraum) |
| `task_status` | Status eines Tasks per UPID |
| `task_log` | Log eines Tasks per UPID |
| `disk_list` | Physische Disks inkl. SMART-Status |
| `disk_smart` | SMART-Werte einer Disk |
| `zfs_list` | ZFS-Pools mit Health |
| `zfs_status` | Detailstatus eines ZFS-Pools (wie `zpool status`) |
| `journal` | System-Journal des PBS |

Schreibend:

| Tool | Beschreibung |
|------|--------------|
| `verify_start` | Verify starten (ganzer Store oder eingegrenzt per ns/Gruppe/Snapshot), Vorschau ohne `confirm` |
| `verify_job_run` | Konfigurierten Verify-Job sofort starten, Vorschau ohne `confirm` |
| `gc_start` | Garbage Collection starten, Vorschau ohne `confirm` |
| `snapshot_forget` | Snapshot unwiderruflich loeschen (nur mit `MCP_ALLOW_DELETE=true`, Vorschau ohne `confirm`) |

## Umgebungsvariablen

| Variable | Pflicht | Standard | Beschreibung |
|---|---|---|---|
| `PBS_URL` | ja | - | z.B. `https://pbs.example.com:8007` |
| `PBS_TOKEN_ID` | ja | - | z.B. `mcp@pbs!claude` |
| `PBS_TOKEN_SECRET` | ja | - | Secret des API-Tokens |
| `PBS_VERIFY_SSL` | nein | `true` | `true`, `false` oder Pfad zu einer CA-Datei (PBS hat oft ein selbstsigniertes Zertifikat) |
| `MCP_READONLY` | nein | `false` | `true` sperrt alle schreibenden Tools |
| `MCP_ALLOW_DELETE` | nein | `false` | `true` erlaubt `snapshot_forget` (zusaetzlich `confirm=true` pro Aufruf) |
| `MCP_AUTH_TOKEN` | ja | - | Statisches Bearer-Token (mind. 32 Zeichen), ohne startet der Server nicht |
| `MCP_HOST` / `MCP_PORT` | nein | `0.0.0.0` / `8000` | Bind im Container |
| `MCP_BIND_ADDR` / `MCP_HOST_PORT` | nein | `127.0.0.1` / `8423` | Port-Mapping auf dem Docker-Host. Standard nur localhost; laeuft der Reverse-Proxy auf einem anderen Host, die LAN-IP des Docker-Hosts setzen |

## 1. API-Token in PBS anlegen

1. Access Control -> User: eigenen User anlegen (z.B. `mcp@pbs`)
2. Access Control -> API Token: Token `claude` fuer diesen User anlegen, Secret notieren (wird nur einmal angezeigt)
3. Access Control -> Permissions: **sowohl dem Token als auch dem User** (bei API-Tokens gilt die Schnittmenge beider Rechte) geben:
   - Rolle `Audit` auf Pfad `/` mit Propagate (Lesen inkl. Disk-/Systemstatus)
   - Rolle `DatastoreAdmin` auf Pfad `/datastore` mit Propagate (Verify, GC, Loeschen; alternativ nur `/datastore/<name>`)

Fehlen die Rechte beim User, liefert PBS oft leere Listen statt eines Fehlers. Bei leerem `datastore_list` oder `task_list` oder 403 zuerst die Rechte von Token UND User pruefen. Fuer reinen Lesebetrieb reicht `Audit`.

## 2. Cloud-Betrieb (Portainer)

Repo nach `kdt-solutions/pbs-mcp` pushen, dann baut GitHub Actions das Image (`.github/workflows/docker-publish.yml`). Einmalig: Package-Sichtbarkeit auf GitHub auf **Public** stellen (wie bei zammad-mcp).

1. Portainer -> Stacks -> Add stack -> Repository, Branch `main`, Compose-Pfad `docker-compose.yml`
2. Environment variables setzen: `PBS_URL`, `PBS_TOKEN_ID`, `PBS_TOKEN_SECRET`, `PBS_VERIFY_SSL`, `MCP_AUTH_TOKEN`
3. Deploy. Updates spaeter per "Pull and redeploy".

Reverse-Proxy (Plesk/nginx) mit TLS auf `<Host-IP>:8423`, Endpoint-Pfad `/mcp`. Der Port ist standardmaessig nur auf `127.0.0.1` des Docker-Hosts gemappt; laeuft der Proxy auf einem anderen Host, `MCP_BIND_ADDR` auf die LAN-IP setzen. `GET /healthz` ist ohne Token erreichbar und liefert nur `{"status": "ok"}` (Docker-Healthcheck, keine Abfrage des PBS); alle anderen Pfade brauchen das Bearer-Token. In Claude als Remote-MCP mit `https://<subdomain>/mcp` und dem `MCP_AUTH_TOKEN` als Bearer-Token einbinden. Der Container muss `PBS_URL` (Port 8007) erreichen koennen.

Fehler der PBS-API gehen nur als Statuscode mit kurzer Meldung an den Client (z.B. `PBS API Fehler 403 bei namespace_list: keine Berechtigung ...`); URL, Query und Rohantwort stehen im Container-Log. Der Container laeuft als unprivilegierter User mit nur lesbarem Root-Dateisystem und ohne Capabilities (`docker-compose.yml`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Die Tests pruefen Bearer-Auth und `/healthz`, die Schutzschalter der schreibenden Tools (`confirm`/Vorschau, `MCP_READONLY`), die Fehlermeldungen (keine URL/Rohantwort an den Client) und die Token-Laenge, alles gegen simulierte PBS-Antworten.

## Getestet

Die neuen Tools (Diagnose, Verify/GC, Loeschen) sind nur gegen simulierte API-Antworten und die Schutzschalter geprueft, **nicht** gegen den echten PBS. Endpunkte und Rechtenamen vor dem produktiven Einsatz gegen die PBS-API-Doku der eingesetzten Version pruefen.
