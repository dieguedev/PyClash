# PyClash

Automatización de Clash of Clans sobre Google Play Games (en desarrollo).

## Estructura

`src/pyclash/`: `device`, `vision`, `screens` (pantallas y estado), `policy`, `tasks`, `runtime`, `interface`.

## Desarrollo

```
python -m pip install -e ".[dev]"
python -m pytest
python tools/check_fixtures.py
```

## Capturas / fixtures

Ninguna captura con datos personales entra al repositorio. Guarda los originales en `captures_raw/`
(ignorado por git) y genera la versión anonimizada:

```
python tools/anonymize.py captures_raw/x.png tests/fixtures/x.png --region 10,10,200,40
```

`tools/check_fixtures.py` (y el CI) falla si hay una imagen sin el marcador de anonimizado.
Ver [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para licencias.
