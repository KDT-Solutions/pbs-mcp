# Changelog

## 0.6

- `verify_start`, `verify_job_run`, `gc_start` brauchen `confirm=true`; ohne confirm kommt eine Vorschau mit dem Umfang (Store, Namespace, Filter). Bestehende Aufrufe ohne `confirm` starten also nichts mehr.
- Fehler der PBS-API gehen nur noch mit Statuscode und kurzer Meldung an den Client; URL (mit Query) und Rohantwort landen im Server-Log. Verbindungsfehler nur mit Fehlertyp.
- `MCP_AUTH_TOKEN` wird zeitkonstant verglichen und muss mindestens 32 Zeichen lang sein.
- Dockerfile laeuft als unprivilegierter User (uid 10001); Compose mit `read_only`, `cap_drop: ALL`, `no-new-privileges`; Host-Port standardmaessig nur auf `127.0.0.1` gemappt (`MCP_BIND_ADDR`, vorher `0.0.0.0`).
- Abhaengigkeiten exakt gepinnt, `requirements-dev.txt` fuer die Tests.
- Tests fuer confirm/Vorschau, `MCP_READONLY`, Fehlermeldungen und Token-Laenge.

## 0.5

- Neuer Endpunkt `GET /healthz` ohne Auth, liefert nur `{"status": "ok"}`. Prüft nur, ob der Prozess antwortet, nicht die PBS-API. Alle anderen Pfade brauchen weiterhin `MCP_AUTH_TOKEN`.
- `HEALTHCHECK` im Dockerfile (Python/urllib auf `/healthz`, interval 30s, timeout 5s, start_period 15s, retries 3).
- HTTP-App in `build_http_app()` ausgelagert (testbar), Verhalten unverändert.
