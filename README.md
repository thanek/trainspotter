# Trainspotter

Mapa pociągów w Polsce na żywo. Backend udostępnia jedno API, które zwraca pozycje
pociągów w zadanym obszarze. Pozycja pochodzi z GPS, jeśli źródło jest dostępne,
a w przeciwnym razie jest szacowana na podstawie danych PKP PLK.

## Uruchomienie lokalne (venv)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
```

Aplikacja działa pod adresem http://localhost:8000, a dokumentacja API pod http://localhost:8000/docs.
Przy pierwszym starcie rozkład (~27 MB) jest pobierany i importowany w tle, co trwa kilka sekund.
Do tego czasu API odpowiada `503`.

Testy:

```bash
.venv/bin/python -m pytest
```

## Docker / Podman

```bash
podman compose up --build      # albo: docker compose up --build
```

Dane (baza SQLite z rozkładem) są trzymane w wolumenie `trainspotter-data`.

Geolokalizacja w przeglądarce wymaga HTTPS (albo `localhost`). Przy wdrożeniu
na serwer postaw przed kontenerem reverse proxy z TLS.

## API

| Endpoint | Opis |
|---|---|
| `GET /api/v1/trains?bbox=minLon,minLat,maxLon,maxLat[&at=ISO][&include_bus=true]` | Pociągi w obszarze jako GeoJSON `FeatureCollection` |
| `GET /api/v1/trains/{trainId}` | Szczegóły kursu: stacje z czasami planowymi i rzeczywistymi, kształt trasy, bieżąca pozycja |
| `GET /api/v1/sources` | Stan źródeł danych |
| `GET /api/v1/health` | Healthcheck |

Każdy pociąg ma w `properties.position`:

- `accuracy`: `gps` albo `estimated`,
- `source`: identyfikator źródła (`gtfs-schedule`, `plk-opendata-mkuran`, źródła GPS),
- `observedAt`: czas pomiaru GPS albo ostatniego zdarzenia potwierdzonego przez PLK,
- `estimate.basedOn`: `confirmed` (ostatni przejazd potwierdzony), `forecast` (prognoza PLK) albo `schedule` (sam rozkład).

`status` przyjmuje wartości `moving`, `at_stop` i `held`. `held` oznacza, że PLK od ponad
10 minut nie potwierdziło przejazdu przez następną stację, więc pociąg jest pokazywany na niej.

## Jak to działa

Nic nie jest liczone w tle. Każde zapytanie wykonuje poniższe kroki tylko dla zadanego bbox:

1. Indeks R-tree w SQLite wybiera kursy, których trasa przecina bbox i które o tej porze mogą jechać.
2. Feed live (cały kraj, jeden plik) jest pobierany tylko wtedy, gdy cache jest starszy niż 30 s.
   Pobranie jest warunkowe (ETag), a równoległe zapytania czekają na jedno pobranie.
3. Pozycja jest interpolowana po kształcie toru (`shapes.txt`) między stacjami,
   z czasami rzeczywistymi lub prognozowanymi.
4. Źródła GPS są odpytywane tylko wtedy, gdy ich zasięg przecina bbox. Świeży odczyt GPS zastępuje estymację.

Przy bbox o przekątnej powyżej 150 km działa tryb `overview`: interpolacja po linii prostej, bez źródeł GPS.

Rozkład jest odświeżany leniwie. Jeśli baza jest starsza niż 24 h, przy kolejnym zapytaniu
rusza import w tle. Do czasu jego zakończenia używana jest stara baza.

## Źródła danych

- Rozkład: [mkuran.pl/gtfs – Polish Trains](https://mkuran.pl/gtfs/) (dane PKP PLK, warunki ponownego wykorzystania PKP PLK i KM).
- Dane live: `updates.json` z tego samego serwisu (CC BY 4.0 + warunki PKP PLK).
  Bywa, że feed nie aktualizuje się przez ponad godzinę. Feed starszy niż 15 min jest pomijany i system przechodzi na sam rozkład.
- Podkład: OpenStreetMap, tory: OpenRailwayMap.
- GPS: na razie brak publicznego, udokumentowanego źródła. Adapter dodaje się jako podklasę `GpsSource` w `app/gps/`.

## Konfiguracja

Zmienne środowiskowe `TS_*`, patrz `app/config.py`. Najważniejsze: `TS_DATA_DIR`,
`TS_LIVE_FEED_CACHE_TTL_S`, `TS_OVERVIEW_MODE_MIN_DIAG_KM`, `TS_MAX_ASSUMED_DELAY_S`.
