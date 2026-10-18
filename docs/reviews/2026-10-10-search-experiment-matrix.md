# Предметное ревью матрицы поисковых экспериментов 009

Дата: **2026-10-10**. Объект: проект [spec 009](../specs/009-search-experiment-matrix.md),
до реализации. Запрос пользователя — проверить фактурность с помощью субагентов:
нужны разные механизмы BM25, обработки мягких условий и построения выдачи,
а не изменение количества полей. Этот отчёт фиксирует найденные слабости и
исправленную исследовательскую матрицу; он не подтверждает качество поиска.

Три субагента независимо читали проект спецификации и текущие owner contracts:

| Субагент | Предмет проверки |
| --- | --- |
| `fulltext_substance_review` | Самостоятельность fulltext mechanisms, мягкие условия, BM25 composition и controls |
| `embedding_substance_review` | Dense admission/representation/aggregation, ablations и содержательные contrasts |
| `factual_contract_review` | Соответствие текущему коду, первичным источникам, фильтрам и выполнимость acceptance |

После переработки первые два проверили новые механизмы повторно. Их уточнения
внесены в контракты function_score, disjoint tiers, safe span mapping и fixed-pool
exact scoring. Финальная повторная проверка `factual_contract_review` не нашла
остаточных противоречий механизмов и acceptance, но выявила необходимость
отдельного подтверждения BM25 similarity; это уточнение также внесено.
Технические источники и граница проверок — в
[research note](../research/2026-10-10-search-experiment-matrix.md).

## Найденные проблемы и решения

**[P1] Основные fulltext slots слабо исследовали разные ranking policies.**
Fuzzy и conservative rewrite занимали два из пяти мест, хотя на обычном
корректном запросе часто совпадали с literal control. Concept вариант пакетировал
aliases, coverage и soft bonus, а fusion повторно учитывал literal путь внутри
другой ветки. Обязательного исследования topic/preference trade-off не было.

Исправлено: F1 — field-centric BM25; F2 — обязательный role/qualification core
с независимым multi-field coverage; F3 — ограниченный preference multiplier;
F4 — приоритет групп all/partial/none; F5 — union/RRF независимых F1/F2.
Fuzzy/rewrite остаются controls. No-alias comparison с одинаковыми field weights
отделяет coverage от словарного расширения. Это вариации композиции BM25 и выдачи,
а не пять k1/b presets: similarity индекса не управляется search-only proxy.

**[P1] Приёмка допускала десять labels без десяти механизмов.**
Presets, корректный renderer и успешные HTTP calls не доказывали самостоятельность
опытов. Не требовалось показать изменение admission или порядка на общем case.

Исправлено: L19 требует для каждого основного механизма ближайший control,
различающий two-vacancy case и negative/abstention case. G08 требует observed
active-path admission/order evidence на реальном поиске. Arithmetic oracles
подтверждают формулу, HTTP fixtures — отправленный план; они не исполняют BM25,
embedding или HNSW. Улучшение качества не предписано: отрицательный результат
гипотезы остаётся исследовательским результатом.

**[P1/P2] Dense matrix чрезмерно опиралась на изменение одного renderer.**
Title-only и structured-card использовали тот же single-vector retrieval;
structured-card мог остаться labels-only при отсутствии metadata и всё равно
считаться самостоятельным основным опытом. Topic и soft preferences не разделялись.

Исправлено: E1 — whole-query single-vector; E2 — topic admission и bounded
preference rerank фиксированного пула; E3 — original/topic multi-query union/RRF;
E4 — independent title/body representations и late fusion; E5 — passage retrieval
и vacancy max. Title-only/structured recipes оставлены абляциями. E2 может только
переставить topical pool, E3 может допустить новые ids; E4 меняет представления
документов, E5 — единицу retrieval и способ document aggregation.

**[P2] Контрпример fuzzy ошибочно называл изменённой первую букву.**
В «водитель» → «вадитель» изменена вторая буква; prefix_length=1 не блокирует
этот случай. Исправлено на «бодитель» как first-letter negative, с оговоркой
о реальном анализаторе.

**[P2] Chunk concentration acceptance противоречила recipe cap.**
Одна вакансия не может занимать >100 chunk hits при cap=64. Исправлено:
три вакансии с ≤64 chunks каждая занимают первые 100 hits, после чего points
loop обязан добрать остальные уникальные вакансии.

**[P2] Max aggregation не доказывает отсутствие length bias.**
Max предотвращает прямой sum/count bonus, но дополнительные chunks дают больше
попыток получить высокий score. Добавлен contrast «один relevant passage
+ 0/5/20 unrelated chunks». Полезный passage в хвосте и opportunity bias
исследуются отдельно.

**[P2, повторное ревью] Multi_match не гарантирует BM25 similarity.**
Внешний индекс может использовать custom similarity. Добавлены source qualification
и G02 evidence effective BM25 участвующих scored fields, версия движка и доступные
параметры. При неизвестной similarity ручные результаты маркируются native lexical,
не используются как подтверждение BM25. Внешний индекс не меняется.

## Контракты, уточнённые повторной проверкой

- F2 — новая opt-in compiler policy. Текущий C4 с literal bypass не обеспечивает
  заявленную обязательность role groups и не выдается за готовый F2.
- F3 сохраняет F2 eligibility, использует baseline weight=1, filtered weights,
  sum/multiply и max_boost=1+α. Нет min_score или дополнительного overall boost.
- F4 использует одинаковые с F3 evidence predicates, filter context и явный
  MSM=1 для any. При нуле пожеланий один поиск; при одном partial tier пуст.
  Cross-tier повторный id означает source inconsistency, а не тихий dedup.
- Text preference evidence может совпасть с отрицанием в документе. Карточка
  «кандидатов без опыта не рассматриваем» обязательна как негативный контроль.
- E2/E3 удаляют soft spans из semantic-normalized S только при безопасной
  однозначной связи с lexical-normalized offsets; квалификации и residual остаются.
  Conflicting/overlapping spans ведут к abstention, validation/network failures — к ошибке.
- E2 использует v1 point UUIDs и native has_id рядом с исходными hard filters,
  exact=true и limit=pool size. Нового payload index или rebuild E1 не требуется.
  Неполный набор ids, duplicates или изменившиеся fields отменяют весь результат.
  Пустой pool не запускает preference inference/query.
- F5/E3 не дают второй голос одинаковому prepared input/plan при abstention.
- E5 TEI offsets — UTF-8 bytes. Native Qdrant grouping имеет внутренние лимиты;
  explicit exact points loop с exclusion document ids задаёт полный vacancy-max
  contract с оговоркой о ties. ANN проверяется отдельно.
- Ручной query/filter/search/read сценарий сохранён. Явные hard equality filters
  участвуют в каждом native retrieval; soft query не создаёт скрытый hard filter.

## Граница результата

Основная матрица теперь содержит пять fulltext и пять dense experiments с разными
правилами допуска, scoring, representation или построения выдачи. Повторное
предметное ревью подтвердило различия механизмов и указанные крайние случаи.

Изменены только документы. Runtime implementation, target-engine BM25 behavior,
модельные scores, numerical parity, relevance и performance этой работой
не подтверждены. Реализация и локальные/live критерии L/G остаются будущей работой.

При первичном ревью проверены 83 локальные Markdown targets в пяти изменённых docs,
пять anchors, синтаксис двух YAML/Python примеров, paired fences, whitespace и
`git diff --check`. Runtime tests и сервисы не запускались.

## Последующее уточнение рабочего интерфейса

После предметного ревью пользователь согласовал формат проработанного notebook
со сравнением. Раздел 15 spec дополнен первым запуском и настройкой site config,
единым входом query/variants/filters, двумя видами сравнения, полными карточками,
snapshot labels, правилами повторного поиска/пересборки и явным сохранением.
Проработанная сессия topic/preferences входит в приёмку N01–N09 и live G09.

Это уточнение не меняет десять поисковых механизмов. Переотображение artifact
не делает HTTP/inference, preview не делает encode, и Run All по умолчанию
не запускает writes/batch/corpus audit. Неудача нового compare не выдаёт старый
snapshot за новый результат. Уточнение проверено основным агентом по текущим
contracts 005/008; отдельное субагентное ревью этой части не проводилось.
После изменения повторно проверены 84 локальных targets и шесть anchors в пяти
документах, синтаксис трёх примеров spec, fences/whitespace и `git diff --check`.

## Независимое ревью реализации

Объект второго этапа — рабочая реализация относительно
`38af3ab513aad4d1d5326e2dfa25be8aba161eca` на `codex/search-study-009`.
При проверке authored files ещё не были закоммичены: просмотрены tracked diff и
все новые study modules/configs/tests, а не только `git diff` без untracked files.
Spec source — 009, стандарты — AGENTS.md и architecture/development; issue tracker
для этой задачи не нужен, исходный контракт находится в репозитории.
Применён двухосевой workflow skill `code-review`; субагенты исключали собственные
реализации из независимой проверки.

| Проверяющий | Независимая область |
| --- | --- |
| `implement_chunks_009` | Standards/ownership: root orchestration, composition, bindings, artifact, fulltext и notebook |
| `implement_dense_009` | Spec: чужие fulltext, E5 chunks/source/build, root orchestration/artifact/configs и контент |
| `implement_fulltext_009` | Дополнительная Spec/Standards проверка чужих E1–E4, representations/cohort и notebook/session |

### Standards

Обнаружены и исправлены пять существенных нарушений границ и provenance:

- **P1:** duplicate `exporters` делал interactive YAML неразбираемым OmegaConf.
- **P2:** F1 компилировал неиспользуемый strict core и отклонял допустимый literal
  query из 17 residual words. Compiler теперь вызывается только активной policy.
- **P2:** карточка читала hardcoded city/active и игнорировала nested source bindings.
  Для `_source` введён explicit `opensearch_source_path`, для dense используются
  declared paths; missing, null и необъявленный путь различаются.
- **P2:** CLI объединял разные comparisons под id/timing первого. Сессия теперь имеет
  один run_id, отдельные `comparison_run_id` и суммарный search timing.
- **P2:** static UserPropertyFilter разрешался в constructor до effective per-call
  context. Constructor сохраняет symbolic property, `_inputs` разрешает её до HTTP.

Повторная проверка актуального кода не обнаружила открытых существенных Standards
замечаний. Smell baseline рассмотрен как эвристика; дополнительные abstractions
или refactor без доказанной потребности не предлагались.

### Spec

Независимая проверка выявила четыре diagnostics/interface gaps:

- **P2:** ошибка выбранного leaf не называла variant/stage. Preview/admission/search
  добавляют contextual exception note, сохраняя исходную ошибку и traceback.
- **P2:** E5 не сохранял фактический iteration body и идентичность общего query
  vector. Теперь сохранены body без значений vector, hash/profile/dimensions,
  exact excluded ids и prepared model input; tests сопоставляют записи с HTTP.
- **P2:** явно неверный reference при render молча заменялся первым вариантом.
  Такой input отклоняется; implicit family reference для subset остаётся явным.
- **P2:** E3/E4 branch provenance не включала RRF contributions и явные native
  dimension/profile. Эти значения добавлены вместе с focused regression evidence.

Отдельно при интеграции исправлены common-cohort admission до encode и полное
восстановление E5 winner passage: title cutoff persistируется в UTF-8 bytes,
fragment schema=2/recipe discriminator запрещают переиспользование старой формы;
reconstruction/hash проверяются без HTTP. Numeric oracles, explicit native plans,
fixed UUID pool, disjoint tiers, atomic failure и fresh-generation boundaries
проверены отдельно от качества результатов.

Локальные HTTP fixtures не исполняют analyzer/BM25, реальный tokenizer, GPU encoding
или Qdrant exact/HNSW. Незакрытые G01–G09 и пользовательский Jupyter frontend
остаются задачей целевой машины. Итоговые gate counts и status принадлежат
[development](../development.md).


Последующая source-display проверка выявила **P2**: первый non-title scored field
ошибочно считался description. Для `[title, company, body]` карточка показывала
company вместо полного body. Теперь description — explicit raw key; разрешено
поле вне scored fields. Добавлены custom composed card regression и независимое
повторное ревью, замечаний нет. Это исправление не меняет retrieval/ranking.
