# The Narrative Court

[![tests](https://github.com/igorverevkin1992/narrative.court/actions/workflows/ci.yml/badge.svg)](https://github.com/igorverevkin1992/narrative.court/actions/workflows/ci.yml)

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

Покрытие (66 тестов): Behaviour Detector (`test_detector.py`), Timeline Exporter
(`test_exporter.py`), вариативность/дрейф/лидерборд/чек-лист/метаданные
(`test_core.py`), end-to-end offline-конвейер (`test_pipeline.py`), пошаговая
оркестрация + авто-сейв + objection/quickfire-resync (`test_studio_steps.py`),
Oxford-дельта/recompute/метрики/экспорт + Topic CRUD (`test_leaderboard_topics.py`),
slug-traversal/FCPXML-escape/markers-sanitize/atomic-save (`test_security.py`),
адаптеры/классификация ошибок/GigaChat-403 (`test_adapters.py`), краевые сценарии
O.1–O.7 (`test_edge_cases.py`), полный 10-шаговый поток (`test_integration.py`),
TTS-resume-длительность/валидаторы/delimiter/secret-cache (`test_audit2.py`).

CI: `.github/workflows/ci.yml` гоняет весь offline-набор на Python 3.11/3.12 при
каждом push/PR. Ставится лёгкий `requirements-test.txt` (pydantic/PyYAML/SQLAlchemy/
tenacity/lxml) — тяжёлые SDK не нужны: они импортируются лениво и в offline не вызываются.

## Безопасность и устойчивость

Аудит P0–P2 закрыт: валидатор `slug` (защита от path traversal в путях/FCPXML/EDL),
лимит параллелизма LLM-вызовов (`max_parallel_requests`, RPM/TPM-safety),
неретраябельные `AuthError` (401/403/400 не повторяются), атомарный автосейв
эпизода, санитайз `markers.md` и control-символов в FCPXML + закалённый XML-парсер
(анти-XXE), выделенное предупреждение `SanctionsBlockedError` в UI, безопасная
подстановка шаблонов, HTML-escape сценария (анти-XSS), ввод ключей в keyring из
Config-экрана. Сервер биндится на `127.0.0.1` (не публиковать наружу).

Второй аудит (корректность): измерение длительности WAV при возобновлении TTS
(точность таймкодов), валидаторы значений `GenParams`/`OxfordDelta`, ретеншен
логов генерации (Block B.3), корректный TTS-checkpoint-summary после переотбора
квикфайра, инвариант одной реплики на раунд, санитайз prompt-делимитеров R1→R3,
кеш секретов (меньше обращений к keychain), узкий `except` в DB-зеркале.

Краевые сценарии (ТЗ Block O.1–O.7) доведены до UI: O.1 callout REFUSED/SUPPRESSED
в ревью, O.2 сводка+сброс TTS-чекпойнта, O.3 баннер «ответы слишком похожи»,
O.4 устойчивый экспорт (EDL+markers пишутся первыми, FCPXML с флагом валидности),
O.5 мягкое предупреждение об эпизодах без Oxford-дельты, O.6 sanctions-предупреждение,
O.7 readiness-баннер о незаданных ключах.

## Статус по фазам (см. ТЗ Раздел 8)

- **Фаза 1 (MVP) — готово:** детерминированное ядро + тесты, offline-конвейер
  end-to-end, NiceGUI (Dashboard + Episode Studio MVP + 6 экранов).
- **Фаза 2 — готово:** полный **Episode Studio из 10 шагов** (smoke-test с reframe,
  ревью с цветными флагами, рулинги objection, TTS-прогресс + resume, отбор
  10 из 12 квикфайра, экспорт FCPXML+EDL, метаданные с copy); пошаговые
  step-функции, авто-сейв/возобновление эпизода.
- **Фаза 3 — готово:** **Leaderboard** (ввод Oxford-дельты → пересчёт 5 метрик →
  экспорт Markdown/CSV, персист `leaderboard_aggregate`); **Topic Bank** CRUD с
  интерактивным чек-листом (авто-статус approved/warning/rejected), фильтрами и
  удалением; **полный Logs-экран** (все прогоны, фильтры эпизод/модель/finish_reason,
  детальный просмотр, экспорт «selected»); **Planner** с сохраняемым переупорядочением.
  *Осталось:* живые провайдеры в проде (нужны ключи, недоступны в этой среде).

## Дисклеймер

Аргументы и голоса генерируются ИИ и не отражают взгляды создателя, провайдеров
моделей или какого-либо государства. GigaChat помечен
**[SANCTIONS RISK — legal review required, INA §329]**; использование требует
юридической консультации.
