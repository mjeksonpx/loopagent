# PLANS.md — план работ

Гейт: `python3 scripts/check_loop.py` → `LOOP_ENGINEERING_STOP_0001` (красный, exit 1) /
`LOOP_ENGINEERING_PASS_0001` (exit 0).

## Этап 1. Каркас петли — ✅ закрыт (round 03, `LOOP_ENGINEERING_PASS_0001`, 8/8 обязательных измерений)

1. ✅ `scripts/check_loop.py` — локальный гейт, read-only, 9 измерений (D01–D09). Стартует красным:
   список failures + `LOOP_ENGINEERING_STOP_0001`; после ремонта — `LOOP_ENGINEERING_PASS_0001`, `exit 0`.
   Evidence: `artifacts/rounds/round_02.md` (красный прогон), `round_03.md` (красный → зелёный за три
   прогона), `artifacts/loop_gate_report.json` (машинный отчёт).
2. ✅ `.agents/skills/loop-engineering/SKILL.md` — контракт, таблица измерений, runbook, роли, инварианты.
3. ✅ Петля прогнана руками end-to-end: build (корпус) → fail → repair → prove, артефакты в `artifacts/rounds/`.

## Этап 2. Роли через ywcrew — ✅ закрыт (round 04, `--strict` → `LOOP_ENGINEERING_PASS_0001`, 9/9)

Роли прогнаны через ywcrew: explorer `2026-10-12-82081883`, reviewer-панель `2026-10-12-67ca60fc` +
`2026-10-12-aa99e37d`, worker `2026-10-12-fbee6cc3`, evaluator `2026-10-12-a1350fe5`. Ревью дало
отказ (4 verdict-а `reject` + второй движок `reject`), worker подтвердил все 5 слабостей гейта
falsification-тестами, evaluator нашёл 2 неверные цифры у нас. По этим evidence гейт усилен
(D01–D05, D08, D09), и строгий режим теперь зелёный. Детали: `artifacts/rounds/round_04.md`,
`handoff/agents/*.md`, `handoff/supervisor_synthesis.md`.

4. explorer: `ywcrew run --mode read-only` — карта репо и текущее состояние гейта.
5. reviewer: `ywcrew panel` — независимое ревью (codex + grok + agy/claude-sonnet).
6. worker: `ywcrew run --mode edit` — точечная правка по evidence.
7. evaluator: read-only + `scripts/check_loop.py`.
8. supervisor synthesis: `handoff/supervisor_synthesis.md` собирает Hermes.

Закрытие этапа = `python3 scripts/check_loop.py --strict` печатает PASS (D09 из SKIP → OK).

## Этап 3. Автоматизация — 🔧 в работе (детектор и cron закрыты, `/loop`-рецепт не проверен живым прогоном)

9. ✅ `scripts/loop_watch.py` — детерминированный детектор изменений: гоняет гейт, сводит результат к
   отпечатку (маркер, статусы измерений, failures, метрики, раунд) и **молчит, если ничего не
   изменилось**; при изменении печатает компактный диф `было -> стало`. Проверено на копии: baseline →
   тишина → инъекция регрессии дала диф → тишина → восстановление дало обратный диф.
10. ✅ cron-джоба заведена 2026-10-13: `loop-system gate watch`, каждые 360 мин, `monitor`-режим
    (`loop_watch.py`: меняется вывод → будится агент с диффом; не меняется → прогон агента пропускается
    вовсе, токены не тратятся), доставка в origin, рабочая папка — корень репозитория. Evidence:
    последний прогон `2026-10-21T15:03` — `ok`, `artifacts/loop_watch_last.json` (13/13 OK, round 13).
11. ⏳ `/loop` внутри сессии — рецепт для iterate-until-green на этом гейте (запускает оператор):

    ```
    /loop Run `python3 scripts/check_loop.py` in the loop-system repo. If it prints
    LOOP_ENGINEERING_PASS_0001, reply exactly LOOP_COMPLETE. Otherwise repair ONLY the dimension named in
    the failure list, then re-run the gate.
    ```

    Стоп-условия: `LOOP_COMPLETE` (гейт зелёный), `--times N`, backstop `loops.max_ticks=100`
    (текущий конфиг: `min_interval_seconds: 30`).

    Граница применимости (Ralph Loop, раздел 5 курса): `/loop` крутит **тот же контекст** — модель
    тащит за собой свои прошлые правки, окно растёт, и после десятков итераций агент деградирует.
    Ralph начинает каждую итерацию с **чистого контекста**, а память держит в файлах (git, правила,
    issues). Отсюда правило: `/loop` — только «дожать гейт до зелёного» на одной задаче в одном
    контексте; поток из N задач идёт **только через очередь карточек** — каждая карточка это новый
    процесс (свежий контекст) и бриф вместо накопленной истории.

## Этап 4. DSH как основной dev worker — ✅ закрыт (проверено прогонами)

12. ✅ Адаптер `harness` в ywcrew доведён до «как у всех»: провайдер выбирается по модели
    (`opencode-go` для трио `deepseek-v4.1-flash` / `glm-5.3-flash` / `mimo-v2.6-flash`,
    `deepseek-official` для официальных), ключ ищется по провайдеру (в т.ч. в `~/.dsh/.env`),
    дефолт = `deepseek-v4.1-flash:high`, бэкенд включён, в routing добавлен путь
    «primary dev worker: feature work, bug fixes, routine coding, tests, script edits».
    Тесты: 75/75, `tsc`/build чисто, `ywcrew install --each`.
13. ✅ Живая проверка: read-only прогон сначала писал в **реальное** дерево (песочницы в
    `dsh headless` нет — старая строка «DSH built-in sandbox» была неправдой); после фикса
    (`nativeReadOnly: false`) read-задачи идут в git-worktree, `artifacts/`+`handoff/` попадают туда
    через `.worktreeinclude`, гейт внутри печатает `9 ok` / `LOOP_ENGINEERING_PASS_0001`, дерево
    оператора чистое. Edit-режим: изолированный worktree, один новый файл, патч собран.

## Этап 5. PR/issues на sample-service — ✅ закрыт в этапе 6 (живой цикл до PR #3, round 11), с оговоркой: авторизация `gh` с тех пор потеряна → этап 8, п. 25

14. `gh` установлен без brew (`~/.local/bin/gh` 2.102.0). На момент закрытия этапа был авторизован
    токеном (`example`); 2026-10-21 состояние авторизации было потеряно (`~/.config/gh` отсутствовал,
    `GH_TOKEN` пуст) и восстановлено оператором — см. этап 8, п. 25 (`ISSUE_BRIDGE_PASS_0001`).
    Репозиторий: `example/sample-service` (SSH-remote `github.com-example`; пуш работает, `git ls-remote`
    отдаёт ветки).
15. Схема: агент создаёт ветку `<role>/<task>` → коммиты → PR → прогон гейта → комментарий-вердикт
    (`gh pr review`) → при красном `ywcrew followup <threadId>` тому же агенту с диффом и именем
    упавшего измерения.
16. Issue-флоу: QA-агент пишет issue → триаж-агент проверяет **валидность** (воспроизводимость по
    шагам; невалидные закрывает с обоснованием) → валидные → очередь → worker (DSH) → PR с фиксом.

## Этап 6. Очередь задач: GitHub issues + Hermes kanban — ✅ закрыт (round 10–11, живой цикл до PR)

17. ✅ GitHub issues = источник истины и внешняя обратная связь (тестеры, оператор, шеф).
18. ✅ Hermes kanban = локальная очередь исполнения: задача клеймится атомарно и исполняется профилем
    в изолированном workspace (`hermes kanban create/assign/dispatch`). Мост
    issues → kanban (assignee = роль) → dispatch → PR: `scripts/sync_issues_to_kanban.py`.
    Отказ на стороне воркера (неполный бандл доказательств) закрыт в самом брифе:
    `scripts/worker_brief.py` рендерит бриф из `configs/repo_profiles.json`, а `verify` проверяет
    транскрипт воркера — каждое обязательное измерение репозитория с видимым exit-кодом.
19. ✅ Правило «одна задача = один PR» + идемпотентность `gh-<owner>-<repo>-<n>` + fail-closed
    (нет acceptance-критериев → needs-triage; нет workspace у профиля → `--apply` отказывает).
    Тот же бриф отдаётся и как 5-частная ywcrew-задача (`worker_brief.py build --format ywcrew-json`,
    бандл — в `task.constraints`), чтобы правило не зависело от пути диспатча.
    Гейт: измерение `D10 issue_bridge` (9 обязательных + 1 опциональное).
20. ✅ Живой цикл (round 11): sample-service зарегистрирован как Hermes-проект (`p_<project>`), issue #2 →
    kanban `t_<card>` → воркер профиля `dev` в изолированном worktree → ветка запушена (`a3ffb61`) →
    PR #3 с транскриптом в теле → `worker_brief.py verify` на живом транскрипте = `WORKER_EVIDENCE_PASS_0001`.
    Ревью отдельными движками: kanban-ревью (3 mutation-probe + сравнение с baseline на живом Postgres),
    `agy:claude-sonnet-4-6` на PR (5 критериев met, один MINOR), `codex` на этап 6 — REJECT с тремя
    дефектами (borrowed exit code, отсутствие источника бандла, триаж-лейбл вместо критериев), все
    закрыты в `72c5395` и закреплены регрессами R14 + пятой фальсификацией D10.

## Этап 7. Продуктовая приёмка и агенты-тестеры — ✅ закрыт (round 13; `--strict` 13/13, продуктовая приёмка 8/8 под python 3.14.7, D13 зелёный на 23 фальсификациях)

21. ✅ Отдельный продуктовый критерий приёмки (не технический): `scripts/check_product.py` +
    `configs/product_scenarios.json` — 8 пользовательских сценариев sample-service (пробы, метрики, привязка
    API-порта, чистый SIGTERM, отказ на невалидной конфигурации, валидация контракта), у каждого строка
    `why` с последствием для пользователя. Печатает `PRODUCT_ACCEPTANCE_PASS_0001` / `_STOP_0001`,
    fail-closed, `--strict` превращает SKIP в FAIL. Гейт: измерение `D11 product_gate` (атакует
    evaluator, мутируя ожидания каждого сценария). Шеф принимает по этому гейту, техгейт — отдельно.
22. ✅ Профиль/роль `qa`: профиль `qa` создан (deepseek-v4.1-flash), мост маппит лейблы `qa`/`ux` на
    него (`assignee_by_label`), в cli-тулсет профиля включён `browser`. Визуальная половина сделана:
    `scripts/render_status_page.py` (страница оператора) + `configs/visual_scenarios.json` +
    `scripts/check_visual.py` (headless Chrome: скриншот + DOM-проверки), гейт-измерение
    `D13 visual_gate`. Первый же vision-разбор скриншота нашёл дефект, которого не видел ни один
    JSON-чек: заголовок в таблице обрезан посреди слова без многоточия — исправлено и закреплено
    правилом в evaluator'е.
24. ✅ Внешний якорь гейта (закрытие остатка `risk=4`): `scripts/anchor_witness.py` подписывает вердикт
    якоря отдельным ключом (`~/.ssh/loop_anchor_ed25519`, публичная половина в `configs/allowed_signers`)
    и публикует дайджест в внешний журнал (`example/sample-service#5`). Измерение `D12 anchor_witness`:
    перезапись якоря перестаёт верифицироваться. Регрессы R19–R21, R24.
23. ✅ Таймбоксы задач: `scripts/estimate_task.py` считает p50/p90 по истории kanban-ранов и пишет
    `configs/task_estimates.json`; мост передаёт `--max-runtime` (на cap-е диспетчер убивает воркера и
    пере-ставит задачу). Оценку делает очередь по своим измерениям, а не супервизор по ощущениям.

## Этап 8. Третий вердикт «не стало хуже», журнал задач в git и защита от перерасхода — ✅ закрыт (п. 25 закрыт 2026-10-21, 26–35 закрыты 2026-10-27; доказательство — D14 в `scripts/check_loop.py`)

Открыт по итогам ревизии восьми внешних проектов (`max-sixty/worktrunk`, `MrLesk/Backlog.md`,
`jnsahaj/lumen`, `repowise-dev/repowise`, `git-ai-project/git-ai`, `feder-cr/git-ai`, `stravu/crystal`,
`Nutlope/aicommits`) и пяти долгов, найденных живыми прогонами в тот же день. Из чужого берём **две
идеи, а не код**: третий вердикт (измерение «изменение не ухудшило кодовую базу») и леджер задач в git.
Остальные проекты остаются внешними инструментами: worktrunk — управление worktree, lumen — поверхность
человеческого ревью, repowise — индекс кода, git-ai — провенанс; свои версии мы не пишем.

Пункты 32–35 добавлены после разбора курса **Ralph Loop** (11 уроков, папка
`<course-materials>/5. Ralph Loop`). Совпадает у нас с курсом ~70%: stop-хук заменён
гейтвеем, `ralph.md` — скиллом `loop-engineering`, MaxIterations — `--max-runtime`, sandbox — worktree
и профилем прав. Курс закрывает то, чего у нас нет: **бюджет итераций, лимит внутренних попыток,
детект «нет прогресса», лестницу HITL→AFK, второй уровень ревью**. Механика хуков и прямой перезапуск
CLI не переносятся: они нарушают D06 (единственная дверь — ywcrew/kanban).

Порядок исполнения — от блокирующего к содержательному: 25 → 26 → 27 → 28 → 29 → 33 → 32 → 35 → 30 → 31 → 34.

25. ✅ **gh-авторизация (P0, блокер очереди) — закрыто 2026-10-21.** Было: `~/.config/gh` отсутствует,
    `GH_TOKEN` и `GITHUB_TOKEN` пусты → `sync_issues_to_kanban.py --profile sample-service` печатал
    `ISSUE_BRIDGE_STOP_0001`, exit 1; внешний вход в очередь был мёртв, хотя SSH-пуш работал.
    What: оператор выполнил `gh auth login` под `example`, состояние живёт в keyring/`~/.config/gh`,
    а не в env интерактивного шелла (cron и launchd не читают `~/.zshrc`).
    Evidence: `gh auth status` → `Logged in to github.com account example` (scopes: `repo`, `read:org`,
    `gist`, `admin:public_key`); `gh issue list -R example/sample-service` отдаёт три открытых issue;
    `python3 scripts/sync_issues_to_kanban.py --profile sample-service` → `ISSUE_BRIDGE_PASS_0001`, exit 0
    (issues: 3 — queued 1 идемпотентно `t_<card>`, would-queue 1, needs-triage 1).
    Остаток: scope `workflow` не выдан — для `issue list` / PR / `gh pr review` хватает `repo`, но правка
    `.github/workflows/*` такому токену недоступна; выдавать только если задача тронет CI.
    Constraints соблюдены: токен не попал в файлы репозитория (D07 OK).

26. ✅ **Закрепление интерпретатора — закрыто 2026-10-27.** Было: `#!/usr/bin/env python3` резолвился в
    `/usr/bin/python3` 3.9.6, где `jsonschema` был повреждён (`No module named 'rpds.rpds'`) → сценарий P08
    уходил в SKIP → `check_product.py --strict` печатал `PRODUCT_ACCEPTANCE_STOP_0001`.
    What: `scripts/doctor.py` (+ `make doctor`, `loopgate doctor`) называет интерпретатор, состояние
    `jsonschema`, Chrome, `gh`, `git`; `check_product.py` теперь различает «пакет не установлен» и
    «установка повреждена» и печатает команду ремонта (`--force-reinstall rpds-py referencing`); в
    `Makefile` есть `PYTHON ?= python3`, чтобы вердикт можно было снять другим интерпретатором осознанно.
    Evidence: `make doctor` → `python 3.9.6 …`, `jsonschema ok`, `gh authenticated`;
    `check_product.py --strict` → `scenarios: 8 ok, 0 fail, 0 skip`, `PRODUCT_ACCEPTANCE_PASS_0001`.
    Остаток: жёсткого пина версии нет намеренно — на трёх версиях проверяет CI, а не шибанг.

27. ✅ **Документация к факту — закрыто 2026-10-27.** Было: `README.md` перечислял `D01-D10` (гейт печатает
    D01-D13), «13 инъекций дефектов» (сьют прогоняет 26) и «этапы 1-5 закрыты». What: числа сверены с
    `DIM_IDS` и фактическим числом кейсов, раздел структуры дополнен новыми файлами, добавлен быстрый
    вход (`make doctor/check/gate/export`, `python3 -m loopgate`) и строка о том, что CI требует не маркер
    PASS, а конкретные измерения. Done-when: `check_loop.py --strict` остаётся PASS (документоведение не
    может изменить вердикт) — проверено.

28. ✅ **Второй repo-профиль — закрыто 2026-10-27.** Факт: в `configs/repo_profiles.json` был один профиль,
    `worker_brief.py profiles` печатал одну строку — механика брифа и очереди была проверена на одном
    Go-репозитории. Второго боевого репозитория нет, поэтому профиль добавлен на сам loop-system — с
    оговоркой: он самореферентный (бандл = гейт самой петли, а не чужой продукт), зато доказывает, что
    бриф не вырезан по лекалу `go build / lint / test`.
    What: профиль `loop-system` (бандл: `check_loop.py --strict`, `regressions.py`,
    `assert_dims.py --require D01 D02 D06 D07 D10 D11 D13`; каждый пункт со ссылкой на AGENTS.md или
    ci.yml), `local_path_env: LOOP_REPO`, исполнение — `--target backlog` (у репозитория пока нет remote).
    Evidence: `worker_brief.py profiles` → две строки; `worker_brief.py build --profile loop-system
    --issue 901` рендерит бриф с тремя командами; `verify` на неполном транскрипте →
    `WORKER_EVIDENCE_STOP_0001` (назван пропущенный `assert_dims`), на полном →
    `WORKER_EVIDENCE_PASS_0001`; dry-run моста по фикстуре → `ISSUE_BRIDGE_PASS_0001`;
    `check_loop.py --strict` → `D10 issue_bridge [OK] 2 repo profile(s)`.
    ⚠️ При публикации (этап 9) профиль `loop-system` убран: он самореферентный (бандл — гейт самой петли)
    и в бриф тёк `local_path` этой машины. В публичном дереве остаётся один профиль — `sample-service`
    (пример с `local_path: null` + `local_path_env: EXAMPLE_REPO`), поэтому `worker_brief.py profiles`
    печатает **одну** строку, а `D10` — `1 repo profile(s)`.

29. ✅ **Воспроизводимость из чистого клона — закрыто 2026-10-27.** Было: `git clone` →
    `check_loop.py --strict` = 8 ok / 5 fail (D03, D04, D05, D09, D12), потому что `artifacts/` и
    `handoff/` в `.gitignore`. Копировать историю этой машины в экспорт нельзя — это фальсификация,
    ровно то, против чего гейт и построен.
    What: третий вердикт вместо выдуманного PASS. `scripts/bootstrap_state.py --init` пишет
    `artifacts/loop_state.json` со `state: "cold"`; гейт печатает измерения состояния как **COLD** (не OK),
    маркер `LOOP_ENGINEERING_COLD_0001`, exit 0 — «код проверен, историю доказать нечем». Декларация
    холодного старта отвергается, как только в дереве появляется раунд или handoff (иначе пустой каталог
    прятал бы сломанную петлю), и якорь для холодного прогона не подписывается — подписывать нечего.
    Evidence (клон `git clone . /tmp/lc2`): до bootstrap — STOP (5 fail); `bootstrap_state.py --init` →
    `LOOP_STATE_COLD_0001`; после — `8 ok, 0 fail, 5 COLD` (D03, D04, D05, D09, D12 названы),
    `acceptance: cold-start`, `LOOP_ENGINEERING_COLD_0001`, exit 0. Появление `artifacts/rounds/round_01.md`
    → снова STOP, а `bootstrap_state.py --init` отвечает `refusing to declare a cold start` +
    `LOOP_STATE_STOP_0001`. `make check` в клоне → 16 ok, 0 failed, 10 skipped, `REGRESSION_SUITE_PASS_0001`.
    Остаток: `LOOP_ENGINEERING_PASS_0001` из чистого клона не воспроизводится и не должен — PASS требует
    истории, которую в клоне неоткуда взять. Это записано в README и в CI (assert_dims).
    Попутно исправлено: D08 (корпус курса) в публичном дереве печатался как skip, а `--strict` считал
    skip провалом — контрибьютор получал красное за материал, которого проект намеренно не поставляет.
    Теперь такой вход печатается как **N/A** и не валит строгий режим; частичный корпус по-прежнему FAIL
    (регресс R8: `corpus below threshold -> D08` → `failed_dims=['D08']`). Публичное дерево:
    `7 ok, 0 fail, 5 cold, 1 n/a (D08)` → `LOOP_ENGINEERING_COLD_0001`, exit 0.

30. ✅ **Третий вердикт «не стало хуже» — закрыто 2026-10-27 (`D14 change_health`).** Два вердикта
    смотрели только на настоящее; дерево, которое остаётся зелёным, теряя измерения и фальсификации,
    проходило незамеченным.
    What: `scripts/change_health.py` считает счётные метрики дерева (`gate_dims`, `regression_cases`,
    `python_lines`, `largest_file_lines`, `python_files`, `todo_markers`) и сравнивает их с записанным
    базлайном (`--write-baseline`) или с любым git-ref (`--base main`, через одноразовый worktree).
    Политика в `configs/change_health.json` говорит, какое направление хуже: `must_not_decrease`,
    `must_not_increase`, `max_growth_pct` с допуском. Метрика без политики не судится; метрика, которой
    нет в базлайне, печатается `unknown`, а не нулём. Измерение `D14` проверяет не «хорошее дерево», а
    механизм: пять фальсификаций против самого компаратора (потерянное измерение, рост сверх допуска,
    лишний TODO краснеют; улучшение и неизменность — нет) плюс `unknown` для незаписанной метрики.
    Evidence: `change_health.py` → `CHANGE_HEALTH_PASS_0001`, exit 0; `--base HEAD~1` →
    `gate_dims 13 → 14 better`, размеры `grew, in tolerance`, exit 0; мутация «базлайн заявляет лучшее
    прошлое» → регресс `R26 ... -> D14` краснеет; `check_loop --strict` →
    `D14 [OK] 6 metric policies … 5 falsifications rejected`, `LOOP_ENGINEERING_PASS_0001`.
    Прогон компаратора нашёл два его собственных дефекта: размерные метрики называли рост «better»
    (теперь вердикт следует направлению политики), а `todo_markers` считал собственный литерал регулярки
    и метку своей же фальсификации (теперь считаются только комментарии).
    Остаток: базлайн — утверждение о дереве, его перезапись должна быть ревьюимым коммитом; `D14` ждёт
    первого настоящего регресса, который поймает.

31. ✅ **Журнал задач в git (леджер) — закрыто 2026-10-27.** Факт: очередь живёт в SQLite (`<board>`), `artifacts/` и
    `handoff/` в `.gitignore` — история «что пытались сделать и почему» чистый клон не содержит.
    Прецедент: Backlog.md держит задачи markdown-файлами в репозитории, и это переживает клон; 7k звёзд —
    сигнал, что «ревьюй таску, а не код» люди покупают.
    What: `scripts/render_task_ledger.py` рендерит карточку в `backlog/<task-id>.md`: спека (слова
    issue, а не бриф, который их цитировал), бандл доказательств профиля, таблица ранов с exit-кодами,
    trail ревью и вердикт. kanban остаётся исполнением, git — памятью; в репозиторий-цель леджер не
    пишется (там правки вне задачи запрещены).
    Evidence: `render_task_ledger.py t_<card> t_<card>` → `TASK_LEDGER_PASS_0001`, файлы с 1 и 2
    ранами; повторный прогон → `[unchanged]` (идемпотентно по содержимому: отметки времени в артефакте нет, иначе каждый рендер был бы диффом); в `t_<card>.md` спека
    восстановлена из `## Issue body (binding acceptance criteria)`, вердикт — `Last run 7 ended as done`.

32. ✅ **Бюджет и «нет прогресса» на карточку — закрыто 2026-10-27.** Было: `--max-runtime` (30 мин) и
    `failure_limit: 2` не мешают воркеру сжечь квоту внутри окна; расхода при этом уже измерим —
    `task_runs.metadata.worker_session_id` → `<usage-table>`.
    What: `scripts/guard_card.py` + `configs/card_budget.json` (лимиты по ролям). Четыре правила:
    токены (`input+output+cache_read+cache_write`), число ранов на карточку, минуты стены, минуты без
    нового коммита в worktree рана. `--enforce` шлёт SIGTERM по `worker_pid` и обновляет доску **через
    её же CLI** (`hermes kanban comment` + `block`), а не правкой БД руками. Когда чего-то измерить
    нельзя (нет session_id, worktree не git) — печатает `unknown`, а не «ноль»: иначе тишина выглядела бы
    как доказательство.
    Evidence (живая инъекция на копии БД, `<state-dir>`): подставной ран
    с `sleep 300`, 282 000 токенов и 120 минутами →
    `CARD_BUDGET_STOP_0001`, exit 1, `- 120 min of wall clock (cap 30)`; с конфигом на 100k токенов —
    `- spent 282,000 tokens (cap 100,000)`; `--enforce` → `SIGTERM -> pid 25070`, `ok: kanban comment`,
    `ok: kanban block`, и `kill -0` подтверждает, что процесс мёртв. На реальной доске без активных
    ранов — `no active runs: nothing is spending`, `CARD_BUDGET_PASS_0001`, exit 0.
    Остаток: cron-джоба `monitor` каждые 5 минут не заведена (ручной запуск); деньги не считаются —
    `cost_status: unknown` у провайдера. Кейсы в `regressions.py` не добавлены: инъекция живёт в
    собственном прогоне скрипта, потому что требует подставного процесса, а не файла.

33. ✅ **Лимит внутренних попыток — закрыто 2026-10-27.** Было: `failure_limit: 2` считает провалы
    **диспатча**, а не итерации внутри сессии — агент мог долбить один падающий тест до `max-runtime`.
    What: правило в бриф (Constraint «Repair is bounded»: третья красная попытка — сдача, не четвёртый
    заход) и проверка в `verify`: транскрипт, где одна команда красная три раза, не принимается. Чтобы
    правило не осталось декоративным, в D10 добавлена седьмая фальсификация (`runaway repair accepted`) —
    гейт краснеет, если валидатор такое пропустит.
    Evidence: `worker_brief.py verify --profile loop-system --evidence` на транскрипте с тремя `exit=1` →
    `WORKER_EVIDENCE_STOP_0001`, exit 1, причины: три «exited 1» + «was red 3 times: repair is bounded at
    three attempts»; правило присутствует в отрендеренном брифе (строка 25); `check_loop.py --strict` →
    `D10 [OK] … attacked with 7 falsification cases`, `LOOP_ENGINEERING_PASS_0001`.

34. ✅ **Лестница HITL → AFK как конфиг — закрыто 2026-10-27.** Факт: сейчас возможен только
    HITL, автоподхвата issues нет вовсе (нет cron на мост), у Ralph переход = смена одного параметра
    (`max.iteration` 1 → 10) и никак не ограниченное право работать всю ночь.
    What: `configs/autonomy.json` (`mode: hitl|afk`, `max_cards`, `budget_tokens`, `active`, потолки
    ролей) + `autonomy_refusal()` в мосте: отказ проверяется **до** проверки источника и до `--apply`;
    в `afk` лишние карточки сверх `max_cards` уходят в `DEFERRED`, а не в очередь. `backlog` разрешён на
    любой ступени — он пишет леджер и ничего не запускает. Отсутствующий конфиг = `hitl` (безопасная
    ступень по умолчанию, а не удобная).
    Evidence: `--apply` при `hitl` → `refusing --apply: autonomy is HITL …` + `ISSUE_BRIDGE_STOP_0001`;
    `afk` без `budget_tokens` → отказ «needs budget_tokens > 0»; `mode: yolo` → «unknown autonomy mode»;
    `afk` с бюджетом → `None` (разрешено), `backlog` → `None` при любой ступени; `--target backlog`
    при `hitl` → `ISSUE_BRIDGE_PASS_0001`.
    Остаток: `max_cards` в живом `--apply` не срабатывал — проверка требует создать реальные карточки;
    cron на мост включается только при `mode: afk` и остаётся операторским шагом.

35. ✅ **Второй уровень ревью перед мержем — закрыто 2026-10-27.** Факт: автоматический уровень у
    нас один — `worker_brief.py verify` (доказательство бандла самим воркером). Архитектурного ревью
    всей фазы перед мержем нет; в курсе (5.7) это второй уровень: локальный Sonnet на задачу + Opus на
    весь PR с комментариями через `gh`.
    What: после открытия PR — `ywcrew panel` на диффе всей фазы (архитектура, безопасность,
    производительность, соответствие брифу), вердикт комментарием в PR через `gh pr review`.
    What сделано: `scripts/review_pr.py` (bundle `loopgate review`): `gh pr view/diff` → англоязычная
    5-частная задача с диффом → `ywcrew run` (по умолчанию `agy:gemini-3.8-flash`; `--panel` — вся
    панель) → `ywcrew result --wait` → вердикт из последней строки `VERDICT:` → `--post` через
    `gh pr review` (APPROVE → `--approve`, CHANGES → `--request-changes`). Нет строки вердикта = не
    одобрение, а STOP: недоступный ревьюер не является согласием.
    Evidence (живой прогон на PR #3 в sample-service): `review_pr.py --profile sample-service --pr 3 --dry-run` →
    бриф с диффом, `PR_REVIEW_PASS_0001`; живой dispatch → run `2026-10-27-d336871a`, ревью с находками
    `file:line`, `verdict: APPROVE`, `PR_REVIEW_PASS_0001`, exit 0. Разбор существующего результата
    (`--result-file`) тоже даёт PASS — путь без повторной траты.
    Остаток: `--post` не выполнялся (публичный PR — нужно ваше «да»); вердикт CHANGES/REJECT на живом
    прогоне не воспроизведён.
    Constraints соблюдены: только через `ywcrew` (D06), Claude-модели не использовались.

**Критерий приёмки этапа 8:** `make gate` (то же, что `python3 scripts/check_loop.py --strict`) печатает
`LOOP_ENGINEERING_PASS_0001` со всеми 14
измерениями и `PRODUCT_ACCEPTANCE_PASS_0001`; `sync_issues_to_kanban.py` печатает
`ISSUE_BRIDGE_PASS_0001`; `worker_brief.py profiles` печатает профиль `sample-service` (второй профиль
`loop-system` убран при публикации — см. п. 28); из чистого клона вердикт
воспроизводится одной командой; в `backlog/` лежит леджер живой карточки; `guard_card.py` держит бюджет
карточки, `review_pr.py` оставляет вердикт в PR, лестница HITL→AFK управляется конфигом. Каждый пункт
закрывается только своим маркером, а не прозой.

## Этап 9. Публикация (OpenSource) — ✅ закрыт (публикация завершена 2026-10-27)

Цель: отдать проект сообществу, не отдавая приватное. Блокеры, найденные аудитом: OCR платного курса в
`course/` (39 файлов), абсолютные пути и имя аккаунта в 8 файлах, жёсткая привязка очереди к `hermes`,
отсутствие лицензии, CI и английской документации.

36. ✅ **Лицензия и упаковка.** `LICENSE` (MIT, «loop-system contributors»), `pyproject.toml`
    (`requires-python >=3.9`, `loopgate` entry point, `jsonschema` как optional extra), `Makefile`
    (`gate` / `product` / `visual` / `regress` / `check` / `export`), пакет `loopgate/` — одно имя на все
    гейты, без переписывания самих скриптов. Evidence: `python3 -m loopgate` печатает инвентарь,
    `python3 -m loopgate dims --require D01 D02 D06` → `CI_DIMS_PASS_0001`, неизвестная команда → exit 2.
37. ✅ **CI.** `.github/workflows/ci.yml`: один интерпретатор 3.12 (матрица 3.9 / 3.12 / 3.14 была убрана —
    три версии умножали поверхность отказов и не говорили контрибьютору ничего действенного; «зелёный бейдж»
    значит «запускается», а не «может запуститься на чём-то»), `compileall`, `assert_dims`
    (только измерения, зависящие от кода), регресс-сьют. Новый `scripts/assert_dims.py` делает различие
    «код» против «состояние» исполняемым: в чистом клоне `LOOP_ENGINEERING_PASS_0001` недостижим по
    дизайну, и CI не делает вид, что достижим.
38. ✅ **Развязка с Hermes.** `sync_issues_to_kanban.py --target backlog` пишет карточку markdown-файлом
    в `backlog/` (спека + бандл + контракт транскрипта) — очередь работает без движка исполнения;
    `render_status_page.py` и `estimate_task.py` при отсутствии `hermes` деградируют с сообщением, а не
    трейсбэком. Evidence: живой прогон `--target backlog --apply` создал
    `backlog/gh-…-2.md` и `…-4.md`; повторный прогон → `already queued 2, queued 0` (идемпотентность
    держится на файлах, а не на БД).
39. ✅ **Обезличивание как команда.** `scripts/export_public.py` + `configs/public_sanitize.json`:
    собирает дерево из коммитируемых файлов (tracked + новые, минус gitignored), исключает `course/`,
    `backlog/`, драйвер корпуса и macOS-OCR, подменяет идентичности, кладёт примеры конфигов
    (`configs/repo_profiles.example.json`, `configs/anchor_witness.example.json`), прогоняет гейт внутри
    экспорта и **fail-closed** сканирует результат на запрещённые строки. Evidence: `--out … --git-init`
    → 39 файлов, `PUBLIC_EXPORT_PASS_0001`, свежая история `5484088`, в экспорте по коду
    (D01, D02, D06, D07, D10, D11, D13) — OK, `CI_DIMS_PASS_0001`.
40. ✅ **Профиль без чужих путей.** В профиле появился `local_path_env`; `local_path` может быть null.
    Гейт (`D11`) принимает профиль, если объявлен хотя бы один способ найти продукт, и падает, если нет
    ни одного. Правило закреплено регрессом `R25 profile loses local_path and local_path_env -> D11`.
41. ✅ **Регресс-сьют честно различает «пропущено».** Раньше кейс, чей инжектор читает отсутствующий
    файл состояния, падал как ошибка (в чистом клоне — 11 ложных FAIL). Теперь: baseline считается
    первым, позитивный кейс в дереве без состояния печатает `[skip]` с причиной («this tree has never
    run the loop»), инжекторы к отсутствующему состоянию дают skip, а не FAIL. Evidence: рабочий репозиторий
    `26 ok, 0 failed, 0 skipped` → `REGRESSION_SUITE_PASS_0001`; экспорт `15 ok, 0 failed, 11 skipped`
    → тот же маркер. Пропуски не считаются проходами и не тонут в счёте.
42. ✅ **Корпус курса — опционален, а не обязателен.** `D08` переведён в `OPTIONAL_DIMS`: дерево без
    `course/` печатает SKIP с причиной («study material, not part of the tool»), частичный корпус —
    по-прежнему FAIL. Иначе гейт проходил бы только на машине автора.
43. ✅ **Документация для сообщества.** `README.md` (английский первым: проблема → маркеры → 60-секундный
    тур → как подключить к своему репозиторию → честное сравнение с Pramaan / Assay / hashgate / aelitium /
    Backlog.md / worktrunk → ограничения), `CONTRIBUTING.md` (правило «новое измерение без фальсификации
    не принимается»), `SECURITY.md`, шаблон PR с блоком доказательств.
44. ✅ **Публикация — закрыто 2026-10-27.** Репозиторий `loopagent` (публичный, создан оператором
    2026-10-27; имя аккаунта в тексте плана не приводится — оно и так вычищается экспортом). Перед пушем прогнан не только экспортёр, но и независимый аудит
    (`scripts/audit_public.py`, 13 проверок) — он нашёл то, чего экспортёр не видел: имя приватного
    продукта в 12 файлах (включая фикстуру и `POSTGRES_DB: sample_service`), локальные id карточек и
    scratch-пути в прозе, русский README со ссылкой на платный курс, настоящее id карточки в примере CLI,
    «loop-system contributors» в LICENSE. Всё вычищено подменами и redaction по шаблону; фикстура
    переименована в `issues_sample.json` (имя файла тоже утекало).
    Evidence: `PUBLIC_EXPORT_PASS_0001` (45 файлов, один коммит `1501e19`, авторство нейтральное
    `loopgate export <export@example.org>`); `AUDIT_PASS_0001` (аудит-скрипт в дерево не входит — он
    перечисляет те самые строки, которые вычищает; воспроизводимый аудит для контрибьютора — это
    fail-closed скан самого `export_public.py` плюс гейт внутри экспорта); клон с GitHub → `make check`
    (`CI_DIMS_PASS_0001`, 15 ok/0 failed/11 skipped) и холодный гейт `LOOP_ENGINEERING_COLD_0001`.
    Первый CI-прогон упал честно и был починен, а не отключён: `setup-python` не находит 3.9 на самой
    новой Ubuntu (сборка 3.9 теперь на `ubuntu-22.04`), а список измерений требовал D08, который проект
    намеренно не поставляет (печатается N/A). Второй прогон — `ci` зелёный на единственном
    интерпретаторе 3.12 (матрица трёх версий убрана: один интерпретатор = один честный ответ)
    (run 40556448252).
    Репозиторий оформлен: описание («gates that accept agent work by proof, not by confidence…»), восемь
    топиков (`ai-agents`, `agentic-coding`, `code-review`, `verification`, `evidence`, `developer-tools`,
    `ci`, `python`) и три `good first issue` с acceptance criteria и требованием бандла доказательств:
    #1 `guard_card` читает очередь из JSON-снимка (работает без конкретного рантайма и проверяется в CI),
    #2 мост принимает файл как авторитетный источник (`--exported-by`, чтобы очередь не зависела от `gh`),
    #3 `review_pr` пинует парсер вердикта фикстурами (APPROVE / CHANGES / вердикта нет).
    Все три выбраны из фактических дыр, найденных при работе, а не из списка «что бы ещё сделать».

**Критерий приёмки этапа 9:** `make export` печатает `PUBLIC_EXPORT_PASS_0001`; в собранном дереве
`make check` зелёный; в дереве нет `course/`, `backlog/`, абсолютных путей и имён аккаунтов; английский
README на первом месте; лицензия и CI на месте; публикация выполнена оператором.

## Этап 10. Восстановление истории петли после потери состояния — ✅ закрыт (2026-10-29, round 01–04)

45. ✅ **Приёмка проекта снова доказывается — закрыто 2026-10-29.** Было: `artifacts/` и `handoff/` в
    `.gitignore`, публикационная чистка их унесла, и дерево на HEAD `692609a` печатало
    `LOOP_ENGINEERING_STOP_0001` по пяти измерениям состояния (D03, D04, D05, D09, D12) — код зелёный,
    история недоказуема. Восстановить её из git нельзя: это состояние, а не исходники.
    What: петля прогнана заново четырьмя ролями через `ywcrew` (explorer `2026-10-29-07c1ed02`, worker
    `2026-10-29-f8b0ee13`, reviewer `2026-10-29-286d4a0e`, evaluator `2026-10-29-1fde84b2`), раунды
    01–04 записаны по реальным транскриптам, `handoff/latest_loop.json` цитирует хэш якоря, якорь подписан.
    Worker правил не пустое место: два заголовка этапов 8 и 9 заявляли «в работе» при всех закрытых
    пунктах (`PLANS.md:132`, `PLANS.md:327`). Reviewer сказал `CHANGES` — и был прав: сжатие дат в
    «п. 25–35 закрыты 2026-10-27» делало утверждение ложным (пункт 25 закрыт 2026-10-21); ремонт внесён.
    Побочно прогон вскрыл **дефект самого гейта**: дерево без якоря не могло выпустить якорь, потому что
    `D12` требует подпись, подпись требует якорь, якорь требует `D12` — приёмка была недостижима в любом
    свежем клоне. Фикс: набор `SELF_CERTIFYING_DIMS = {D05, D12}` (оба измерения читают якорь, а не дерево)
    применён к условию выпуска якоря, хэшу вердиктов, сверке состояний в `D05` и полю `excluded_from_hash`;
    ревью отдельным движком `2026-10-29-8bef6edb` → `VERDICT: APPROVE`, находок нет.
    Evidence: `python3 scripts/check_loop.py --strict` → `13 ok, 0 fail, 1 n/a (D08)`,
    `acceptance: strict-complete`, `LOOP_ENGINEERING_PASS_0001`, `exit=0`, хэш якоря `fe0dd8a6d8ff7640`;
    `anchor_witness.py verify` → `ANCHOR_WITNESS_PASS_0001`; `make check` → 26 ok / 0 failed / 1 skipped,
    `REGRESSION_SUITE_PASS_0001` (кейсы R19/R21/R24 по-прежнему ловят подделку якоря), `PR_REVIEW_PASS_0001`.
    Остаток: фикс гейта **не закоммичен** — он меняет семантику приёмки и ждёт ратификации оператором;
    внешний свидетель (`anchor_witness.py publish`) остаётся сетевым шагом оператора.

## Критерий приёмки всего проекта

`python3 scripts/check_loop.py --strict` печатает `LOOP_ENGINEERING_PASS_0001`, `exit 0`, а `handoff/`
содержит синтез и артефакты четырех ролей, воспроизводимые другим оператором.
