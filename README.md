# The Narrative Court

Производственный конвейер YouTube-шоу, где две LLM аргументируют противоположные
стороны спорного тезиса в формате зала суда. Система автоматизирует путь
**от темы → до готового DaVinci Resolve таймлайна** (FCPXML + EDL) для ручного монтажа.

Полное техническое задание: [`TZ_Narrative_Court.md`](TZ_Narrative_Court.md).

## Технический стек (июнь 2026)

- **UI:** NiceGUI 3.13 (локально, async, drag-and-drop, без JS, без Docker)
- **Данные:** SQLite + SQLAlchemy 2.x · Pydantic v2
- **Секреты:** OS keyring (+ `.env` fallback)
- **LLM:** `openai`/`anthropic`/`google-genai` + единый `ModelAdapter` ABC (15 моделей)
- **TTS:** ElevenLabs (`pcm_44100` → WAV)
- **Таймлайн:** FCPXML 1.11 (`lxml`, primary) + EDL CMX3600 + `_markers.md` (fallback)

## Быстрый старт

```bash
python -m pip install -r requirements.txt
cp config/.env.example config/.env      # заполнить ключи (или через keyring)
python main.py                          # http://127.0.0.1:8080
```

При первом запуске создаются `data/*`, БД и реестр моделей (onboarding).

### Offline-режим (без ключей)

В Episode Studio включите **Offline (mock)** — конвейер прогоняется целиком на
mock-провайдере и placeholder-WAV, выдавая реальную папку эпизода:

```
data/episodes/<slug>/
  audio/      r1_*.wav · r2_q*_*.wav · r3_*_*.wav · r4_*.wav
  script/     episode_script.md · host_cues.md · behaviour_flags.json
  timeline/   <slug>.fcpxml · <slug>.edl · <slug>_markers.md
```

## Тесты

```bash
python -m pytest tests/ -q
```

Покрытие: Behaviour Detector (`test_detector.py`), Timeline Exporter
(`test_exporter.py`), вариативность/дрейф/лидерборд/чек-лист/метаданные
(`test_core.py`), end-to-end offline-конвейер (`test_pipeline.py`).

## Статус по фазам (см. ТЗ Раздел 8)

- **Фаза 1 (MVP) — готово:** детерминированное ядро + тесты, offline-конвейер
  end-to-end, NiceGUI (Dashboard + Episode Studio + 6 экранов).
- **Фаза 2:** живые LLM-адаптеры в проде, smoke-test UI, drag-and-drop Planner,
  полный Quickfire-отбор и Translation-layer в UI.
- **Фаза 3:** Leaderboard Engine (Oxford-дельта), Topic Bank CRUD, полный Logs.

## Дисклеймер

Аргументы и голоса генерируются ИИ и не отражают взгляды создателя, провайдеров
моделей или какого-либо государства. GigaChat помечен
**[SANCTIONS RISK — legal review required, INA §329]**; использование требует
юридической консультации.
