# Основания матрицы полнотекстового и embedding-поиска

Дата: **2026-10-10**. Исследование для [spec 009](../specs/009-search-experiment-matrix.md).
Факты источников отделены от предлагаемых гипотез. Серверы, inference, модели
и пользовательские данные не запускались и не загружались.

Проверены текущие owner contracts, код query compiler, RRF, renderer, vector
source/indexer и первичные материалы ниже. Рекомендации сверены с pinned
исходниками TEI/Qdrant.
После [предметного ревью](../reviews/2026-10-10-search-experiment-matrix.md)
матрица пересобрана: рецепты полей и обработка опечаток остались абляциями,
основные места занимают разные admission/score/SERP policies.

## Полнотекстовые механизмы

**Weighted best_fields.** OpenSearch multi_match поддерживает веса полей и
best_fields с tie_breaker. Operator/MSM применяются внутри field queries:
совпадения роли в title и оборудования в description не обязательно удовлетворят
порог одного поля. Title boost и optional phrase — управляемые гипотезы,
не гарантия релевантности. [O1]

**Ограниченный fuzzy.** Fuzzy сопоставляет индексные термы на расстоянии редактирования;
analyzed match с fuzziness сначала анализирует вход. Prefix и max_expansions
ограничивают механизм. Fuzzy queries могут быть запрещены
search.allow_expensive_queries=false. Это существенно другой механизм, чем
исправление запроса по локальному словарю. Из наличия fuzzy не следует знание
профессии, различение оборудования или сохранение квалификации. [O2]

**Concept и rewrite.** Это проектные query compilers, а не встроенные
entity understanding OpenSearch. Их исполнение опирается на bool, multi_match,
phrase и dis_max. Внешний bool.filter ограничивает все альтернативы; текстовая
eligibility остаётся обязательной. При must/filter optional should по умолчанию
не обязан совпасть. Dis_max выбирает лучший score и долю остальных по tie_breaker;
нулевой tie_breaker не суммирует score альтернатив одного смысла. [O1, O3, O4]

**Core coverage F2.** Обязательные role/qualification groups и независимый поиск
каждой группы по нескольким полям позволяют отделить покрытие темы от условий.
Это новая композиция существующего DSL, не BM25F и не готовый текущий C4:
literal bypass старого C4 не обеспечивает обязательность role group.
Контроль с теми же полями и без aliases нужен, чтобы не приписать coverage эффект
словарному расширению. k1/b принадлежат similarity индекса; search-only proxy
не даёт query-time sweep этих параметров. [O1, O3, O4]
Наличие multi_match не подтверждает BM25: источник должен отдельно подтвердить
effective similarity scored fields. При неизвестной similarity ручная выдача
остаётся native lexical, вывод о BM25 не делается.

**Bounded preferences F3.** Function_score с weight=1 для match_all и
filtered weights предпочтений, score_mode=sum, boost_mode=multiply даёт
core_score × (1+Σw×evidence). Max_boost=1+α ограничивает function multiplier,
а не итоговый _score. При Σw=α и w≥0 multiplier лежит в [1,1+α].
Functions.filter не меняет core eligibility; min_score в этом опыте запрещён.
Scalar evidence либо positive text phrase — наблюдаемое совпадение, а не
доказательство условия: «без опыта не рассматриваем» обязателен как контрпример. [O5]

**Preference tiers F4.** All/partial/none — разбиение того же core corpus
по тем же predicates. Filter context сохраняет core score; any требует явного
MSM=1, потому что при must/filter default MSM=0. Три отдельные searches и concat
сначала по tier, затем по native score исследуют иной trade-off, чем bounded bonus.
При отсутствии пожеланий выполняется только core search. [O3]

**RRF.** Формула суммы 1/(k+rank) не требует общей шкалы поисковых scores.
Статья Cormack/Clarke/Buettcher исследует rank fusion; её результаты на других
коллекциях не доказывают пользу на вакансиях. В проекте уже реализованы
последовательные ветки и domain-id RRF. Дублирование одинаковых веток меняет
вклад документов, поэтому в основной матрице нужны две содержательно разные
ветки и сравнение с каждой из них. [R1]
F5 объединяет самостоятельные F1/F2, а не literal branch с композицией,
уже содержащей ту же literal branch. Совпавший план при abstention получает один голос.

Дополнительная фактура и source references находятся в
[исследовании OpenSearch](2026-09-26-opensearch-lexical.md) и
[Query Understanding](2026-09-29-query-understanding.md).

## Пять dense-механизмов на одной модели

Проверенная card pinned multilingual-e5-base требует query:/passage: также для
неанглийского retrieval, показывает mean pooling/L2 и предел 512 tokens.
Перестановка/добавление текста меняет representation; она не эквивалентна
BM25 field boost. Нормы и шкалы similarity не являются relevance judgments. [E1]

| Вариант | Основание механизма | Исследовательская гипотеза |
| --- | --- | --- |
| E1: целая вакансия, полный query | Один passage title + description, right truncation | Простой исходный baseline |
| E2: topic-first preference rerank | Topic admission, затем exact cosine всех candidate ids для topic-anchored preference queries | Мягкие условия изменят порядок при ограниченном topic trade-off |
| E3: original/topic RRF | Два query vectors, независимые retrieval и union/RRF | Original branch восстановит полезных кандидатов за пределами topic pool |
| E4: title/body RRF | Два document representations и domain-id rank fusion | Поля сохранят самостоятельный сигнал, не смешанный в одном embedding |
| E5: chunks → vacancy max | Несколько passages одной вакансии, максимум similarity | Релевантный хвост длинной вакансии сможет участвовать в retrieval |

Это предложения, а не утверждения о превосходстве. Для независимого title/body
сравнения общий cohort должен иметь непустые title и description. Нельзя незаметно
сократить только title-only/body-only корпус или включить fallback на другое поле.

E2/E3 — предлагаемые новые query policies, а не готовые возможности E5 модели.
Удаление soft spans допускается только при безопасном соответствии semantic
текста lexical offsets; квалификации и unknown residual сохраняются. Отрицания,
ambiguity и конфликтные spans требуют abstention; input/network errors остаются
ошибками. Whole generation общая с E1, профиль модели не меняется.

Для E2 native has_id ограничивает точные point UUIDs пула, вычисленные v1
source.point_id(domain_id). Hard filters остаются соседними must conditions.
Has_id запрещён внутри nested object filter; дополнительный payload index здесь
не нужен. Exact query с limit=|pool| без score_threshold обязан вернуть весь pool;
неполный набор — ошибка. Это новый composition contract поверх Query Points. [Q2, Q3]
Utility u(c)=(c+1)/2 и Σw=α ограничивают дополнительный score [0,α]:
topic gap>α не может поменять порядок. Это арифметическая граница, не confidence
и не гарантия semantic отрицаний. Initial ANN pool с последующим exact scoring
остаётся приближённым по admission.

Title-only, field order и structured-card — полезные representation ablations.
Добавленные labels при отсутствии новых source fields не считаются основным опытом.
E3 допускает neighboring-role документы через original branch, E4 может наследовать
шум body, E5 — opportunity bias; эти негативные случаи обязательны.

## Chunking: два важных ограничения первичных источников

**Offsets TEI — UTF-8 bytes.** Pinned TEI into_tokens копирует offsets tokenizer
и извлекает text через input.bytes().skip(start).take(stop-start); special tokens
имеют отсутствующие offsets. Сlicing Python str по этим позициям неверен для
кириллицы. Chunk boundaries надо проверять по UTF-8 bytes, строго декодировать,
затем повторно tokenize полный passage с title/prefix/special tokens. Нельзя
склеивать token.text: это не восстановление исходной строки. [T1, строки 501–531]

**Native groups имеют внутренний budget.** В pinned Qdrant grouping driver
MAX_GET_GROUPS_REQUESTS=5 и MAX_GROUP_FILLING_REQUESTS=5. Поэтому даже exact
group query нельзя без отдельного доказательства считать полным top-N вакансий:
top chunks могут концентрироваться на нескольких документах. [Q1]

Для первой реализации предлагается явный цикл обычных Query Points:
один query vector, исходные filters и исключение уже найденных document_id;
до нужного числа уникальных вакансий либо пустого ответа. В exact-режиме первое
попадание вакансии даёт её максимальный chunk score с оговоркой о равных scores.
В ANN это приближённый поиск; качество его document ranking измеряется отдельно.
Достижение лимита calls без заполнения/исчерпания — ошибка, а не полный результат.
Max исключает прямое суммирование по количеству chunks, но не статистический
эффект дополнительных попыток получить высокий score. Контроль: одинаковый
relevant passage плюс 0/5/20 unrelated chunks. Для concentration case при cap=64
chunks/vacancy три вакансии могут занять первые 100 point hits; одной вакансии
с >100 chunks в этой recipe существовать не может.

Query Points поддерживает filter и search params; фильтр должен участвовать
в retrieval, а не применяться к итоговому top-k. Payload indexes и cardinality
влияют на physical plan. Сохранение всех fields у каждого chunk увеличивает
payload/storage: budget нельзя считать только по vectors. [Q2, Q3]

## Источники

- **O1:** [OpenSearch multi-match](https://docs.opensearch.org/latest/query-dsl/full-text/multi-match/).
- **O2:** [OpenSearch fuzzy](https://docs.opensearch.org/latest/query-dsl/term/fuzzy/);
  [match](https://docs.opensearch.org/latest/query-dsl/full-text/match/).
- **O3:** [OpenSearch bool](https://docs.opensearch.org/latest/query-dsl/compound/bool/).
- **O4:** [OpenSearch dis_max](https://docs.opensearch.org/latest/query-dsl/compound/disjunction-max/).
- **O5:** [OpenSearch function_score](https://docs.opensearch.org/latest/query-dsl/compound/function-score/).
- **R1:** [Cormack, Clarke, Buettcher — Reciprocal Rank Fusion, SIGIR 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).
- **E1:** [multilingual-e5-base, pinned model card](https://huggingface.co/intfloat/multilingual-e5-base/blob/d128750597153bb5987e10b1c3493a34e5a4502a/README.md).
- **T1:** [TEI pinned tokenization source](https://github.com/huggingface/text-embeddings-inference/blob/e80ef225ed0e6cb1717ce632a6a84b6cf211bb67/core/src/tokenization.rs#L501).
- **Q1:** [Qdrant v1.19.2 grouping driver](https://github.com/qdrant/qdrant/blob/v1.19.2/lib/shard/src/grouping/driver.rs#L7).
- **Q2:** [Qdrant Query Points](https://api.qdrant.tech/api-reference/search/query-points);
  pinned schema и source references — в [Qdrant contract](2026-10-10-qdrant-contract.md).
- **Q3:** [Qdrant filtering](https://qdrant.tech/documentation/concepts/filtering/).

O1/O2/O3/O4/O5, E1, T1, Q1 и Q3 прочитаны 2026-10-10 в этой работе.
R1 и Q2 используются вместе с прежними проверенными research notes.
Remote compatibility, численная parity, качество и performance не проверены.
