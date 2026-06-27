# RUNBOOK — первый живой эпизод (production MVP)

Пара по умолчанию: **Gemini 3.1 Pro (prosecution)** vs **Claude Sonnet 4.6 (defense)**
(`config.yaml → production`). Всё ниже выполняется на машине оператора, где есть
ключи и установлен DaVinci Resolve 19.

## 0. Установка
```bash
python -m pip install -r requirements.txt   # полный набор (nicegui, openai, anthropic,
                                            # google-genai, elevenlabs, lxml, soundfile, ...)
python main.py                              # http://127.0.0.1:8080
```

## 1. Ключи (экран Config → «Установить / обновить ключ» → keyring)
Минимум для этой пары:
- `GOOGLE_API_KEY` — Google AI Studio / Gemini API.
- `ANTHROPIC_API_KEY` — Anthropic.
- `ELEVENLABS_API_KEY` — ElevenLabs (план Pro+ нужен для WAV/PCM 44.1 kHz).

Альтернатива: `cp config/.env.example config/.env` и вписать ключи.

## 2. Голоса ElevenLabs
В `config/tts_presets.json` заменить плейсхолдеры реальными `el_voice_id` (UUID из
вашего аккаунта) минимум для:
- `gemini-3.1-pro` (сейчас `REPLACE_VOICE_04`)
- `claude-sonnet-4-6` (сейчас `REPLACE_VOICE_SONNET`)

## 3. Сверка имён моделей (важно)
`model_name` в `config.yaml` — это то, что уходит провайдеру:
- Gemini: `gemini-3.1-pro`; Claude: `claude-sonnet-4-6`.
Если провайдер вернёт 404/«model not found», подставьте актуальный id из их консоли
(`AuthError`/`AdapterError` в preflight покажет точную причину).

## 4. Preflight (обязательно перед первым эпизодом)
Config → **«Запустить preflight»**. Должно быть 4 зелёных:
модель prosecution, модель defense, ElevenLabs, voice presets. Любой красный —
чините по тексту ошибки (нет ключа / неверное имя модели / плейсхолдер voice_id).

## 5. Сборка эпизода (Episode Studio, 10 шагов)
1–2. Тема (дефолт — пара уже выбрана) + **снять галочку Offline** + «Оценить
стоимость» (I6). 3. Smoke-test (PASS/FAIL → reframe). 4. Полная генерация
(стриминг + факт стоимости). 5. Ревью: прослушать реплики (аудио-плееры),
при слабой/отказной — «Перегенерировать» / «3 варианта → Выбрать». 6. Рулинги
objection. 7. TTS (прогресс + resume). 8. Отбор 10 из 12 квикфайра. 9. Экспорт
(FCPXML + EDL + markers + OTIO). 10. Метаданные YouTube (copy) + **Publish pack**.

## 6. Импорт в DaVinci Resolve 19
Папка эпизода: `data/episodes/<slug>/timeline/`.
1. **FCPXML (primary):** File → Import → Timeline → `<slug>.fcpxml`.
2. Если не импортируется/кривое мультитрек-аудио — **fallback:**
   - `<slug>.otio` (Timelines → Import → AAF/EDL/XML/DRT/ADL/OTIO), либо
   - `<slug>.edl` + расставить маркеры вручную по `<slug>_markers.md`.
3. Релинк аудио из `../audio/*.wav` при запросе.
4. Проверить 5 дорожек: HOST_VOICE (пусто), PROSECUTION, DEFENSE, SFX_MARKERS,
   MUSIC_BED; что маркеры (OBJECTION_*/REFUSED/…) на месте.

> Если FCPXML помечен как невалидный на шаге 9 (O.4) — используйте EDL/OTIO; это
> ожидаемый и поддержанный путь.

## 7. После публикации
Leaderboard → ввести Oxford-дельту (≥30 голосов в каждом опросе, иначе
`no_quorum`) → пересчёт 5 метрик → экспорт MD/CSV.

## Траблшутинг
- **AuthError (401/403/400)** — неверный/просроченный ключ или имя модели; не
  ретраится. Проверьте Config.
- **SanctionsBlockedError** — санкционная модель (GigaChat); в этой паре не
  используется.
- **FCPXML невалиден** — берите EDL/OTIO (см. §6.2).
- **Дорого** — снизьте `max_tokens`/`quickfire_max_tokens` в шаге 2; цены в
  `config.yaml → pricing`.
