# Лексический поиск вакансий через существующий OpenSearch

Записка сохраняет исследовательский контекст 2026-09-26. Последовавшая
[spec 004](../specs/004-end-to-end-search.md) реализована; новый русский профиль
и ranking/QU определены в [spec 006](../specs/006-query-and-painless-candidates.md).
Текущая готовность и свидетельства — в [development.md](../development.md).

Дата обращения к источникам: **2026-09-26**. Статус: исследовательская записка,
а не утверждённая спецификация или свидетельство качества на реальном индексе.
Использованы официальные документы OpenSearch, исходный код OpenSearch 2.19.0
и Javadoc Lucene 10.3.1. Документы OpenSearch по `/latest/` изменяются со временем;
версия, настройки и анализаторы целевого кластера пока неизвестны. Наличие функции
в документации не подтверждает её доступность через конкретный прокси.

## Вывод для проекта

На существующем полнотекстовом индексе можно построить содержательный jobboard-поиск
без embeddings, переиндексации и административного доступа: взвешенный поиск
по полям, отдельные phrase boosts, ограниченное расширение запросов, контролируемая
опечаточная ветка, независимые поисковые ветки и клиентский RRF. Эти механизмы
доступны через search DSL или уже реализованы в проекте. Их сочетание — гипотеза
качества, которую нужно сравнивать с текущим `multi_match` на одинаковых запросах,
а не объявлять улучшением по одному усложнённому JSON. [S2–S8, S14]

Главные ограничения:

- Индекс уже определил токены, similarity и доступные поля. Query DSL не возвращает
  различия, уничтоженные при анализе исходных документов. [S1, S9–S11]
- `cross_fields` не является универсальным улучшением `best_fields`: у него есть
  ограничения анализаторов, boosts и совместимости с fuzziness. [S2]
- Синонимы в приложении и настроенный на сервере `synonym_graph` — разные механизмы.
  Нельзя передать определение нового analyzer/token filter обычным search body. [S10–S12]
- Rescore меняет порядок ограниченных кандидатов на каждом shard, а не находит
  документы, пропущенные первым retrieval. [S13]
- Серверный RRF требует отдельной серверной возможности и конфигурации; для
  текущих прав естественнее существующий клиентский RRF. [S14]

Рекомендация этой записки: строить **конкретный эксперимент лексического поиска
вакансий**, переиспользуя текущие pipeline/branches/RRF. Не вводить универсальный
язык запросов, новый backend или общую платформу ranking ради одного сценария.

## 1. Что действительно можно настраивать без изменения индекса

| Механизм | При наличии только search | Условие/ограничение |
| --- | --- | --- |
| Fields, boosts, `best_fields`, `tie_breaker`, operator/MSM | Да | Поля и их анализ должны подходить задаче. [S2] |
| `bool`, `dis_max`, `match_phrase`, slop | Да | Текст анализируется сервером; phrase работает по позициям токенов. [S3–S5] |
| Fuzzy matching | Да, если разрешён кластером | Ограниченные expansions; некоторые query types не поддерживают fuzziness. [S2, S6–S8] |
| Query-time analyzer | Можно выбрать доступный analyzer | Это не создание нового analyzer и не переиндексация исходных токенов. [S10] |
| Application query expansion | Да, сформировав явные clauses | Собственный словарь и правила, отличный от `synonym_graph` контракт. [S11–S12] |
| Query rescore | Да, если поддержан целевой версией/прокси | Ограниченное окно на shard; несовместим с произвольной сортировкой. [S13] |
| Date/numeric `function_score` | Да | Нужны реально существующие поля соответствующего типа и политика missing. [S15] |
| BM25 `k1`, `b`, index analyzer, новые subfields | Нет как параметр обычного search | Это index/mapping settings, выходящие за данную границу. [S1, S9–S10] |
| Клиентский RRF | Да | Несколько разрешённых searches; неизменные фильтры во всех ветках. |
| Серверный `score-ranker-processor` | Не предполагать | Нужны поддержка версии и доступная search pipeline. [S14] |
| `dfs_query_then_fetch` | Возможен как search request option | Это query parameter API; текущий клиент не предоставляет такой параметр. [S16] |

Последние два локальных утверждения проверены по
[`OpenSearchIntegration`](../../src/integrations/opensearch/client.py) и
[`ReciprocalRankFusionTransformer`](../../src/core/transformers/aggregation/rrf.py).
Индекс/поля в примерах ниже обозначают роли, а не найденный mapping целевого источника.

## 2. BM25: как понимать boosts и длинные описания

BM25 — стандартная similarity OpenSearch. Документация указывает defaults
`k1=1.2`, `b=0.75`, `discount_overlaps=true`. `k1` управляет насыщением вклада
частоты терма, `b` — нормализацией по длине. Lucene вычисляет среднюю длину из
`sumTotalTermFreq / docCount` в статистике поля. Поэтому анализировать следует
длину конкретного поля, а не общую длину всех полей документа. [S1, S9]

При прочих равных length normalization уменьшает преимущество длинного текста,
а saturation ограничивает выгоду повторения одного слова. Из этого не следует,
что длинные descriptions автоматически «исправлены»: там может быть много
разнообразных случайных совпадений, а распределения термов и анализаторы полей
различаются. **Рекомендация:** явно отделить короткие поля роли/заголовка от
описания и экспериментировать с их boosts; не выравнивать влияние полей одним
произвольным порогом `_score`. [S1–S2, S9]

`k1` и `b` не являются knobs `multi_match`. OpenSearch относит их к similarity
settings; изменение default similarity требует операций с индексом, а не поиска.
Нельзя обещать их настройку при search-only доступе. Аналогично неизвестно,
использует ли реальный индекс defaults вообще. [S1]

## 3. Multi-match: какие варианты для каких полей

| Type | Фактическая семантика | Вывод для вакансий |
| --- | --- | --- |
| `best_fields` | Создаёт поиск по полям; берёт лучший field score. При `tie_breaker=t` добавляет `t` от scores остальных совпавших полей. Это default type. [S2] | Подходящая начальная гипотеза для title/description с разными ролями и boosts. |
| `most_fields` | Объединяет scores совпавших полей; документирован прежде всего для нескольких по-разному проанализированных представлений одного текста. [S2] | Не считать универсальным усилением recall: простой суммарный score может награждать дублирование содержания. |
| `cross_fields` | Term-centric поиск, смешивающий статистику по полям с одним analyzer; разные analyzers образуют группы. [S2] | Полезен, когда части одного понятия намеренно распределены по совместимым коротким полям. Нельзя без проверки подменить им weighted title + long description. |

В `best_fields` и `most_fields` `operator` и `minimum_should_match` применяются
**к каждому полю**, а не ко всему документу. Запрос из нескольких слов с `AND`
может не найти вакансию, в которой одно слово находится в title, другое только
в description. Это ожидаемая семантика, а не баг OpenSearch. [S2]

Для `cross_fields` официальная документация отдельно предупреждает: meaningful
blending обычно получается на коротких строковых полях с boost 1; boosts, term
frequencies и length normalization в остальных случаях осложняют интерпретацию.
Fuzziness не поддерживается. При разных analyzer groups снова возникают проблемы
field/group-centric operator/MSM. Явный `analyzer` может создать одну группу,
но способен рассогласовать search tokens с index tokens. [S2, S10]

**Рекомендации для первого сравнения:**

1. Сохранить текущий basic retrieval как baseline.
2. Проверить weighted `best_fields`, явно задав fields, boosts, operator/MSM
   и `tie_breaker`; повышенный boost title — гипотеза, а не установленная константа.
3. Отдельной вариацией проверять `cross_fields` только для известной группы
   совместимых коротких полей. Не смешивать эту вариацию с fuzzy поиском.
4. Избегать wildcard fields: состав mapping иначе незаметно меняет запрос и число
   clauses. OpenSearch ограничивает число bool clauses настройкой кластера;
   публикация новой схемы fields не должна неявно расширять поиск. [S2]

## 4. Eligibility, phrase boosts и альтернативы

Критическая особенность `bool`: default `minimum_should_match` равен 0, если
есть `must` или `filter`, и 1 в противном случае. Поэтому композиция
`filter=[город]`, `should=[текстовый поиск]` без явного MSM может вернуть все
документы города, даже не совпавшие с текстом. [S3]

**Рекомендуемый инвариант:** группы, определяющие допустимость кандидата,
имеют явный MSM; optional boosts отделены от eligibility. Например:

```text
bool
  filter: обязательные статические и пользовательские условия
  must:
    bool
      should: [исходный lexical query, разрешённые alternative queries]
      minimum_should_match: 1
  should: [phrase boost по title, другие явно optional сигналы]
  minimum_should_match: 0
```

Это схема смысла, не новый формат конфигурации. Внутренний MSM по словам
`multi_match` и внешний MSM по alternative clauses — разные величины. `operator=AND`
и MSM нужно выбирать осознанно; случайное добавление нового `filter` не должно
менять обязательность текста.

`match_phrase` ищет последовательность проанализированных термов; `slop`
разрешает смещения позиций. Phrase boost по title может выделить вакансии
«data engineer» относительно случайного присутствия двух слов. Это **boost**, а не
универсальное обязательное ограничение: обязательная фраза может потерять
допустимые перестановки/вставки. Multi-match `phrase`/`phrase_prefix` не поддерживает
fuzziness. Цитирование строки не превращает анализируемый text в keyword. [S2, S4]

`dis_max` берёт лучший score вложенных запросов и, при ненулевом `tie_breaker`,
часть остальных scores. **Рекомендация:** использовать его для альтернативных
форм одного смысла, если не нужно награждать документ за совпадение сразу со всеми
вариантами. `bool.should`, напротив, подходит для сознательно накопительных
сигналов. Выбор зависит от смысла, а не от желания добавить ещё один слой DSL. [S3, S5]

## 5. Fuzziness и защищённые названия технологий

При **включённом** `fuzziness=AUTO` стандартные пороги `AUTO:3,6` означают:
0–2 символа — 0 edits; 3–5 — до 1 edit; от 6 — до 2 edits.
`prefix_length` фиксирует начальные символы; default 0. `max_expansions`
ограничивает число expansions; default 50. Большие expansions особенно дороги
при нулевом prefix. Lucene FuzzyQuery поддерживает максимум 2 edits и имеет
дополнительное ограничение scaled distance для очень коротких слов. [S6–S8]

Не путать это с «любой match по умолчанию исправляет опечатки». В проверенном
исходном коде OpenSearch 2.19.0 `MatchQueryBuilder.fuzziness` и
`MatchQuery.fuzziness` изначально `null`; fuzzy query создаётся только при ненулевом
значении. Формулировка таблицы текущей документации о default AUTO недостаточно
ясно отделяет default стратегии от включения fuzzy matching. Для эксперимента
намерение фиксируется явно: strict clause без fuzziness, fuzzy clause с ним. [S17]

Термовый `fuzzy` query и analyzed `match` с fuzziness не тождественны: первый
ищет похожие индексные термы, второй сначала анализирует пользовательский текст.
Документация предупреждает, что fuzzy queries не исполняются при
`search.allow_expensive_queries=false`; доступность нужно проверить на целевом
кластере, не обходить молчаливым расширением поиска. [S6–S7]

**Рекомендация для jobboard:** fuzzy — отдельный ограниченный сигнал/ветка,
а не `AUTO` на всех полях и каждом слове. Задать budget expansions и кандидатов,
разрешённые поля, ограничения длины токенов и явные защищённые технологии.
Проверить, не вытесняют ли fuzzy результаты точные совпадения.

Особая группа: `C++`, `C#`, `.NET`, `Go` и короткие аббревиатуры.
Standard analyzer использует Unicode segmentation и lowercasing; punctuation
не гарантирует отдельной сохранённой семантики. Конкретные tokens этих строк
зависят от analyzer/версии, поэтому без целевого evidence нельзя утверждать,
как именно они представлены в индексе. [S10–S11]

Для них рекомендуется:

- сохранять исходную строку запроса и punctuation; не превращать `C++`/`C#` в `C`
  собственной универсальной очисткой;
- распознавать защищённые названия до общей нормализации/expansion и исключать
  их из общей опечаточной обработки;
- не считать `Go` с нулём допустимых edits семантически однозначным: отсутствие
  fuzzy edits не отличает язык программирования от омонима;
- добавлять только явные проверяемые alternative forms, например доменный alias,
  с собственным весом; не объявлять их встроенными синонимами сервера;
- включить отдельные regression cases на `C`, `C++`, `C#`, `.NET`, `Go`, чтобы
  увидеть ложные слияния понятий.

Если индекс уже потерял различие и нет другого подходящего поля, query-time
analyzer не может восстановить его. При search-only доступе это фиксируется как
предел источника; требование абсолютного различения тогда не принимается как
выполненное. Право вызвать `_analyze` также не подразумевается: возможны
контролируемые search probes и предоставленный владельцем индекса mapping.

## 6. Synonym graph и расширение в приложении

`synonym_graph` — token filter, поддерживающий одно- и многословные синонимы
и их графовую структуру. Для многословных замен Lucene рекомендует query-time
применение: indexer напрямую не потребляет такой граф, а flattening при индексации
теряет часть позиционной информации. OpenSearch `match` по multi-term synonyms
может автоматически строить phrase queries через
`auto_generate_synonyms_phrase_query` (default true). [S6, S12]

Это не даёт search-only приложению права определить новый серверный synonym
filter. Можно выбрать **уже доступный** analyzer. Его совместимость с индексными
tokens и наличие правил должны быть известны. Обход через произвольный
`analyzer` в каждом запросе не равен управлению анализом индекса. [S10, S12]

**Практическая альтернатива для этого проекта:** небольшая Hydra-конфигурация
application aliases/expansions, которую эксперимент компилирует в ограниченные
alternative clauses. Например, альтернативная форма многословной роли выражается
отдельной phrase/match clause, а не конкатенацией всех синонимов в исходную строку.
Нужны направление замен, защищённые названия, максимальное число альтернатив,
отдельные веса и сохранение исходного варианта. Это рекомендация, а не гарантия
эквивалентности `synonym_graph`.

Оригинальный `QueryFilter` в проекте immutable/append-only. Expansion не должен
удалять или заменять его либо добавлять несколько `QueryFilter` в существующий
transformer, требующий ровно один. Выбор представления производных clauses
принадлежит конкретному эксперименту; вводить общую новую модель payload только
ради этого исследования не требуется.

## 7. Rescore: точность на ограниченном окне

Query rescore выполняется после initial retrieval **на каждом shard**,
перед финальным объединением результатов. `window_size` — число документов
на shard, default 10. `query_weight` и `rescore_query_weight` по умолчанию 1;
`score_mode=total` суммирует взвешенные scores. Допустимы также другие способы
комбинации, но их нужно выбрать явно по смыслу. [S13]

Ограничения: несовместим с явной сортировкой, кроме `_score desc`; при пагинации
window_size должен оставаться постоянным. При hybrid query rescore применяется
к subqueries на shard до их итогового объединения, поэтому его нельзя считать
rerank итоговой клиентской RRF выдачи. [S13]

**Рекомендация:** сначала доказать пользу обычного phrase boost. Если он дорог,
проверить phrase rescore с явным достаточно широким окном. Размер окна — часть
эксперимента; `size=k` итогового ответа не заменяет его. Rescore не улучшает recall
за пределами initial candidates и не должен маскировать узкий первый retrieval.

## 8. Свежесть и числовые признаки: необязательная отдельная вариация

`function_score` поддерживает `field_value_factor`, weights и decay functions
`gauss`/`exp`/`linear` для numeric/date/geo fields. `score_mode` комбинирует функции,
`boost_mode` комбинирует их результат с исходным query score; default обоих —
`multiply`. `boost_mode=replace` отбрасывает lexical score. [S15]

Семантика missing различается: у `field_value_factor` есть параметр `missing`,
а decay для отсутствующего поля возвращает 1. Для date origin default — `now`;
numeric/geo origin обязателен. У decay есть offset, scale и decay; multi-valued
fields требуют осмысленного `multi_value_mode`. Это может неожиданно давать
вакансии без даты преимущество перед старой вакансией. [S15]

**Рекомендация:** не делать свежесть/популярность обязательным baseline, пока не
известны типы и семантика полей. При введении вариации зафиксировать field,
missing policy, origin, offset/scale, score/boost modes и влияние на relevance.
Для сравнимого эксперимента фиксировать timestamp origin вместо меняющегося
`now`. Не добавлять script_score/случайный score без отдельной необходимости.
Текстовая eligibility остаётся обязательной внутри function_score.

## 9. RRF: клиентский механизм соответствует имеющимся правам

RRF суммирует `1 / (rank_constant + rank)` по спискам, поэтому ему не требуется
одинаковая шкала BM25 scores разных retrieval strategies. Серверный OpenSearch
`score-ranker-processor` появился в 2.19, работает с hybrid query между query/fetch
фазами и требует search pipeline; документация показывает её создание через
`PUT /_search/pipeline/...`. Документированный default rank_constant — 60. [S14]

Из этого не следует, что существующий прокси предоставляет создание pipeline
или поддерживает hybrid/RRF. В проекте уже есть клиентский RRF; его разумно
использовать для независимых lexical веток без нового серверного контракта.
Rank constant остаётся явным параметром исследования, а не «правильной» константой.

**Рекомендуемые ограничения:** общий index и смысл document id, одинаковые
обязательные filters, детерминированный порядок веток и tie breaks, отдельные
candidate budgets на ветку и final top-k. Две почти одинаковые ветки не являются
двумя независимыми доказательствами релевантности: лишнее дублирование даёт
документу дополнительный вклад RRF. Сравнивать fusion с каждой сильной одиночной
веткой и отключать ветки без доказанной пользы. Эти ограничения — проектные
рекомендации, а не требования OpenSearch API.

## 10. Shards, наблюдаемость и DFS

Search API использует `query_then_fetch` по умолчанию: scores опираются на локальную
статистику shard. `dfs_query_then_fetch` сначала собирает глобальные term/document
frequencies, обычно увеличивая стоимость запроса. Это опция search API,
а не гарантия качества и не замена хорошей query construction. [S16]

Текущая интеграция принимает только index/body и запрещает query в base URL;
следовательно, `search_type=dfs_query_then_fetch` нельзя «просто вписать в URL».
Если сравнение требует DFS, нужен отдельный узкий явный request option с проверкой
поддержки прокси. Это необязательная диагностическая вариация, не блокер первого
лексического решения.

Ответ search содержит `took`, `timed_out`, `_shards`, hits; `took` — серверное время,
а не весь клиентский elapsed time. API также предоставляет `explain` и named
query matches; эти возможности полезны для отдельных диагностических запросов,
но не обязательны на каждом интерактивном вводе. [S16]

**Рекомендация для экспериментальных evidence:** знать branch, итоговый search
body без credentials, candidate count, client elapsed, server took, failures,
index/profile и parameters. Не выдавать локально измеренную длительность за
серверную, не терять timed_out/shard failure и не сравнивать результаты разных
индексных состояний как детерминированный алгоритмический эффект.

## 11. Минимальная программа проверки гипотез

Это рекомендации для будущей spec, а не выполненные измерения:

1. **Профиль источника:** известные fields, index/alias, версия/доступные функции,
   условия auth/TLS, full `_source`, смысл document id, ограничения размеров.
   Использовать подтверждённый mapping и доступные операции API.
2. **Baseline:** нынешний multi_match с фиксированными fields/size/filters.
3. **Один усиленный retrieval:** weighted fields + explicit eligibility/MSM +
   optional phrase boost. Проверить слова, разделённые между полями, длинный
   description, точные роли, пустой/stopword query и фильтрацию.
4. **Раздельные ablations:** bounded expansion, затем fuzzy branch, затем RRF.
   Не включать сразу все механизмы: иначе неизвестно, что улучшило/ухудшило выдачу.
5. **Jobboard cases:** точное название роли, skill+role, русская/английская форма,
   multiword alias, typo, короткие/защищённые технологии, ложные друзья/омонимы,
   отсутствие релевантных результатов, hard filters. Наличие кейсов не означает
   автоматической релевантности выбранных результатов.
6. **Evidence качества:** одинаковые запросы и source profile; сравнение выдач,
   latency и явная разметка. NDCG допускается только с определённой политикой
   покрытия judgments; незнакомый id не становится автоматически grade 0.
   Качественный порог и приемлемая latency согласуются с владельцем задачи.
7. **Evidence корректности:** actual HTTP request bodies в локальном composed
   test; одинаковые filters во всех ветках; budgets; protected tokens; explicit
   MSM; нет пропуска ошибок и частичного success artifact. Отдельный live smoke
   подтверждает фактический index, анализ и возможности; fixtures не заменяют его.

Rescore, date/numeric boost и DFS стоит добавлять после появления проверяемого
основания. Код раскладывается по существующим владельцам
`src`: domain, components разбора, core transformers/DSL, integrations и orchestration
в experiments. Конкретная карта и обязательный контент находятся в
[spec 004](../specs/004-end-to-end-search.md) и её приложении; принадлежность одному
эксперименту не означает хранения всей реализации в каталоге experiments.
Это сохраняет каркас без универсального framework поверх DSL.

## Источники

Все ссылки проверены чтением 2026-09-26. Никакой целевой OpenSearch endpoint,
пользовательский dataset или credentials при исследовании не использовались.

- **S1:** [OpenSearch — Similarity](https://docs.opensearch.org/latest/im-plugin/similarity/): defaults BM25, index/field settings, изменение similarity.
- **S2:** [OpenSearch — Multi-match](https://docs.opensearch.org/latest/query-dsl/full-text/multi-match/): types, boosts, tie_breaker, analyzer groups, MSM/operator, ограничения fuzziness.
- **S3:** [OpenSearch — Boolean query](https://docs.opensearch.org/latest/query-dsl/compound/bool/): must/filter/should и default minimum_should_match.
- **S4:** [OpenSearch — Match phrase](https://docs.opensearch.org/latest/query-dsl/full-text/match-phrase/): analysis, positions и slop.
- **S5:** [OpenSearch — Disjunction max](https://docs.opensearch.org/latest/query-dsl/compound/disjunction-max/): best score и tie_breaker.
- **S6:** [OpenSearch — Match](https://docs.opensearch.org/latest/query-dsl/full-text/match/): analysis, fuzziness, synonyms, zero_terms_query.
- **S7:** [OpenSearch — Fuzzy](https://docs.opensearch.org/latest/query-dsl/term/fuzzy/): AUTO thresholds, expansions, prefix и expensive-query restriction.
- **S8:** [Apache Lucene 10.3.1 — FuzzyQuery](https://lucene.apache.org/core/10_3_1/core/org/apache/lucene/search/FuzzyQuery.html): максимум edit distance и ограничения коротких слов.
- **S9:** [Apache Lucene 10.3.1 — BM25Similarity](https://lucene.apache.org/core/10_3_1/core/org/apache/lucene/search/similarities/BM25Similarity.html): параметры и средняя длина поля.
- **S10:** [OpenSearch — Search analyzers](https://docs.opensearch.org/latest/analyzers/search-analyzers/): порядок выбора analyzer и риск рассогласования с index-time analysis.
- **S11:** [OpenSearch — Standard analyzer](https://docs.opensearch.org/latest/analyzers/supported-analyzers/standard/): Unicode segmentation и lowercasing.
- **S12:** [OpenSearch — Synonym graph](https://docs.opensearch.org/latest/analyzers/token-filters/synonym-graph/) и [Lucene 10.3.1 — SynonymGraphFilter](https://lucene.apache.org/core/10_3_1/analysis/common/org/apache/lucene/analysis/synonym/SynonymGraphFilter.html): token graphs, multiword synonyms и query-time применение.
- **S13:** [OpenSearch — Rescore](https://docs.opensearch.org/latest/query-dsl/rescore/): per-shard window, weights, modes, sort/pagination/hybrid ограничения.
- **S14:** [OpenSearch — Score ranker processor](https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/): RRF, introduced 2.19, search pipeline и rank_constant.
- **S15:** [OpenSearch — Function score](https://docs.opensearch.org/latest/query-dsl/compound/function-score/): field_value_factor, decay, missing, score_mode и boost_mode.
- **S16:** [OpenSearch — Search API](https://docs.opensearch.org/latest/api-reference/search-apis/search/): search_type, timing/shard response, explain и named queries.
- **S17:** OpenSearch **2.19.0**, [MatchQueryBuilder.java](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/index/query/MatchQueryBuilder.java#L98) и [MatchQuery.java](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/index/search/MatchQuery.java#L602): fuzziness=null и условное создание fuzzy query.
