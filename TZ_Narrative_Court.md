# Техническое задание — система производства YouTube-шоу «The Narrative Court»

> **Дата актуальности:** июнь 2026.
> **Язык документа:** русский. Технические термины, имена функций/переменных, SQL,
> примеры кода, XML, комментарии в коде — английский.
> **Назначение документа:** самодостаточное ТЗ, по которому Claude Code разрабатывает
> систему без дополнительных вопросов. Каждый модуль снабжён контрактами входа/выхода,
> схемами данных, пронумерованными алгоритмами, списком зависимостей и протоколом
> обработки ошибок.

---

## Раздел 1. Обзор системы

### 1.1. Что система делает

«The Narrative Court» — англоязычное YouTube-шоу, в котором две языковые модели (LLM)
аргументируют противоположные стороны спорного геополитического или исторического
тезиса в формате судебного заседания. Ведущий-человек выступает судьёй честности
аргументации (не истины); зрители — присяжные.

Система автоматизирует производственный конвейер **от идеи темы до готового
DaVinci-Resolve-таймлайна** для ручного финального монтажа. Жёсткая граница:

```
ВХОД:  тема + тезис эпизода + выбор пары моделей
   ↓  [СИСТЕМА — всё внутри]
ВЫХОД: папка эпизода:
   /audio/    — WAV-файлы всех реплик
   /script/   — текстовый сценарий + флаги поведения
   /timeline/ — FCPXML (primary) + EDL (fallback) для DaVinci Resolve
```

Видеомонтаж, графика/оверлеи, запись голоса ведущего, публикация — **вручную, вне
системы**.

### 1.2. Оператор

Соло-создатель, нетехнический пользователь. Управление — через локальный веб-интерфейс
в браузере (Mac / Windows / Linux). Python-бэкенд запущен локально. Без Docker, без
облака. Ни один экран не требует знания Python/JSON/API.

### 1.3. Итоговый технический стек (выбор по каждому компоненту с обоснованием)

| Компонент | Выбор | Версия (июнь 2026) | Краткое обоснование |
|---|---|---|---|
| Web-фреймворк | **NiceGUI** | 3.13.0 | Единственный из рассмотренных (Streamlit, FastAPI+HTMX/React, Flask+Jinja2, Gradio), кто нативно закрывает разом все обязательные критерии: async-фоновые задачи (генерация+TTS 2–5 мин без зависания UI), real-time streaming (`ui.log`, `@ui.refreshable`), нативный drag-and-drop (`.props('draggable')` + события), чистый Python без JS, кросс-платформенность без Docker. Streamlit проигрывает из-за rerun-модели (фоновые задачи и per-item-стриминг требуют ручных потоков и `session_state`-хаков), FastAPI+React требует JS и значительного boilerplate. |
| База данных | **SQLite + SQLAlchemy 2.x ORM** | SQLAlchemy 2.0.43 | Встроенный, без сервера, один файл (`narrative_court.db`) — тривиальный бэкап и копирование. Транзакции ACID защищают от частичных записей. SQLAlchemy 2.x даёт типизированный ORM (`Mapped[...]`) и Core для аналитических запросов лидерборда. Объёма (~400 эпизодов, ~1000 вопросов) хватает с многократным запасом. TinyDB/JSON не дают транзакций и индексов; Peewee беднее по типобезопасности. |
| Хранение секретов | **`keyring`** (+ `.env` fallback) | keyring 25.6.0 | Один код-путь поверх Keychain (macOS) / Credential Manager (Windows) / SecretService (Linux). Секреты не в репозитории, не в plaintext на диске. Onboarding-wizard пишет ключи в keyring. `python-dotenv` (`config/.env`) — документированный fallback, который `config.py` читает, если в keyring нет записи (headless Linux без SecretService). |
| LLM-доступ | **`openai` SDK + `ModelAdapter` ABC** | openai 1.99.1 | Большинство провайдеров OpenAI-совместимы (разные `base_url`) → один SDK. Несовместимые (Anthropic, Google, Yandex, GigaChat, Falcon, HyperCLOVA) — адаптеры-врапперы к единому интерфейсу `GenerationResult`. |
| TTS | **ElevenLabs `elevenlabs` SDK** | elevenlabs 2.45.0 | Non-streaming `client.text_to_speech.convert()`, `output_format="pcm_44100"` → WAV 44.1 кГц на диск. Resumable batch с чекпойнтами. |
| Таймлайн | **FCPXML 1.11 (primary, `lxml`) + EDL CMX3600 (fallback)** | lxml 6.0.0 | DaVinci Resolve 19 импортирует AAF/EDL/XML/DRT/ADL/OTIO, но у FCPXML/OTIO в Resolve задокументированы баги мультитрек-аудио (source-channel → mute, roles → «супертрек») и неполный round-trip маркеров. Поэтому: ручная генерация Resolve-совместимого FCPXML по проверенному скелету как primary, плюс простой EDL + сопроводительный `.md` с таймкодами маркеров как гарантированный fallback. OTIO — опциональный экспорт. |
| Длительность аудио | **`soundfile`** (+ `mutagen` fallback) | soundfile 0.13.2 | Читает длительность WAV из заголовка через libsndfile, **без зависимости от ffmpeg**. `mutagen` — pure-python запасной вариант. |
| Retry/устойчивость | **`tenacity`** | tenacity 9.1.2 | Exponential backoff + jitter, отдельные политики для reasoning-моделей. |
| Async HTTP | **`httpx`** | httpx 0.28.1 | Для кастомных адаптеров (Yandex, GigaChat, Falcon, HyperCLOVA). |
| Валидация данных | **Pydantic v2** | pydantic 2.12.5 | Все доменные объекты + `@model_validator` для чек-листа тем. |

### 1.4. Политика выбора методов (где промпт требовал «один из»)

- **Quickfire variability (Block E.1)** → лексические эвристики без API ($0, детерминированно,
  оффлайн): composite из Jaccard-distance, нормированной разницы длин, детектора
  антонимных пар и расхождения числовых фактов. LLM-judge (Gemini) — опциональный
  переключатель в `config.yaml`.
- **Усечение 15-сек реплики (Block E.2)** → основной рычаг `max_tokens≈60` при генерации
  + word-count post-trim как страховка + UI-флаг, если измеренная длительность TTS > 15 с.
- **Translation drift (Block F.2)** → keyword/entity/number overlap без API → авто-откат
  к оригиналу при дрейфе.
- **Hedging detector (Block C.2)** → детерминированный keyword/regex-список (≥ 30 паттернов);
  ≥ 3 срабатываний → `EVASIVE`, 1–2 → `WEAK`. Дёшево, прозрачно, аудируемо.

---

## Раздел 2. Архитектурная диаграмма

```
                         ┌──────────────────────────────────────────────┐
                         │            WEB UI (NiceGUI, :8080)            │  ◄── оператор (браузер)
                         │  Dashboard · TopicBank · EpisodePlanner ·     │
                         │  EpisodeStudio(10 шагов) · ScriptViewer ·     │
                         │  Leaderboard · Config · Logs                  │
                         └───────────────┬──────────────────────────────┘
                                         │ вызовы (async, background tasks)
        ┌────────────────────────────────┼─────────────────────────────────────────┐
        │                                 │                                          │
        ▼                                 ▼                                          ▼
┌───────────────┐   ┌──────────────────────────────────────────────┐    ┌────────────────────┐
│ (14) CONFIG & │   │           (12) EPISODE MANAGER                │    │  (10) TOPIC BANK   │
│   SECURITY    │   │  оркестрация статус-машины эпизода:           │    │  Topic + checklist │
│ config.yaml + │   │  draft→smoke_tested→generated→tts_done→       │    │  (Pydantic valid.) │
│ keyring/.env  │   │  exported→published                           │    └─────────┬──────────┘
└──────┬────────┘   └───┬───────────┬──────────┬─────────┬──────────┘              │
       │ ключи/пути      │           │          │         │                         │
       ▼                 ▼           ▼          ▼         ▼                         ▼
┌──────────────┐  ┌────────────┐ ┌─────────┐ ┌────────┐ ┌──────────┐        ┌──────────────┐
│ (1) LLM      │  │ (3)EPISODE │ │(4)QUICK-│ │(5)TRANS│ │ (6) TTS  │        │ SQLite DB    │
│ ORCHESTRATOR │◄─┤ GENERATOR  │ │FIRE MGR │ │-LATION │ │  ENGINE  │        │ (SQLAlchemy) │
│ ModelAdapter │  │ +smoke_test│ │variabil.│ │ Gemini │ │ElevenLabs│        │ episodes,    │
│   ABC        │  └─────┬──────┘ └────┬────┘ │corrector│ │ batch+   │        │ models,      │
│  ┌─────────┐ │        │             │      └────┬────┘ │ resume   │        │ pairs,       │
│  │ openai  │ │        ▼             ▼           │      └────┬─────┘        │ behaviour_   │
│  │ anthropic│ │  ┌──────────────────────────────▼───────────▼─────┐       │ flags,       │
│  │ google   │ │  │            (2) BEHAVIOUR DETECTOR              │       │ objection_   │
│  │ yandex   │ │  │  deepseek_censor (REFUSED/SUPPRESSED) +        │       │ events,      │
│  │ gigachat │ │  │  hedging_patterns (EVASIVE/WEAK)               │       │ oxford_      │
│  │ falcon   │ │  └───────────────────────┬───────────────────────┘       │ deltas,      │
│  │hyperclova│ │                          │ BehaviourFlag[]                │ leaderboard  │
│  └─────────┘ │                          ▼                                 └──────┬───────┘
└──────┬───────┘  ┌───────────────────────────────────────────┐                    │
       │          │           (7) SCRIPT BUILDER               │                    │
       │ GenLog   │  episode_script.md · host_cues.md ·        │                    ▼
       ▼          │  behaviour_flags.json                      │            ┌──────────────┐
┌──────────────┐  └──────────────────┬────────────────────────┘            │(9)LEADERBOARD│
│ (13?) LOGS   │                     │                                      │   ENGINE     │
│ GenerationLog│                     ▼                                      │ recompute +  │
│ JSONL files  │  ┌───────────────────────────────────────────┐            │ MD/CSV export│
└──────────────┘  │        (8) TIMELINE EXPORTER               │            └──────────────┘
                  │  timecode_calculator (soundfile) →         │
                  │  fcpxml_generator (lxml, primary) +        │            ┌──────────────┐
                  │  edl_generator (EDL + .md, fallback)       │            │(11)METADATA  │
                  └──────────────────┬────────────────────────┘            │  GENERATOR   │
                                     │                                      │ title/descr/ │
                                     ▼                                      │ tags/polls   │
                  ┌───────────────────────────────────────────┐            └──────────────┘
                  │  ФАЙЛОВАЯ СИСТЕМА                          │
                  │  data/episodes/epNNN_slug/{audio,script,   │
                  │  timeline}/  ·  data/logs/  ·  data/exports/│
                  └───────────────────────────────────────────┘

ВНЕШНИЕ API: OpenAI · Anthropic · Google Gemini · DeepSeek · DashScope(Qwen) ·
Mistral · xAI · Zhipu · AI21 · Sarvam · OpenRouter(Rakuten) · YandexCloud ·
Sber GigaChat(OAuth)[SANCTIONS] · TII/HF(Falcon) · Naver(HyperCLOVA) · ElevenLabs(TTS)
```

**14 модулей:** (1) LLM Orchestrator, (2) Behaviour Detector, (3) Episode Generator,
(4) Quickfire Manager, (5) Translation Layer, (6) TTS Engine, (7) Script Builder,
(8) Timeline Exporter, (9) Leaderboard Engine, (10) Topic Bank, (11) Metadata Generator,
(12) Episode Manager, (13) Web UI, (14) Config & Security. (Logs — подсистема внутри
Config & Security / Orchestrator, поверхность — экран Logs.)

---

## Раздел 3. Модульные спецификации (14 модулей)

Формат каждой спецификации: **Ответственность · Вход · Выход · Алгоритм ·
Зависимости · Обработка ошибок · Что тестировать.**

### Модуль 1 — LLM Orchestrator (`modules/llm/`)

**Ответственность.** Единая точка вызова любой из 15 моделей через общий интерфейс
`ModelAdapter`. Скрывает различия SDK/HTTP, выполняет retry, тайм-ауты, логирование
всех прогонов (`GenerationLog`), нормализует ответ в `GenerationResult`.

**Вход.** `model_id: str` (из реестра), `system: str`, `user: str`,
`temperature: float`, `max_tokens: int`, `seed: int | None`. Пример:
`generate("deepseek-v4-pro", system=PROSECUTION_S1, user="...", temperature=0.7, max_tokens=800, seed=42)`.

**Выход.** `GenerationResult` (см. Раздел 4). Пример полей:
`content="The dissolution was driven by..."`, `reasoning_content="<CoT>"`,
`finish_reason="stop"`, `model_version="deepseek-v4-pro-0531"`, `latency_ms=44210`,
`prompt_hash="sha256:ab12…"`, `raw=<provider dict>`.

**Алгоритм.**
1. Загрузить запись модели из `models_registry` по `model_id`; если нет — `UnknownModelError`.
2. Выбрать адаптер по `api_format` (`openai|anthropic|google|yandex|gigachat|falcon|hyperclova`).
3. Получить API-ключ из `Config.get_secret(api_key_env)`; если пуст — `MissingKeyError`.
4. Вычислить `timeout`: `reasoning_timeout_seconds`, если `behaviour_profile.reasoning`, иначе `timeout_seconds`.
5. Выполнить вызов через `tenacity` (см. «Обработка ошибок»); замерить `latency_ms`.
6. Нормализовать ответ → `GenerationResult` (для OpenAI-совместимых reasoning-моделей
   прочитать `choices[0].message.reasoning_content`).
7. Записать `GenerationLog` (JSONL в `data/logs/<episode_id>/<model_id>.jsonl`,
   `selected=False`, `selection_policy="first_valid"`).
8. Вернуть результат. Первый валидный прогон помечается `selected=True` вызывающим кодом.

**Зависимости.** `openai`, `anthropic`, `google-genai`, `httpx`, `tenacity`; модули
`config`, `schemas`.

**Обработка ошибок (Block B.2).**
- Retry: `tenacity` — `stop_after_attempt(retry_attempts=3)`,
  `wait_exponential(multiplier=2, min=2, max=30) + wait_random(0,1)` (jitter).
  Ретраим только: `429`, `5xx`, `APITimeoutError`, `APIConnectionError`. **Не** ретраим
  `4xx` (кроме 429), `content_filter`.
- HTTP timeout reasoning-моделям: 180 с (connect=10, read=180). Нереасонинг: 120 с.
- Rate limits (наиболее строгие на июнь 2026: Anthropic tier-1, DeepSeek, Mistral free):
  при `429` адаптер читает `Retry-After`/`x-ratelimit-reset`; `tenacity` уважает.
  Глобальный `asyncio.Semaphore(per_provider_concurrency)` ограничивает RPM без ручного
  вмешательства.
- Partial failure (модель упала в середине Round 3): Orchestrator поднимает
  `GenerationFailed(model_id, round_id)`; Episode Generator ловит, ставит эпизод на
  паузу и уведомляет оператора (UI: «retry этого раунда» / «заменить модель»). Уже
  сгенерированные реплики сохранены — повтор только упавшего вызова.

**Что тестировать.** `MockAdapter` возвращает фикстуры; проверить: выбор адаптера по
`api_format`, корректное чтение `reasoning_content`, запись `GenerationLog`, retry на
смоделированном `429` (через `tenacity` с патченым sleep), отсутствие retry на `4xx`.

---

### Модуль 2 — Behaviour Detector (`modules/detector/`)

**Ответственность.** Помечать реплики маркерами `WEAK` / `EVASIVE` / `REFUSED` /
`SUPPRESSED`. Состоит из `deepseek_censor.py` (REFUSED/SUPPRESSED по полям API) и
`hedging_patterns.py` (EVASIVE/WEAK по лексике). Работает оффлайн, без доп. API-вызовов.

**Вход.** `GenerationResult` + `model_id` + `round_id`. **Выход.** `list[BehaviourFlag]`
(возможно пустой). Пример: `BehaviourFlag(flag_type="SUPPRESSED", confidence=0.8,
evidence="reasoning_content len=12 < 80", rule_triggered="rule3_empty_reasoning",
model_id="deepseek-v4-pro", round_id="r3_p2", timestamp="2026-06-24T10:00:00Z")`.

**Алгоритм — DeepSeek (Block C.1), опирается на arXiv 2504.17130 «Steering the
CensorShip» (COLM 2025, механизм *thought suppression*) и 2506.12349 «Information
Suppression in DeepSeek».**
1. Извлечь `reasoning_content`, `content`, `finish_reason`.
2. **Rule 1:** `finish_reason == "content_filter"` → `REFUSED` (confidence 1.0).
3. **Rule 2:** регулярка по списку refusal-строк (case-insensitive, нормализация
   кавычек/пробелов) → `REFUSED` (0.95). Список: `REFUSAL_PATTERNS` =
   `["sorry,? that'?s beyond my current scope", "i can'?t (help|assist) with that",
   "i'?m unable to discuss", "this (topic|question) is outside", "as an ai.*cannot"]`.
   Exact-match недостаточно — нужен regex (вариативность пунктуации/регистра).
4. **Rule 3:** `len(reasoning_content.strip()) < THRESHOLD(80)` при
   `len(content.strip()) > MIN_CONTENT_LEN(200)` → `SUPPRESSED` (0.8). Порог 80 символов
   обоснован: содержательный CoT reasoning-модели практически всегда > 80 симв.;
   пустой/обрубленный CoT при длинном ответе — сигнатура thought-suppression
   (2504.17130). Пороги вынесены в `config.detector`.
5. **Rule 4 (семантический gap, без API):** взять топ-N (5) существительных/именованных
   сущностей из `reasoning_content` (простая частотная эвристика + стоп-слова); если
   < 20% из них присутствуют в `content` → `SUPPRESSED` с confidence 0.45 (низкая,
   помечается для ручного ревью). Реализуется набором множеств токенов, без LLM.

**Алгоритм — hedging (Block C.2).** Считать совпадения по `HEDGING_PATTERNS` (≥ 30
паттернов, см. `hedging_patterns.py` ниже) в `content`. `hits ≥ 3` → `EVASIVE` (conf
0.6+0.1·(hits−3), макс 0.95); `hits ∈ {1,2}` → `WEAK` (conf 0.4). Сравнение
case-insensitive по нормализованному тексту; каждая находка пишет `evidence` (цитата).
Regex/keyword выбран вместо LLM-judge: дешевле ($0), детерминирован, аудируем; LLM-judge
доступен как опция `detector.method="llm_judge"` для будущего.

**Зависимости.** Только stdlib (`re`), `schemas`. Никаких внешних API.

**Обработка ошибок.** Пустой `content` и `finish_reason="stop"` → `REFUSED`(0.7,
"empty_content"). `reasoning_content is None` у нереасонинг-модели — Rule 3/4
пропускаются (не ошибка).

**Что тестировать (`tests/test_detector.py`, обязателен для MVP).**
Фикстуры: (a) `finish_reason=content_filter` → REFUSED; (b) content со строкой
«Sorry, that's beyond my current scope» в разном регистре → REFUSED; (c) короткий
reasoning + длинный content → SUPPRESSED; (d) 4 hedging-фразы → EVASIVE; (e) 1 hedging →
WEAK; (f) чистый уверенный ответ → нет флагов.

---

### Модуль 3 — Episode Generator (`modules/generator/`)

**Ответственность.** Оркестрировать генерацию всех раундов эпизода с максимальной
параллельностью (`asyncio`), внедрять аргументы оппонента из Round 1 в Round 3, прогонять
Behaviour Detector, выполнять smoke-test (go/no-go) с reframe.

**Вход.** `Episode`(status=`draft`) с `thesis`, `prosecution_model_id`,
`defense_model_id`, `quickfire_bank: list[str]`, `gen_params`. **Выход.** `Episode`
(status=`generated`) с заполненными `rounds`, `behaviour_flags`, `quickfire_results`.

**Граф зависимостей и параллельность (Block D.1).**
```
Round1_Prosecution ─┐
Round1_Defense ─────┼─► (инъекция аргументов R1) ─► Round3_P1/P2/P3 (+objection) ─┐
                    │                                                              ├─► Round4_*
Quickfire_Q1..Q12 (все параллельно, обе стороны) ─► variability scoring ─► select 10 │
Round4_Prosecution ─┐ (зависит от Round3)                                            │
Round4_Defense ─────┘                                                                ▼
```
- **Параллельно:** R1_pros ∥ R1_def; все 12 quickfire-вопросов (24 вызова) ∥ R1;
  внутри R3 три под-раунда последовательны логически, но pros/def каждого под-раунда ∥.
- **Строго последовательно:** R1 → R3 (нужны аргументы оппонента); R3 → R4 (заключения
  опираются на ребаттлы).

**Инъекция аргументов R1 в R3 (точный шаблон слота).** В `user`-промпт Round 3
добавляется блок:
```
[OPPONENT_OPENING_STATEMENT]
{opponent_round1_text}
[/OPPONENT_OPENING_STATEMENT]

You are arguing {side}. Rebut Point {n} of the opponent's opening above.
Attack the weakest factual claim. Do not concede your position.
```

**Алгоритм.**
1. Собрать 4 system-промпта по сезону/стороне (см. ниже).
2. `asyncio.gather`: R1_pros, R1_def, 24 quickfire-вызова. Стримить каждую готовую
   реплику в UI.
3. Прогнать Behaviour Detector по R1 (и далее по каждому ответу).
4. Quickfire Manager: variability scoring → пометить рекомендованные 10.
5. Сформировать R3 user-промпты с инъекцией R1 оппонента; `gather` по под-раундам.
6. Сформировать R4 (инъекция итогов R3); `gather` pros/def.
7. Заполнить `Episode.rounds`, `behaviour_flags`; статус → `generated`.

**Smoke-test runner (Block D.3), `smoke_test.py`.**
1. Сгенерировать Round1 обеих сторон.
2. Сгенерировать первые 3 quickfire-вопроса из банка (обе стороны).
3. Прогнать Behaviour Detector на всех ответах.
4. Go/No-Go: (a) DeepSeek — нет `REFUSED`/`SUPPRESSED` в R1; (b) все модели — нет
   `EVASIVE`/`REFUSED` в R1; (c) variability ≥ порога для всех 3 вопросов.
5. PASS → `SmokeTestResult(passed=True, flags=[], reframe=None)`.
6. FAIL → сгенерировать reframe (academic framing + переформулировка тезиса из
   `thesis_variants`); вернуть `passed=False, reframe=<text>`.
7. Повтор с reframe, максимум **N=2** (итого 3 попытки). Обоснование N: эмпирически
   ≥ 3 прогонов на тему — это уже сигнал, что тема структурно проблемна; дешевле
   переключиться, чем жечь бюджет.
8. При финальном FAIL → предложить тему из банка запасных (status остаётся `draft`).

**4 system-промпта (Block C.3)** — хранятся в `modules/generator/prompts/`:
`prosecution_s1.txt`, `defense_s1.txt`, `prosecution_s2.txt`, `defense_s2.txt`. Точные
тексты — в Разделе 4.5 настоящего ТЗ. Техника: academic-adversarial framing +
«maintain your position» + явный запрет хеджирования.

**Зависимости.** `modules.llm`, `modules.detector`, `modules.quickfire`,
`modules.translation` (для S2-моделей), `asyncio`, `schemas`.

**Обработка ошибок.** `GenerationFailed` из Orchestrator → пауза эпизода, событие в UI
(retry раунда / замена модели). Translation-layer-сбой (S2) → использовать оригинальный
английский текст модели + флаг в UI. Smoke-test 3× FAIL → не блокировать, предложить
запасную тему.

**Что тестировать.** Граф зависимостей (R3 не стартует без R1) — на `MockAdapter`;
корректность инъекции R1→R3 (слот заполнен текстом оппонента); smoke-test PASS/FAIL/reframe
ветви; счётчик попыток N=2.

---

### Модуль 4 — Quickfire Manager (`modules/quickfire/`)

**Ответственность.** Из 12 сгенерированных вопросов отобрать 10 с максимальной
«вариативностью» ответов двух моделей; усечь реплики под 15-секундный таймер.

**Вход.** `list[QuickfireExchange]` (вопрос + ответ pros + ответ def, 12 шт).
**Выход.** Те же 12 с проставленным `variability_score: float` и `recommended: bool`
(топ-10); плюс усечённые тексты для TTS.

**Алгоритм отбора (Block E.1) — лексические эвристики, ОДИН метод, без API.**
Для каждой пары ответов (`a`, `b`):
1. Токенизировать (lowercase, `\w+`), убрать стоп-слова → множества `Sa`, `Sb`.
2. `jaccard_dist = 1 − |Sa∩Sb| / |Sa∪Sb|`.
3. `len_delta = abs(len(a_tokens) − len(b_tokens)) / max(len_a, len_b, 1)`.
4. `antonym_score`: доля найденных противоположных пар из `ANTONYM_PAIRS`
   (`yes/no`, `increase/decrease`, `valid/invalid`, `legal/illegal`, `true/false`,
   `success/failure`, `rise/fall`, `support/oppose`, …) — присутствие слова из пары в
   `a` и его антонима в `b`.
5. `numeric_divergence`: извлечь числа (regex); 1.0 если множества чисел различны, 0 если
   совпадают, 0.5 если одно пусто.
6. `variability_score = 0.45·jaccard_dist + 0.15·len_delta + 0.25·antonym_score +
   0.15·numeric_divergence` (∈ [0,1]).
Отбор: отсортировать по убыванию, `recommended=True` для топ-10. Метод выбран как
$0/детерминированный/оффлайн; LLM-judge (Gemini, ~$0.01/вопрос) — опция
`quickfire.variability_method="llm_judge"`.

**Усечение 15 с (Block E.2) — ОДИН основной метод + страховка.**
- **Primary (Block E.2-A):** при генерации quickfire `max_tokens=60`. Соотношение для
  ElevenLabs turbo_v2_5: ~150–160 слов/мин → ~2.6 слова/с → 15 с ≈ 38–40 слов ≈ 55–60
  токенов (EN ≈ 1.3 ток/слово).
- **Safety (Block E.2-B):** post-trim до 42 слов перед TTS.
- **Guard (Block E.2-C):** после TTS измерить длительность (`soundfile`); если > 15.0 с —
  выставить `QuickfireExchange.over_limit=True`, показать в UI жёлтый флаг
  «реплика длиннее 15 с» с предложением сократить вручную. Аудио не режется автоматически.

**Зависимости.** `re`, `schemas`, `soundfile` (для guard). Без внешних API в режиме
`lexical`.

**Обработка ошибок.** Пустой ответ модели на вопрос → `variability_score=0`, вопрос не
рекомендуется. Все 12 ниже порога (Block O.3) → UI-предупреждение «вопросы слишком
похожи», предложить регенерацию банка или ручной выбор.

**Что тестировать.** Composite-score на синтетических парах (идентичные → ~0;
противоположные → высоко); отбор ровно 10; усечение по словам; guard-флаг при >15 с.

---

### Модуль 5 — Translation Layer (`modules/translation/`)

**Ответственность.** Для S2-моделей с нефлагманским английским (GigaChat, YandexGPT,
Falcon-H1, HyperCLOVA X, Rakuten AI 3.0) — грамматическая коррекция Gemini 3.1 Pro
**только грамматики/синтаксиса**, без изменения аргументов/фактов/позиции, с детекцией
смыслового дрейфа и авто-откатом.

**Вход.** `original_text: str`, `model_id: str`. **Выход.** `TranslationResult`
(см. Раздел 4): `original_text`, `corrected_text`, `drift_detected`, `drift_score`,
`used_text`, `correction_applied`.

**Constrained prompt Gemini-корректора (Block F.1)** — точный system-prompt:
```
You are a strict copy-editor. Fix ONLY grammar, syntax, spelling, and article/preposition
usage in the user's English text. You MUST NOT:
- rephrase or reorder arguments,
- add, remove, or alter any fact, number, name, or claim,
- change the tone, stance, intensity, or hedging of the position,
- add commentary or meta-text.
Return ONLY the corrected text, nothing else. If the text is already correct, return it
verbatim. Preserve sentence count and argument order exactly.
```
Параметры вызова: `temperature=0.0`, `max_tokens = len(original)·1.3`.

**Drift-детекция (Block F.2) — ОДИН метод: keyword/entity/number overlap, без API.**
1. Множества: числа (regex), Capitalized-токены (имена/сущности), значимые
   существительные (после стоп-слов) — для `original` и `corrected`.
2. `overlap = |keys_corr ∩ keys_orig| / |keys_orig|` (если `keys_orig` пусто → 1.0).
3. `drift_score = 1 − overlap`. `drift_detected = overlap < drift_threshold(0.70)`.
4. Если drift — `used_text = original_text`, `correction_applied=False` (авто-откат);
   иначе `used_text = corrected_text`, `correction_applied=True`. UI показывает откаты.

**Алгоритм.**
1. Если у модели `translation_layer == False` → вернуть `used_text=original`,
   `correction_applied=False` (no-op).
2. Вызвать Gemini-корректор (через Orchestrator, `gemini-3.1-pro`).
3. Прогнать drift-детекцию; собрать `TranslationResult`.

**Зависимости.** `modules.llm` (Gemini), `re`, `schemas`.

**Обработка ошибок.** Сбой Gemini → `used_text=original`, `correction_applied=False`,
лог-warning (не блокирует эпизод). Пустой ответ корректора → откат к оригиналу.

**Что тестировать.** No-op для не-translation моделей; авто-откат при подмене числа/имени
(drift); пропуск корректной правки (overlap=1.0); сбой Gemini → оригинал.

---

### Модуль 6 — TTS Engine (`modules/tts/`)

**Ответственность.** Сгенерировать WAV-файл для каждой реплики через ElevenLabs по
пресету модели; batch с возобновлением из чекпойнта.

**Вход.** Список задач `TTSJob(clip_id, llm_model_id, text, out_path)`; пресеты из
`tts_presets.json`. **Выход.** WAV-файлы в `data/episodes/<ep>/audio/` + `TTSProgress`
чекпойнт (`tts_progress.json`).

**ElevenLabs (Block G.1, июнь 2026).**
- SDK `elevenlabs==2.45.0`; вызов non-streaming: `client.text_to_speech.convert(
  voice_id=..., model_id=..., text=..., output_format="pcm_44100",
  voice_settings=VoiceSettings(stability, similarity_boost, style, use_speaker_boost))`.
- `output_format="pcm_44100"` → записываем как WAV 44.1 кГц/16-бит (PCM). DaVinci Resolve
  19 импортирует такой WAV без перекодирования. (Альтернатива `wav_48000` для 48 кГц/24-бит
  при наличии Pro; формат настраивается в `config.tts_defaults`.) PCM/WAV ≥ 44.1 кГц
  требует Pro-плана.
- Параллелизм: `asyncio.Semaphore(batch_max_parallel=3)` — щадящий лимит для Creator/Pro.
- `VoiceSettings`: `stability, similarity_boost, style, use_speaker_boost` из пресета.

**Алгоритм.**
1. Загрузить/создать `tts_progress.json` (`{clip_id: "done"|"pending"|"failed"}`).
2. Для каждой задачи со статусом ≠ `done`: применить translation layer (если нужно),
   взять пресет по `llm_model_id`, вызвать `convert`, записать PCM в WAV
   (`soundfile.write(out_path, data, 44100, subtype='PCM_16')` или прямой стрим байт).
3. Обновить чекпойнт после каждого файла (atomic write через temp+rename).
4. Измерить длительность (`soundfile.info`) → сохранить в `TimelineData` clip.
5. Прогресс в UI: «18/22 файлов готово…».

**Нейминг/папки (Block G.3).** Только `[a-z0-9_]`, нижний регистр, `_` как разделитель,
без пробелов/спецсимволов (совместимо с DaVinci 19 Mac+Windows; путь < 200 симв).
Структура:
```
data/episodes/ep001_dissolution_ussr/
  audio/  r1_prosecution.wav · r1_defense.wav · r2_q01_prosecution.wav … ·
          r3_p1_prosecution.wav … r3_p3_defense.wav · r4_prosecution.wav · r4_defense.wav
  script/ episode_script.md · host_cues.md · behaviour_flags.json
  timeline/ ep001_dissolution_ussr.fcpxml (primary) · ep001_dissolution_ussr.edl (fallback)
            · ep001_dissolution_ussr_markers.md
  tts_progress.json
```

**Зависимости.** `elevenlabs`, `soundfile`, `asyncio`, `modules.translation`, `schemas`.

**Обработка ошибок (Block O.2).** ElevenLabs недоступен → пометить текущий clip
`failed`, не трогать `done`; UI: «Сгенерировано 5/18, провайдер недоступен» + кнопка
«Возобновить» (resume с чекпойнта, повтор только `pending`/`failed`). `429` → tenacity
backoff. Превышение квоты символов → явное сообщение оператору. **Offline-режим:** если
ключ отсутствует или `app.offline=True` — писать placeholder-WAV (тишина) рассчитанной
длительности, чтобы конвейер/таймлайн прошли end-to-end.

**Что тестировать.** Resume пропускает `done`; atomic-чекпойнт; нейминг clip→filename;
offline placeholder создаёт валидный WAV с измеримой длительностью.

---

### Модуль 7 — Script Builder (`modules/script/`)

**Ответственность.** Собрать человекочитаемый сценарий, реплики ведущего и
машиночитаемые флаги поведения.

**Вход.** `Episode`(generated) + `behaviour_flags` + `timeline_data` (таймкоды).
**Выход.** три файла в `script/`:
- `episode_script.md` — полный сценарий по блокам 1–9 с таймкодами и инлайн-флагами;
- `host_cues.md` — реплики/ремарки ведущего (Блоки 2, 8: интро, вердикт);
- `behaviour_flags.json` — массив `BehaviourFlag` (для UI и таймлайна).

**Алгоритм.**
1. Пройти по структуре эпизода (Блоки 1–9, хронометраж зафиксирован в Разделе 4.6).
2. Для каждой реплики вывести: таймкод (из `timeline_data`), сторону, текст, инлайн-флаги
   (`⟦REFUSED⟧`, `⟦EVASIVE⟧`, …).
3. Вынести host-cues отдельно (intro/context, host verdict).
4. Сериализовать `behaviour_flags.json`.

**Зависимости.** `schemas`, stdlib. **Обработка ошибок.** Отсутствие таймкодов (TTS не
сделан) → собрать сценарий без таймкодов с пометкой «TIMECODES PENDING».

**Что тестировать.** Корректная сборка md из фикстуры эпизода; инлайн-флаги в нужных
репликах; валидный JSON флагов.

---

### Модуль 8 — Timeline Exporter (`modules/timeline/`)

**Ответственность.** Самый сложный модуль. По длительностям WAV рассчитать таймкоды
всех клипов и маркеров поведения, сгенерировать FCPXML 1.11 (primary) и EDL+`.md`
(fallback) для DaVinci Resolve 19.

**Вход.** `Episode` с путями WAV + `behaviour_flags` + `objection_events`. **Выход.**
`<ep>.fcpxml`, `<ep>.edl`, `<ep>_markers.md`; `Episode.timeline_data: TimelineData`.

**Выбор формата (Block H.1, обоснование).** DaVinci Resolve 19 импортирует
AAF/EDL/XML/DRT/ADL/OTIO. **FCPXML 1.11 — primary**, генерируется вручную через `lxml`
(полный контроль над Resolve-совместимой структурой), потому что только он несёт
мультитрек-аудио + текстовые маркеры в одном файле. Известные ограничения Resolve:
FCPXML/OTIO роли могут схлопываться в «супертрек», source-channel мультитрек-аудио
иногда приходит как `mute` — поэтому экспортируем **по одному моно-клипу на дорожку с
явным `lane`** и валидируем по проверенному скелету. **EDL CMX3600 — fallback** (прост
для Python, универсально импортируется, но один трек и без маркеров), поэтому к EDL
прилагается `_markers.md` с таймкодами маркеров для ручной расстановки. OTIO —
опциональный экспорт (`timeline.emit_otio`).

**5 аудио-дорожек (Block H.2).** lane 1 `HOST_VOICE` (placeholder-гэпы «HOST VOICE
HERE»), lane 2 `PROSECUTION` (TTS), lane 3 `DEFENSE` (TTS), lane 4 `SFX_MARKERS`
(placeholder ROUND_START/QUICKFIRE_START), lane 5 `MUSIC_BED` (placeholder).
Маркеры: `OBJECTION_SUSTAINED`, `OBJECTION_OVERRULED`, `REFUSED`, `EVASIVE`, `WEAK`,
`ROUND_START_{N}`, `POINT_{N}`.

**Расчёт таймкодов (Block H.3).** `soundfile` (без ffmpeg) измеряет длительность WAV;
`mutagen` — fallback.
1. После генерации каждого WAV измерить `duration` (сек).
2. Накапливать `timeline_cursor` (running total) по дорожке стороны.
3. Для реплики: `clip_start = cursor`; `clip_end = cursor + duration`; `cursor = clip_end`.
4. Для `BehaviourFlag` в реплике — маркер в `clip_start + relative_offset` (offset по
   позиции цитаты в тексте × средняя скорость речи).
5. Конвертировать секунды → frames (`round(sec·fps)`), fps из `config.timeline.fps=30`.
6. Сохранить всё в `Episode.timeline_data`.

**Минимальный рабочий FCPXML-скелет (Block H.4)** — 2 аудио-дорожки (lanes), 3 клипа
разной длины, 1 текстовый маркер; well-formed, структурирован для DaVinci Resolve 19:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
  <resources>
    <!-- 30 fps timeline format; audio-only project still needs a video format -->
    <format id="r1" name="FFVideoFormat1080p30" frameDuration="1/30s"
            width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)"/>
    <!-- one asset per WAV; durations are exact frame counts at 30 fps -->
    <asset id="a1" name="r1_prosecution" start="0s" duration="240/30s"
           hasVideo="0" hasAudio="1" audioSources="1" audioChannels="1" audioRate="44100">
      <media-rep kind="original-media"
                 src="file://./data/episodes/ep001_dissolution_ussr/audio/r1_prosecution.wav"/>
    </asset>
    <asset id="a2" name="r1_defense" start="0s" duration="300/30s"
           hasVideo="0" hasAudio="1" audioSources="1" audioChannels="1" audioRate="44100">
      <media-rep kind="original-media"
                 src="file://./data/episodes/ep001_dissolution_ussr/audio/r1_defense.wav"/>
    </asset>
    <asset id="a3" name="r2_q01_prosecution" start="0s" duration="120/30s"
           hasVideo="0" hasAudio="1" audioSources="1" audioChannels="1" audioRate="44100">
      <media-rep kind="original-media"
                 src="file://./data/episodes/ep001_dissolution_ussr/audio/r2_q01_prosecution.wav"/>
    </asset>
  </resources>
  <library>
    <event name="The Narrative Court">
      <project name="ep001_dissolution_ussr">
        <!-- audioLayout/audioRate set the master bus; tcStart 0, non-drop -->
        <sequence format="r1" duration="540/30s" tcStart="0s" tcFormat="NDF"
                  audioLayout="stereo" audioRate="44100">
          <spine>
            <!-- primary storyline is a silent gap; audio clips hang on negative lanes -->
            <gap name="Timeline" offset="0s" duration="540/30s" start="0s">
              <!-- PROSECUTION track = lane -1 -->
              <asset-clip ref="a1" lane="-1" offset="0s" name="r1_prosecution"
                          duration="240/30s" audioRole="dialogue.prosecution">
                <marker start="60/30s" duration="1/30s" value="ROUND_START_1"/>
              </asset-clip>
              <asset-clip ref="a3" lane="-1" offset="240/30s" name="r2_q01_prosecution"
                          duration="120/30s" audioRole="dialogue.prosecution"/>
              <!-- DEFENSE track = lane -2 -->
              <asset-clip ref="a2" lane="-2" offset="0s" name="r1_defense"
                          duration="300/30s" audioRole="dialogue.defense">
                <marker start="150/30s" duration="1/30s" value="OBJECTION_SUSTAINED"/>
              </asset-clip>
            </gap>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
```

**EDL fallback (Block H.5)** — те же 3 клипа (CMX3600, один трек, без маркеров):
```
TITLE: ep001_dissolution_ussr
FCM: NON-DROP FRAME

001  r1_prosec AA     C        00:00:00:00 00:00:08:00 00:00:00:00 00:00:08:00
* FROM CLIP NAME: r1_prosecution.wav
002  r1_defen  AA     C        00:00:00:00 00:00:10:00 00:00:08:00 00:00:18:00
* FROM CLIP NAME: r1_defense.wav
003  r2q01_pro AA     C        00:00:00:00 00:00:04:00 00:00:18:00 00:00:22:00
* FROM CLIP NAME: r2_q01_prosecution.wav
```
Сопроводительный `ep001_dissolution_ussr_markers.md`:
```markdown
# Markers — ep001_dissolution_ussr (place manually on a marker track)
| Timecode (30fps) | Marker              | Track       | Source clip          |
|------------------|---------------------|-------------|----------------------|
| 00:00:02:00      | ROUND_START_1       | SFX_MARKERS | r1_prosecution.wav   |
| 00:00:13:00      | OBJECTION_SUSTAINED | SFX_MARKERS | r1_defense.wav       |
```

**Зависимости.** `lxml`, `soundfile` (+`mutagen`), `schemas`.

**Обработка ошибок (Block O.4).** FCPXML не импортируется в DaVinci → диагностика:
(1) `lxml` валидирует well-formedness при генерации; (2) UI-кнопка «использовать EDL
fallback»; (3) `_markers.md` всегда генерируется. Отсутствующий WAV → клип пропускается
с предупреждением, таймлайн строится из доступных.

**Что тестировать (`tests/test_exporter.py`, обязателен для MVP).**
(a) таймкоды: 3 клипа 8/10/4 с → корректные frame-offsets и накопление cursor;
(b) FCPXML парсится `lxml.etree.fromstring` без ошибок (well-formed), содержит 3
`asset-clip`, 2 разных `lane`, ≥ 1 `marker`; (c) EDL содержит 3 события и корректные
record-таймкоды; (d) `_markers.md` содержит строку на каждый `BehaviourFlag`.

---

### Модуль 9 — Leaderboard Engine (`modules/leaderboard/`)

**Ответственность.** Хранить и пересчитывать 5 метрик **по модели** (не по роли) и
экспортировать Markdown/CSV.

**5 метрик (по модели).** (1) Win-count; (2) % objection sustained; (3) % explicit
refusals (только DeepSeek); (4) среднее sustained-objections на эпизод; (5) win-streak.

**Вход.** Введённая оператором Oxford-дельта эпизода + накопленные
`behaviour_flags`/`objection_events`. **Выход.** строки `LeaderboardEntry`,
`leaderboard.md`, `leaderboard.csv` в `data/exports/`.

**Oxford-дельта (фиксировано).**
```
Δ_prosecution = (%"agree" after) − (%"agree" before)
Δ_defense     = (%"disagree" after) − (%"disagree" before)
winner = argmax(Δ_prosecution, Δ_defense)
```
Кворум: ≥ 30 голосов в каждом опросе. Меньше → эпизод `no_quorum`, win не засчитывается.

**Алгоритм пересчёта (Block I.2).**
1. Определить `winner_model` по Δ (если кворум; иначе stop, пометить `no_quorum`).
2. `win_count += 1` для победителя.
3. Пересчитать `% objection sustained` = sustained / (sustained+overruled) по модели.
4. Пересчитать `% explicit refusals` (только DeepSeek) = REFUSED-эпизоды / всего эпизодов.
5. Пересчитать среднее sustained на эпизод (rolling).
6. Обновить `win_streak` (победа: +1; поражение: reset 0).
7. Экспортировать Markdown + CSV.

**Markdown-шаблон (Block I.3).**
```markdown
## Season 1 Standings — Episode 8 of 10

| Model           | Wins | Obj% | Ref% | Avg Sus | Streak |
|-----------------|------|------|------|---------|--------|
| Claude Opus 4.8 |  5   | 78%  |  n/a |   1.8   |   3    |
| GPT-5.5         |  4   | 65%  |  n/a |   1.5   |   0    |
| DeepSeek V4     |  2   | 45%  |  12% |   1.1   |   0    |

Last updated: 2026-11-03 after Ep.8 "USSR Dissolution"
```

**Зависимости.** `SQLAlchemy`, `schemas`, `csv`. **Обработка ошибок (Block O.5).**
Oxford-дельта не введена для опубликованного эпизода → лидерборд считается без него,
строка эпизода помечается `pending_delta`; при старте нового эпизода — мягкое
предупреждение (не блокировка).

**Что тестировать.** Argmax winner; кворум-гейт (29 голосов → no_quorum);
streak reset; rolling-average; корректность MD/CSV.

---

### Модуль 10 — Topic Bank (`modules/topics/`)

**Ответственность.** Хранить темы с тезисами/вариантами, авто-чек-лист утверждения,
банк quickfire-вопросов.

**Вход/Выход.** `Topic` (Pydantic, Раздел 4) ↔ таблица `topics`. **Чек-лист (Block J.2)**
реализован как `@model_validator(mode="after")`, вычисляющий `auto_status`:
- `approved`: все bool=True И `deepseek_risk ∈ {low, medium}`;
- `rejected`: `deepseek_risk == "guaranteed_refused"`;
- `warning`: иначе.

**Алгоритм.** При сохранении темы: валидировать Pydantic → `auto_status` → запись в БД.
Фильтры по `status`/`category`/`monetization_risk`/`deepseek_risk` для UI.

**Зависимости.** `pydantic`, `SQLAlchemy`, `schemas`. **Обработка ошибок.** Невалидная
тема (нет тезиса/< 1 quickfire) → ошибка валидации с русским сообщением в UI.

**Что тестировать.** `auto_status` по матрице правил; фильтры; сериализация в БД.

---

### Модуль 11 — Metadata Generator (`modules/metadata/`)

**Ответственность.** Шаблонизировать YouTube-метаданные и Community-поллы.

**Вход.** `Episode` (+ модели, тезис). **Выход.** `dict`: `title` (≤ 60 симв), `description`,
`tags`, `chapters`, `pre_poll`, `post_poll`.

**Шаблоны (Block K).**
```
TITLE_TEMPLATE = "{conflict_phrase} | Two AIs Debate, You Judge"   # ≤ 60 chars
```
`DESCRIPTION_TEMPLATE` — с hook, motion, ролями, таймкодами (Блоки 0:00…22:00),
disclaimer и AI-раскрытием, тегами. Фиксированный disclaimer:
«This is an educational debate. The arguments are generated by AI language models
assigned to sides by random draw and do NOT represent the views of the creator, the
model providers, or any government.» AI-раскрытие: «Voices and arguments in this video
are AI-generated.»

**Community-поллы (Block K.2).** `PRE_POLL`: «Before watching: do you AGREE or DISAGREE
that "{thesis}"?» (Agree/Disagree). `POST_POLL`: «After watching: AGREE or DISAGREE that
"{thesis}"?» — для Oxford-дельты.

**Зависимости.** `schemas`, stdlib. **Обработка ошибок.** `conflict_phrase` > 60 симв →
усечь по словам + многоточие. **Что тестировать.** Длина title; подстановка переменных;
наличие disclaimer/AI-раскрытия.

---

### Модуль 12 — Episode Manager (`modules/episodes/`)

**Ответственность.** Статус-машина эпизода и оркестрация шагов Episode Studio; авто-сейв
состояния каждого шага.

**Статусы.** `draft → smoke_tested → generated → tts_done → exported → published`.
Переход разрешён только вперёд (или явный re-run шага). Каждый переход — транзакция БД.

**Вход/Выход.** `Episode` (Pydantic в памяти ↔ ORM в БД). **Алгоритм.** Координирует
вызовы модулей 1–11 по шагам, сохраняет промежуточное состояние (resume после
перезапуска), валидирует предусловия перехода.

**Зависимости.** все доменные модули, `SQLAlchemy`, `schemas`. **Обработка ошибок.**
Несоблюдённое предусловие (напр. export до tts_done) → блок с подсказкой. Краш в
середине → восстановление из последнего сохранённого шага.

**Что тестировать.** Легальные/нелегальные переходы; восстановление состояния; запись
папки эпизода.

---

### Модуль 13 — Web UI (`ui/`)

**Ответственность.** 8 экранов NiceGUI для нетехнического оператора.

**8 экранов (Block L.1).** (1) **Dashboard** — текущий эпизод, top-5 лидерборда,
ближайшие 3 эпизода, быстрые статусы; (2) **Topic Bank** — таблица+фильтры, форма
добавления, интерактивный чек-лист 6 критериев с авто-рекомендацией; (3) **Episode
Planner** — сезонная сетка 10 эпизодов с drag-and-drop, назначение пары, статус-бейджи;
(4) **Episode Studio** — главный экран, 10 шагов (ниже); (5) **Script Viewer** — полный
сценарий, цветовые флаги, копирование секций, таймкоды; (6) **Leaderboard** — 5 метрик,
история, форма Oxford-дельты, экспорт MD/CSV; (7) **Config** — API-ключи (masked),
15 TTS-пресетов (редактируемые), дефолты, пути; (8) **Logs** — история вызовов, фильтры
по эпизоду/модели/статусу, просмотр всех вариантов, экспорт прогона как `selected`.

**Episode Studio — 10 шагов (Block L.1.4).** 1 выбор темы+пары; 2 настройка
(temperature/seed/max_tokens); 3 smoke-test (PASS/FAIL + reframe + retry); 4 полная
генерация (прогресс-бар + streaming-лог, каждая реплика по готовности); 5 ревью контента
(флаги цветом: REFUSED/SUPPRESSED — красный, EVASIVE/WEAK — жёлтый); 6 управление
objection (Sustained/Overruled только для factual); 7 TTS batch (прогресс, resume);
8 ревью quickfire (12 со score, выбрать 10, рекомендованные выделены); 9 экспорт
таймлайна (FCPXML+EDL, индикатор/повтор); 10 метаданные YouTube (copy-to-clipboard).

**UX-требования (Block L.2).** Подтверждение деструктивных действий; прогресс-бар +
streaming-лог для долгих операций; русские сообщения об ошибках с рекомендуемым
действием и кнопкой повтора; авто-показ reframe при smoke FAIL; ни один экран не требует
Python/JSON/API; tab-навигация в Studio; авто-сейв каждого шага.

**Мобильная адаптация (Block L.3) — вывод: НЕ делать.** Система — рабочая станция со
сложным multi-step UI (drag-and-drop, длинные сценарии, таблицы пресетов). Мобильный
«пульт» не оправдывает затрат; NiceGUI отдаёт адаптивный layout «как есть», отдельная
мобильная версия не разрабатывается.

**Зависимости.** `nicegui`, все доменные модули. **Обработка ошибок.** Любое исключение
бэкенда → `ui.notify` (русский текст) + лог; долгие задачи в `background_tasks` не
блокируют event loop. **Что тестировать.** (UI — ручная проверка по accept-критериям;
автоматизируется логика страниц, не рендер.)

---

### Модуль 14 — Config & Security (`config.py` + `config/`)

**Ответственность.** Загрузка `config.yaml`, секретов (keyring→.env), валидация при
старте, onboarding первого запуска, аудит-воспроизводимость.

**Вход.** `config/config.yaml`, keyring/`config/.env`. **Выход.** объект `Config`
(валидированный Pydantic-settings) + `get_secret(env_name) -> str | None`.

**Хранение секретов (Block M.2).** `get_secret`: сначала `keyring.get_password("narrative_court", env_name)`;
если `None` — `os.environ` (загруженный `python-dotenv` из `config/.env`). Onboarding-wizard
пишет ключи в keyring. Секреты никогда не пишутся в репозиторий/логи (маскирование).

**Воспроизводимость (Block M.3).** Каждый прогон фиксирует в `GenerationLog`: `seed`,
`temperature`, `model_version` (из ответа API, если есть), `timestamp`, `prompt_hash`
(sha256 system+user). Для аудита этики («как выбран этот аргумент?»).

**Onboarding (Block O.7).** Первый запуск (пустая БД, незаполненный конфиг, нет ключей):
запустить setup-wizard — (1) создать `data/*` каталоги и БД (`create_all`), (2) форма
ввода ключей → keyring, (3) проверка доступности провайдеров (lightweight ping/whoami),
(4) выбор путей. Без ключей выбранной пары — мягкая блокировка генерации с инструкцией.

**Зависимости.** `pydantic`, `PyYAML`, `keyring`, `python-dotenv`. **Обработка ошибок.**
Отсутствует `config.yaml` → создать из дефолтного шаблона. Битый YAML → явная ошибка со
строкой. **Что тестировать.** `get_secret` keyring→env приоритет; валидация конфига;
маскирование секретов; ветка onboarding при пустой БД.

---

## Раздел 4. Схемы данных (Pydantic v2)

Канонический источник — `modules/schemas.py`. Все доменные объекты — Pydantic v2.

### 4.1. Enums и базовые типы
```python
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, model_validator


class EpisodeStatus(str, Enum):
    DRAFT = "draft"
    SMOKE_TESTED = "smoke_tested"
    GENERATED = "generated"
    TTS_DONE = "tts_done"
    EXPORTED = "exported"
    PUBLISHED = "published"


class TopicStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    REJECTED = "rejected"


class Side(str, Enum):
    PROSECUTION = "prosecution"
    DEFENSE = "defense"


FlagType = Literal["WEAK", "EVASIVE", "REFUSED", "SUPPRESSED"]
DeepSeekRisk = Literal["low", "medium", "high", "guaranteed_refused"]
MonetizationRisk = Literal["green", "yellow", "red"]
```

### 4.2. Генерация и поведение
```python
class GenerationResult(BaseModel):
    content: str
    reasoning_content: str | None = None      # DeepSeek/Claude CoT, when exposed
    finish_reason: str                        # "stop" | "length" | "content_filter" | ...
    model_id: str
    model_version: str | None = None          # echoed by provider when available
    latency_ms: int = 0
    prompt_hash: str = ""                      # sha256(system + "\x00" + user)
    usage: dict | None = None                  # tokens, when provided
    raw: dict | None = None                    # provider-native payload for audit


class BehaviourFlag(BaseModel):
    flag_type: FlagType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str                              # quote / measurement that triggered the flag
    rule_triggered: str                        # e.g. "rule3_empty_reasoning"
    model_id: str
    round_id: str                              # e.g. "r1_prosecution", "r3_p2_defense"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GenerationLog(BaseModel):
    log_id: UUID = Field(default_factory=uuid4)
    episode_id: UUID
    model_id: str
    round_id: str
    attempt_number: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    system_prompt: str
    user_prompt: str
    temperature: float
    seed: int | None = None
    result: GenerationResult
    selected: bool = False                     # operator/auto chose this run
    selection_policy: Literal["first_valid", "manual"] = "first_valid"


class TranslationResult(BaseModel):
    original_text: str
    corrected_text: str
    drift_detected: bool
    drift_score: float | None = None
    used_text: str                             # original if drift else corrected
    correction_applied: bool


class TTSPreset(BaseModel):
    llm_model_id: str
    el_voice_id: str
    stability: float = Field(ge=0.0, le=1.0)
    similarity_boost: float = Field(ge=0.0, le=1.0)
    style: float = Field(ge=0.0, le=1.0)
    use_speaker_boost: bool = True
    el_model_id: str = "eleven_turbo_v2_5"
    character_notes: str = ""
```

### 4.3. Композиция эпизода и таймлайн
```python
class Replica(BaseModel):
    round_id: str
    side: Side
    model_id: str
    text: str
    used_text: str | None = None               # post translation/truncation, fed to TTS
    audio_path: str | None = None
    duration_sec: float | None = None
    flags: list[BehaviourFlag] = Field(default_factory=list)


class QuickfireExchange(BaseModel):
    question: str
    prosecution_answer: str
    defense_answer: str
    variability_score: float = 0.0
    recommended: bool = False
    over_limit: bool = False                    # TTS audio measured > 15 s


class ObjectionEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    round_id: str
    side: Side
    claim: str                                  # the factual claim being challenged
    ruling: Literal["sustained", "overruled", "pending"] = "pending"
    timestamp: datetime | None = None


class TimelineClip(BaseModel):
    clip_id: str                                # e.g. "r1_prosecution"
    track: Literal["HOST_VOICE", "PROSECUTION", "DEFENSE", "SFX_MARKERS", "MUSIC_BED"]
    lane: int                                   # FCPXML lane (negative for audio under spine)
    audio_path: str | None = None               # None => placeholder gap
    start_frames: int
    duration_frames: int


class TimelineMarker(BaseModel):
    marker_type: str                            # OBJECTION_SUSTAINED / REFUSED / ROUND_START_1 ...
    frame: int
    track: str = "SFX_MARKERS"
    note: str = ""


class TimelineData(BaseModel):
    fps: int = 30
    sample_rate: int = 44100
    clips: list[TimelineClip] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    total_frames: int = 0


class GenParams(BaseModel):
    temperature: float = 0.7
    max_tokens: int = 800
    quickfire_max_tokens: int = 60
    seed: int | None = None


class Episode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: EpisodeStatus = EpisodeStatus.DRAFT
    thesis: str
    slug: str                                   # filesystem-safe, e.g. "ep001_dissolution_ussr"
    topic_id: UUID | None = None
    prosecution_model_id: str
    defense_model_id: str
    gen_params: GenParams = Field(default_factory=GenParams)
    # round_id -> replicas (r1_prosecution, r1_defense, r3_p1_*, r4_*, ...)
    rounds: dict[str, list[Replica]] = Field(default_factory=dict)
    quickfire: list[QuickfireExchange] = Field(default_factory=list)
    behaviour_flags: list[BehaviourFlag] = Field(default_factory=list)
    objections: list[ObjectionEvent] = Field(default_factory=list)
    tts_files: list[str] = Field(default_factory=list)
    timeline_data: TimelineData | None = None
    leaderboard_result: dict | None = None      # filled after Oxford delta entry
    youtube_metadata: dict = Field(default_factory=dict)
```

### 4.4. Темы, чек-лист, лидерборд, Oxford
```python
class TopicChecklist(BaseModel):
    has_evidence_both_sides: bool
    is_debatable: bool                          # not a false symmetry
    deepseek_risk: DeepSeekRisk
    monetization_risk: MonetizationRisk
    reach_vs_safety: int = Field(ge=1, le=5)
    freshness_vs_evergreen: int = Field(ge=1, le=5)
    auto_status: Literal["approved", "warning", "rejected"] = "warning"

    @model_validator(mode="after")
    def _compute_status(self) -> "TopicChecklist":
        if self.deepseek_risk == "guaranteed_refused":
            self.auto_status = "rejected"
        elif (self.has_evidence_both_sides and self.is_debatable
              and self.deepseek_risk in ("low", "medium")):
            self.auto_status = "approved"
        else:
            self.auto_status = "warning"
        return self


class QuickfireQuestion(BaseModel):
    id: str
    text: str


class Topic(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thesis: str
    thesis_variants: list[str] = Field(default_factory=list, max_length=3)
    category: Literal["safe", "optimal", "hot"]
    monetization_risk: MonetizationRisk
    deepseek_risk: DeepSeekRisk
    recommended_pair: tuple[str, str]
    factual_anchors: list[str] = Field(default_factory=list)
    quickfire_bank: list[QuickfireQuestion] = Field(default_factory=list)
    status: TopicStatus = TopicStatus.DRAFT
    episode_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    checklist: TopicChecklist
    notes: str = ""


class OxfordDelta(BaseModel):
    episode_id: UUID
    agree_before: float                         # percent 0..100
    agree_after: float
    disagree_before: float
    disagree_after: float
    votes_before: int
    votes_after: int

    @property
    def no_quorum(self) -> bool:
        return self.votes_before < 30 or self.votes_after < 30

    @property
    def delta_prosecution(self) -> float:
        return self.agree_after - self.agree_before

    @property
    def delta_defense(self) -> float:
        return self.disagree_after - self.disagree_before

    @property
    def winner_side(self) -> Side | None:
        if self.no_quorum:
            return None
        return Side.PROSECUTION if self.delta_prosecution >= self.delta_defense else Side.DEFENSE


class LeaderboardEntry(BaseModel):
    model_id: str
    display_name: str
    season: int
    win_count: int = 0
    objection_sustained_pct: float | None = None
    explicit_refusal_pct: float | None = None   # only DeepSeek; None => "n/a"
    avg_sustained_per_episode: float = 0.0
    win_streak: int = 0
```

### 4.4.1. Пример валидного `Episode` (JSON, сокращён)
```json
{
  "id": "0a3f5e2c-1b44-4f7a-9c1e-2f9d4b6a8c10",
  "created_at": "2026-06-24T10:00:00Z",
  "status": "generated",
  "thesis": "The dissolution of the USSR was inevitable.",
  "slug": "ep001_dissolution_ussr",
  "topic_id": "9b2e1d77-3a55-4c0b-8e21-7f6a1c2d3e44",
  "prosecution_model_id": "gpt-5.5",
  "defense_model_id": "deepseek-v4-pro",
  "gen_params": {"temperature": 0.7, "max_tokens": 800, "quickfire_max_tokens": 60, "seed": 42},
  "rounds": {
    "r1_prosecution": [{"round_id": "r1_prosecution", "side": "prosecution",
      "model_id": "gpt-5.5", "text": "The command economy could not...",
      "used_text": "The command economy could not...",
      "audio_path": "audio/r1_prosecution.wav", "duration_sec": 8.0, "flags": []}],
    "r1_defense": [{"round_id": "r1_defense", "side": "defense",
      "model_id": "deepseek-v4-pro", "text": "Structural reform was viable...",
      "audio_path": "audio/r1_defense.wav", "duration_sec": 10.0,
      "flags": [{"flag_type": "SUPPRESSED", "confidence": 0.8,
        "evidence": "reasoning_content len=12 < 80", "rule_triggered": "rule3_empty_reasoning",
        "model_id": "deepseek-v4-pro", "round_id": "r1_defense",
        "timestamp": "2026-06-24T10:00:05Z"}]}]
  },
  "quickfire": [{"question": "Was the August coup decisive?",
    "prosecution_answer": "Yes, it shattered central authority.",
    "defense_answer": "No, decline predated it.",
    "variability_score": 0.71, "recommended": true, "over_limit": false}],
  "behaviour_flags": [],
  "objections": [{"id": "11111111-2222-3333-4444-555555555555",
    "round_id": "r3_p1_prosecution", "side": "prosecution",
    "claim": "GDP fell 40% in 1991", "ruling": "sustained", "timestamp": "2026-06-24T10:05:00Z"}],
  "tts_files": ["audio/r1_prosecution.wav", "audio/r1_defense.wav"],
  "timeline_data": {"fps": 30, "sample_rate": 44100,
    "clips": [{"clip_id": "r1_prosecution", "track": "PROSECUTION", "lane": -1,
      "audio_path": "audio/r1_prosecution.wav", "start_frames": 0, "duration_frames": 240}],
    "markers": [{"marker_type": "OBJECTION_SUSTAINED", "frame": 150,
      "track": "SFX_MARKERS", "note": "GDP fell 40% in 1991"}], "total_frames": 540},
  "leaderboard_result": null,
  "youtube_metadata": {}
}
```

### 4.5. Системные промпты (Block C.3) — `modules/generator/prompts/`

Техника: academic-adversarial framing + явная инструкция «maintain your position» +
запрет хеджирования. Слот `{thesis}` подставляется генератором. Под-стороны Round 3/4
получают дополнительный user-блок инъекции (см. Модуль 3).

**`prosecution_s1.txt`**
```
You are PROSECUTION in a structured academic debate ("The Narrative Court"). This is a
formal adversarial exercise for educational analysis. You have been ASSIGNED, by random
draw, to argue that the motion STANDS. Your assignment reflects neither truth nor your
own view; your task is to build the strongest possible case FOR the motion.

MOTION: "{thesis}"

Rules:
1. Argue ONLY the prosecution side. Maintain your position for the entire debate. Never
   concede the motion or adopt the defense's framing.
2. Do NOT hedge. Forbidden moves include: "however the other side argues", "it depends",
   "on the other hand", "both sides have merit", "it's worth noting the opposing view",
   "it's important to acknowledge". State claims with conviction.
3. Ground every point in verifiable evidence: specific facts, dates, figures, and causal
   mechanisms. Prefer concrete mechanisms over abstractions.
4. Be concise and broadcast-ready: short, declarative sentences for spoken audio.
5. Stay in role. Output ONLY your argument. No preamble, no meta-commentary.
```

**`defense_s1.txt`** — идентичен, но: `argue that the motion FAILS`, `Argue ONLY the
defense side`, `Never concede the motion or adopt the prosecution's framing`.

**`prosecution_s2.txt`** — как `prosecution_s1.txt` плюс строка 6:
```
6. Write in clear, simple English. Your text may be lightly copy-edited for grammar and
   syntax only; its arguments, facts, and stance will be preserved exactly.
```

**`defense_s2.txt`** — `defense_s1.txt` плюс та же строка 6.

### 4.6. Эталонная структура эпизода (хронометраж зафиксирован)
| Блок | Время | Содержание | Источник аудио |
|---|---|---|---|
| 0 | offscreen | Pre-poll (Community-пост) | — |
| 1 | 0:00–0:20 | Тизер/хук | монтаж |
| 2 | 0:20–3:00 | Интро + контекст | голос ведущего (placeholder) |
| 3 | 3:00–3:20 | Назначение сторон | монтаж |
| 4 | 3:20–8:00 | Round 1: позиции | `r1_prosecution`, `r1_defense` |
| 5 | 8:00–12:00 | Round 2: quickfire (8–10 Q, split-screen, таймер 15 с) | `r2_qNN_*` |
| 6 | 12:00–17:00 | Round 3: ребаттлы (Point 1/2/3 + objection) | `r3_pN_*` |
| 7 | 17:00–20:00 | Round 4: заключения | `r4_prosecution`, `r4_defense` |
| 8 | 20:00–22:00 | Вердикт ведущего | голос ведущего (placeholder) |
| 9 | 22:00–23:00 | Вердикт зрителей | монтаж |

`round_id`-конвенция: `r1_prosecution`, `r1_defense`, `r2_q01_prosecution`…`r2_q12_defense`,
`r3_p1_prosecution`…`r3_p3_defense`, `r4_prosecution`, `r4_defense`.

### 4.7. Схема SQLite (DDL, Block I.1)
```sql
PRAGMA foreign_keys = ON;

CREATE TABLE models (
    id                TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    provider          TEXT NOT NULL,
    api_format        TEXT NOT NULL CHECK (api_format IN
                        ('openai','anthropic','google','yandex','gigachat','falcon','hyperclova')),
    model_name        TEXT NOT NULL,
    season            INTEGER NOT NULL CHECK (season IN (1,2)),
    translation_layer INTEGER NOT NULL DEFAULT 0 CHECK (translation_layer IN (0,1)),
    sanctions_risk    INTEGER NOT NULL DEFAULT 0 CHECK (sanctions_risk IN (0,1))
);

CREATE TABLE topics (
    id                TEXT PRIMARY KEY,
    thesis            TEXT NOT NULL,
    category          TEXT NOT NULL CHECK (category IN ('safe','optimal','hot')),
    monetization_risk TEXT NOT NULL CHECK (monetization_risk IN ('green','yellow','red')),
    deepseek_risk     TEXT NOT NULL CHECK (deepseek_risk IN ('low','medium','high','guaranteed_refused')),
    status            TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','approved','scheduled','completed','rejected')),
    auto_status       TEXT CHECK (auto_status IN ('approved','warning','rejected')),
    payload_json      TEXT NOT NULL,   -- full Topic dump: variants, anchors, quickfire bank, checklist
    created_at        TEXT NOT NULL
);

CREATE TABLE episodes (
    id                   TEXT PRIMARY KEY,
    slug                 TEXT NOT NULL UNIQUE,
    thesis               TEXT NOT NULL,
    topic_id             TEXT REFERENCES topics(id) ON DELETE SET NULL,
    status               TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft','smoke_tested','generated','tts_done','exported','published')),
    prosecution_model_id TEXT NOT NULL REFERENCES models(id),
    defense_model_id     TEXT NOT NULL REFERENCES models(id),
    season               INTEGER NOT NULL,
    episode_no           INTEGER,
    payload_json         TEXT NOT NULL,   -- full Episode dump
    created_at           TEXT NOT NULL,
    published_at         TEXT
);
CREATE INDEX idx_episodes_status ON episodes(status);
CREATE INDEX idx_episodes_season ON episodes(season, episode_no);

CREATE TABLE episode_pairs (
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    side       TEXT NOT NULL CHECK (side IN ('prosecution','defense')),
    model_id   TEXT NOT NULL REFERENCES models(id),
    PRIMARY KEY (episode_id, side)
);

CREATE TABLE behaviour_flags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id    TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    model_id      TEXT NOT NULL REFERENCES models(id),
    round_id      TEXT NOT NULL,
    flag_type     TEXT NOT NULL CHECK (flag_type IN ('WEAK','EVASIVE','REFUSED','SUPPRESSED')),
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence      TEXT,
    rule_triggered TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_flags_episode ON behaviour_flags(episode_id);
CREATE INDEX idx_flags_model   ON behaviour_flags(model_id, flag_type);

CREATE TABLE objection_events (
    id         TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    round_id   TEXT NOT NULL,
    side       TEXT NOT NULL CHECK (side IN ('prosecution','defense')),
    model_id   TEXT NOT NULL REFERENCES models(id),
    claim      TEXT NOT NULL,
    ruling     TEXT NOT NULL DEFAULT 'pending' CHECK (ruling IN ('sustained','overruled','pending')),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_obj_episode ON objection_events(episode_id);

CREATE TABLE oxford_deltas (
    episode_id      TEXT PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    agree_before    REAL NOT NULL, agree_after    REAL NOT NULL,
    disagree_before REAL NOT NULL, disagree_after REAL NOT NULL,
    votes_before    INTEGER NOT NULL, votes_after  INTEGER NOT NULL,
    winner_model_id TEXT REFERENCES models(id),
    no_quorum       INTEGER NOT NULL DEFAULT 0 CHECK (no_quorum IN (0,1)),
    created_at      TEXT NOT NULL
);

CREATE TABLE leaderboard_aggregate (
    model_id                 TEXT PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
    season                   INTEGER NOT NULL,
    win_count                INTEGER NOT NULL DEFAULT 0,
    objection_sustained_pct  REAL,
    explicit_refusal_pct     REAL,                 -- only DeepSeek; NULL => n/a
    avg_sustained_per_episode REAL NOT NULL DEFAULT 0,
    win_streak               INTEGER NOT NULL DEFAULT 0,
    updated_at               TEXT NOT NULL
);
```

---

## Раздел 5. Конфигурационные файлы

Канонические артефакты лежат в репозитории и являются источником истины:
- **`config/config.yaml`** — несекретные настройки: `app`, `generation_defaults`,
  `tts_defaults`, `quickfire`, `detector`, `translation`, `timeline`, `oxford`, реестр
  `models` (все 15 с `api_format`, `base_url`, `translation_layer`, `season`,
  `sanctions_risk`, `behaviour_profile`), `translation_corrector`. GigaChat помечен
  `sanctions_risk: true` — **[SANCTIONS RISK — legal review required, INA §329]**.
- **`config/.env.example`** — имена всех ключей (15 моделей + ElevenLabs + GigaChat OAuth
  + Yandex folder). Копируется в `config/.env` (gitignored). Приоритет загрузки:
  keyring → `.env`.
- **`config/tts_presets.json`** — 15 пресетов (`el_voice_id` — placeholders для замены
  через экран Config).

`config.py` валидирует `config.yaml` через Pydantic-settings при старте; при отсутствии
файла создаёт из дефолта; при битом YAML — явная ошибка с номером строки.

---

## Раздел 6. Файловая структура проекта
```
narrative.court/
├── main.py                       # entry point: load config, init DB, launch NiceGUI
├── requirements.txt
├── TZ_Narrative_Court.md         # this document
├── config/
│   ├── config.yaml
│   ├── tts_presets.json
│   ├── .env.example
│   └── .env                      # gitignored, created by operator
├── modules/
│   ├── __init__.py
│   ├── schemas.py                # all Pydantic v2 models (Раздел 4)
│   ├── config.py                 # Config loader, get_secret(), onboarding
│   ├── db.py                     # SQLAlchemy engine, session, create_all, DDL models
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── orchestrator.py
│   │   ├── models_registry.py
│   │   └── adapters/
│   │       ├── __init__.py
│   │       ├── base.py           # ModelAdapter ABC
│   │       ├── openai_adapter.py # also drives every OpenAI-compatible base_url
│   │       ├── anthropic_adapter.py
│   │       ├── google_adapter.py
│   │       ├── yandex_adapter.py
│   │       ├── gigachat_adapter.py   # [SANCTIONS RISK]
│   │       ├── falcon_adapter.py
│   │       ├── hyperclova_adapter.py
│   │       └── mock_adapter.py   # offline deterministic responses
│   ├── detector/
│   │   ├── __init__.py
│   │   ├── behaviour_detector.py
│   │   ├── hedging_patterns.py
│   │   └── deepseek_censor.py
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── episode_generator.py
│   │   ├── smoke_test.py
│   │   └── prompts/
│   │       ├── prosecution_s1.txt
│   │       ├── defense_s1.txt
│   │       ├── prosecution_s2.txt
│   │       └── defense_s2.txt
│   ├── quickfire/
│   │   ├── __init__.py
│   │   ├── manager.py
│   │   └── variability.py
│   ├── translation/
│   │   ├── __init__.py
│   │   └── gemini_corrector.py
│   ├── tts/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── presets.py
│   ├── script/
│   │   ├── __init__.py
│   │   └── builder.py
│   ├── timeline/
│   │   ├── __init__.py
│   │   ├── exporter.py
│   │   ├── fcpxml_generator.py
│   │   ├── edl_generator.py
│   │   └── timecode_calculator.py
│   ├── leaderboard/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── export.py
│   ├── topics/
│   │   ├── __init__.py
│   │   ├── bank.py
│   │   └── checklist.py
│   ├── metadata/
│   │   ├── __init__.py
│   │   └── generator.py
│   └── episodes/
│       ├── __init__.py
│       └── manager.py
├── ui/
│   ├── __init__.py
│   ├── app.py                    # NiceGUI assembly + navigation
│   ├── pages/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── topic_bank.py
│   │   ├── episode_planner.py
│   │   ├── episode_studio.py
│   │   ├── script_viewer.py
│   │   ├── leaderboard.py
│   │   ├── config_page.py
│   │   └── logs.py
│   └── components/
│       ├── __init__.py
│       └── progress.py
├── data/                         # created on first run
│   ├── episodes/
│   ├── exports/
│   └── logs/
└── tests/
    ├── __init__.py
    ├── test_detector.py          # mandatory for MVP
    └── test_exporter.py          # mandatory for MVP
```

---

## Раздел 7. requirements.txt (PyPI, июнь 2026)

Канонический файл — `requirements.txt` в корне. Точные пины:
```
nicegui==3.13.0
openai==1.99.1
anthropic==0.69.0
google-genai==1.21.0
elevenlabs==2.45.0
pydantic==2.12.5
PyYAML==6.0.2
python-dotenv==1.1.1
keyring==25.6.0
SQLAlchemy==2.0.43
httpx==0.28.1
tenacity==9.1.2
soundfile==0.13.2
mutagen==1.47.0
lxml==6.0.0
pytest==8.4.2
pytest-asyncio==1.0.0
```

---

## Раздел 8. Фазы разработки и accept-критерии (Block N.1)

### Фаза 1 — MVP (пилотный эпизод)
**Работает:** `config.py`+keyring/.env; `schemas.py`; `db.py` (create_all);
Behaviour Detector (+`test_detector.py`); Timeline Exporter FCPXML/EDL
(+`test_exporter.py`); Episode Generator + TTS Engine в **offline/mock-режиме**
(mock-аргументы, placeholder-WAV); Script Builder; минимальный NiceGUI (Dashboard +
Episode Studio MVP-поток).
**Ещё нет:** полный UI всех 10 шагов с авто-сейвом; реальные провайдеры (нужны ключи);
Quickfire UI-отбор; Translation Layer в проде; Leaderboard; Topic Bank UI; Metadata;
Logs-экран.
**Accept-критерий:** оператор запускает систему (`python main.py`), выбирает тему и пару
моделей, запускает конвейер и получает папку `data/episodes/epNNN_slug/` с WAV-файлами
(`audio/`) и текстовым сценарием (`script/episode_script.md`); `pytest tests/` зелёный.

### Фаза 2 — Полный воркфлоу
**Добавлено:** живые LLM-адаптеры (все `api_format`); Smoke Test с reframe; Quickfire
Manager с отбором 10/12 и guard-флагом; Translation Layer (Gemini-корректор + drift);
полный UI Episode Studio (10 шагов) + Script Viewer + Config + Episode Planner
(drag-and-drop); resumable TTS.
**Accept-критерий:** реальный эпизод от выбора темы до FCPXML+EDL проходит целиком через
UI; smoke-test и quickfire-отбор функциональны; translation-layer применяется к
S2-моделям с авто-откатом при дрейфе.

### Фаза 3 — Сериализация и аналитика
**Добавлено:** Leaderboard Engine (5 метрик, пересчёт, MD/CSV, Oxford-дельта UI);
Topic Bank с чек-листом и фильтрами; Metadata Generator (title/description/tags/poll +
copy-to-clipboard); полный Logs-экран (все прогоны, фильтры, экспорт `selected`).
**Accept-критерий:** после публикации оператор вводит Oxford-дельту → лидерборд
пересчитывается и экспортируется; темы проходят авто-чек-лист; метаданные генерируются;
Logs показывает все варианты генерации.

---

## Раздел 9. Краевые сценарии (O.1–O.7)

Для каждого: что система делает автоматически · что показывает · кнопки в UI.

**O.1 DeepSeek отказывается от Round 1 (REFUSED).** Авто: Behaviour Detector ставит
`REFUSED`; Episode Generator не падает, помечает реплику. UI (шаг 5): реплика красным
«DeepSeek: REFUSED — модель отказалась». Кнопки: «Повторить раунд», «Reframe тезиса»,
«Заменить модель защиты». Лидерборд: refusal учитывается в метрике 3.

**O.2 ElevenLabs недоступен (5/18 готово).** Авто: `tts_progress.json` хранит статусы;
`done`-файлы не трогаются. UI (шаг 7): «Сгенерировано 5/18, провайдер недоступен (HTTP
5xx)». Кнопки: «Возобновить с чекпойнта» (повтор только pending/failed), «Полный
перезапуск». Прогресс сохраняется между запусками приложения.

**O.3 Обе модели почти идентичны в quickfire (variability < порога).** Авто: Quickfire
Manager помечает вопрос низким score, не рекомендует. UI (шаг 8): жёлтое «вопросы похожи,
вариативность ниже {threshold}». Кнопки: «Регенерировать банк вопросов», «Выбрать
вручную», «Снизить порог».

**O.4 FCPXML не импортируется в DaVinci.** Авто: `lxml` валидирует well-formedness при
экспорте; всегда генерируются и EDL, и `_markers.md`. UI (шаг 9): «Если FCPXML не
открылся в Resolve — используйте EDL fallback». Кнопки: «Открыть папку timeline»,
«Повторить экспорт», «Экспортировать только EDL».

**O.5 Oxford-дельта не введена, начат следующий эпизод.** Авто: предыдущий эпизод
помечается `pending_delta`, лидерборд считается без него. UI: мягкое предупреждение «у
эпизода {slug} не введена Oxford-дельта — лидерборд неполон» (не блокировка). Кнопка:
«Ввести дельту сейчас», «Продолжить».

**O.6 GigaChat отказ по санкциям (HTTP 403 / спец-ответ).** Авто: адаптер распознаёт
403/ошибку авторизации, не ретраит. UI: красное «GigaChat недоступен — возможно
санкционное/региональное ограничение доступа. **[SANCTIONS RISK — legal review required,
INA §329]**. Проверьте юридическую допустимость использования.» Кнопки: «Заменить модель»,
«Открыть заметку о санкциях». Эпизод не блокируется для других моделей.

**O.7 Первый запуск (пустая БД, нет конфига/ключей).** Авто: `config.py` детектит
отсутствие БД/ключей → запускает onboarding-wizard: создаёт `data/*` и БД (`create_all`),
сидирует таблицу `models` из `config.yaml`, открывает форму ввода ключей (→ keyring),
ping-проверка провайдеров. UI: пошаговый wizard на русском. Без ключей выбранной пары —
мягкая блокировка генерации с инструкцией «добавьте ключи на экране Config».

---

## Раздел 10. Ограничения и допущения

**Система НЕ делает (вне границы автоматизации):**
- Видеомонтаж, графику, оверлеи, split-screen, анимацию таймера — вручную в DaVinci.
- Запись/синтез голоса ведущего (Блоки 2, 8) — только placeholder-дорожка `HOST_VOICE`.
- Публикацию на YouTube, постинг Community-поллов, сбор голосов — вручную.
- Оценку **истинности** аргументов — судится только честность аргументации; objection
  применяется лишь к проверяемым фактическим утверждениям, не к суждениям.
- Автоматический ввод Oxford-дельты — вводится оператором вручную после публикации.
- Юридическую оценку санкционных рисков GigaChat — система только помечает флагом
  **[SANCTIONS RISK — legal review required, INA §329]**; решение об использовании —
  на операторе после юридической консультации.

**Допущения:**
- Среда исполнения: локальная рабочая станция Mac/Windows/Linux, Python 3.11–3.13, без
  Docker/облака. Один оператор, без многопользовательского доступа.
- На июнь 2026 доступны перечисленные провайдеры и версии SDK; у оператора активны
  платные планы (ElevenLabs Pro для 44.1 кГц+ WAV; ключи всех используемых моделей).
- Ростер из 15 моделей и их идентификаторы зафиксированы заказчиком как требование;
  часть моделей — перспективные релизы 2026 года. При расхождении `model_name`/`base_url`
  с реальностью провайдера правится только `config.yaml`, без изменения кода (адаптеры
  параметризованы).
- DeepSeek: используются ТОЛЬКО `deepseek-v4-pro` / `deepseek-v4-flash`; `deepseek-chat`
  и `deepseek-reasoner` запрещены (отключение 2026-07-24 15:59 UTC, без fallback).
- DaVinci Resolve — версия 19.x; FCPXML 1.11 как primary, EDL+`.md` как гарантированный
  fallback ввиду известных ограничений Resolve по мультитрек-аудио и маркерам.
- Воспроизводимость: каждый прогон фиксирует seed/temperature/model_version/timestamp/
  prompt_hash в `GenerationLog`; политика отбора — «первый валидный прогон», все прогоны
  логируются для аудита.

---
*Конец ТЗ.*
