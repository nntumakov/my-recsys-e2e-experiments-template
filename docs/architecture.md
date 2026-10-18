# Архитектура

Эксперимент — обычный Python-сценарий. Hydra собирает его из типизированных
компонентов; registry связывает шаги по именам. Один и тот же механизм исполняет
локальное ранжирование, поиск через OpenSearch и векторный поиск через TEI/Qdrant.

## Владельцы поведения

| Слой | Ответственность | Основные модули |
| --- | --- | --- |
| Domain | Документы, запросы, фильтры, векторы и идентичность данных | `src/domain/` |
| Composition | Именованные шаги, последовательности, ветки и объединение | `src/core/registry/`, `src/core/transformers/` |
| Components | Разбор запроса, подготовка текста, метрики и представление результата | `src/components/` |
| Integrations | HTTP, внешний протокол и преобразование ответов | `src/integrations/` |
| Experiments | Вход, порядок исполнения, сравнение и результат запуска | `src/experiments/` |
| Configuration | Реализации компонентов, параметры и связи между ними | `configs/` |

Доменные термины определены в [CONTEXT](../CONTEXT.md). Команды находятся
в [руководстве](usage.md), выполненные проверки — в [development](development.md).

## Композиция через имена

`BaseRegistry[T]` задаёт lookup, `SimpleRegistry` хранит готовые экземпляры.
`BasicTransformerRegistry` специализирует его для transformers и при создании
проверяет ссылки и циклы последовательностей и веток.

Каждый transformer реализует `transform(data, *, registry) -> PipelineData`.
Составной шаг разрешает имена через переданный registry; листовой шаг получает
клиент и настройки в конструкторе. Pipeline сам является transformer, поэтому
может входить в другую последовательность или ветку.

Граф задан конфигурацией. Registry не выводит маршрут из типов и не создаёт
новый объект при каждом обращении. Hydra-интерполяция связывает параметры,
но сама по себе не гарантирует identity Python-объектов: за экземпляры отвечает
сборка эксперимента и registry.

В [локальном примере](examples/offline-ranking.md) это выглядит так:

```text
CompareExperiment
  ├─ text       → text_score → sort → top
  ├─ preference → preference_score → sort → top
  └─ fused      → branches ┬ text_score → sort       ┐
                          └ preference_score → sort ┴ RRF → top
```

Ветки RRF получают полные выдачи; ограничение top-k применяется после объединения.
[Конфиг](../configs/experiments/vacancy-ranking/local/002-vacancies.yaml) использует
те же `PipelineTransformer` и `BranchTransformer`, что и поисковые эксперименты.

## Состояние обработки

`PipelineData` содержит:

| Поле | Смысл |
| --- | --- |
| `context` | Пользовательские свойства `dict[str, JSONValue]` |
| `items` | Коллекция доменных документов |
| `filters` | Добавляемые условия обработки, включая исходный `QueryFilter` |
| `branches` | Временные результаты независимых веток |

Transformer может менять payload. Фильтры при этом неизменяемы: уже добавленное
условие нельзя удалить, заменить или изменить, в том числе его вложенные значения.
Исходный текст живёт в `QueryFilter`; клиенты, embeddings и результаты разбора
не передаются через пользовательский context.

`copy()` создаёт новые внешние контейнеры. `copy(deep=True)` копирует context,
документы и ветки через `deepcopy`, сохраняя общие неизменяемые фильтры. Добавление
условия в копии не влияет на исходный набор. Поверхностная копия не изолирует
вложенные поля документов.

`BranchTransformer` сначала разрешает все имена и готовит глубокие копии, затем
выполняет ветки последовательно. Результаты появляются в родительском `branches`
только после успеха всех веток. Повторное ветвление требует пустого workspace;
вложенная ветка должна объединить собственные результаты перед возвратом.

`ReciprocalRankFusionTransformer` объединяет документы по id и суммирует
`1 / (rank_constant + rank)` с позициями от 1. Поля берутся из первого появления,
результат содержит отдельные документы с RRF score. При равенстве сохраняется
порядок первого появления. Повтор id внутри одной ветки — ошибка. После успеха
workspace очищается; условия, добавленные внутри веток, остаются локальными.

Остальные шаги разделяют вычисление оценки, порядок и ограничение выдачи:

- `FieldScoreExtractor` проверяет всю коллекцию, затем переносит числовое поле в score.
- `ScoreSortTransformer` сортирует конечные числовые scores по убыванию со стабильными
  равенствами. Неверный score отклоняется до замены коллекции; документы и её тип сохраняются.
- `TopKTransformer` оставляет первые k документов без сортировки.

Контракты композиции и метрик: [spec 002](specs/002-evaluation-and-composition.md),
локального примера — [offline-ranking](../openspec/specs/offline-ranking/spec.md).

## Документы и внешние представления

`BaseDocument` задаёт `id`, произвольные JSON `fields` и score конкретного попадания.
`Vacancy` специализирует документ, `SERP` — упорядоченную коллекцию. Score относится
к одному поиску, а не к постоянным свойствам вакансии; порядок — часть результата.

`BaseExternalDocument` и `BaseExternalDocumentCollections` отделяют внешний формат
от доменного. Например, `VacancyOpenSearchDocument` преобразует hit в Vacancy,
а `OpenSearchResult` собирает SERP. Все исходные поля сохраняются, включая те,
которые не показаны краткой карточкой. Сырой HTTP response остаётся у интеграции.

## Поиск через OpenSearch

```text
search(text) → QueryFilter / PipelineData → transformer → OpenSearch DSL
             → integration.search → проверка hits → SERP → снимок результата
```

`OpenSearchTransformer` строит immutable Request из фильтров, компилирует DSL,
вызывает `OpenSearchIntegration` и заменяет items после полной проверки ответа.
Требуется ровно один QueryFilter; TermFilter и UserPropertyFilter соединяются AND.
Неизвестное условие или отсутствующее свойство context отклоняется до HTTP.

Интеграция использует стандартный POST `/{index}/_search` через urllib. URL
включает префикс прокси; Authorization приходит из окружения. TLS проверяется,
redirect и retries отключены. Приложение читает существующий индекс; его создание
и наполнение принадлежат владельцу источника. Контракт —
[spec 001](specs/001-interactive-search.md).

### Разбор запроса и ranking

`JobSearchTransformer` использует тот же транспорт: Request → parser → compiler →
HTTP → конвертация → замена items. `ParsedQuery` и `QueryHint` неизменяемы и живут
внутри вызова. Стадии `ModularQueryParser` читают один нормализованный текст;
словари собираются Hydra до поиска.

`SourceProfile` объявляет text-поля, title и типы разрешённых term-полей.
Удалённый mapping автоматически не инспектируется. `LexicalQueryConfig` задаёт
веса, aliases, phrase, coverage, fuzzy и бюджеты запроса. Literal и concept paths
объединяются через `dis_max` с нулевым tie breaker. Мягкий атрибут не заменяет
тематическое совпадение. Неизвестный остаток текста сохраняется, неоднозначные
сокращения не расширяются; неподдержанные связки и отрицания отключают structured
и fuzzy paths. Полные поддержанные фразы, например «без опыта», заданы словарём.
Fuzzy обрабатывает неизвестные длинные буквенные tokens, сохраняя остальные anchors.

`SlotQueryParser` добавляет график, опыт, занятость, квалификацию и условия работы.
Полная профессия подавляет вложенные role/skill, а правила surface-prefix защищают
категории прав от совпадения с предлогами. Квалификация обязательна в structured
path; literal path сохраняет собственные условия допуска. Распознанное место,
график или зарплата сами по себе не создают жёсткого фильтра.

`ConservativeRewriter` проверяет curated, layout и spelling proposals тем же parser
по полному изменённому запросу. Разные concepts хотя бы в одном span отменяют
rewrite. Не более двух замен дают одну строку без каскада. Original и rewritten
компилируются в две ветки одного search; исходный Request сохраняется.

`research_registry` собирает выбранные `ResearchSearchTransformer` с общими parser,
source, HTTP-клиентом и фиксированным временем сессии. Query и ranking — типизированные
значения; Painless scripts и params находятся в Hydra YAML. `RankingConfig` выбирает
один режим: relevance, script_score, script_sort или phrase_rescore. Custom sort
и rescore несовместимы; активные field bindings проверяются до HTTP.

Диагностика ranking читает только активные `_source` поля. Точный ключ, включая
null, имеет приоритет перед dotted path; массивы и mapping aliases не обходятся,
`.keyword` не отбрасывается. Missing/null сохраняет `missing_source_<role>`;
это не проверка doc values. Числовые даты — epoch milliseconds, без bool;
будущая дата строго больше `now_ms`. ISO с timezone сравнивается как момент времени.
Неразбираемые даты сохраняют диагностический flag и исходный JSON-тип.

`ObservedSERP` хранит request, understanding, timing и sort отдельно от полей вакансии.
C3 требует уникальные id и sort keys: конечный numeric tier, numeric score либо null,
непустой stable id. `_score` сохраняется отдельно, клиент не пересортировывает ответ.
Отсутствующий `took` даёт null; присутствующий должен быть неотрицательным int
без bool, float или null. Неверный последний hit или timing отклоняет весь ответ.

Контракты: [lexical](specs/004-end-to-end-search.md),
[контент RU/EN](specs/004-jobboard-content.md),
[ranking/QU](specs/006-query-and-painless-candidates.md),
[русский профиль](specs/006-blue-collar-content.md),
[диагностика](specs/007-research-evidence-corrections.md).

## Векторный поиск

[vector.yaml](../configs/vector.yaml) связывает профиль кодирования, TEI, рецепт
текста, Qdrant source и эксперимент. Поиск использует обычные SearchExperiment,
PipelineData и TopK; построением поколения владеет `VectorIndexExperiment`.

### Кодирование и идентичность

`EmbeddingProfile` фиксирует model/tokenizer revision, pooling, нормализацию,
prefixes и длины. Embedding связывает неизменяемый вектор с profile id.
Canonical JSON и SHA-256 из `domain/serialization` определяют identity профиля,
рецепта и источника: порядок ключей не влияет на hash, порядок полей рецепта влияет.

`JSONVacancyReader` проверяет весь непустой массив `{id, fields}`: точные непустые
id уникальны; повторные ключи, invalid Unicode, неконечные числа и неподдерживаемые
integers отклоняются до записи. `VacancyText` нормализует объявленные верхнеуровневые
поля по отдельности и соединяет separator. Исходные fields сохраняются полностью.

`TextEmbedder` применяет prefixes и query/document policy, проверяет count/byte
budgets, число возвращённых векторов, dimension, finite float32 и L2 norm.
Нормализация query сохраняет регистр, «ё», отрицания и пунктуацию; separator документа
повторно не схлопывается. Слишком длинный query отклоняется, целый документ может
усекаться справа tokenizer/runtime. Клиент не исправляет полученные векторы.

`TEIIntegration` вызывает `/info`, `/health`, `/embed` и `/tokenize`. Совместимость
`/info` с профилем проверяется до encoding; `model_sha` отражает переданный revision,
но не аттестует загруженные weights. Token audit проверяет подготовленные документы
по одному с отдельным response budget. Модель и ML runtime находятся вне Python.

### Допуск к поиску

`VectorSearchTransformer.prepare` строит план без HTTP: original query, model input,
identities, filters и retrieval parameters. Размер UTF-8 input и полного JSON body
с escaping проверяется до HTTP. `transform` проверяет коллекцию, кодирует запрос
и делает native query. Все points проверяются до замены items; embedding остаётся
локальным значением шага.

`QdrantSource` задаёт payload, детерминированные point UUID и scalar filter bindings.
Domain id и полные fields сохраняются в payload. Hydra vector schema должна
совпадать с profile dimension и Cosine. Каждый фильтр имеет объявленный path/type;
TermFilter и UserPropertyFilter соединяются AND, ошибки значения отклоняются до HTTP.

Поиск требует физическую ready collection, совместимые profile/recipe/corpus/runtime,
полный descriptor, payload indexes и один unnamed Cosine vector. Alias и building
collection не допускаются. Native scores и порядок сохраняются; TopK применяется
после получения кандидатов.

### Построение поколения

До remote writes проверяются путь receipt, весь corpus, ids, payloads, recipe,
encoding batches, версии серверов, capacity и query/document probes. Corpus digest
учитывает точные id и полные fields в стабильном порядке.

Поколение проходит `building → ready`: создание, payload indexes, encoding/upload,
exact count, полный bounded read-back payloads/vectors и filtered smoke. Публикация
заменяет descriptor и подтверждается GET. Существующее имя отклоняется; автоматических
retry, resume и delete нет. Неопределённый исход PATCH разбирается чтением remote state.

Ошибка до публикации оставляет коллекцию недоступной поиску. Ошибка локального
receipt export после remote ready не отменяет готовность. Новый corpus, recipe,
profile или runtime требует нового имени поколения. Qdrant process/storage и
восстановление после отказа принадлежат оператору.

TEI launcher запускает один pinned container на полном GPU UUID с тем же Hydra
profile/runtime. До Docker проверяется конфигурация; существующий container
не присваивается. Credentials передаются окружением. Health/info/probes используют
общий deadline; `integrations/http_response` прерывает чтение body и chunk framing,
завершая timer до возврата. DNS и initial headers сохраняют ограничения urllib/socket.
Успех HTTP probes сам по себе не подтверждает GPU execution.

Версии, payload formats и критерии проверки — в [spec 008](specs/008-vector-search.md).

## Сравнение поисковых политик

[study.yaml](../configs/study.yaml) через `study_experiment` создаёт только выбранные
варианты в обычном BasicTransformerRegistry. По умолчанию это F1–F5; неактивный
embedding-вариант не требует endpoint или поколения. Composition fingerprint
учитывает parser content и выбранные параметры, исключая credentials, URL и cache paths.

`StudySearchTransformer` добавляет pure `preview`, `describe` и remote `admit`.
`StudyExperiment` компилирует все планы до HTTP, затем допускает все выбранные
поколения до первого encode/search. Статические и per-call equality filters
соединяются AND; каждый вызов создаёт новый payload. При нескольких dense-вариантах
corpus SHA-256 должен совпадать. Для общей fulltext/dense матрицы дополнительно
нужны `comparability.comparable=true`, общий `cohort_sha256` и подтверждение владельца
OpenSearch: search-only proxy сам не аттестует состав индекса.

Bindings содержат `type`, `opensearch_field`, `qdrant_path` и optional
`opensearch_source_path` для карточки. Indexed field автоматически не становится
raw path, `.keyword` не отбрасывается; отсутствующий путь, missing value и null
различаются. `study.fulltext_description_field` объявляет raw key полного описания
независимо от порядка scored fields.

| Политика | Механизм |
| --- | --- |
| F1 | Literal weighted best-fields |
| F2 | Role/qualification core и cross-field coverage без literal bypass |
| F3 | Тот же допуск и ограниченный multiplier предпочтений |
| F4 | Непересекающиеся all/partial/none tiers с тем же core |
| F5 | RRF независимых F1/F2; одинаковый fallback получает один голос |
| E1 | Cosine исходного query |
| E2 | Фиксированный topic pool, exact scoring всех его UUID для предпочтений |
| E3 | RRF независимых original/topic запросов |
| E4 | Title/body representations одного corpus, одно кодирование query |
| E5 | Поиск по passages, score вакансии — maximum passage score |

E2 использует native `has_id`; неполные id или изменённые fields отклоняются.
Bonus равен `α × mean((cos+1)/2)`, контроль — `α × min((cos+1)/2)`, без предпочтений — 0.
Derived query отображает normalized spans на исходный текст; неоднозначное удаление
сохраняет original query с указанной причиной. Ошибки конфигурации и транспорта
не превращаются в такой fallback. BM25 label требует similarity/analyzer evidence;
без него результат помечается `native_lexical`. Scores разных политик не образуют
общей численной шкалы.

E5 использует ChunkVacancyText, ChunkQdrantSource и ChunkIndexExperiment. Payload/source
и fragment schema=2, `title-stop-bytes-v2` несовместимы с vacancy source v1. Offsets
относятся к нормализованному UTF-8; passage и prefixed model input восстанавливаются
локально, digest проверяется при admission, конвертации hits и read-back. UUIDv5
включает corpus/recipe/domain id/ordinal/model-input digest; исходные fields сохранены.
Audit проверяет coverage, overlap/progress и пределы 64-title/384-body/64-overlap/max64
chunks; encoding использует `truncate=false`. До ready проверяются все N документов
и M points. Поиск исключает уже найденные domain ids и добирает unique depth;
поздний отказ, stalled progress или повреждённый point отменяет весь вызов.
Число passages не даёт sum bonus; ANN сохраняет approximate provenance,
exact — сведения о tie boundary.

`SearchStudyArtifact` — независимый snapshot schema=1, `artifact_type=search-study`.
Он хранит поля, ranks, score types, происхождение веток, native timing, model inputs
и фактическое число HTTP calls. CLI session имеет один run_id; отдельные вводы —
comparison_run_id. Время сравнения исключает ожидание ввода, время сессии включает его.

`StudyNotebookSession` сбрасывает `current` перед compare и сохраняет только полный
успех. Rank lists, matrix, карточки и diagnostics читают snapshot без HTTP; HTML
экранируется, E5 winner passage показан отдельно. При разных источниках матрицы
разделяются. Save пишет `outputs/search-study/<run_id>/search-study.json` без overwrite.
Audit/build/batch/save выключены по умолчанию. Смена query/filters/subset не требует
rebuild; параметры требуют новой composition, document recipe/profile/corpus —
новых поколений. Полный контракт — [spec 009](specs/009-search-experiment-matrix.md).

## Сценарий, результат и сохранение

`SearchExperiment.search(text, *, context=None, filters=())` создаёт независимый
payload, объединяет static/per-call context и filters, добавляет QueryFilter
и возвращает глубокий снимок завершённой SERP. Context вызова переопределяет
верхнеуровневые свойства. Пустой текст, дополнительный QueryFilter, необъединённые
ветки и иной тип результата отклоняются. `run()` использует этот же метод
для терминального ввода; каждый ввод независим от предыдущего.

`BatchSearchExperiment` проверяет структуру всего CSV до первого поиска, затем
последовательно вызывает search. Результат сохраняет id/текст запроса, полную
выдачу и отдельную строку для пустого поиска. Ошибка прекращает batch без экспорта;
уже выполненные HTTP calls не откатываются. Формат — [spec 005](specs/005-search-research.md).

`CompareExperiment` для каждой пары case/pipeline копирует входные данные.
NDCG использует независимые judgments по id и строит IDCG по всей разметке;
нет оценки для найденного документа — ошибка. ComparisonArtifact содержит полные
выдачи, метрики и описательную статистику без автоматического выбора победителя.
`ResearchExperiment` дополнительно компилирует все query plans до первого HTTP
и возвращает отдельный ResearchArtifact schema=1 с диагностикой запроса.

`BaseExperiment.run()` возвращает BaseArtifact без записи файлов. `_wrapped_run`
вызывает exporters после успеха; BaseExporter владеет внешней записью. JSON/CSV
публикуются временным файлом и atomic hard link в том же каталоге: существующий
результат сохраняется даже при конкурентном запуске. Несколько exporters
не образуют общей транзакции. Артефакты хранят снимки, не live-view исходных объектов.

CLI сохраняет результат при EOF и выходит с кодом 0. Ошибка даёт 1, Ctrl-C — 130;
в обоих случаях итоговый артефакт не сохраняется. Пустая успешная выдача отличается
от ошибки. Credentials не включаются в диагностические сообщения; resolved Hydra
config может их раскрыть, поэтому его публикация требует удаления секретов.

Notebook использует Compose API тех же конфигов с отключёнными exporters или
явным путём. Изменённый config нужно собрать заново. `build_query`, `plan` и
`preview` строят те же планы, что исполнение, без HTTP и изменения входных данных.
Сборка через compose/instantiate не запускает поиск или построение коллекции.
Воспроизводимость локального примера задаётся фиксированными входами; результаты
внешнего поиска также зависят от состояния индекса, модели и удалённого runtime.
