"""Episode Studio (Module 13 / Block L) -- the full 10-step operator workflow.

A vertical ui.stepper drives the episode one operator-controlled step at a time:

  1. Topic + model pair        6. Objection rulings (factual claims only)
  2. Generation settings       7. TTS batch (progress + resume)
  3. Smoke-test (+ reframe)     8. Quickfire review (pick 10 of 12)
  4. Full generation (stream)  9. Timeline export (FCPXML + EDL)
  5. Content review (flags)   10. YouTube metadata (copy)

Every step autosaves the episode to data/episodes/<slug>/episode.json so the
operator can leave and resume (Block L.2). Long operations run off the event
loop via run.io_bound with a live progress indicator.
"""
from __future__ import annotations

from pathlib import Path

from nicegui import run, ui

from modules.config import Config
from modules.episodes import objections
from modules.episodes.diagnostics import refused_or_suppressed_rounds
from modules.economics import episode_cost_from_logs, estimate_episode_cost
from modules.episodes.manager import (
    episode_dir,
    export_bundle,
    generate_variants,
    list_saved_episodes,
    load_episode,
    n_clips,
    regenerate_replica,
    reset_tts_checkpoint,
    save_episode,
    select_variant,
    step_export,
    step_generate,
    step_metadata,
    step_script,
    step_smoke_test,
    step_tts,
    sync_quickfire_selection,
    tts_checkpoint_summary,
)
from modules.leaderboard.service import episodes_pending_delta
from modules.llm.adapters.base import SanctionsBlockedError
from modules.quickfire.manager import all_below_threshold
from modules.schemas import Episode, GenParams, ObjectionEvent, Side
from ui.state import AppState

_FLAG_COLORS = {"REFUSED": "red", "SUPPRESSED": "red", "EVASIVE": "amber", "WEAK": "amber"}

_ROUND_GROUPS = [
    ("Раунд 1 — Вступительные позиции", lambda k: k.startswith("r1_")),
    ("Раунд 2 — Квикфайр (выбранные)", lambda k: k.startswith("r2_q")),
    ("Раунд 3 — Перекрёстный допрос", lambda k: k.startswith("r3_")),
    ("Раунд 4 — Заключения", lambda k: k.startswith("r4_")),
]


async def _copy(text: str) -> None:
    await ui.clipboard.write(text)
    ui.notify("Скопировано в буфер", type="positive")


def _report_exc(exc: Exception, log, prefix: str) -> None:
    """Surface a pipeline error; sanctions blocks get the legal warning (Block O.6)."""
    if isinstance(exc, SanctionsBlockedError):
        msg = ("[SANCTIONS RISK — legal review required, INA §329] "
               f"Провайдер отклонил доступ: {exc}")
        log.push(msg)
        ui.notify(msg, type="negative", timeout=12000)
    else:
        log.push(f"ОШИБКА: {exc}")
        ui.notify(f"{prefix}: {exc}", type="negative")


def render(state: AppState) -> None:
    cfg: Config = state.config
    ctx: dict = state.extras.setdefault("studio", {})
    model_opts = {m["id"]: m["display_name"] for m in cfg.models}
    sanctioned = {m["id"] for m in cfg.models if m.get("sanctions_risk")}

    def ep() -> Episode | None:
        return ctx.get("episode")

    def autosave() -> None:
        if ep() is not None:
            try:
                save_episode(ep(), cfg)
            except Exception as exc:  # never let autosave crash a step
                ui.notify(f"Автосохранение не удалось: {exc}", type="warning")

    ui.label("Episode Studio").classes("text-2xl font-bold")
    ui.label("Полный воркфлоу из 10 шагов. Состояние автосохраняется после каждого шага.")\
        .classes("text-sm text-grey")

    # --- O.5 soft warning: previous episodes awaiting an Oxford delta --------
    try:
        _pending = episodes_pending_delta(cfg)
    except Exception:
        _pending = []
    if _pending:
        with ui.card().classes("w-full bg-amber-1 q-mt-sm"):
            ui.label(f"⏳ O.5: {len(_pending)} эпизод(ов) ждут Oxford-дельту "
                     "(лидерборд не закрыт). Можно продолжать — это не блокировка.")\
                .classes("text-sm")
            ui.link("Ввести дельту → Leaderboard", "/leaderboard")

    # --- Resume an autosaved draft -------------------------------------------
    try:
        drafts = list_saved_episodes(cfg)
    except Exception:
        drafts = []
    if drafts:
        with ui.row().classes("items-center q-mt-sm gap-2"):
            ui.icon("history").classes("text-grey")
            draft_opts = {d.slug: f"{d.slug} · {d.status.value}" for d in drafts}
            sel = ui.select(draft_opts, label="Возобновить черновик").classes("w-96")

            def _resume():
                if not sel.value:
                    return
                path = episode_dir_for(cfg, sel.value) / "episode.json"
                if path.exists():
                    ctx["episode"] = load_episode(path)
                    ctx.pop("claims", None)
                    _refresh_all()
                    ui.notify(f"Загружен {sel.value}. Прокрутите к нужному шагу.", type="info")
            ui.button("Загрузить", on_click=_resume).props("flat color=primary")

    # ------------------------------------------------------------------ panels
    @ui.refreshable
    def review_panel() -> None:
        e = ep()
        if e is None or not e.rounds:
            ui.label("Эпизод ещё не сгенерирован (шаг 4).").classes("text-grey")
            return
        problems = refused_or_suppressed_rounds(e)
        if problems:
            with ui.card().classes("w-full bg-red-1"):
                ui.label("⚠ O.1: отказ/подавление рассуждения в основном раунде")\
                    .classes("font-bold text-red")
                for p in problems:
                    ui.label(f"• [{p['round_id']}] {p['model_id']}: {p['flag_type']} — "
                             f"{p['evidence'][:80]}").classes("text-xs")
                ui.label("Рекомендация: вернитесь к шагу 3 (smoke-test/reframe) или "
                         "перегенерируйте раунды (шаг 4) со сменой тезиса/пары.")\
                    .classes("text-xs text-grey")
        starts = {}
        if e.timeline_data:
            starts = {c.clip_id: c.start_frames for c in e.timeline_data.clips}
        fps = e.timeline_data.fps if e.timeline_data else 30
        for title, pred in _ROUND_GROUPS:
            keys = sorted(k for k in e.rounds if pred(k))
            if not keys:
                continue
            ui.label(title).classes("font-bold q-mt-sm")
            for k in keys:
                for rep in e.rounds[k]:
                    color = "blue" if rep.side == Side.PROSECUTION else "deep-orange"
                    with ui.card().classes("w-full q-my-xs"):
                        with ui.row().classes("items-center gap-2"):
                            ui.badge(rep.side.value, color=color)
                            ui.label(model_opts.get(rep.model_id, rep.model_id)).classes("text-xs text-grey")
                            if k in starts:
                                tc = _frames_to_tc(starts[k], fps)
                                ui.label(tc).classes("text-xs text-grey")
                            for fl in rep.flags:
                                ui.badge(fl.flag_type, color=_FLAG_COLORS.get(fl.flag_type, "grey"))\
                                    .tooltip(f"{fl.rule_triggered}: {fl.evidence[:80]}")
                        ui.label(rep.used_text or rep.text).classes("text-sm")
                        if rep.audio_path and Path(rep.audio_path).exists():
                            ui.audio(f"/media/{e.slug}/audio/{k}.wav").classes("w-full")  # I4
                        with ui.row().classes("items-center gap-2"):
                            async def _regen(rid=k):
                                await regenerate_replica(e, cfg, rid, offline=offline.value)
                                autosave()
                                review_panel.refresh()
                                ui.notify(f"{rid}: перегенерировано", type="positive")

                            async def _variants(rid=k):
                                await generate_variants(e, cfg, rid, n=3, offline=offline.value)
                                autosave()
                                review_panel.refresh()
                            ui.button("Перегенерировать", on_click=_regen).props("flat dense color=primary")
                            ui.button("3 варианта", on_click=_variants).props("flat dense")
                        for v in rep.variants:
                            with ui.row().classes("items-center gap-2"):
                                def _pick(text=v, rid=k):
                                    select_variant(e, rid, text)
                                    autosave()
                                    review_panel.refresh()
                                    ui.notify("Вариант выбран", type="positive")
                                ui.button("Выбрать", on_click=_pick).props("flat dense color=positive")
                                ui.label(v[:120]).classes("text-xs text-grey")

    @ui.refreshable
    def objection_panel() -> None:
        e = ep()
        if e is None or not e.rounds:
            ui.label("Сначала сгенерируйте эпизод (шаг 4).").classes("text-grey")
            return
        if "claims" not in ctx:
            ctx["claims"] = objections.suggest_claims(e)
        claims: list[ObjectionEvent] = ctx["claims"]
        ui.label("Возражения допустимы только к проверяемым фактическим утверждениям.")\
            .classes("text-xs text-grey")
        if not claims:
            ui.label("Кандидатов-фактов не найдено. Можно добавить вручную ниже.").classes("text-grey")
        for obj in claims:
            with ui.card().classes("w-full q-my-xs"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(f"[{obj.round_id}] {obj.claim}").classes("text-sm")
                    tog = ui.toggle(
                        {"pending": "—", "sustained": "Sustained", "overruled": "Overruled"},
                        value=obj.ruling,
                    ).props("dense")
                    tog.on_value_change(lambda ev, o=obj: setattr(o, "ruling", ev.value))
        # Manual add
        with ui.row().classes("items-center gap-2 q-mt-sm"):
            claim_in = ui.input("Добавить факт вручную").classes("w-96")
            side_in = ui.toggle({"prosecution": "Pros", "defense": "Def"}, value="prosecution").props("dense")

            def _add():
                if not claim_in.value.strip():
                    return
                rid = "r1_prosecution" if side_in.value == "prosecution" else "r1_defense"
                claims.append(ObjectionEvent(round_id=rid, side=Side(side_in.value), claim=claim_in.value.strip()))
                claim_in.value = ""
                objection_panel.refresh()
            ui.button("Добавить", on_click=_add).props("flat color=primary")

    @ui.refreshable
    def quickfire_panel() -> None:
        e = ep()
        if e is None or not e.quickfire:
            ui.label("Квикфайр появится после генерации (шаг 4).").classes("text-grey")
            return
        threshold = float(cfg.get("quickfire", "variability_threshold", default=0.35))
        select_n = int(cfg.get("quickfire", "questions_selected", default=10))
        if all_below_threshold(e.quickfire, threshold):
            with ui.card().classes("w-full bg-red-1"):
                ui.label("⚠ O.3: все ответы слишком похожи (variability < порога). "
                         "Перегенерируйте квикфайр (шаг 4) или смените вопросы/пару моделей.")\
                    .classes("text-sm text-red")
        ui.label(f"12 вопросов · variability score · выберите {select_n}. "
                 f"Рекомендованные (score ≥ {threshold}) отмечены.").classes("text-xs text-grey")
        boxes = []
        for exch in sorted(e.quickfire, key=lambda x: x.variability_score, reverse=True):
            with ui.row().classes("items-center gap-2 w-full"):
                cb = ui.checkbox(value=exch.recommended).props("dense")
                boxes.append((cb, exch))
                low = exch.variability_score < threshold
                ui.label(f"{exch.variability_score:.2f}")\
                    .classes(f"text-xs {'text-red' if low else 'text-green-700'} w-12")
                ui.label(exch.question).classes("text-sm")
        ctx["qf_boxes"] = boxes
        count_lbl = ui.label("").classes("text-xs q-mt-xs")

        def _refresh_count():
            n = sum(1 for cb, _ in boxes if cb.value)
            count_lbl.text = f"Выбрано: {n} / {select_n}"
            count_lbl.classes(replace="text-xs q-mt-xs " + ("text-red" if n > select_n else "text-grey"))
        for cb, _ in boxes:
            cb.on_value_change(lambda _e: _refresh_count())
        _refresh_count()

    @ui.refreshable
    def metadata_panel() -> None:
        e = ep()
        meta = (e.youtube_metadata if e else None) or {}
        if not meta:
            ui.label("Метаданные ещё не сгенерированы.").classes("text-grey")
            return
        for label, key in [("Title", "title"), ("Description", "description"), ("Tags", "tags")]:
            value = meta.get(key, "")
            if isinstance(value, list):
                value = " ".join(value)
            with ui.card().classes("w-full q-my-xs"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label(label).classes("font-bold")
                    ui.button("Copy", on_click=lambda v=value: _copy(str(v))).props("flat dense color=primary")
                ui.label(str(value)).classes("text-sm whitespace-pre-wrap")

    def _refresh_all():
        review_panel.refresh()
        objection_panel.refresh()
        quickfire_panel.refresh()
        metadata_panel.refresh()

    # --------------------------------------------------------------- stepper
    with ui.stepper().props("vertical flat").classes("w-full q-mt-md") as stepper:

        # ---- Step 1: topic + model pair ----
        with ui.step("1. Тема и пара моделей"):
            try:
                from modules.topics.bank import list_topics
                topics = list_topics()
            except Exception:
                topics = []
            if topics:
                topt = {str(t.id): t.thesis for t in topics}
                tsel = ui.select(topt, label="Из банка тем (опционально)").classes("w-full")

                def _fill(_):
                    t = next((x for x in topics if str(x.id) == tsel.value), None)
                    if t:
                        thesis.value = t.thesis
                        if t.recommended_pair:
                            pros.value, deff.value = t.recommended_pair
                tsel.on_value_change(_fill)
            thesis = ui.input("Тезис эпизода",
                              value="The dissolution of the USSR was inevitable.").classes("w-full")
            slug = ui.input("Slug (имя папки, [a-z0-9_])", value="ep001_dissolution_ussr").classes("w-full")
            with ui.row().classes("w-full"):
                _pp = cfg.get("production", "default_prosecution", default="gpt-5.5")
                _pd = cfg.get("production", "default_defense", default="deepseek-v4-pro")
                pros = ui.select(model_opts, label="Prosecution", value=_pp).classes("w-64")
                deff = ui.select(model_opts, label="Defense", value=_pd).classes("w-64")
            sanctions_note = ui.markdown("").classes("text-red")

            def _check_sanctions(*_):
                flagged = [m for m in (pros.value, deff.value) if m in sanctioned]
                sanctions_note.set_content(
                    f"**[SANCTIONS RISK — legal review required, INA §329]** Выбрана модель "
                    f"`{flagged[0]}`. Подтвердите юридическую допустимость использования." if flagged else "")
            pros.on_value_change(_check_sanctions)
            deff.on_value_change(_check_sanctions)
            _check_sanctions()

            with ui.stepper_navigation():
                def _s1_next():
                    if pros.value == deff.value:
                        ui.notify("Prosecution и Defense должны быть разными моделями", type="warning")
                        return
                    if not slug.value.strip():
                        ui.notify("Укажите slug", type="warning")
                        return
                    stepper.next()
                ui.button("Далее", on_click=_s1_next).props("color=primary")

        # ---- Step 2: generation settings ----
        with ui.step("2. Настройки генерации"):
            with ui.row().classes("items-center gap-4"):
                temp = ui.number("temperature", value=0.7, min=0, max=1, step=0.1).classes("w-32")
                maxtok = ui.number("max_tokens", value=800, min=64, step=50).classes("w-32")
                seed = ui.number("seed (0=random)", value=42, min=0).classes("w-32")
            offline = ui.checkbox("Offline (mock, без API-ключей)", value=state.offline_default)
            cost_lbl = ui.label("").classes("text-sm text-grey")

            def _estimate():
                tmp = Episode(
                    thesis=(thesis.value or "Tmp thesis"), slug="ep_estimate_tmp",
                    prosecution_model_id=pros.value, defense_model_id=deff.value,
                    gen_params=GenParams(temperature=float(temp.value), max_tokens=int(maxtok.value),
                                         seed=(int(seed.value) or None)))
                est = estimate_episode_cost(tmp, cfg)
                cost_lbl.text = (f"≈ Оценка стоимости: ${est['total_usd']} "
                                 f"(LLM ${est['llm_usd']} + TTS ${est['tts_usd']}); "
                                 f"вывод ~{est['est_output_tokens']} ток.")
            ui.button("Оценить стоимость (I6)", on_click=_estimate).props("flat dense color=primary")
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")

                def _s2_next():
                    e = Episode(
                        thesis=thesis.value, slug=slug.value.strip(),
                        prosecution_model_id=pros.value, defense_model_id=deff.value,
                        gen_params=GenParams(
                            temperature=float(temp.value), max_tokens=int(maxtok.value),
                            seed=(int(seed.value) or None)),
                    )
                    ctx["episode"] = e
                    ctx.pop("claims", None)
                    autosave()
                    _refresh_all()
                    stepper.next()
                ui.button("Создать эпизод и далее", on_click=_s2_next).props("color=primary")

        # ---- Step 3: smoke test ----
        with ui.step("3. Smoke-test"):
            ui.label("Round 1 + 3 квикфайр-вопроса → критерии go/no-go. При FAIL — reframe.")\
                .classes("text-xs text-grey")
            smoke_log = ui.log(max_lines=200).classes("w-full h-40 bg-black text-green-400 text-xs")
            smoke_report = ui.column().classes("w-full")
            smoke_btn = ui.button("Запустить smoke-test").props("color=primary")

            async def _run_smoke():
                if ep() is None:
                    return
                smoke_btn.disable()
                smoke_report.clear()
                smoke_log.clear()
                smoke_log.push("Старт smoke-test...")
                try:
                    res = await step_smoke_test(
                        ep(), cfg, offline=offline.value, on_log=lambda m: smoke_log.push(m))
                except Exception as exc:
                    _report_exc(exc, smoke_log, "Ошибка smoke-test")
                    smoke_btn.enable()
                    return
                ctx["smoke"] = res
                autosave()
                with smoke_report:
                    if res.passed:
                        ui.label(f"PASS ✓ (попытка {res.attempt})").classes("text-green-600 font-bold")
                    else:
                        ui.label(f"FAIL ✗ после {res.attempt} попыток").classes("text-red font-bold")
                        ui.label(res.detail).classes("text-sm")
                        if res.reframe:
                            ui.label("Предложенный reframe:").classes("font-bold q-mt-xs")
                            ui.label(res.reframe).classes("text-sm italic")

                            def _apply_reframe():
                                ep().thesis = res.reframe
                                thesis.value = res.reframe
                                autosave()
                                ui.notify("Тезис обновлён reframe-формулировкой. Повторите smoke-test.",
                                          type="info")
                            ui.button("Повторить с этим фреймингом", on_click=_apply_reframe).props("color=primary")
                    if res.flags:
                        ui.label(f"Флаги Round 1: " +
                                 ", ".join(f"{f.flag_type}({f.model_id})" for f in res.flags))\
                            .classes("text-xs text-grey")
                smoke_btn.enable()
            smoke_btn.on_click(_run_smoke)
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.button("Далее (пропустить/продолжить)", on_click=stepper.next).props("color=primary")

        # ---- Step 4: full generation ----
        with ui.step("4. Полная генерация"):
            gen_log = ui.log(max_lines=400).classes("w-full h-56 bg-black text-green-400 text-xs")
            gen_done = ui.label("").classes("text-sm")
            gen_btn = ui.button("Сгенерировать все раунды").props("color=primary")

            async def _run_gen():
                if ep() is None:
                    return
                gen_btn.disable()
                gen_log.clear()
                gen_done.text = ""
                gen_log.push("Генерация раундов (R1 ∥ Quickfire → R3 → R4)...")
                try:
                    await step_generate(ep(), cfg, offline=offline.value, on_log=lambda m: gen_log.push(m))
                except Exception as exc:
                    _report_exc(exc, gen_log, "Ошибка генерации")
                    gen_btn.enable()
                    return
                ctx.pop("claims", None)
                autosave()
                _refresh_all()
                est = estimate_episode_cost(ep(), cfg)
                act = episode_cost_from_logs(ep(), cfg)
                gen_done.text = (f"Готово ✓  Реплик: {n_clips(ep())} · "
                                 f"Флагов поведения: {len(ep().behaviour_flags)} · "
                                 f"Квикфайр-обменов: {len(ep().quickfire)} · "
                                 f"≈${est['total_usd']} оценка / ${act['total_usd']} факт (I6)")
                gen_done.classes(replace="text-green-600 font-bold")
                ui.notify("Раунды сгенерированы", type="positive")
                gen_btn.enable()
            gen_btn.on_click(_run_gen)
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.button("Далее", on_click=stepper.next).props("color=primary")

        # ---- Step 5: content review ----
        with ui.step("5. Ревью контента"):
            ui.label("REFUSED/SUPPRESSED — красный, EVASIVE/WEAK — жёлтый.").classes("text-xs text-grey")
            review_panel()
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.button("Далее", on_click=stepper.next).props("color=primary")

        # ---- Step 6: objections ----
        with ui.step("6. Возражения (objection)"):
            objection_panel()
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")

                def _save_obj():
                    e = ep()
                    if e is None:
                        return
                    e.objections = [o for o in ctx.get("claims", []) if o.ruling in ("sustained", "overruled")]
                    autosave()
                    ui.notify(f"Сохранено возражений: {len(e.objections)}", type="positive")
                    stepper.next()
                ui.button("Сохранить рулинги и далее", on_click=_save_obj).props("color=primary")

        # ---- Step 7: TTS ----
        with ui.step("7. TTS (озвучка)"):
            tts_bar = ui.linear_progress(value=0.0, show_value=False).props("instant-feedback").classes("w-full")
            tts_status = ui.label("Готов к запуску.").classes("text-sm")
            ckpt_lbl = ui.label("").classes("text-xs text-grey")

            def _refresh_ckpt():
                e = ep()
                if e is None:
                    return
                s = tts_checkpoint_summary(e, cfg)
                ckpt_lbl.text = (f"Чекпойнт O.2: готово {s['done']} · ошибок {s['failed']} · "
                                 f"всего {s['total']} · осталось {s['remaining']}")
            _refresh_ckpt()

            with ui.row().classes("items-center gap-2"):
                tts_btn = ui.button("Запустить / возобновить TTS").props("color=primary")

                def _reset_ckpt():
                    if ep() is None:
                        return
                    existed = reset_tts_checkpoint(ep(), cfg)
                    _refresh_ckpt()
                    ui.notify("Чекпойнт сброшен — следующий запуск перегенерирует все клипы"
                              if existed else "Чекпойнта не было", type="info")
                ui.button("Сбросить чекпойнт", on_click=_reset_ckpt).props("flat color=warning")

            async def _run_tts():
                e = ep()
                if e is None or not e.rounds:
                    ui.notify("Сначала сгенерируйте эпизод (шаг 4).", type="warning")
                    return
                tts_btn.disable()
                prog = {"i": 0, "n": n_clips(e), "msg": ""}

                def _cb(i, n, msg):
                    prog.update(i=i, n=n, msg=msg)

                def _tick():
                    n = prog["n"] or 1
                    tts_bar.value = prog["i"] / n
                    tts_status.text = f"{prog['i']}/{prog['n']} · {prog['msg']}"
                timer = ui.timer(0.1, _tick)
                try:
                    await run.io_bound(step_tts, e, cfg, offline=offline.value, on_progress=_cb)
                except Exception as exc:
                    timer.deactivate()
                    _refresh_ckpt()
                    tts_status.text = (f"ОШИБКА: {exc} — нажмите «Возобновить» "
                                       "(готовые клипы не перегенерируются)")
                    tts_status.classes(replace="text-sm text-red")
                    ui.notify(f"Ошибка TTS: {exc}", type="negative")
                    tts_btn.enable()
                    return
                timer.deactivate()
                tts_bar.value = 1.0
                tts_status.text = f"Готово ✓  {n_clips(e)} файлов в {episode_dir(e, cfg) / 'audio'}"
                tts_status.classes(replace="text-sm text-green-600 font-bold")
                _refresh_ckpt()
                autosave()
                ui.notify("Озвучка готова", type="positive")
                tts_btn.enable()
            tts_btn.on_click(_run_tts)
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.button("Далее", on_click=stepper.next).props("color=primary")

        # ---- Step 8: quickfire selection ----
        with ui.step("8. Ревью квикфайра"):
            quickfire_panel()
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")

                def _apply_qf():
                    e = ep()
                    if e is None:
                        return
                    boxes = ctx.get("qf_boxes", [])
                    select_n = int(cfg.get("quickfire", "questions_selected", default=10))
                    chosen = [exch for cb, exch in boxes if cb.value]
                    if len(chosen) > select_n:
                        ui.notify(f"Выбрано {len(chosen)} > {select_n}. Снимите лишние.", type="warning")
                        return
                    for cb, exch in boxes:
                        exch.recommended = cb.value
                    count = sync_quickfire_selection(e)
                    autosave()
                    ui.notify(f"Выбрано {count}. Экспорт до-озвучит недостающие клипы.", type="positive")
                    stepper.next()
                ui.button("Применить выбор и далее", on_click=_apply_qf).props("color=primary")

        # ---- Step 9: export ----
        with ui.step("9. Экспорт таймлайна (DaVinci)"):
            exp_status = ui.label("FCPXML (primary) + EDL + markers.md (fallback).").classes("text-sm")
            exp_result = ui.column().classes("w-full")
            exp_btn = ui.button("Экспортировать FCPXML + EDL").props("color=primary")

            async def _run_export():
                e = ep()
                if e is None or not e.rounds:
                    ui.notify("Нет сгенерированного эпизода.", type="warning")
                    return
                exp_btn.disable()
                exp_result.clear()
                exp_status.text = "Экспорт... (до-озвучка недостающих клипов, сборка таймлайна)"

                def _export_flow():
                    # Defensive: ensure audio matches the current quickfire selection,
                    # then build timeline + script. step_tts is resumable so this only
                    # synthesizes clips that are missing.
                    step_tts(e, cfg, offline=offline.value)
                    exp = step_export(e, cfg)
                    scr = step_script(e, cfg)
                    return exp, scr
                try:
                    exp, scr = await run.io_bound(_export_flow)
                except Exception as exc:
                    exp_status.text = f"ОШИБКА экспорта: {exc}"
                    exp_status.classes(replace="text-sm text-red")
                    ui.notify(f"FCPXML не сгенерирован: {exc}. Используйте EDL fallback.", type="negative")
                    exp_btn.enable()
                    return
                state.last_episode = e
                state.last_result = {
                    "episode_dir": str(episode_dir(e, cfg)),
                    "fcpxml": exp["fcpxml"], "edl": exp["edl"], "markers_md": exp["markers_md"],
                    "script": scr["script"], "flags": scr["flags"],
                    "n_clips": n_clips(e), "n_flags": len(e.behaviour_flags),
                }
                review_panel.refresh()  # now shows timecodes
                autosave()
                valid = exp.get("fcpxml_valid", True)
                exp_status.text = "Готово ✓" if valid else "Готово — FCPXML невалиден (см. O.4 ниже)"
                exp_status.classes(replace="text-sm " + (
                    "text-green-600 font-bold" if valid else "text-orange-700 font-bold"))
                with exp_result:
                    if not valid:
                        ui.label("⚠ O.4: FCPXML не прошёл проверку и может не импортироваться в "
                                 "DaVinci. Используйте EDL + markers.md (гарантированный fallback).")\
                            .classes("text-sm text-red")
                    ui.label(f"FCPXML (primary){' ✓' if valid else ' — невалиден'}: {exp['fcpxml']}")\
                        .classes("text-xs")
                    ui.label(f"EDL (fallback): {exp['edl']}").classes("text-xs")
                    ui.label(f"Маркеры: {exp['markers_md']}").classes("text-xs")
                    ui.label(f"Сценарий: {scr['script']}").classes("text-xs")
                    if exp.get("otio"):
                        ui.label(f"OTIO (native DaVinci): {exp['otio']}").classes("text-xs")  # I8
                    ui.link("Открыть Script Viewer →", "/script")

                    def _bundle():  # I9
                        path = export_bundle(e, cfg)
                        ui.notify(f"Publish-pack собран: {path}", type="positive")
                        ui.download(path)
                    ui.button("📦 Publish pack (zip для монтажёра)", on_click=_bundle)\
                        .props("flat color=primary")
                ui.notify("Таймлайн экспортирован" if valid else
                          "Экспорт готов, но FCPXML невалиден — используйте EDL (O.4)",
                          type="positive" if valid else "warning")
                exp_btn.enable()
            exp_btn.on_click(_run_export)
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.button("Далее", on_click=stepper.next).props("color=primary")

        # ---- Step 10: metadata ----
        with ui.step("10. Метаданные YouTube"):
            meta_btn = ui.button("Сгенерировать метаданные").props("color=primary")

            def _gen_meta():
                if ep() is None:
                    return
                step_metadata(ep(), cfg)
                autosave()
                metadata_panel.refresh()
                ui.notify("Метаданные готовы", type="positive")
            meta_btn.on_click(_gen_meta)
            metadata_panel()
            with ui.stepper_navigation():
                ui.button("Назад", on_click=stepper.previous).props("flat")
                ui.label("Эпизод собран. Скопируйте поля и переходите к ручному монтажу.")\
                    .classes("text-green-700")


# --------------------------------------------------------------------------- #
# Small local helpers (avoid importing private names from the calculator)
# --------------------------------------------------------------------------- #
def episode_dir_for(cfg: Config, slug: str) -> Path:
    return cfg.resolve_path("episodes_dir") / slug


def _frames_to_tc(frames: int, fps: int) -> str:
    total, f = divmod(int(frames), fps)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
