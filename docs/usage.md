# Поиск с внешними сервисами

Подробные команды для OpenSearch, Query Understanding, notebook, batch и vector search.
Для первого запуска без сервисов начни с [Python-only примера](examples/offline-ranking.md).

## Запуск

Нужны Python 3.12+ и [Poetry 2.2+](https://python-poetry.org/docs/#installation).
Выполняй команды из корня репозитория. Poetry управляет зависимостями
через `poetry.lock`; проект работает из checkout, без установки собственного пакета:

```sh
poetry install
poetry run python -m src.run \
  connection.url=https://search.example.org/opensearch \
  search.index=vacancies \
  'search.fields=[title,description]' \
  search.size=10
```

URL, индекс и поля — пример: замени их реальными значениями. URL указывает
на корень API, включая префикс прокси. Программа добавляет `/{index}/_search`.
Допустимы HTTP и HTTPS; проверка TLS включена. Redirect не поддерживается —
укажи конечный URL. Другие API OpenSearch не вызываются.

Если нужна авторизация, задай `OPENSEARCH_AUTHORIZATION` полным значением
заголовка: `Bearer …` или `Basic …` (base64 от `username:password`).
Скрытый ввод в Bash без записи значения в историю:

```bash
read -r -s -p 'Authorization header: ' OPENSEARCH_AUTHORIZATION
export OPENSEARCH_AUTHORIZATION
printf '\n'
```

Без переменной заголовок не передаётся. Не передавай секрет через CLI override
или URL: Hydra сохраняет overrides. Конфиг содержит ссылку на окружение,
а не значение секрета. `--cfg job --resolve` явно раскрывает разрешённый конфиг,
включая секреты, поэтому не используй его для публикации диагностики.

В приглашении `search>` вводи текст. Пустая строка пропускается. Каждый ввод —
независимый поиск с `multi_match`. Поля могут содержать boost:
`'search.fields=[title^2,description]'`.

- EOF (Ctrl-D в Unix-терминале) завершает сессию и сохраняет `searches.json`.
- Ctrl-C прерывает с кодом 130 без итогового артефакта.
- Ошибка запроса, неполный ответ или ошибка экспорта завершает с кодом 1.
  После ошибки поиска частичный артефакт не сохраняется; уже показанные результаты
  остаются в терминале.

Результат находится в `outputs/<дата>/<время>-<уникальный id>/searches.json`.
Hydra также сохраняет конфиги и лог. Существующий файл результата не перезаписывается.
Каталог можно задать через `hydra.run.dir=/path/to/new-run`.
Timeout HTTP регулируется `connection.timeout=30` (секунды); retries отсутствуют.

## Настройка эксперимента

Базовый конфиг — [configs/main.yaml](../configs/main.yaml), композиция —
[001-basic.yaml](../configs/experiments/vacancy-search/interactive/001-basic.yaml).
Registry хранит `search` и `search_pipeline`, эксперимент выбирает pipeline
по имени. Hydra создаёт объекты; шаги обмениваются общим `PipelineData`.

Например, статические фильтры и контекст в конфиге эксперимента:

```yaml
context:
  city: Москва
filters:
  - _target_: src.domain.request.filters.TermFilter
    field: remote
    value: true
  - _target_: src.domain.request.filters.UserPropertyFilter
    field: city.keyword
    property: city
```

Условия объединяются AND. QueryFilter добавляется из ввода и не допускается
в статических filters. Имена полей должны соответствовать mapping индекса.
Отсутствующее свойство пользователя или неизвестный фильтр — ошибка,
а не повод выполнить более широкий поиск.

## Jobboard: отдельный lexical-эксперимент

[Spec 004](../docs/specs/004-end-to-end-search.md) и
[контент 004A](../docs/specs/004-jobboard-content.md) задают эксперимент поверх
существующего полнотекстового индекса. Он включает модульный RU/EN parser,
контролируемые aliases, веса полей и phrase, мягкие признаки уровня/места/формата,
отдельные варианты RRF и fuzzy. Это исследовательские гипотезы; начальные веса
не объявляются оптимальными. Глобальный эксперимент по умолчанию остаётся прежним.

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/jobboard/lexical@experiment=001-interactive' \
  connection.url=https://search.example.org/opensearch \
  jobboard.source.index=vacancies \
  'jobboard.source.fields=[title,description]' \
  jobboard.source.title_field=title
```

Здесь заполняется `jobboard.source`, прежний блок `search` не нужен. Поля должны
соответствовать реальному mapping. При одном text-поле укажи его в `fields`, а
`title_field` оставь `null`: title-only bonus отключится. Для жёстких filters явно
объяви допустимые поля и типы, например
`'+jobboard.source.term_fields={city:keyword,remote:boolean}'`.
Распознанное слово «Москва» само по себе такого фильтра не создаёт.

Точки настройки в [профиле](../configs/jobboard/lexical/profile.yaml):

| Настройка | Выбор |
| --- | --- |
| `experiment.pipeline` | `baseline`, `lexical`, `parsed` (default), `fused` |
| `jobboard.candidate_depth` / `jobboard.final_k` | Глубина каждой поисковой ветки / размер финальной выдачи; defaults 100 / 20 |
| `jobboard/lexical/parsing@jobboard.parser` | `rules`, `roles_skills`, `no_entities` |
| `jobboard/lexical/retrieval@jobboard.retrieval.parsed` | `parsed`, `no_aliases`, `no_topical_gate`, `strict_coverage` |
| `jobboard/lexical/retrieval@jobboard.retrieval.lexical` | `lexical`, `no_phrase` |
| `jobboard/lexical/graph@jobboard.registry` | `lexical`, `fuzzy` |

Fuzzy включается двумя overrides: выбором graph `fuzzy` и
`experiment.pipeline=fuzzy`. Fused делает два последовательных HTTP search;
прочие pipelines — один. RRF получает полную глубину обеих веток и только затем
применяет final_k. Исходный QueryFilter и явные AND-filters сохраняются.

`jobboard.retrieval.parsed.title_field_weight=4` и
`jobboard.retrieval.parsed.default_field_weight=1` задают начальные веса;
`field_weights` разрешает явные overrides объявленных полей. Аналогичные параметры
есть у lexical и fuzzy. Для абляции одного сигнала меняй соответствующую ветку.
`no_aliases` отключает также подстановку canonical. При отключении parser у parsed
контрольная lexical-ветка сохраняет собственный no-entities parser.

[Словарь jobboard-seed-v1](../configs/jobboard/lexical/lexicons/ru-en.yaml) содержит
73 записи: 27 ролей, 30 навыков, 7 уровней, 3 формата работы и 6 мест. Категории
выбираются независимыми стадиями. PM/QA и некоторые короткие технологии имеют
явные правила неоднозначности; C++/C#/.NET сохраняются при нормализации.
Неподдержанные отрицания и связки отключают expansion всего запроса. «Не продажи»
остаётся текстовым запросом, а не гарантированным исключением продаж.

### Локальная проверка и сравнение

```sh
poetry run pytest tests/test_jobboard_parser.py tests/test_jobboard_search.py \
  tests/test_jobboard_content.py tests/test_jobboard_workflow.py
```

Тесты сами запускают временный HTTP-сервер. Они используют 20 вымышленных вакансий,
24 case с полной матрицей 480 оценок, отдельный case явного фильтра и два коротких
smoke case. Контролируемые ответы проверяют CLI → Hydra → HTTP → SERP/RRF →
NDCG → атомарный JSON-export, включая сбои и сохранность прежних результатов.
Сервер не исполняет BM25, анализаторы или fuzzy: эта проверка не измеряет поисковое
качество и не требует установки OpenSearch.

Для сравнения через CLI выбери `002-comparison` вместо `001-interactive`.
Его default cases — **synthetic**: они подходят только источнику с теми же fixture ids.
Для реального индекса создай собственную Hydra cases group по формату
[cases/synthetic.yaml](../configs/jobboard/lexical/cases/synthetic.yaml), затем выбери её
через `jobboard/lexical/cases@jobboard.cases=<имя>`.
Comparison получает одинаковые cases и judgments для baseline/lexical/parsed.
Для явного выбора набора pipelines сначала удали mapping, затем задай новый:

```text
'~experiment.pipelines'
'+experiment.pipelines={lexical:lexical,parsed:parsed,fused:fused}'
```

Сначала собери реальные retrieval-артефакты, разметь pool результатов выбранных
вариантов и раздели development/held-out queries. Отсутствующий judgment не равен
нулю и запрещает экспорт сравнения. Не переносить synthetic grades на реальные ids.
Hydra сохраняет конфигурацию рядом с результатом; для исследовательского отчёта
зафиксируй также версию/дату индекса, словаря и cases, ablations, качество по queries
и slices, число HTTP и latency. Изменения удалённого индекса не являются replay.

Для внешней приёмки нужны реальные URL/auth, версия OpenSearch, подтверждённые
поля/analyzers и judgments. Клиент не создаёт индекс, не меняет BM25 `k1/b` и не
восстанавливает различия C/C++/C#, если их уже потерял анализатор индекса.

## Исследование в ноутбуке

Открой [search.ipynb](../src/experiments/notebooks/search.ipynb) в Jupyter или VS Code
с Python kernel окружения проекта. Jupyter не входит в runtime-зависимости.
В notebook есть готовые ячейки настройки, повторной Hydra-композиции, просмотра
разбора и DSL без HTTP, одиночного e2e поиска и пакетной CSV-выгрузки.
Пример URL и mapping нужно заменить значениями вашего контура; Authorization
берётся из окружения kernel, как в CLI. Notebook поставляется без сохранённых outputs.

После сборки эксперимента:

```python
serp = experiment.search("python backend developer")
rows = [
    {"id": item.id, "score": item.score, "fields": item.fields} for item in serp.items
]
```

Метод применяет context/filters из конфига и возвращает независимую SERP без печати
и записи файлов. Дополнительные `context={...}` переопределяют свойства верхнего
уровня, `filters=[...]` добавляют условия. QueryFilter передавать не нужно.
После ошибки можно повторить ручной вызов. Для изменения настроек заново собери
эксперимент: редактирование cfg не меняет уже созданные Python-объекты.

При самостоятельной сборке используй `initialize_config_dir` как context manager,
`compose` и `instantiate(..., _convert_="all")`. Задай `experiment.exporters=[]`:
default путь exporter зависит от `${hydra:runtime.output_dir}`, отсутствующего
в Compose API. Можно также задать явный путь. Пример полностью показан в notebook.

## Выгрузка выдач по CSV-набору

Вход содержит ровно колонки `id,text`; порядок колонок произвольный. Id уникальны,
id и текст непустые; пробелы по краям удаляются. Поддерживаются UTF-8 с BOM или
без него и стандартное CSV quoting. Пример — [examples/queries.csv](../examples/queries.csv).
Весь файл проверяется до первого HTTP; оценки релевантности не требуются.

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/jobboard/lexical@experiment=003-batch' \
  connection.url=https://search.example.org/opensearch \
  jobboard.source.index=vacancies \
  'jobboard.source.fields=[title,description]' \
  jobboard.source.title_field=title \
  experiment.search.pipeline=parsed \
  experiment.input_path=examples/queries.csv
```

Результат — `outputs/<дата>/<время>-<id>/searches.csv`. Для fused выбери
`experiment.search.pipeline=fused`; overrides parsing/retrieval/graph и source
остаются прежними. Контекст и статические фильтры батча находятся в
`experiment.search.context` и `experiment.search.filters`.

Для обычного multi_match без jobboard:

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/batch@experiment=001-basic' \
  connection.url=https://search.example.org/opensearch \
  search.index=vacancies \
  'search.fields=[title,description]' \
  search.size=10 \
  experiment.input_path=examples/queries.csv
```

CSV содержит `query_id,query,rank,document_id,score,fields`: строку на документ,
ранг с 1, полный JSON полей в `fields`. Запрос без результатов имеет одну строку
с пустыми колонками результата; null score — пустая ячейка. Порядок запросов и SERP
сохраняется. Ошибка прекращает батч без частичного CSV, прежний файл не
перезаписывается. Батч хранит результаты в памяти; streaming/resume не реализованы.

Для программного использования `batch.run()` возвращает артефакт без записи;
`artifact.to_rows()` даёт таблицу для просмотра, `artifact.to_dict()` — полные
структурированные результаты. Экспорт вызывается явно через exporters. CLI делает
это после успешного run. Контракт и проверки — [spec 005](../docs/specs/005-search-research.md).

## Пять экспериментов для рабочих профессий (006)

[Новый notebook](../src/experiments/notebooks/blue-collar.ipynb) содержит русские
подборки для склада, транспорта, производства, торговли, условий и ошибок ввода.
Профиль включает 64 профессии, оборудование, графики и квалификации. В
[наборе запросов](../examples/blue-collar/queries.csv) — 249 примеров.
Это курируемый стартовый материал, а не доказательство качества на реальном индексе.

| Вариант | Механизм |
| --- | --- |
| `c1` | Веса text-полей и фразы в title |
| `c2` | Ограниченные бонусы свежести и качества через Painless |
| `c3` | Приоритет категорий графика через Painless sort |
| `c4` | Понятия, role/skill routing, квалификации и мягкие атрибуты |
| `c5` | C4 плюс исправления опечаток, раскладки и curated transliteration |

Начать без ranking metadata можно с C1/C4/C5. Названия полей и URL ниже — примеры:

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/jobboard/research@experiment=001-compare' \
  connection.url=https://search.example.org/opensearch \
  jobboard.source.index=vacancies \
  'jobboard.source.fields=[title,description]' \
  jobboard.source.title_field=title \
  jobboard.session.now_ms=1790640000000 \
  'experiment.variants=[c1,c4,c5]' \
  'experiment.text="комплектовшик ТСД без опыта"'
```

`now_ms` — фиксированное время сессии в Unix milliseconds; пример соответствует
2026-09-29 00:00 UTC. Notebook устанавливает время один раз. Для batch выберите
`002-batch` вместо `001-compare`; default CSV — `examples/blue-collar/queries.csv`.
Можно задать `experiment.input_path=/path/queries.csv`. Каждый запрос делает один
HTTP search на выбранный вариант. Полный batch хранит результаты в памяти.

Результат `research.json` содержит original text, точный request body, parse/rewrite
trace, timing и полные hits, включая sort keys. Credentials в артефакт не входят.
Если один поиск не удался, частичный batch не публикуется; прежний файл не заменяется.
В notebook `experiment.preview(text)` не делает HTTP, `experiment.compare(text)`
возвращает отдельный артефакт, а `experiment.searches['c4'].search(text)` сохраняет
обычный контракт одиночного поиска. Для прежнего CSV используйте этот SearchExperiment
через BatchSearchExperiment, как показано в notebook.

Для C2/C3 объявите действительно существующие single-valued поля с doc values:

```text
'+jobboard.source.ranking_fields={published_at:{field:published_at,type:date},quality:{field:card_completeness,type:numeric},category:{field:schedule_category,type:keyword},stable_id:{field:vacancy_id,type:keyword}}'
'experiment.variants=[c1,c2,c3,c4,c5]'
```

`card_completeness` — пример осмысленного numeric signal, не требуемое production-имя.
Готовый `freshness-only` требует только published_at, `quality-only` — только quality.
C3 требует category и уникальный непустой stable_id у каждого документа. Fixture
использует категории day/night/shift_2_2/other; задайте реальные значения своего поля.
Приоритет группы сильнее score: слабый дневной результат может обогнать сильный ночной.

В локальной diagnostics имена вроде `ranking.quality` читаются по вложенному
`_source`; если есть точный верхнеуровневый ключ с таким именем, он имеет приоритет,
даже при null. Массивы и суффиксы mapping вроде `.keyword` автоматически не
разрешаются. Числовая дата в `_source` трактуется как Unix milliseconds; ISO-дата
с timezone также поддерживается. Эти значения описывают возвращённый `_source`,
а не doc values, использованные сервером; подробный контракт —
[spec 007](../docs/specs/007-research-evidence-corrections.md).

Конфигурация собирается в [profile.yaml](../configs/jobboard/lexical/research/profile.yaml).
Основные настройки добавляются к той же CLI-команде:

```text
jobboard.query.title_field_weight=8
jobboard.query.title_phrase_boost=4
+jobboard.query.concept_coverage=1
'+jobboard.query.field_weights_by_kind={role:{title:4,description:1},skill:{title:2,description:3}}'
jobboard/lexical/ranking@jobboard.rankings.signals=freshness-only
jobboard/lexical/ranking@jobboard.rankings.tiers=night-first
jobboard/lexical/research/ablation@_global_=no-aliases
```

Rankings: relevance, balanced, freshness-only, quality-only, phrase-rescore,
phrase-only; для C3 — day-first, night-first, shift-2-2-first.
`relevance` в signals/tier group — готовая абляция text-only/no-tiers.
Default C4/C5 — slots-default/rewrite-default. Общая ablation group содержит
no-phrase, title-2, title-8, coverage-strict, no-aliases, no-attributes,
strict-coverage, shared-fields, no-spelling, no-layout, no-curated-rewrites,
rewrite-off, rewrite-weight-0.4. Одновременно выбирается одна группа, остальные
независимые параметры можно переопределить явно.

Полные Painless sources находятся в [ranking/](../configs/jobboard/lexical/ranking/),
редактируются прямо в YAML. Имена полей связываются через `field_params`, числа —
через `script.params`; фиксированное время — через `now_param`. Source не вставляет
текст пользователя как код. Изменение coefficients не требует правок Python.
В phrase-rescore `_score` внутри скрипта означает score вложенного phrase query,
а не first-pass score; window_size действует на каждом shard. Custom sort запрещён.

Словари — [blue-collar/ru/](../configs/jobboard/lexical/lexicons/blue-collar/ru/).
`forms` распознают словоформы, `aliases` разрешают expansion, `ambiguous`/`requires`
задают контекст. `surface_prefixes` защищают короткие обозначения от совпадения
с предлогами. Циклические rewrites и цели вне словаря отклоняются до HTTP.
Дополнительные поля `skills`/`qualifications` не предполагаются без source declaration.

После смены параметров в notebook повторите **compose и instantiate**:
ранее разрешённый cfg может содержать материализованные интерполяции. Ноутбук
не хранит live-view объектов. Для нового экспорта выбирайте новый путь.

Локальные проверки используют HTTP fixture и не исполняют OpenSearch/Painless.
Для реального запуска сервер должен разрешать соответствующий script context и
expensive queries; настройки сервера автоматически не меняются. Утверждения о
качестве, анализаторах, remote compatibility и latency требуют вашего окружения.

## Векторный поиск 008

[configs/vector.yaml](../configs/vector.yaml) — отдельный Hydra root для dense vector
search. Начальный профиль — pinned `intfloat/multilingual-e5-base`: 768 dimensions,
mean pooling/L2, `query: ` и `passage: `, 512 tokens. Приложение использует TEI 1.9.4
на одной выбранной GPU и предоставленный пользователем Qdrant 1.19.2. Qdrant нужно
запустить и настроить самостоятельно; приложение создаёт только собственные новые
поколения коллекций. OpenSearch остаётся default для обычного `src.run`.

### Подготовка машины и общего конфига

На целевой машине нужны Python/Poetry, Docker и NVIDIA Container Toolkit. До
установки зависимостей, image pull и загрузки модели проверь свободное место
(`df -h /`). Определи реальное устройство:

```sh
nvidia-smi -L
nvidia-smi --query-gpu=name,uuid,memory.total,driver_version --format=csv
```

Выбери совместимый image **с digest** по
[матрице spec 008](../docs/specs/008-vector-search.md#142-image-pin-и-hardware-evidence).
Обозначение «NVIDIA T…» не подтверждает T4. Подготовь существующий writable cache
directory, свободный loopback port TEI и Qdrant endpoint. Пример общего site config,
включая конкретный image для подтверждённой T4, находится в
[разделе 14.4](../docs/specs/008-vector-search.md#144-общий-site-config-для-всех-запусков).

Сохрани конфиг вне репозитория, например `/srv/recsys/config/vector-site.yaml`:

```yaml
defaults:
  - vector
  - _self_

embedding:
  runtime:
    image: <совместимый официальный tag@sha256:digest из spec>
    gpu_uuid: <полный UUID выбранной GPU>
    cache_dir: /srv/recsys/model-cache
corpus:
  identity: vacancies-research-v1
  path: /srv/recsys/corpus/vacancies.json
qdrant:
  url: http://127.0.0.1:6333
  collection: vacancies-e5-g001
```

Замени placeholders и пути до запуска. Launcher, index, search, batch и notebook
читают один site config; override одной команды не переносится на следующую.
Опциональные `TEI_API_KEY` и `QDRANT_API_KEY` передавай через окружение процесса,
без значений в YAML/CLI. Не публикуй `--cfg job --resolve`: он раскрывает secrets.
Read-only проверка композиции без запуска сервисов:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site --cfg job
```

### Корпус, построение и поиск

Корпус — непустой UTF-8 JSON-массив. Каждый элемент содержит ровно `id` и `fields`:

```json
[
  {"id": "vacancy-1", "fields": {"title": "Сварщик", "description": "Работа на заводе", "city": "Тула"}}
]
```

Id уникальны и сохраняются точно, включая пробелы по краям непустой строки.
Исходные fields полностью возвращаются в выдаче. По умолчанию текст собирается из
optional `title` и `description`: отсутствующие поля, null и пустые строки
пропускаются. Общий пустой текст и другие нестроковые значения отклоняются.
Recipe настраивается в `corpus.text.fields` и
`corpus.text.separator` общего site config. Query overflow отвергается; длинные
документы усекаются справа до 512 tokens вместе с prefix и special tokens.
До полного build выполни token audit в notebook и оцени долю усечённых вакансий.

```sh
poetry run python -m src.integrations.embedding.launch \
  --config-dir /srv/recsys/config --config-name vector-site

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site \
  experiments/vacancy-search/vector@experiment=001-index

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site \
  experiments/vacancy-search/vector@experiment=002-interactive
```

Launcher не принимает существующий container; адресные restart/stop выполняет
оператор. Успешные HTTP probes подтверждают готовность API. Для подтверждения GPU
нужны CUDA startup logs и process/VRAM выбранного UUID через `nvidia-smi`.

Index сначала проверяет весь корпус, затем создаёт новое поколение, indexes и
points. Exact count, полный read-back и filtered smoke предшествуют публикации
`ready`. Receipt сохраняется в `outputs/<дата>/<run>/vector-index.json`.
Коллекция с существующим именем не перезаписывается; retry/resume/delete отсутствуют.
Для нового корпуса, recipe, profile или runtime image используй новое имя поколения.
После ошибки до публикации building collection не допускается к поиску. Ошибка
локального экспорта receipt после remote ready не отменяет готовность коллекции.

Векторный поиск использует обычный `search>` и сохраняет `searches.json` при EOF.
Default candidate depth — 100, final top-k — 20; параметры `retrieval.candidate_depth`,
`retrieval.final_k`, `retrieval.exact`, `retrieval.hnsw_ef` задаются через Hydra.
`indexed_only` сохраняется `false`. Явные filters требуют scalar declarations
`qdrant.filter_fields` до build; их path/type и payload indexes входят в source
contract. Формат объявлений описан в
[spec 008, раздел 12.2](../docs/specs/008-vector-search.md#122-declared-scalar-filters).
Слова в query сами по себе не создают жёстких фильтров.

### Notebook и batch

[vector.ipynb](../src/experiments/notebooks/vector.ipynb) использует тот же site YAML.
В нём доступны чистый preview model input/filters, recipe preview, последовательный
token audit, явный build, отдельные embedding/native query и обычный search/batch.
Сборка коллекции включается явно через `build_collection`; чистый prepare не
вызывает TEI/Qdrant. Для нового экспорта нужен новый путь.

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site \
  experiments/vacancy-search/vector@experiment=003-batch \
  experiment.input_path=/srv/recsys/queries.csv
```

CSV входа сохраняет контракт `id,text`, результат — прежний `searches.csv` с
полными fields. Ошибка прекращает batch без частичного артефакта.
Локальные HTTP fixtures проверяют contracts/orchestration; реальные GPU inference,
tokenizer boundaries, Qdrant filtering/HNSW, persistence и качество здесь не
подтверждены. Актуальное evidence находится в [development](../docs/development.md),
целевая приёмка G01–G06 — в [spec 008](../docs/specs/008-vector-search.md).


## Десять вариантов поиска и notebook сравнения 009

Основное рабочее место — [search-study.ipynb](../src/experiments/notebooks/search-study.ipynb).
Он показывает выдачи рядом, union matrix рангов и изменений относительно reference,
полные карточки, winner passage E5 и diagnostics конкретного запуска. Изменение
query, city/active и выбранного подмножества не запускает builds. Просмотр карточки
и повторный render читают сохранённый snapshot без нового поиска.

Скопируй [site.example.yaml](../examples/study/site.example.yaml) вне репозитория,
например в `/srv/recsys/config/study-site.yaml`. Заполни URL OpenSearch/TEI/Qdrant,
index/fields, реальные equality mappings, corpus path/identity, pinned TEI image
и физические имена ready whole/title/body/chunk generations. Credentials передавай
через `OPENSEARCH_AUTHORIZATION`, `TEI_API_KEY`, `QDRANT_API_KEY`. JupyterLab или
VS Code notebook frontend использует project environment; frontend устанавливает
оператор на целевой машине. В setup notebook укажи `SITE_CONFIG` либо environment
`RECSYS_STUDY_SITE`; overrides задаются списком строк Hydra (environment
`RECSYS_STUDY_OVERRIDES` — JSON array). Не отображай resolved site YAML с secrets.

Corpus study требует непустые title и description у всех документов.
`StudyCohortReader.audit()` показывает все missing/nonstring/empty ids, `read()`
отклоняет весь невалидный cohort. Build не исключает записи молча. Для E1–E3 нужна
одна whole generation, для E4 — title и body, для E5 — chunk. Запуск TEI использует
существующий launcher 008 и тот же study site config; Qdrant предоставляет оператор.
Для каждой подготовки используй новое физическое имя:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=index study.build_kind=whole

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=index study.build_kind=title

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=index study.build_kind=body

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=chunk-index
```

Обычный исследовательский цикл notebook: compose → входная ячейка `text`,
`variants`, `filter_values`, `reference_variant` → preview → compare → summary,
rank lists и matrix → выбрать id для полной card → изменить вход и повторить.
Начальный пример выбирает F2/F3/F4/E1/E2; затем доступны любые зарегистрированные
F1–F5/E1–E5. Все фильтры exact AND; слова запроса не создают hard filters.
`study.fulltext_description_field` задаёт точный raw key полного описания
OpenSearch, независимо от порядка scored fields (default `description`).
`opensearch_source_path` объявляет путь значения в исходной карточке отдельно от
indexed `opensearch_field`; dense raw path задаёт `qdrant_path`.

Если новый compare падает, `current` очищается: старый HTML подписан прежним
run_id, новый частичный результат прочитать или сохранить нельзя. Успешный пустой
результат отображается как 0 результатов. `SAVE_CURRENT=False` требует явного
включения; save пишет `outputs/search-study/<run_id>/search-study.json` без нового
search и без перезаписи. Advanced audit/build/native embedding/batch выключены
по умолчанию. Изменение весов/depth/policy требует compose заново; изменение
recipe/model/corpus требует fresh generations, даже если имя preset прежнее.

Для CLI выбери один registered variant; EOF сохраняет schema=1 search-study JSON.
Batch принимает прежний CSV `id,text`, но публикует отдельный study artifact с
полными карточками и provenance, не меняя старые export formats:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=interactive 'study.registered=[f2]'

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=batch \
  experiment.input_path=/srv/recsys/queries.csv
```

Каждый вариант имеет механизм-off control и positive/negative contrast в
[study cases](../configs/study/cases/scenarios.yaml), дополнительные boundaries —
[boundaries.yaml](../configs/study/cases/boundaries.yaml). Вымышленные 80 карточек в
`tests/fixtures/study/vacancies.json` проверяют контент, а не качество рынка.
Сверхдлинный `s31-positive` намеренно превышает E5 max64 и нужен для отказа audit;
этот pack целиком нельзя объявлять готовым E5 cohort без проверки.

Cross-family matrix допускается только с owner evidence внешнего OpenSearch cohort
и совпадающим `cohort_sha256` dense generations. По умолчанию источники помечены
несопоставимыми, матрицы разделены. Label BM25 требует `study.similarity_evidence`;
без подтверждения доступен честный `native_lexical`. Методика, формулы и живые
критерии G01–G09 принадлежат [spec 009](../docs/specs/009-search-experiment-matrix.md),
фактически исполненные проверки — [development](../docs/development.md).
