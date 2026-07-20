# WSP 3.1 — Hermes-Live-Testplan

Copy-paste-Prompts für Live-Tests der 3.1-Features im echten Hermes-Agent.
Voraussetzung: Plugin vom Branch `integration/v3.1` in `~/.hermes/plugins/web-search-plus`
installiert, Keys in `~/.hermes/.env`.

Vor dem Testlauf einmalig (Shadow-Modus aktivieren):
```bash
# in config.json des Plugins:
{ "routing": { "policy_mode": "shadow" } }
```

---

## 1. Smoke: Grundfunktion unverändert (Default-Verhalten)

> Suche nach "PostgreSQL 17 release notes" und gib mir die Top-3-Quellen mit URLs.

> Extrahiere den Inhalt von https://tokio.rs/blog/2019-10-scheduler und fasse die Kernidee in 3 Sätzen zusammen.

Erwartung: identisches Verhalten wie 3.0.2 — Provider-Attribution sichtbar,
saubere Quellen, keine neuen Felder ohne Opt-in.

## 2. Shadow Observer (Feature 1)

Nacheinander (mind. 5 unterschiedliche Query-Klassen, damit Divergenzen entstehen):

> Suche: "beste Wanderwege Steiermark"
> Suche: "EU AI Act enforcement timeline official"
> Suche: "python asyncio TaskGroup tutorial"
> Suche: "NVIDIA quarterly earnings guidance"
> Suche: "site:reddit.com best budget DAC"

Danach lokal prüfen (nicht im Agent):
```bash
python3 ui.py --port 8765   # dann:
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8765/api/v3/shadow-evaluation | jq
```
Erwartung: `total_evaluations` ≥ 5, plausible `agreement_rate`, Divergenz-Paare
(classic vs shadow) — und die Suchergebnisse selbst UNVERÄNDERT klassisch geroutet
(`affected_execution` immer false).

Kill-Switch-Gegenprobe: `WSP_ROUTING_CLASSIC_ONLY=1` setzen, 2 Suchen wiederholen
→ Zähler im Console-Endpoint darf NICHT steigen.

## 3. Budget Preflight (Feature 2)

config.json ergänzen:
```json
{ "budget_preflight": { "enabled": true, "max_provider_calls_per_request": 1, "on_exceed": "degrade" } }
```

> Recherchiere im Research-Modus: "compare Rust async runtimes tokio vs async-std vs smol" mit Quellenangaben.

Erwartung: Research-Fan-out wird auf 1 Provider-Call degradiert; die Antwort nennt
die Degradation ehrlich (Receipt/Metadata: policy action `budget_preflight`), kein
stiller Leerlauf. Danach `on_exceed: "abort"` testen → typisierter Fehler OHNE
Provider-Call. Danach Preflight wieder deaktivieren.

## 4. Diversity Score (Feature 3)

> Suche mit Quality-Report: "best wireless headphones 2026 review" — zeig mir den Quality-Report.

Erwartung: Der Quality-Report enthält einen `diversity`-Block (score, components:
domain_diversity, url_duplication, content_diversity, provider_mix). Ranking
unverändert (rerank ist default off).

## 5. Extraction Cache (Feature 4)

Zweimal DIESELBE Extraktion direkt hintereinander:

> Extrahiere https://www.anthropic.com/news — nur Titel und erste Absätze.
> (exakt wiederholen)

Erwartung: zweiter Aufruf als Cache-Hit mit identischem Inhalt inkl. Provider-
Attribution (cache disposition im Receipt). Dann dieselbe URL mit anderem
Output-Format/anderen Limits → muss ein Miss sein (request-genaue Identität).

## 6. Self-hosted-Profil (Feature 5) — nur wenn SearXNG-Instanz vorhanden

config.json: `{ "profile": "self_hosted", "searxng": { "base_url": "http://<instanz>" } }`

> Suche: "aktuelle Nachrichten Österreich"

Erwartung: Auto-Routing nutzt NUR SearXNG/Keenable (kein bezahlter Provider im
Receipt). Explizite Provider-Wahl (z.B. "nutze provider serper") funktioniert
weiter, markiert aber `profile_deviation` in den Metadaten. Ohne SearXNG-Config:
typisierter Fehler, kein stiller Fallback auf bezahlte Keys. Danach Profil
zurück auf `standard`.

## 7. Semantic Spans (Feature 6)

> Extrahiere https://tokio.rs/blog/2019-10-scheduler mit spans=true und spans_query "work stealing scheduler" — zeig mir die Spans mit Offsets.

Erwartung: `spans`-Array mit start/end (Codepoints, half-open), span-Text ==
Slice des NFC-Texts, Spans thematisch zur Query passend. Gleiche URL mit anderer
spans_query → andere Spans. Ohne spans-Option → Response wie bisher (kein Feld).

## 8. Provider SDK (Feature 7) — lokal, nicht im Agent

```bash
python3 setup.py new-provider testvendor       # Scaffold entsteht in providers.d/
python3 setup.py status                        # testvendor erscheint NICHT (kein Key, non-prod Gate greift nur fürs Beispiel)
WSP_SDK_ALLOW_NON_PRODUCTION=1 python3 -m pytest tests/test_provider_conformance.py -q
```
Erwartung: Scaffold + Conformance grün; Default-Provider-Anzahl in `status`
bleibt 14 (keine Surface-Änderung).

## 9. Console-Gesamtbild (nach allen Tests)

```bash
curl -s ... /api/v3/overview | jq          # Provider-Readiness
curl -s ... /api/v3/provider-health | jq   # Tages-Buckets: Fehlerraten/Latenzen der Testläufe
curl -s ... /api/v3/receipts?limit=20 | jq # Receipts: preflight actions, cache dispositions, shadow_observation
```
Erwartung: Alle heutigen Live-Tests tauchen als Receipts auf; provider-health
zeigt die Attempts des Tages; nirgendwo Query-Texte oder URLs in Operator-Payloads.

## Abbruchkriterien

Jede Abweichung von "Erwartung" ist ein Release-Blocker-Kandidat: notieren
(Prompt, Receipt-execution_id, Beobachtung) und vor dem RC fixen.
