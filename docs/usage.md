# Поиск с внешними сервисами

Для запуска без сервисов используй [локальный пример](examples/offline-ranking.md).
Здесь нужны свои OpenSearch endpoint, индекс и mapping; для векторного поиска —
также TEI, Qdrant и корпус. Команды выполняются из корня checkout.

## OpenSearch

Установи Python 3.12+ и Poetry 2.2+, затем укажи адрес API, индекс и text-поля:

```sh
poetry install
poetry run python -m src.run \
  connection.url=https://search.example.org/opensearch \
  search.index=vacancies \
  'search.fields=[title,description]' \
  search.size=10
```

URL включает префикс прокси. Клиент добавляет `/{index}/_search`, проверяет TLS
и не следует redirect. Поддерживается только search; индекс нужно подготовить отдельно.
`connection.timeout=30` задаёт timeout в секундах, повторных запросов после ошибки нет.
Поля поддерживают boosts: `'search.fields=[title^2,description]'`.

Для авторизации передай полный заголовок через `OPENSEARCH_AUTHORIZATION`:
`Bearer …` или `Basic …`. Скрытый ввод в Bash:

```bash
read -r -s -p 'Authorization header: ' OPENSEARCH_AUTHORIZATION
export OPENSEARCH_AUTHORIZATION
printf '\n'
```

Без переменной заголовок не передаётся. Секреты в CLI overrides сохраняются Hydra;
используй окружение. `--cfg job --resolve` также раскрывает значения секретов.

В `search>` вводи запросы по одному. Пустая строка пропускается. EOF сохраняет
`outputs/<дата>/<run>/searches.json`; `hydra.run.dir=/path/to/new-run` меняет каталог.
Ошибка завершает CLI с кодом 1, Ctrl-C — 130, без итогового артефакта.
Существующие файлы не перезаписываются.

### Статические условия

[main.yaml](../configs/main.yaml) выбирает
[интерактивный эксперимент](../configs/experiments/vacancy-search/interactive/001-basic.yaml).
В его конфиге можно задать context и AND-фильтры:

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

Поле и тип значения должны соответствовать индексу. UserPropertyFilter берёт
значение из context. Неизвестный фильтр или отсутствующее свойство — ошибка
до HTTP. QueryFilter создаётся из текста вызова, в статических filters он не нужен.

## Jobboard: lexical-поиск

Профиль добавляет RU/EN parser, aliases, phrase, веса полей, fuzzy и RRF:

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/jobboard/lexical@experiment=001-interactive' \
  connection.url=https://search.example.org/opensearch \
  jobboard.source.index=vacancies \
  'jobboard.source.fields=[title,description]' \
  jobboard.source.title_field=title
```

Настраивается `jobboard.source`; блок `search` используется только базовым
экспериментом. Для единственного text-поля оставь `title_field=null`.
Жёсткие фильтры требуют объявления типов, например
`'+jobboard.source.term_fields={city:keyword,remote:boolean}'`.
Распознанная в запросе «Москва» сама по себе фильтр не создаёт.

Настройки [профиля](../configs/jobboard/lexical/profile.yaml):

| Настройка | Выбор |
| --- | --- |
| `experiment.pipeline` | `baseline`, `lexical`, `parsed` (default), `fused` |
| `jobboard.candidate_depth` / `jobboard.final_k` | Глубина ветки / финальная выдача; defaults 100 / 20 |
| `jobboard/lexical/parsing@jobboard.parser` | `rules`, `roles_skills`, `no_entities` |
| `jobboard/lexical/retrieval@jobboard.retrieval.parsed` | `parsed`, `no_aliases`, `no_topical_gate`, `strict_coverage` |
| `jobboard/lexical/retrieval@jobboard.retrieval.lexical` | `lexical`, `no_phrase` |
| `jobboard/lexical/graph@jobboard.registry` | `lexical`, `fuzzy` |

Для fuzzy выбери graph `fuzzy` и `experiment.pipeline=fuzzy`. Fused выполняет два
search и объединяет полную глубину RRF до final_k; остальные pipelines делают один search.

`jobboard.retrieval.parsed.title_field_weight` и `default_field_weight` задают
веса title и остальных полей, `field_weights` — overrides объявленных полей.
Аналогичные параметры есть у lexical и fuzzy. `no_aliases` отключает в том числе
подстановку canonical; контрольный lexical сохраняет собственный no-entities parser.

[RU/EN словарь](../configs/jobboard/lexical/lexicons/ru-en.yaml) разбит на категории.
Короткие неоднозначные обозначения имеют контекстные правила; C++/C#/.NET
сохраняются при нормализации. Неподдержанные связки и отрицания отключают expansion:
«не продажи» остаётся текстом, а не гарантированным исключением профессии.

### Сравнение и разметка

Выбери `002-comparison` вместо `001-interactive`. Default cases — вымышленные,
с ids из test fixture. Для своего индекса создай Hydra cases group по образцу
[cases/synthetic.yaml](../configs/jobboard/lexical/cases/synthetic.yaml) и выбери
`jobboard/lexical/cases@jobboard.cases=<имя>`. Для смены pipelines удали прежний mapping:

```text
'~experiment.pipelines'
'+experiment.pipelines={lexical:lexical,parsed:parsed,fused:fused}'
```

Каждый найденный id должен иметь judgment. Неизвестная оценка — ошибка, не ноль.
Разметь общий pool вариантов и отдели оценочные запросы от тех, на которых меняешь
веса. Фиксируй версию индекса, словаря и cases: состояние внешнего индекса меняется
независимо от сохранённой Hydra-конфигурации. Клиент не меняет analyzers или BM25 k1/b.

Адресные тесты используют локальный HTTP-сервер с заданными ответами:

```sh
poetry run pytest tests/test_jobboard_parser.py tests/test_jobboard_search.py \
  tests/test_jobboard_content.py tests/test_jobboard_workflow.py
```

Механика parser/compiler: [spec 004](specs/004-end-to-end-search.md),
состав учебных данных: [004A](specs/004-jobboard-content.md).

## Исследование в ноутбуке

Открой [search.ipynb](../src/experiments/notebooks/search.ipynb) в Jupyter или VS Code
с kernel окружения проекта. Jupyter устанавливается отдельно. В ячейке параметров
укажи URL, поля и pipeline; Authorization берётся из окружения kernel.

После compose/instantiate можно вызвать поиск напрямую:

```python
serp = experiment.search("python backend developer")
rows = [
    {"id": item.id, "score": item.score, "fields": item.fields} for item in serp.items
]
```

`search()` возвращает независимую SERP без печати и записи. Context вызова
переопределяет верхнеуровневые свойства, filters добавляются к статическим.
Для смены параметров повтори сборку: ранее созданный объект не меняется вместе с cfg.
`build_query(data)` показывает DSL одного листа без HTTP; у fused два листа.

При самостоятельной сборке используй `initialize_config_dir` как context manager,
`compose` и `instantiate(..., _convert_="all")`. Отключи `experiment.exporters=[]`
или задай путь явно: Compose API не создаёт `${hydra:runtime.output_dir}`.

## CSV batch

Вход — UTF-8 CSV с ровно двумя колонками `id,text`, в любом порядке, с BOM или без.
Id уникальны, id/text непустые; пробелы по краям удаляются. Поддерживается стандартное
quoting. Весь файл проверяется до первого HTTP. Пример: [queries.csv](../examples/queries.csv).

Jobboard batch:

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

Для RRF выбери `experiment.search.pipeline=fused`. Статические условия находятся
в `experiment.search.context` и `experiment.search.filters`.

Базовый multi_match без parser:

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

Результат — `searches.csv`: `query_id,query,rank,document_id,score,fields`.
Строка соответствует одному документу, rank начинается с 1, fields содержит полный
JSON. Для пустой выдачи сохраняется строка с пустыми полями результата; null score
даёт пустую ячейку. Порядок запросов и SERP сохраняется. Ошибка отменяет экспорт
всего batch. Результаты хранятся в памяти; streaming/resume нет.

Из Python `batch.run()` возвращает артефакт, `to_rows()` — таблицу, `to_dict()` —
структурированный результат. Контракт: [spec 005](specs/005-search-research.md).

## Поиск рабочих профессий

[blue-collar.ipynb](../src/experiments/notebooks/blue-collar.ipynb) сравнивает пять
вариантов на русскоязычных запросах. [CSV](../examples/blue-collar/queries.csv)
содержит примеры ролей, графиков, условий и ошибок ввода.

| Вариант | Механизм |
| --- | --- |
| `c1` | Веса полей и phrase |
| `c2` | Ограниченные бонусы свежести и качества, rescore |
| `c3` | Строгий приоритет категорий графика |
| `c4` | Понятия, field routing, квалификация и мягкие предпочтения |
| `c5` | C4 с исправлением опечаток, раскладки и curated forms |

C1/C4/C5 работают без ranking metadata:

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

`now_ms` фиксируется на сессию; значение в примере — 2026-09-29 00:00 UTC.
Для batch замени `001-compare` на `002-batch` и при необходимости задай
`experiment.input_path`. Default CSV — `examples/blue-collar/queries.csv`.
На запрос приходится один search на выбранный вариант.

`research.json` содержит original text, request body, parse/rewrite trace, timing,
полные hits и sort keys. В notebook `preview()` не делает HTTP, `compare()`
возвращает артефакт, `experiment.searches['c4'].search(text)` — обычную SERP.
Этот SearchExperiment можно передать BatchSearchExperiment для прежнего CSV-формата.

C2/C3 требуют объявленных single-valued полей с doc values:

```text
'+jobboard.source.ranking_fields={published_at:{field:published_at,type:date},quality:{field:card_completeness,type:numeric},category:{field:schedule_category,type:keyword},stable_id:{field:vacancy_id,type:keyword}}'
'experiment.variants=[c1,c2,c3,c4,c5]'
```

`card_completeness` — пример numeric signal. `freshness-only` требует только
published_at, `quality-only` — quality. Для C3 нужны category и уникальный
непустой stable_id. Категории day/night/shift_2_2/other из fixture замени значениями
своего индекса. Приоритет группы сильнее score: слабый дневной результат может
обогнать сильный ночной.

Диагностика читает `_source`, не doc values. Точный dotted key имеет приоритет
над вложенным path, даже при null. Массивы и `.keyword` автоматически не обходятся.
Числовые даты — Unix milliseconds; также поддерживается ISO с timezone.
Точные правила: [spec 007](specs/007-research-evidence-corrections.md).

В [research profile](../configs/jobboard/lexical/research/profile.yaml) можно менять:

```text
jobboard.query.title_field_weight=8
jobboard.query.title_phrase_boost=4
+jobboard.query.concept_coverage=1
'+jobboard.query.field_weights_by_kind={role:{title:4,description:1},skill:{title:2,description:3}}'
jobboard/lexical/ranking@jobboard.rankings.signals=freshness-only
jobboard/lexical/ranking@jobboard.rankings.tiers=night-first
jobboard/lexical/research/ablation@_global_=no-aliases
```

Ranking presets: relevance, balanced, freshness-only, quality-only, phrase-rescore,
phrase-only; tiers: day-first, night-first, shift-2-2-first. Relevance отключает
соответствующий сигнал. Default C4/C5 — slots-default/rewrite-default.
Абляции находятся в [research/ablation](../configs/jobboard/lexical/research/ablation):
одновременно выбирается одна group, отдельные параметры переопределяются независимо.

Painless scripts лежат в [ranking/](../configs/jobboard/lexical/ranking/).
`field_params` связывает имена полей, `script.params` — числа, `now_param` — время.
В phrase-rescore `_score` относится к вложенному phrase query; window_size действует
на каждом shard, custom sort несовместим с rescore. Сервер должен разрешать нужный
script context и expensive queries; клиент эти настройки не меняет.

Словари находятся в [blue-collar/ru/](../configs/jobboard/lexical/lexicons/blue-collar/ru/):
forms распознают текст, aliases расширяют поиск, ambiguous/requires задают контекст.
Surface-prefix защищает категории прав от предлогов. Циклические rewrites и цели
вне словаря отклоняются. Optional skills/qualifications требуют source declaration.
Контракты: [006](specs/006-query-and-painless-candidates.md) и
[006A](specs/006-blue-collar-content.md).

## Векторный поиск

[vector.yaml](../configs/vector.yaml) использует pinned multilingual-e5-base:
768 dimensions, mean pooling/L2, prefixes `query: ` / `passage: `, 512 tokens.
TEI 1.9.4 работает на одной GPU, Qdrant 1.19.2 запускается отдельно.

### Машина и site config

Нужны Docker, NVIDIA Container Toolkit, доступный Qdrant и место для image/model cache.
Проверь `df -h /` и характеристики GPU:

```sh
nvidia-smi -L
nvidia-smi --query-gpu=name,uuid,memory.total,driver_version --format=csv
```

Выбери совместимый image с digest по [матрице](specs/008-vector-search.md#142-image-pin-и-hardware-evidence).
Подготовь writable cache и свободный loopback port. Сохрани общий site config
вне checkout, например `/srv/recsys/config/vector-site.yaml`:

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

Замени placeholders и пути. Launcher, index, search, batch и notebook должны читать
один site config. `TEI_API_KEY` и `QDRANT_API_KEY` передаются окружением.
Проверка композиции без запуска сервисов:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site --cfg job
```

### Корпус и запуск

Корпус — непустой UTF-8 JSON-массив из `{id, fields}`:

```json
[
  {"id": "vacancy-1", "fields": {"title": "Сварщик", "description": "Работа на заводе", "city": "Тула"}}
]
```

Id уникальны и сохраняются точно, включая пробелы непустой строки. По умолчанию
recipe соединяет optional title/description; отсутствующие/null/пустые поля
пропускаются. Пустой общий текст и нестроковые значения отклоняются.
`corpus.text.fields` и `separator` меняют рецепт. Длинные документы усекаются справа
до 512 tokens вместе с prefix и special tokens; сначала оцени усечение token audit
в [vector notebook](../src/experiments/notebooks/vector.ipynb). Длинный query отклоняется.

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

Index создаёт новое поколение, проверяет exact count, полные payloads/vectors
и filtered smoke, затем публикует ready. Receipt — `vector-index.json` в каталоге
запуска. Существующее имя не перезаписывается. Новый corpus, recipe, profile или
runtime image требует нового имени. До ready ошибочная коллекция недоступна;
сбой локального receipt export после ready не откатывает удалённое состояние.

Launcher принимает только новое имя container; restart/stop выполняется отдельно.
Для подтверждения GPU нужны CUDA logs и process/VRAM выбранного UUID через nvidia-smi.
В notebook build включается через `build_collection`; preview не вызывает TEI/Qdrant.

Поиск использует `search>` и сохраняет searches.json при EOF.
`retrieval.candidate_depth`, `final_k`, `exact`, `hnsw_ef` задают поиск;
`indexed_only=false`. Фильтры требуют `qdrant.filter_fields` до build:
[path/type declarations](specs/008-vector-search.md#122-declared-scalar-filters).
Текст сам по себе жёстких фильтров не создаёт.

Batch использует тот же site YAML и CSV `id,text`, результат — searches.csv:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name vector-site \
  experiments/vacancy-search/vector@experiment=003-batch \
  experiment.input_path=/srv/recsys/queries.csv
```

## Сравнение десяти вариантов

[search-study.ipynb](../src/experiments/notebooks/search-study.ipynb) показывает
выдачи рядом, матрицу рангов относительно reference, полные карточки и E5 passages.
Политики F1–F5/E1–E5 описаны в [архитектуре](architecture.md#сравнение-поисковых-политик).

Скопируй [site.example.yaml](../examples/study/site.example.yaml) в свой site YAML.
Заполни подключения, index/fields, scalar bindings, corpus identity/path, TEI image
и физические имена whole/title/body/chunk generations. Notebook frontend использует
окружение проекта. Задай `SITE_CONFIG` или `RECSYS_STUDY_SITE`; overrides — список
Hydra strings либо JSON array в `RECSYS_STUDY_OVERRIDES`. Секреты остаются в окружении.

У всех документов должны быть непустые title и description. `StudyCohortReader.audit()`
показывает неверные ids, `read()` отклоняет весь невалидный cohort. Для E1–E3 нужна
whole generation, E4 — title/body, E5 — chunk. Каждая подготовка требует нового имени:

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

В notebook выполни compose, затем меняй `text`, `variants`, `filter_values`,
`reference_variant`. Preview показывает план, compare делает поиск, summary/rank lists/
matrix/card читают сохранённый snapshot без новых запросов. Reference меняет отображение,
не ranking. Фильтры — exact AND; false является значением, не отключением фильтра.

`study.fulltext_description_field` задаёт raw key описания. `opensearch_source_path`
отдельно объявляет raw path карточки, `opensearch_field` — индексированное поле;
у dense raw path задаёт qdrant_path. Порядок scored fields не выбирает поле описания.

Новый compare сбрасывает current. При ошибке нельзя прочитать или сохранить старый
результат как новый. `SAVE_CURRENT=False`; после включения save пишет
`outputs/search-study/<run_id>/search-study.json` без нового поиска и overwrite.
Audit/build/native embedding/batch по умолчанию выключены. Смена query/filters/subset
не требует rebuild, параметры требуют compose заново, recipe/model/corpus — новых поколений.

CLI interactive выбирает один вариант; EOF сохраняет study JSON schema=1.
Batch принимает id,text и сохраняет отдельный study artifact:

```sh
poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=interactive 'study.registered=[f2]'

poetry run python -m src.run \
  --config-dir /srv/recsys/config --config-name study-site \
  experiments/vacancy-search/study@experiment=batch \
  experiment.input_path=/srv/recsys/queries.csv
```

Общая fulltext/dense matrix требует совпадения cohort SHA-256 и подтверждения
владельца OpenSearch. Иначе семейства показываются отдельно. BM25 label требует
`study.similarity_evidence`, без него используется `native_lexical`.
[Test cases](../configs/study/cases/scenarios.yaml) содержат controls и contrasts;
сверхдлинный s31-positive намеренно превышает max64 E5 и проверяет отказ audit.
Fixture-корпус целиком не подходит для E5 build без предварительной проверки.

HTTP fixtures проверяют запросы и обработку ответов. Реальные analyzers, Painless,
GPU, Qdrant ranking и Jupyter UI проверяются отдельно. Результаты и незакрытые
проверки — в [development](development.md), методика сравнения — в
[spec 009](specs/009-search-experiment-matrix.md).
