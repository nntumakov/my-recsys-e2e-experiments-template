# 009. Десять поисковых экспериментов и ручное исследование с фильтрами

Статус: **локальная поставка завершена; live G01–G09 не выполнены**. Дата: **2026-10-10**.
Основание: пользователь хочет до переноса сформировать пять фактурных
полнотекстовых и пять embedding-поисков, сохранив возможность отправлять запросы,
задавать некоторые жёсткие фильтры и читать выдачу. Уточнение пользователя:
embedding-варианты различаются прежде всего механизмами и представлением текста.
Основные варианты должны различаться admission, score policy или построением
выдачи на одном corpus/query/hard-filter case; смена числа полей, labels,
одного веса или обработки только опечаток не заполняет отдельное место матрицы.

Изученный baseline: master, **38af3ab513aad4d1d5326e2dfa25be8aba161eca**,
чистое дерево до документационных изменений. Действующие контракты —
[architecture](../architecture.md), [005](005-search-research.md),
[006](006-query-and-painless-candidates.md), [006A](006-blue-collar-content.md),
[007](007-research-evidence-corrections.md) и [008](008-vector-search.md).
Фактическая готовность принадлежит [development](../development.md).
Первичные основания новой матрицы —
[research note](../research/2026-10-10-search-experiment-matrix.md).
Предметное ревью и проверка новой матрицы —
[review 009](../reviews/2026-10-10-search-experiment-matrix.md).

## 1. Наблюдаемый результат

Исследователь выбирает один или несколько из десяти именованных вариантов,
вводит русский запрос о вакансии, задаёт явные точные ограничения и получает
упорядоченную выдачу полных вакансий. Он может повторять запросы, читать отдельные
карточки, сравнивать позиции и видеть, что именно ушло в поиск.
Judgments, метрики и полный batch не нужны для первого ручного исследования.

Основное рабочее место — один проработанный Jupyter notebook со сравнением.
Обычный цикл: настроить подключения один раз → выбрать варианты/ввести query и
hard filters → получить выдачи и общую таблицу позиций → открыть карточку →
изменить вход и повторить. Просмотр результатов не запускает поиск повторно.
Подготовка vector generations выполняется отдельно от ежедневного исследования.

Ровно пять основных fulltext-вариантов и пять dense-вариантов перечислены ниже.
Абляции не считаются дополнительными основными вариантами.
Все dense-варианты используют текущую pinned E5-base: проверяем влияние
представления и retrieval, не смешивая его со сменой модели.

### Основная матрица

| ID | Название | Что меняется | Основной контроль |
| --- | --- | --- | --- |
| F1 | Field-centric BM25 | Весь query и лучшее поле | Исходный literal baseline |
| F2 | Core coverage BM25 | Обязательная тема, покрытие между полями; soft не scored | F1, no-alias control |
| F3 | Bounded preference bonus | Тот же core + ограниченные bonuses по мягким predicates | F2, α=0 |
| F4 | Query-driven preference tiers | Все/часть/нет пожеланий: строгий порядок при том же core | F2 и F3 |
| F5 | Lexical/core RRF | Independent F1/F2 candidate union и rank fusion | Каждая ветка отдельно |
| E1 | Whole-query single-vector | Полный query по целой вакансии | Текущий dense baseline |
| E2 | Topic-first preference rerank | Тематический пул, затем ограниченный semantic soft score | E1, α=0 |
| E3 | Original/topic RRF | Два query vectors, union/rank fusion вместо rerank пула | E1 и E2(α=0) |
| E4 | Title/body RRF | Один query, отдельные document representations и rank fusion | E1, каждая ветка отдельно |
| E5 | Passage max | Несколько фрагментов, максимум score на вакансию | E1 на длинных документах |

### Граница поставки

Входят reusable Hydra presets, manual notebook, выбор одного варианта в CLI,
одиночный поиск и сравнение выбранных вариантов, batch на том же пути, явные
scalar equality filters, диагностический артефакт и подготовка vector generations.
Должны быть готовы содержательные query/contrast packs, а не только десять labels.

Не входят hybrid OpenSearch + Qdrant, новые embedding-модели, sparse/ColBERT,
cross-encoder, LLM rewrite/summary, новые runtime services/dependencies,
автоматическое извлечение условий из документов, диапазоны зарплаты,
автоматические жёсткие filters из текста, online updates и resume/retries.
Пять C1–C5 из 006 сохраняются; C2/C3 и phrase-rescore остаются дополнительными
ранее поставленными исследованиями вне основной матрицы 009.

## 2. Общие условия честного сравнения

Начальные значения: candidate depth **100**, final top-k **20**, evaluation k **10**.
Для fusion depth относится к каждой ветке до RRF; для F4 — к каждой tier query,
для E5 — к уникальным вакансиям, для E2 — к initial topical pool.
Финальное ограничение применяется после объединения/агрегации.
Размеры являются начальными гипотезами, не измеренным оптимумом.

Fulltext: один source index, объявленные поля и текущий blue-collar/ru.
Dense: одна pinned multilingual-e5-base revision, TEI 1.9.4, Qdrant 1.19.2,
768 dimensions, mean/L2, FP16 inference, float32 Cosine storage,
query:/passage:, предел 512 model tokens. Правила query normalization из 008
сохраняют регистр, ё, числа, пунктуацию и отрицания.

Новые исследовательские dense presets по умолчанию **exact=true**,
indexed_only=false. Действующий default vector.yaml остаётся прежним.
ANN — отдельная явная серия тех же вариантов с exact=false и hnsw_ef=128;
изменение exact/HNSW не создаёт шестого embedding-варианта.

### Общий cohort и состояние источников

Для основной матрицы требуется один явно выделенный comparison corpus с
непустыми строковыми title и description после semantic normalization.
Это необходимо для независимых title/body представлений E4. Snapshot содержит точные
domain ids и полные исходные fields; F1–F5 должны искать по тем же ids/документам
для количественного сопоставления с E1–E5.

Если исходный корпус шире, пользователь предоставляет/согласует единый cohort.
Audit показывает количество и ids записей без title/description до builds.
Приложение не исключает их молча, не дополняет title описанием и не сокращает
корпус только для отдельных вариантов. Подготовка данных не входит в search.
Выводы по cohort явно ограничены его покрытием.

Через существующий search-only proxy приложение не создаёт, не замораживает и
не выгружает автоматически OpenSearch index. Для сопоставимого исследования
владелец источника предоставляет согласованные snapshot/cohort и подтверждение
состояния индекса. Domain id должен совпадать с id JSON-корпуса.
Нельзя предполагать, что поле vacancy_id всегда равно OpenSearch _id.
Новый adapter id допустим только как явное source binding с проверкой уникальности.

Без такого контура ручные поиски обоих семейств разрешены, но в отчёте сравнение
между семействами помечается **несопоставимые источники**; superiority/дельта
метрики им не приписывается. Для внутри-семейства fulltext фиксируются дата,
идентичность/версия индекса и порядок выполнения; configured index не называется digest.

### Подтверждение BM25 источника

Multi_match использует effective similarity участвующих полей, которая может
отличаться от BM25. Для BM25-серии владелец источника подтверждает BM25 для
всех scored text fields, analyzer bindings и версию движка; доступные k1/b/
discount_overlaps фиксируются, недоступные параметры помечаются unknown.
Подтверждение — mapping/settings evidence либо явная декларация владельца
с указанным происхождением; приложение через search-only proxy их не запрашивает
и не меняет индекс. Конфигурационная декларация сама не доказывает remote mapping.

Ручное знакомство с неизвестной similarity разрешено: diagnostics помечают
similarity_status=unknown и native lexical score. Оно не подтверждает выводы
о BM25 и не закрывает соответствующую часть G02/G08. Это статус evidence
существующей fulltext policy, а не дополнительный поисковый вариант.

## 3. Жёсткие фильтры

Фильтры — самостоятельный явный вход исследователя. Например:
запрос «комплектовщик ТСД» вместе с city=Москва и active=true.
«Москва» внутри query не равнозначна city filter.
«Не вахта» остаётся текстом, пока исследователь не задаст поддержанное точное
значение реального поля графика. Условия в выдаче не являются гарантией
юридического соответствия квалификации.

### Набор и source bindings

Минимальный демонстрационный профиль поддерживает **city:keyword** и
**active:boolean**. Рекомендуемые дополнительные поля — schedule:keyword и
employment:keyword, только при наличии соответствующих scalar source fields.
Для целевого источника unavailable поле явно недоступно; оно не пропускается.

Новая study-конфигурация задаёт логическое имя, общий scalar type, физическое
поле OpenSearch и путь Qdrant. Логические имена живут на границе study input;
существующие SourceProfile и QdrantSource продолжают компилировать свои bindings.
Например:

~~~yaml
# Содержимое study.filters в site config.
filters:
  city:
    type: keyword
    opensearch_field: city.keyword
    opensearch_source_path: [city]
    qdrant_path: [city]
  active:
    type: boolean
    opensearch_field: active
    opensearch_source_path: [active]
    qdrant_path: [active]
  schedule:
    type: keyword
    opensearch_field: schedule_category
    opensearch_source_path: [schedule_category]
    qdrant_path: [schedule_category]
~~~

Значения сравниваются точно, без implicit trim/casefold/перевода категорий:
Москва, moscow и код города — разные значения. Study profile обязан явно
согласовать значения двух источников до сравнения; скрытая нормализация запрещена.
Scalar types: keyword, boolean, integer, float. Boolean не считается integer,
числа конечны; сохраняются действующие precision/range ограничения Qdrant.
Null, массивы, OR, NOT и range не входят в пользовательский equality API.
Binding adapter явно переводит study boolean в Qdrant bool и соответствующий
OpenSearch boolean; имена физических scalar types не угадываются.

### Семантика

- Статические filters и filters одного вызова соединяются AND; значения
  не заменяют друг друга. Противоречивые условия могут дать успешную пустую выдачу.
- UserPropertyFilter разрешается из эффективного context до поиска;
  отсутствующее свойство — ошибка. Context не становится вторым query input.
- Для F1–F3 hard ограничения сохраняются во внешнем core bool.filter,
  для F4 — в каждом tier search, для F5 — в обеих ветках.
- Для E1–E5 filter.must применяется внутри Qdrant retrieval, включая каждый
  field/chunk search. Post-filter уже выбранного top-k не допускается.
- Все chunks одной вакансии несут одинаковые исходные filter fields.
  Никакой fragment text не становится источником значения города/графика.
- Unknown filter, wrong type, неподдержанная capability или неверный mapping
  локальной декларации отклоняются до inference/search. Для compare все выбранные
  варианты preflight-ятся до первого внешнего вызова.
- Поле без значения в источнике не удовлетворяет equality. Это не повод искать
  шире; полный _source сам по себе не доказывает наличие doc values.

Текущий immutable QueryFilter и append-only filters не меняются. Study boundary
сохраняет логические исходные условия и создаёт отдельные физические условия для
каждого источника; underlying SearchExperiment получает новый независимый payload.
Внутренние E2 pool restrictions и E5 exclusions принадлежат native query plan
и не добавляются в user filters.

## 4. F1 — field-centric BM25

**Гипотеза.** Лучшее поле с повышенным весом title достаточно хорошо отделяет
точную профессию от случайных упоминаний в description. Весь запрос, включая
мягкие пожелания, участвует в одном буквальном BM25-поиске.

Mandatory multi_match best_fields: title^4, description^1, tie_breaker=0.1,
MSM=2<75%; optional title phrase boost=2, slop=0. Aliases, fuzzy и rewrite
выключены. Ранжирование — native relevance; soft predicates отдельно не применяются.
Это field-centric query composition над similarity реального индекса.

**Различающий case F1/F2.** Query «комплектовщик ТСД»:
A — title «Комплектовщик», description «Сбор заказов с ТСД»;
B — title «Комплектовщик ТСД», description «Сбор заказов».
При двух обязательных analyzed terms F1 может не допустить A: MSM применяется
внутри field query. F2 допускает role из title и skill из description.
Реальное доказательство требует целевого analyzer, не заранее заданных fixture ranks.

**Soft contrast.** Для «комплектовщик ТСД без опыта 2/2» точный профессиональный
документ без этих слов может проиграть более общему тексту, насыщенному условиями.
F1 служит контролем такой связи тематического и нетематического BM25 score.

**Абляции.** Title weights 1/4/8; phrase off/2; MSM default/100%.
Это controls внутри одного режима, не дополнительные основные эксперименты.

**Наблюдать.** Candidate membership, field evidence, topic precision,
потерю cross-field совпадений, влияние редких слов условий на score.

### Какие BM25-вариации действительно исследуются

F1 — field-centric best_fields; F2 — concept/group-centric композиция того же BM25;
F3/F4 меняют отношение тематического score к предпочтениям; F5 меняет fusion ranks.
Это **не** пять наборов k1/b и **не BM25F**. k1/b и similarity принадлежат индексу:
search-only proxy не позволяет их менять. Most_fields и cross_fields допустимы
как дополнительные source-qualified controls: sum field evidence или blended
term statistics. Cross_fields требует совместимых analyzer groups и осторожности
с boosts/длинными полями; через query нельзя восстановить потерянный analyzer смысл.
Если понадобятся настоящие k1/b sweeps, владелец индекса должен предоставить
отдельные сопоставимые индексы; это отдельный scope, не скрытый параметр 009.

## 5. F2 — core coverage BM25

**Гипотеза.** Обязательное профессиональное ядро с multi-field группами позволит
найти роль и навык в разных полях, отделяя тематический смысл от пожеланий.

Новый opt-in compiler использует parser 006, но **не равен текущему C4**:
нет literal bypass и нет мягких bonuses. Если распознаны роли, каждая role group
обязательна; квалификации также обязательны. Skills и неизвестный residual образуют
отдельные coverage groups: все при n≤2, ceil(0.75*n) при n>2.
Когда ролей нет, требуется хотя бы одна skill/residual group; пустой core не создаёт
match_all. Мягкие однозначные experience/schedule/employment/work_condition/location
исключены из тематических eligibility и score. Неоднозначный текст остаётся residual.

Внутри каждой группы literal и curated equivalents объединяются dis_max,
alias weight=0.7, максимум четыре формы. Multiword equivalents ищутся phrase;
группы независимо ищут по title^4/description^1, skill — title^2/description^1.
Конкретные bindings и budgets сохраняются. Неизвестный residual не удаляется,
квалификация не ослабляется округлением coverage; topic — textual evidence,
не юридическая гарантия профессии/допуска.

**Различающий case.** «комплектовщик ТСД без опыта 2/2»:
A — точная роль/ТСД, но опыт и график не указаны;
B — менее сильное совпадение роли/ТСД, есть «без опыта, 2/2».
F2 допускает обе и не награждает B за условия. F3/F4 меняют порядок того же core.
Документ соседней профессии с нужными условиями не обходит обязательную role group
через literal original, как мог бы сделать прежний C4.

**Guard policy.** Unsupported negation/логика или отсутствие пригодного core
вызывает declared abstention до F1(original). Это не silently compiled core.
Для F3/F4 используются тот же abstention и причина; различающие cases обязаны
включать активный core path. Неопознанные числа/зарплата остаются residual.
Текст «без опыта» допустим как whitelist, «не вахта» не превращается в вахту.

**Абляции.** No aliases, coverage=100%, shared fields. В F1/F2 comparison
обязательно показать literal-only equivalent groups с одинаковыми field weights:
так отделяется cross-field coverage от эффекта aliases.

**Наблюдать.** Role/qualification eligibility, group coverage, recovered candidates,
дополнительные false positives и отсутствие soft contribution.

## 6. Общая политика мягких условий F3/F4 и dense decomposition

Мягкий атрибут — явное распознанное пожелание в query, не внешний hard filter.
Допустимые категории: experience, schedule, employment, work_condition, location.
Условия показываются отдельно от role/skill/qualification и unknown residual.
Не более четырёх однозначных preferences; превышение — declared abstention
до базового original path, без случайного выбора первых четырёх.

Для F3/F4 каждый preference имеет один и тот же boolean **evidence predicate**:
сначала declared scalar field + явная concept/value map, если они есть;
иначе curated positive phrase по объявленным text fields.
Missing scalar даёт false; неизвестный code не считается совпадением.
Необъявленный обязательный binding — config error до HTTP.
Evidence origin сохраняется как scalar_value или positive_text_phrase.

Text phrase не понимает произвольное отрицание документа:
«кандидатов без опыта не рассматриваем» может содержать «без опыта».
Это существенный негативный case, а не подтверждение удовлетворённого условия.
Приложение не объявляет evidence юридической/semantic гарантией.
В одной серии F3/F4 должны использовать одинаковые evidence bindings;
нельзя сравнивать точную scalar проверку с текстовой как один ranking effect.

Совместимые пожелания оцениваются отдельно; известные конфликтные exact scalar
значения одного single-valued поля помечаются conflicting_preferences, и policy
воздерживается. «Дневные смены» и «2/2» не объявляются конфликтом автоматически.
Неподдержанные отрицания, ambiguity и неизвестные constraints сохраняются в query,
не становятся positive preference. Без preferences F3/F4 совпадают с F2.

## 7. F3 — bounded preference bonus

**Гипотеза.** Предпочтения могут немного компенсировать слабее выраженное
профессиональное совпадение, но не должны перекрывать большой разрыв topic score.

Eligibility и тематическая query точно как F2. Для документа:
S3 = S_core × (1 + sum(w_j × evidence_j)), evidence_j ∈ {0,1}.
Стартовый общий bonus budget α=0.20; одинаковые веса α/m для m preferences.
Суммарный multiplier ограничен [1,1.20], независимо от числа условий.
α=0 — контроль F2; α=0.1/0.2/0.3 — серия, не измеренный optimum.

Native function_score: безусловная функция weight=1, по одной filter/weight
функции на preference; score_mode=sum, boost_mode=multiply, max_boost=1+α.
Все weights конечны и неотрицательны, α∈[0,.3] для этой серии.
Min_score и дополнительный overall boost не используются: они изменили бы
eligibility или заявленную границу. Max_boost ограничивает function multiplier,
а не финальный _score. Базовая функция имеет filter=match_all и weight=1.
Predicates входят в functions.filter, а не в outer eligibility.
Требуется support обычного DSL, не новый runtime и не Painless чтение _source.
При запрете функции сервером вариант сообщает ошибку доступности.

**Различающий case.** Query «комплектовщик ТСД без опыта»:
A — core score 10, condition evidence отсутствует;
B — core score 9, positive evidence есть.
При α=.2: A=10, B=10.8, B выше. Если A=15, B остаётся ниже.
Это deterministic ranking oracle на заданных core scores, не прогноз BM25 по строкам.
На live source отдельно фиксируются реальные scores и reason возможного обмена.

**Негативный case.** Соседняя профессия с условиями не должна пройти core.
Text contradiction может дать ложный bonus; измерить отдельно от ranking arithmetic.

**Абляции.** α=0, одиночные preferences, scalar/text evidence при доступных полях.
**Наблюдать.** Перестановки при одинаковой eligibility F2/F3, bonus contribution
и обмен topic precision на preference evidence.

## 8. F4 — query-driven preference tiers

**Гипотеза.** Продукт может предпочитать сначала тематические вакансии,
выполняющие все пожелания, даже при меньшем BM25 core score. Это сильнее F3;
неподходящие по пожеланиям вакансии остаются доступны после предпочитаемой группы.

Core и evidence predicates точно как F2/F3. Три группы:
T0 — все preferences имеют evidence;
T1 — хотя бы одно, но не все;
T2 — нет evidence. При одном preference T1 пуста.
Порядок: tier ascending, затем native core score внутри tier.
Это не постоянный day-first 006: предпочтения приходят из конкретного запроса.

Три mutually exclusive native searches строятся из одного core:
T0 filter=all; T1 filter=any AND must_not(all); T2 must_not(any).
Any — bool.should с явным minimum_should_match=1; all — filter conjunction.
При m=1 partial query можно пропустить с empty-tier diagnostic; при m=0
не создаётся empty should, выполняется только F2.
Исходные hard filters присутствуют во всех. Soft predicates выполняются в
filter context и не меняют _score; union групп сохраняет eligible core corpus.
По 100 candidates на tier, затем tier concat до общего depth=100 и top-20.
Все выполненные calls учитываются в latency, обычно три при m≥2.
Нового sortable stable_id/doc values/Painless для tiers не требуется:
внутри равных scores сохраняется возвращённый source порядок с tie caveat.
Native partial/shard failures и repeated id между tiers отклоняют результат.

**Различающий case.** A имеет core score 15 и график 5/2;
B core score 9 и evidence 2/2. Query «комплектовщик ТСД 2/2».
F3 при α=.2 оставляет A выше (15 против 10.8); F4 ставит B выше,
а A остаётся в T2. На final top-20 A может не поместиться:
soft tier меняет presentation, но не становится глобальным hard exclusion.

**Несколько пожеланий.** Документ A выполняет 2/2, B — без опыта,
C — оба условия. F4 ставит C в T0, A/B в T1. F3 может оставить более сильного
тематического A над слабым C. Проверить missing против explicit opposite,
partial/all evidence и непонимаемые отрицания в тексте документов.

**Абляции.** Tier off=F2, одно/несколько preferences, F3 с теми же predicates.
**Наблюдать.** Trade-off core/preference, crowd-out final top-k, tier membership,
дубли/пропуски групп, source mutation между calls.

## 9. F5 — independent lexical/core RRF

**Гипотеза.** Field-centric буквальный поиск и core-coverage поиск дают разные
candidate lists; rank fusion может объединить их сильные стороны без общей шкалы.

Ровно две самостоятельные ветки: F1(original) и F2(core).
F2 не содержит F1 literal bypass. Depth=100 на ветку, existing domain-id RRF:
S(d)=sum(1/(60+rank_branch(d))), затем общий depth=100 и final top-20.
Порядок веток F1,F2; ties/fields follow existing RRF contract.
В отсутствие активного core path второй одинаковый план не даёт второй голос:
F5 возвращает одиночную F1 выдачу с abstention reason.

**Различающий ranking oracle.** A имеет ranks F1=1,F2=10;
B ranks F1=2,F2=1. Тогда B=1/62+1/61, A=1/61+1/70, B выше,
хотя F1 предпочитает A. Case corpus дополнительно содержит vacancy, допущенную
только одной веткой: проверяется admission union, не только перестановка.
Строки карточек и реальные ranks проверяются live, fixture — только RRF arithmetic.

Одинаковые hard filters/context каждой ветки, два sequential searches.
Нельзя fuse обрезанные top-20 или дать дополнительные голоса F3/F4, которые
используют тот же core. Fields одного id, различающиеся между ветками,
помечаются source inconsistency; такой результат не является immutable replay.

**Абляции.** F1 only, F2 only, depth 50/100/200, constant 10/60/100.
**Наблюдать.** Union/overlap, branch membership/ranks, RRF contributions,
качество против обеих самостоятельных веток и стоимость двух calls.

Fuzzy, conservative rewrite, phrase-rescore, freshness/quality и старые C4/C5
сохраняются дополнительными controls; они не занимают места основной матрицы.
Для prefix_length=1 первая буква «бодитель» не исправляется к «водитель»;
«вадитель» меняет вторую букву и не является таким negative case.

## 10. E1 — whole vacancy

**Гипотеза.** Один passage всей карточки — достаточный semantic baseline,
особенно когда описание выражает задачи иначе, чем запрос пользователя.

Текущий renderer 008: normalized title + пустая строка + normalized description,
без labels/повторов/добавленных aliases; passage: добавляет encoder.
Один vector на вакансию, полный fields payload, один query embedding и
один native Qdrant query. Полный model input усекается справа до 512 tokens.

**Контрасты.** «сборщик заказов со сканером», «работа на вилочном погрузчике»;
общий title «работник склада» с точными задачами; длинная реклама работодателя
до требований; важные ТСД/НАКС/CE только в хвосте.

**Абляции.** Description→title order против baseline; candidate depth.
Перестановка — новый recipe/generation. ANN сравнивается с exact отдельно.
Раннее расположение title не называется гарантированным semantic boost.

**Наблюдать.** Relevance соседних профессий, literal-vs-semantic отличия,
token lengths/truncation share и расположение потерянного evidence.

## 11. E2 — topic-first preference rerank

**Гипотеза.** Допуск по профессии/навыкам отдельно от условий не даст мягким
пожеланиям вытеснить профессию, а небольшой semantic preference вклад улучшит
порядок внутри уже тематического пула.

### Derived query contract E2/E3

Original сохраняется. Для decomposition используется общий parser, но его
NFKC/casefold spans нельзя применять к raw text.
S — NFC/whitespace semantic-normalized original; L — lexical-normalized S.
Удаление spans разрешено только если каждый codepoint S преобразуется
NFKC+casefold в ровно один codepoint и конкатенация результатов точно равна L.
Тогда позиции L однозначно соответствуют S; removal выполняется на S с сохранением
регистра, ё, чисел, punctuation, qualification и unknown residual.
Иначе span_mapping_unavailable и весь вариант воздерживается до E1(original).
Нельзя молча срезать raw string по lexical offsets или lower-case model input.

Topic input исключает только однозначные мягкие spans experience/schedule/
employment/work_condition/location; роли/skills/qualification/residual остаются.
Pref input_j = topic text + «. » + соответствующий original-normalized soft span.
Каждый реально отличный текст кодируется обычным encode_query с query:,
тем же profile/pooling/L2. Усреднения/вычитания vectors и artificial renormalization нет.
Derived recipe/inputs/span mapping фиксируются отдельно от embedding profile.

Неподдержанная логика/negation, пустой core, >4 preferences, ambiguity,
конфликтующие или пересекающиеся извлекаемые spans вызывают declared abstention
до E1. Ошибка input/config/parser validation остаётся ошибкой, а не abstention.
Без preferences E2=E1.
«Без опыта» распознаётся цельной whitelist-фразой; «не вахта» не удаляется и
не становится положительным «вахта». Совпадающие derived queries не дублируют
inference/голоса. Gate abstention не означает network/error fallback.

### Candidate pool и score

Один topic query получает 100 candidates из той же whole-vacancy generation E1
со всеми hard filters. Далее каждый pref query оценивает **все те же candidate ids**:
native Qdrant query с прежними hard filters AND has_id по point UUIDs пула:
v1 source.point_id(domain_id) для каждого candidate. Условие has_id находится
рядом с hard-filter conditions в must, не внутри nested object filter.
В whole generation один point на вакансию; дополнительный payload index
или rebuild для pool restriction не нужен.
limit=|pool|, exact=true. Ожидается ровно полный pool ids, без extras/duplicates.
Мalformed/неполный ответ — ошибка, не нулевой bonus для недостающего документа.
Пустой topical pool — успешная пустая выдача без preference inference и queries.

Каждый cosine c конечен и в [-1,1] с документированной numeric tolerance;
только tiny tolerance overshoot ограничивается до границы, грубое нарушение — ошибка.
Стартовая tolerance=1e-5; исходные native values сохраняются отдельно.
u(c)=(c+1)/2. S2=topic_cosine + sum(w_j × u(preference_cosine_j)).
Старт α=sum(w)=0.20, равные веса α/m; α∈[0,.3] для этой серии.
Добавочный вклад в [0,.20] при начальном α,
не растёт просто из-за числа пожеланий. Topic gap>α не может быть компенсирован
(>.20 для начального α=.20).
α=0 возвращает topic ranking. Это **utility**, не calibrated confidence и
не evidence исполнения условия. Даже negative preference cosine может дать
малый ненулевой bonus после u; никакой match threshold из этой формулы не следует.

В основной exact серии все calls exact. При ANN только initial topic retrieval
приближённый; точный scoring фиксированного пула остаётся exact и так помечается.
Inference count — число разных topic/pref inputs; retrieval calls — 1+m.
Native score_threshold не используется. Ответы preference scoring обязаны
содержать тот же set ids и те же полные fields, что snapshot topical pool;
изменение fields означает source inconsistency. Weights конечны и неотрицательны.
Все ids/filters/ready admission проверяются владельцами до downstream calls.

**Различающий oracle.** A: topic=.85,pref=.40; B: topic=.82,pref=.90.
При α=.2: A=.99,B=1.01 — B выше. При topic_A=.95 A=1.09 и остаётся выше B.
A/B — одинаково тематические документы с разными условиями; отдельно проверить
ошибку «берём без опыта» versus «кандидатов без опыта не рассматриваем».
Cosine values в oracle заданы для арифметики, live значения обязаны быть измерены.

**Отличие admission от E3.** Vacancy вне topical depth не может попасть в E2,
даже если её full-query semantic score высок. Она может попасть в E3 через другую ветку.

**Абляции.** α=0/.1/.2/.3; отдельные preferences; mean против min как
отдельная coverage-policy абляция, не новый основной вариант.
`study.preference_aggregation=mean` — основная формула выше; `min` задаёт
`S2=topic_cosine + α × min_j u(preference_cosine_j)`. При отсутствии preferences
bonus=0; α=0 отключает preference encoding/scoring в обоих режимах.
**Наблюдать.** Candidate membership до rerank, topic/soft contributions,
topic/preference trade-off, inference/native calls, ошибки semantic отрицаний.

## 12. E3 — original/topic multi-query RRF

**Гипотеза.** Полный query и topic-only query задают разные направления поиска;
union может восстановить кандидатов, которых topic-first rerank не увидит,
без смешивания native cosine scores разных query vectors.

Та же whole-vacancy generation E1 и derived recipe E2.
Original query и topic query кодируются независимо; два native retrieval
depth=100 с одинаковыми hard filters. Union по domain id, existing RRF constant=60,
порядок original,topic; потом общий depth=100 и top-20.
Это query-side multi-representation; E4 отличается document-side representations.

При отсутствии пригодной decomposition, no preferences или одинаковых model
inputs выполняется один E1(original) поиск; второй identical список не даёт
дополнительный голос. Причина видна в diagnostics.
Нельзя добавлять третий vote каждого soft span: это подменило бы thematic admission
поиском вакансий, совпавших только по условию.

**Различающий case E2/E3.** A входит в topic top-100;
B релевантна полному запросу, topic rank=150, original rank=1.
E2 не допускает B; E3 допускает через original branch.
Для двух включённых вакансий ranking oracle:
A original rank=1,topic rank=20; B original rank=3,topic rank=1.
RRF может предпочесть B, сохраняя измеренное происхождение обоих ranks.
На live source контраст подтверждается vectors/ranks, не заданной fixture выдачей.

**Негативный case.** Vacancy соседней профессии с точно выраженными условиями
может войти через original branch; RRF не гарантирует обязательную роль или
Boolean topic evidence. Это цена расширенного admission, исследуемая относительно E2.

**Абляции.** Original only=E1, topic only=E2 при α=0,
depth 50/100/200, constant 10/60/100.
**Наблюдать.** Новые ids за topical pool, agreement/branch specificity,
utility rerank versus rank fusion, topic precision и два encode/retrieval calls.

Title-only и structured-card recipes сохраняются representation ablations E1/E4,
а не основными механизмами E2/E3. Если нужны labels/metadata recipe,
binding/type/map/identity/audit задаются явно и source fields не выдумываются.
Отсутствие metadata не превращает labels-only renderer в новый основной опыт.

## 13. E4 — title/body RRF

**Гипотеза.** Независимые представления title и description дадут полезное
дополнение, которое одно смешанное embedding E1 сглаживает.

Две физические unnamed-vector collections: title-only и description-only.
Одинаковые cohort digest, model/runtime, scalar filters и payload fields;
различаются recipe/source identities. Никаких Qdrant named/multivectors.
Description-only — техническая ветка/абляция, не шестой основной вариант.

Один query embedding на вызов E4. Оба ready descriptors проверяются до encode;
тот же immutable vector используется в двух sequential native queries.
Depth=100 на ветку, RRF constant=60, порядок title,description, затем top-20.
Исполнитель E4 владеет общим encode локально; vector не помещается в context,
filters, branches или global cache. Веточные SERP агрегируются текущей RRF-семантикой.

F5 и E4 используют одинаковый rank fusion contract; не суммируются и не усредняются
cosine scores. Нативные scores сохраняются в branch diagnostics.
Одна generation не может молча заменяться whole-vacancy recipe.
Общие missing-field/cohort правила обязательны до build.

**Контрасты.** Хороший title, шумный description; общий title, точное описание;
title с ролью и body с навыком; конфликт ролей в двух полях.

**Абляции.** Каждая ветка отдельно, depth 50/100/200 и constant 10/60/100.
Для одного query в exact/ANN comparison сохраняется одно фактическое embedding.

**Наблюдать.** Complementarity и relevance против E1 и каждой ветки,
native branch ranks/scores, RRF contributions, две query durations.

## 14. E5 — passage max

**Гипотеза.** Полезный навык/условие в конце длинной вакансии сможет участвовать
в retrieval, если описание представлено несколькими passages; нерелевантные
chunks не должны суммировать score просто из-за длины документа.
Max устраняет прямое суммирование, но **не** opportunity bias: больше chunks
даёт больше шансов случайного высокого cosine. Это отдельный измеряемый эффект.

Один point на chunk; итоговая единица выдачи — одна полная Vacancy.
Score вакансии — **max cosine score её chunks**, не сумма и не среднее.
Рецепт сохраняет title context в каждом passage.

### Разбиение

Исходные title/description нормализуются отдельно как в 008.
Начальный recipe: title context не более 64 non-special tokenizer tokens;
description windows не более 384 non-special tokens, overlap 64.
Offsets pinned TEI — UTF-8 byte offsets, не индексы Python str.
Special tokens не задают текстовых границ; token.text не используется для
восстановления строки. Границы проверяются, slices строго декодируются.
Для выбора границ tokenize получает normalized title/description без model prefix;
для окончательного audit — полный реальный model input со всеми prefixes.

Window boundaries выбираются по whitespace boundaries исходной normalized строки:
максимальный конец, укладывающийся в 384 tokens, следующий старт сохраняет
до 64 tokens overlap и обязан продвинуться хотя бы на одно целое слово.
Повторяющиеся tokenizer offsets одной Unicode последовательности не порождают
дубликаты/пустые окна. Whitespace gap сохраняется в покрытии description.
Чрезмерно длинное неделимое слово, неверные offsets или отсутствие прогресса —
preflight error; символы не отбрасываются ради budget.

Слишком длинный title context обрезается на последней целой границе в 64 tokens;
если первая неделимая часть не помещается — ошибка. Сокращение фиксируется в audit.
Полные title и description в fields не меняются.
Model input: passage: + title context + newline + description window.
**Каждый полный input повторно tokenize-ится с prefix/special tokens и обязан
поместиться в 512.** Если не помещается, body window детерминированно уменьшается
на целые слова, с повторной проверкой и сохранением покрытия/прогресса.
Если даже title с одним body-словом не помещается — ошибка.
Encoding chunks использует truncate=false; скрытого усечения chunks нет.

Для этого у encoder появляется узкая возможность кодировать уже audited
bounded passages с отказом при overflow. Прежний encode_documents 008 сохраняет
right truncation и прежний profile; query encoder не используется вместо
document encoder. Новый renderer/recipe выбирается по явной version/discriminator:
structured/chunk recipes не притворяются прежним vacancy-fields-v1.

Максимум 64 chunks на вакансию в стартовом recipe. Превышение отклоняет build
до создания коллекции, показывая id/число; первые 64 не выдаются за весь документ.
Limits, Unicode boundary policy и chunker version входят в recipe identity.
Разбиение всего corpus и все input/payload budgets проверяются до remote writes.
Tokenization при build является явным read-only HTTP, не «чистым preview».

### Retrieval уникальных вакансий

Native groups Qdrant не используются как доказательство полного top-N:
в pinned source grouping имеет внутренние лимиты итераций.
Фиксированный top-100 chunks с последующим dedup тоже недостаточен.

1. После ready admission кодируется один query; исходные compiled filters фиксируются.
2. Выполняется обычный points/query: point page size=100, одинаковые search params,
   исходные filters плюс native exclusion уже найденных document_id.
3. Каждый response полностью проверяется. Для новых id сохраняется лучший
   возвращённый chunk, его score и provenance; id дедуплицируются.
4. Если собрано ≥candidate depth уникальных вакансий, выбираются лучшие 100.
   Иначе следующий запрос исключает **все** уже найденные id.
5. Пустой response означает исчерпание допустимых candidates.
   Максимум candidate depth native calls; отсутствие прогресса или достижение
   budget без заполнения/исчерпания — ошибка, без частичной SERP.
6. Max scores сортируются descending; при равенстве — точный domain id ascending,
   затем chunk ordinal для выбора объясняющего passage. После этого top-20.

В exact-режиме первый найденный chunk нового id задаёт его глобальный max среди
eligible chunks, кроме допустимого равенства scores. Выбор состава при tie на
границе depth может различаться; это отражается в comparison.
В ANN такой гарантии нет: нужны document-level recall/overlap к exact.
При ANN ошибка/исчерпание не означает доказанную полноту всего filtered corpus.
В основном исследовании фиксируется один query vector для всех итераций.

Native exclusion — внутренняя поисковая механика, не пользовательский NOT filter.
document_id индексируется как keyword; исходные AND filters действуют в каждой
итерации. Кандидат, нарушивший exclusion, duplicate native id, несовпадающие fields
chunks одного документа или malformed последний hit отклоняют весь поиск.
Native форма exclusion — must_not с keyword match.any по списку точных
document_id. Body каждой итерации проверяется целиком по byte budget до отправки.

### Коллекция и publication

E5 — **новый явно версионированный source/payload contract v2**. V1 008 сохраняется:
его point identity и ready descriptors не переинтерпретируются.
E5 source отклоняет v1 коллекцию, обычный vector source отклоняет chunk v2.
Новая physical generation обязательна; миграции/перезаписи старой нет.

Point payload содержит точные document_id, полные fields и отдельный fragment:
fragment schema version=2, ordinal, normalized title `title_stop` bytes,
normalized description start/stop bytes, digest model input.
Технические значения не добавляются в fields. Все chunks несут одинаковый
независимый snapshot исходных fields. Model input восстановим по recipe и offsets без tokenizer или HTTP:
`document_prefix + normalized_title[:title_stop] + newline + description[start:stop]`
(срезы по UTF-8 bytes). Cutoff должен попадать в границу UTF-8 и целого слова,
reconstruction проверяет digest при hit admission и полном read-back.
Recipe discriminator `fragment_layout=title-stop-bytes-v2` запрещает переиспользование
поколения с прежней формой fragment.
Алгоритм point identity — uuid5-fragment-v2:
project namespace = UUIDv5(NAMESPACE_URL, «urn:recsys-experiments:vacancy-fragments:v2»),
corpus namespace = UUIDv5(project namespace, точная corpus identity).
Name — UTF-8 строка canonical JSON объекта с recipe_id, document_id, ordinal,
model_input_sha256; UUIDv5(corpus namespace, name) определяет point id.
Canonical serialization из domain/serialization сохраняется; алгоритм/version
входят в source identity. Collisions проверяются до writes.

Descriptor хранит cohort/corpus digest, profile/runtime, полный chunk recipe,
N документов, M points, filter indexes и chunk-schema version.
Build ready требует exact count=M и полного bounded read-back всех M payloads/vectors,
проверки покрытия/ordinals по всем N, единообразия fields, filtered/exclusion smoke.
Проверка только N points недостаточна. Batch encoder/storage limits из 008 сохраняются;
chunk/input batches и points не накапливают все vectors корпуса.
building→ready, fresh-name admission и receipt failure semantics сохраняются.
E5 receipt — отдельный versioned record с N/M/chunk audit и verification.

**Контрасты.** CE/НАКС/ТСД после 512-token границы; длинная реклама перед задачами;
одна вакансия с множеством похожих chunks против короткой точной;
соседняя профессия в отдельном фрагменте; ограничения города/active/schedule.
Обязательна серия: один и тот же relevant passage плюс 0/5/20 unrelated chunks,
чтобы отдельно измерить max opportunity bias.

**Абляции.** Window 256/384; overlap 0/64; title context off/64;
max aggregation остаётся общей политикой, sum aggregation не вводится.

**Наблюдать.** Recovered tail relevance, ложные совпадения по чужому фрагменту,
winner passage/offset, количество calls/points, build/storage cost и ANN recall.

## 15. Как исследователь отправляет запросы и читает выдачу

### 15.1. Рабочее место и первый запуск

Основная точка входа —
[search-study.ipynb](../../src/experiments/notebooks/search-study.ipynb).
Исследователь открывает его в JupyterLab или VS Code с Python kernel окружения
проекта. [Руководство по внешним сервисам](../usage.md) содержит порядок первого
запуска и инструкции подготовки источников. Jupyter предоставляет пользователь;
runtime dependencies проекта не расширяются. Существующие три notebooks сохраняются.

Управление — короткие подписанные Python-ячейки и HTML-представления результатов
внутри notebook. Для первого исследования достаточно менять значения входной
ячейки и повторять ячейки запуска/чтения. Виджеты, отдельный frontend и новый
JavaScript runtime не требуются. Все реализации отображения и orchestration
располагаются в src; notebook вызывает публичные компоненты и задаёт входы.

Перед первой сессией оператор один раз готовит site config: source bindings,
scalar maps, cohort, TEI и готовые whole/title/body/chunk generations.
Контракты подготовки заданы в 008 и разделах 2/14/21 этой spec. Она не скрывается
в ячейке ручного поиска. В notebook есть краткий checklist и ссылка на эти инструкции;
построение коллекций не требуется перед каждым новым запросом.

Первый запуск состоит из явных шагов:

1. Указать путь к study site config и overrides в блоке настройки окружения.
   Credentials разрешаются из environment и не отображаются в выводе.
2. Выполнить локальную Hydra compose/instantiate с отключёнными exporters.
   Получить список зарегистрированных вариантов, краткие названия механизмов,
   объявленные filters и привязки к источникам/поколениям.
3. При необходимости выполнить отдельную read-only проверку выбранных источников.
   Remote readiness для dense следует 008; OpenSearch search-only proxy не
   проверяется выдуманными mapping/health APIs. Локальная декларация помечается
   «настроено, удалённо не проверено», успешная remote проверка — своим временем.
4. Перейти к входной ячейке и первому сравнению. Каждый search всё равно
   выполняет обязательный актуальный admission выбранных generations.

Открытие notebook и instantiate не запускают search, inference, model/image
download, build или export. Недоступный вариант показан с причиной; выбор такого
варианта даёт явную ошибку, без автоматического сокращения сравнения.
Готовность к исследованию означает успешный полный поиск выбранного набора,
а не только успешный импорт notebook.

### 15.2. Входная ячейка и границы перенастройки

В начале рабочей части располагается один короткий блок:

~~~python
# Входная ячейка study notebook.
text = "комплектовщик ТСД без опыта 2/2"
variants = ["f2", "f3", "f4", "e1", "e2"]
filter_values = {"city": "Москва", "active": True}
reference_variant = "f2"
~~~

Пример начинает исследование topic/preferences; соседние пояснения дают
готовые наборы fulltext-only F1–F5, embedding-only E1–E5 и all-ten.
Исследователь может выбрать один вариант или произвольный непустой набор без
дубликатов. Reference входит в выбранный набор, по умолчанию — первый variant.
Оно задаёт только отображение rank differences, не меняет поиск и fusion.

Filter values преобразуются в типизированные equality filters через study
bindings. Отсутствующий ключ означает отсутствие per-call условия; false не
означает отсутствие boolean filter. Неизвестный ключ/тип — ошибка до inference/HTTP.
Статические ограничения site config остаются AND и показываются отдельно:
замена per-call city не отменяет static city. Демонстрационный site config не
скрывает city/active в static filters, чтобы их можно было менять в этой ячейке.

| Что меняет исследователь | Что нужно повторить | Что происходит с источниками |
| --- | --- | --- |
| Query, per-call filter values, выбранные зарегистрированные variants | Входную ячейку и search/compare; preview при желании | Новый запрос, прежние поколения |
| Reference, раскрываемую карточку, поднабор уже полученных выдач | Ячейку отображения текущего результата | Нет HTTP/inference |
| Candidate depth/top-k, weights, coverage, RRF constant, exact/ANN, bindings или static filters | Hydra compose/instantiate, затем search/compare | Новые параметры; recipe-compatible поколения переиспользуются |
| Document recipe, модельный профиль, corpus/cohort | Отдельную подготовку свежих generations и новую compose/instantiate | Поиск только после publication/admission |

Выбор зарегистрированных вариантов — per-call input. Сборка может включать
явный доступный поднабор матрицы; нельзя требовать отсутствующие embedding
настройки для fulltext-only сессии. Выбор незарегистрированного варианта требует
явной новой сборки с его конфигурацией. Изменение YAML/overrides не мутирует
уже созданные объекты: после перенастройки notebook показывает новую identity.

### 15.3. Поиск, preview и состояние результата

Public contract study-сценария:

~~~python
# study собран через site config; API не выполняется при чтении spec.
from src.domain.request.filters import TermFilter

serp = study.search(
    "комплектовщик ТСД",
    variant="f3",
    filters=[TermFilter("city", "Москва"), TermFilter("active", True)],
)
comparison = study.compare(
    "комплектовщик ТСД",
    variants=["f1", "f3", "e1", "e5"],
    filters=[TermFilter("city", "Москва"), TermFilter("active", True)],
)
~~~

Одиночный search возвращает независимый snapshot SERP без печати/экспорта.
Compare возвращает самостоятельный diagnostic artifact; judgments не требуются.
Study preview принимает тот же query/variants/context/filters и возвращает
независимый снимок выбранных планов без HTTP; он использует planners владельцев
поиска, а не вторую реализацию compilers в notebook.
Статические и переданные filters соединяются, context override — как в 005.
Все selected plans проверяются до HTTP. SearchExperiment остаётся владельцем
независимых payloads и проверки завершённой SERP, его прежний API не меняется.

Рабочие ячейки имеют явные подписи «Preview: без сети», «Search/compare: внешние
вызовы», «Чтение результата: без сети». Preview показывает планы всех выбранных
вариантов, effective filters, original/topic/preference inputs, core/soft spans
и объявленный active path либо abstention. Вектор не выдумывается; реальные
encode/native body остаются отдельным явным исследовательским действием.

Каждый успешный compare — независимый снимок одного query и выбранного набора,
с run_id, временем, composition identity и исходными/effective входами.
Ячейка запуска сбрасывает current result до начала вызова и устанавливает его
только после полного успеха. Если новая попытка завершилась ошибкой, чтение и
save текущего результата недоступны; предыдущий успешный снимок можно сохранить
в отдельной Python-переменной, но он явно подписан собственным query/run_id.
Прежний вывод Jupyter может оставаться на экране: каждый блок содержит подпись
своего run_id/query/filters, поэтому его нельзя принять за новый успешный результат.

Ошибка остаётся ошибкой с variant/stage и исходной причиной, без credentials;
пустая SERP отображается как успешные 0 результатов. После исправления входа
или источника следующий ручной вызов возможен в том же kernel.
Повторный запуск compare выполняет новый поиск; рендер текущего снимка — только
чтение. Идентичность входов не обещает идентичную выдачу изменяемого OpenSearch.

### 15.4. Обязательные представления сравнения

После поиска notebook показывает четыре представления одного snapshot.
Диагностика раскрывается отдельно, чтобы не вытеснять чтение вакансий.

**Паспорт запуска.** Query, effective hard filters, выбранные варианты с названием
механизма, фактический active path/abstention, depth/top-k, exact/ANN и reference.
Таблица summary содержит количество возвращённых вакансий, длительность полного
вызова и counts encode/retrieval/admission. Comparability и similarity evidence
видны здесь; timing fixtures не объявляются измеренной производительностью.

**Выдачи по позициям.** Каждый выбранный variant имеет отдельный подписанный
столбец/блок, строки идут по исходному final rank от 1 до top-k. На одной позиции
видны id, title и короткий description excerpt; score и его тип доступны рядом.
Общая vacancy имеет одинаковую подпись id во всех выдачах. Все top-20 доступны,
а ограничение длины excerpt не усекает полную карточку. При большом числе variants
допустима горизонтальная прокрутка и локальное отображение поднабора; оно не
выполняет поиск. Пустой variant остаётся видимым блоком «0 результатов».

**Матрица вакансий.** Строки — union уникальных ids final top-k выбранных выдач;
столбцы — варианты, ячейка — final rank. Первые строки следуют reference выдаче,
новые ids добавляются в порядке первого появления в остальных выбранных variants.
Id/title идентифицируют строку, полное описание открывается отдельно.
Показаны общие и появившиеся относительно reference документы; отсутствие
отображается «—: не попала в top-k», без придуманного rank или нулевого score.
Delta=reference_rank−variant_rank вычисляется только при наличии обоих ranks:
положительное значение означает подъём. Score величины разных типов не вычитаются.

Пример формы матрицы ниже — макет с вымышленными ranks, не прогноз BM25/E5:

| Вакансия | F2 | F3 | F4 | E1 | E2 |
| --- | --- | --- | --- | --- | --- |
| v001 — Комплектовщик, ТСД; условия не указаны | 1 | 2 | 2 | 3 | 2 |
| v002 — Комплектовщик, без опыта, 2/2 | 2 | 1 | 1 | 1 | 1 |
| v003 — Работник склада | — | — | — | 2 | — |

Связь строк по id требует общего identity/cohort contract раздела 2. При
несопоставимых OpenSearch/Qdrant источниках показываются отдельные матрицы
fulltext и dense с заметной подписью; одинаковые строковые ids между источниками
не объединяются автоматически. Внутри одной матрицы разные fields одного id
из разных variant snapshots показываются как source difference с provenance,
без молчаливого выбора «канонической» версии. Внутренние branch/tier/chunk
inconsistencies по-прежнему подчиняются failure semantics владельцев.

**Карточка и диагностика.** Исследователь задаёт точный document_id из результата
и variant/source, если требуется различить snapshots. На карточке видны полный
title/description, все исходные fields, присутствующие city/active/schedule и
позиции в выбранных вариантах. Missing condition field показывается как «не
указано». Для E5 рядом доступен winner passage с offsets; для fusion — branch
ranks/contributions; для F3 — доступное evidence и bonus; для F4 — tier;
для E2 — topic/preference scores и итоговая utility. Original/native values
сохраняются. Неизвестный id не вызывает дополнительного fetch.

Diagnostics включает подготовленные тексты/DSL, native query metadata,
abstention и фактические calls; данные берутся только из artifact. Если engine
не вернул объяснение field/term score, notebook его не восстанавливает из title
или _source и не выдаёт textual evidence за доказательство условия.
HTML экранирует весь source content; ссылки и source HTML не исполняются.
Отображение сравнения/карточки/диагностики не делает внешних вызовов.

### 15.5. Проработанная исследовательская сессия

При готовых источниках пользователь проходит такой сценарий:

1. Выполняет setup один раз. Выбирает F2/F3/F4/E1/E2, reference=F2, query
   «комплектовщик ТСД без опыта 2/2», city=Москва и active=true.
2. В preview проверяет: город/активность — hard filters; роль/ТСД — core;
   опыт/график — soft preferences. Если decomposition воздержалась, это видно
   до поиска; query не считается примером активного preference механизма.
3. Запускает compare. Читает пять выдач и матрицу позиций. Проверяет, какие
   вакансии поднялись у F3, какая группа первая у F4, что переставил E2.
   Конкретный обмен местами зависит от фактических результатов и не предписан.
4. Открывает сильную тематическую вакансию без условий и вакансию с пожеланиями.
   Читает полные тексты и evidence, включая «без опыта не рассматриваем».
   Все эти действия используют полученный snapshot.
5. Убирает «без опыта 2/2» из query, повторяет search и проверяет active paths:
   F3/F4 переходят к F2, E2 — к E1; старые результаты сохраняют прежнюю подпись.
6. Возвращает исходный query, заменяет per-call city на Санкт-Петербург и
   повторяет preview/search без пересборки или build. Во всех calls новый
   effective city; прежнее per-call значение не накапливается.
7. Для отдельного опыта меняет α F3/E2 в Hydra overrides, повторяет compose/
   instantiate и сравнение с теми же query/filters. Новый snapshot фиксирует
   новые параметры; whole generation и другие совместимые sources сохраняются.
8. Явно сохраняет выбранное успешное сравнение и получает точный путь артефакта.
   Затем может выбрать F1–F5, E1–E5 или все десять и повторить тот же цикл.

### 15.6. Сохранение, batch и повторное выполнение ячеек

Отдельная ячейка save экспортирует выбранный завершённый snapshot в
`outputs/search-study/<run_id>/search-study.json` через existing publication
contract. Путь можно задать явно; успешный вывод содержит путь и run_id.
Сохранение не перезапускает запрос и не использует новые значения входной ячейки
для изменения старого результата. Повторная запись того же пути отклоняется
без overwrite; для повторной копии требуется другой явный путь.
Saved JSON содержит результаты, входы, source/parameter identities и diagnostics,
а не executable config с credentials. Автоматическое восстановление kernel
из JSON и replay источников в этот outcome не входят.

Ячейки batch, token audit, build и явного native embedding вынесены в отдельный
дополнительный блок после основного сценария. Они явно подписаны по внешним
действиям. Build/model downloads не запускаются через повседневный compare.
Batch использует тот же study composition и входной CSV `id,text`.
Основной путь не требует разметки, запуска всего query pack или batch.

Обычное последовательное выполнение notebook может выполнить демонстрационный
search при настроенных источниках. Save, batch, expensive corpus audit и любые
writes по умолчанию отключены явными flags; Run All не публикует артефакты,
не строит коллекции и не запускает весь набор запросов. Ячейки setup и main
scenario можно выполнить повторно в том же kernel; локальные результаты имеют
свои snapshots, запросы не наследуют прежние per-call filters.

### 15.7. Терминальный сценарий

CLI выбирает один из десяти presets через Hydra, принимает обычный search>
и показывает полный JSON каждой SERP. Hard filters задаются общим study site config
либо Hydra overrides до запуска. Новый inline язык команд фильтрации не нужен:
для смены filters по запросу используется notebook/API, в CLI — новый запуск.
EOF сохраняет сессию; ошибка/Ctrl-C сохраняют действующие exit/no-partial-export правила.

## 16. Наблюдаемость и результаты

Прежние searches.json, comparison.json и CSV id/text→SERP не меняются.
Новый отдельный **search-study.json, schema_version=1** описывает исследование:

- Session: Git revision, selected variant ids, effective parameter/recipe identities,
  query set identity, source identity и declared comparability; effective similarity
  declaration, статус/происхождение её подтверждения и доступные параметры полей.
  Для ручного сравнения — run_id и время запуска; reference является параметром
  отображения и сохраняется отдельно от retrieval policy.
- Query: id, original text, effective logical filters и разрешённые source bindings.
- Variant: fulltext request bodies, parser/rewrite/fuzzy trace либо prepared semantic
  input, profile/recipe/source ids, physical generations и admitted corpus digests.
- Active policy: core eligibility/groups, soft predicates и evidence origin,
  бонусные вклады, tier, derived query inputs/span-map или abstention reason.
  Declared variant id и фактически активный execution path показываются отдельно.
- Calls: source/branch/iteration, admission/encode/retrieval/end-to-end durations
  и counts; OpenSearch took отдельно от client elapsed.
- Native vector queries: body без query vector, его dimension/profile/hash;
  это явно не полный byte-for-byte HTTP body. Vector можно изучить в kernel.
- Hits: уникальный domain id, final rank, score и его тип, полные fields;
  branch ranks/native scores/RRF contributions, либо E5 winner fragment и max score.
- Capacity: N/M, token/truncation/chunk audit, фактические exact/HNSW settings.

Diagnostics принадлежат результату поиска, а не Vacancy.fields/user context.
SERP может иметь исследовательскую специализацию, которую SearchExperiment
deep snapshot сохраняет. Исследовательская fusion composition сохраняет branch
diagnostics перед RRF; текущий generic RRF не обязан менять свой public contract.
Нельзя собирать evidence из global last_request/last_response после выполнения.

BM25, cosine и RRF — разные score types, их величины напрямую не сравниваются.
При unknown/non-BM25 similarity fulltext native score не маркируется BM25;
bonus multiplier, tier и RRF contributions показываются отдельно.
Пустая выдача сохраняется как успех с нулём hits; error не записывается как empty.
Sensitive source/query data входит в artifacts явно; URL/auth/credentials и
resolved config не включаются. Missing/invalid timing сохраняет строгие правила 007.
Publication атомарна, без overwrite, только после полного успеха выбранного сравнения.
Ошибка batch не публикует ранее успешные queries как завершённый batch.

## 17. Query pack и проверка гипотез

База — 249 входов 006A; сохранены их ожидаемые parse/rewrite outcomes.
Новый study pack содержит минимум **40 отдельно описанных сценариев**:
не менее восьми на каждый из пяти срезов ниже, пересечения помечаются явно.
Это содержательные пары query/documents с rationale, не декартово умножение ролей.

| Срез | Содержательные контрасты |
| --- | --- |
| Точная профессия / соседняя | Грузчик–водитель погрузчика, оператор–наладчик ЧПУ; соседняя роль с точными пожеланиями |
| Разные формулировки / поля | Title-only роль, skill только в description, task paraphrase, общий title |
| Мягкие условия / topic trade-off | Сильная роль без пожеланий, слабее роль с пожеланиями; all/partial/none; negated condition text |
| Длинный документ / структура | Evidence после 512, structured fields до description, много одинаковых chunks |
| Жёсткие ограничения | City+active AND, schedule, отсутствующее поле, contradictory/empty result |

Каждый scenario имеет query id, цель сравнения, effective filters, positive и
negative documents, ожидаемое contract behavior и отдельную human relevance rationale.
Для каждого основного механизма обязательны: ближайший контроль, общий query/corpus/
hard filters, пара вакансий с различающим admission/order case, механизм-off
абляция и негативный/abstention case. Смена полей/labels/одного веса сама по себе
не закрывает этот критерий.
Topic/preferences активируются на корректных запросах, а не только опечатках.
Отдельно сохраняются старые cases ошибок/защиты и требуется их регрессия.
Synthetic fixtures не объявляются реальными результатами BM25/E5.
Новый boundary pack содержит title/description missing, invalid scalar/array,
Unicode byte offsets, title cutoff, chunk limit и concentration top chunks.

### Количественные измерения

Ручное чтение — обязательный первый способ; quality grades не обязательны для запуска.
После него pool — union top-20 всех десяти вариантов на выбранных queries.
Человек размечает пары query/vacancy с учётом явных условий:
0 нерелевантно, 1 частично, 2 релевантно, 3 точное полезное совпадение.
Неизвестный judgment не равен 0. NDCG@10 использует существующий контракт.
Если ANN/абляция приносит новый id в оцениваемом top-k, его сначала размечают.

Development и held-out queries разделяются до tuning; группы одного исходного
query/опечатки/переформулировки остаются в одном split.
Сообщаются per-query/per-slice результаты, zero-hit share и изменения относительно
контроля. Precision@10 — дополнительный показатель с явным порогом grade≥2,
знаменателем 10 и нулевым вкладом отсутствующих позиций.
Recall к размеченному pool называется pooled recall, не полнотой всего рынка.
Numeric quality threshold или победитель заранее не назначаются.
Для decomposition/tier/fusion сообщаются active-path и abstention counts.
Повторившийся E1/F1 plan не считается доказательством самостоятельного механизма.

ANN оценивается на той же generation/filter/query vector против exact.
Для E4 сравнивается финальная fused SERP; для E5 — уникальные вакансии,
не только chunks. Tie boundary допускает эквивалентные по exact score результаты.
Latency измеряется cold/warm, encode, native retrieval и полный путь p50/p95;
admission GETs входят в end-to-end, для fusion/loop считаются все calls.
Длительности ручных отдельных запросов не выдаются за нагрузочный benchmark.

## 18. Ownership, конфигурация и compatibility

Вся конфигурация — Hydra, код реализации — src.
Именованные leafs/pipelines собираются обычным transformer registry.
Новый study namespace имеет вложенные fulltext/embedding groups и manual/batch
wrappers; main.yaml и vector.yaml сохраняют defaults.
Параметры располагаются у query/ranking/renderer/retrieval/source владельцев,
а study orchestration выбирает variant names и inputs.

| Поведение | Владелец |
| --- | --- |
| Logical input filters, variant preflight, сравнение/сессия | Study experiment на существующих SearchExperiment |
| Field-centric/core BM25 DSL | OpenSearch jobboard compiler/transformers |
| Query-soft evidence, bounded functions и tier partition | Query/ranking compiler, study composition |
| Derived topic/preference queries и безопасное span mapping | Query understanding и dense query planner |
| Exact field filters и готовность generation | Source и соответствующая integration |
| Structured/chunk document representation | Embedding document renderer и versioned recipe |
| Chunk point identity/payload и conversion | Отдельный Qdrant chunk source v2 |
| E4 общий query embedding / два retrieval | Специализированная embedding composition |
| E2 restricted preference scoring / E3 multi-query RRF | Специализированная dense composition |
| E5 iterations / vacancy aggregation | Специализированный chunk search transformer |
| Branch fusion и final top-k | Существующие RRF/TopK с study diagnostics на границе |
| Index verification / remote ready | Index experiment и versioned source, как в 008 |
| Artifact snapshot/publication | Reporting artifact и существующие exporters |

Новый universal graph executor, registry protocol, mutable global state,
payload поля для ML clients/embedding и compatibility guessing не нужны.
F2 strict-core compiler extension opt-in; C4 literal bypass и старый parsed/fuzzy
не переопределяются. F3 query-dependent функции и F4 disjoint tiers — новые
opt-in policies, не скрытая замена C2/C3 006.
E2/E3 derived recipe меняет query composition, а не embedding space.
Title/structured renderer controls не объявляются новыми search mechanisms.
E5 source v2 не ослабляет v1 identity/ready admission.
Ни один вариант не требует наличия ranking metadata 006 C2/C3.

Изменения public behavior при реализации одновременно отражаются в architecture,
CONTEXT при новых терминах, README и development. Действующие interfaces и owner contracts отражены в architecture;
локальные и live evidence разделены в development.

Equality binding при реализации содержит `type`, `opensearch_field`,
`qdrant_path` и optional `opensearch_source_path` — отдельный raw `_source` path
для отображения условий fulltext карточки. Суффиксы indexed field не угадываются;
для dense raw fields используется declared `qdrant_path`. Missing и null различаются.
Static UserPropertyFilter хранится символически до effective-context preflight.
`study.fulltext_description_field` объявляет точный raw key полного описания
fulltext карточки (default `description`), независимо от порядка scored fields.

## 19. Failure semantics

Невалидный input/selected config отклоняется до поиска; полный comparison preflight
не допускает HTTP первой ветки при локальной ошибке второй.
Ready admission всех embedding collections предшествует encode выбранного варианта.
Missing generation/несовпадение cohort/profile/recipe/runtime/version — ошибка,
без fallback на E1, другой корпус или available subset variants.
Declared abstention безопасного query decomposition — часть выбранной политики,
с исходным запросом и reason; она не применяется к config/parser validation
errors, malformed response, network failure или missing generation.

Мalformed последний hit/embedding/chunk response не меняет items, filters,
context/branches и не публикует частичный artifact.
Неудача второй fusion ветки или поздней E5 итерации отменяет весь результат вызова.
Следующий ручной вызов остаётся возможным без global остатка прошлой ошибки.
Read-only calls не откатываются; writes build подчиняются building→ready.
Query overflow отвергается, whole-document truncation разрешена по recipe,
chunk truncation запрещена. Бюджеты не повышаются молча.

Build проверяет новые inputs/все scalar fields/identities/payload bytes до writes;
E1/E2/E3 используют одну whole-vacancy generation; E4 требует title/body,
E5 — chunk generation. Изменение document recipe любой абляции требует нового имени.
Existing name не принимается, delete/resume
не добавляются. При failure building недоступен search.
Remote ready после receipt export failure сохраняется, как в 008.
При работе с несколькими collections они публикуются независимо:
E4 preflight требует обе готовые, частично готовая пара не становится E4.
Транзакции и rollback между поколениями не вводятся.

## 20. Acceptance evidence

### Локальные критерии реализации

| ID | Проверяемый observable contract |
| --- | --- |
| L01 | Все десять Hydra presets instantiate, старые defaults/CLI остаются рабочими |
| L02 | Filter bindings/type/AND и preflight выбранной матрицы; неизвестный filter до HTTP |
| L03 | F1 field-centric, F2 mandatory role/qualification core, no literal bypass; cross-field coverage и guards |
| L04 | F3 eligibility=F2, multiplier bound; F4 all/partial/none partition, explicit MSM, no soft global exclusion |
| L05 | F5 full-depth branches, известный RRF, два одинаково filtered requests, no partial second-branch result |
| L06 | E2 safe span map, topic/preference inputs, one fixed pool, complete exact scoring с теми же ids/fields |
| L07 | E3 original/topic union и RRF, distinct inputs, one vote при duplicate/abstention, same generation |
| L08 | E4 два compatible ready sources до encode, один vector/two queries, known RRF и full fields |
| L09 | E5 Unicode byte offsets/coverage/overlap/progress/title cutoff, complete tokenize audit и truncate=false |
| L10 | E5 N/M identity, v1/v2 mutual rejection, full M read-back/indexes/ready; bad last chunk blocks publication |
| L11 | E5 concentration: три vacancies с ≤64 chunks занимают первые 100 hits; цикл добирает unique depth=100 |
| L12 | E5 max без sum-count bonus, opportunity-bias cases; exclusions/filters и exhausted/timeout/stall semantics |
| L13 | Manual API: повторный query/смена filters, deep snapshots, empty success, retry после failure |
| L14 | Notebook contract N01–N09: cells дважды с локальными HTTP fixtures, без saved outputs |
| L15 | Study schema/score types/branch provenance/timing/counts и старые JSON/CSV formats |
| L16 | CLI EOF/error/Ctrl-C, batch failure/no overwrite/атомарная publication, credentials не попадают в output |
| L17 | ≥40 contrast scenarios и boundary pack, полный прежний query pack проходит preview |
| L18 | Ruff/format/ty/pytest, Markdown links, Hydra composed paths и documentation diff checks |
| L19 | Каждый main variant имеет механизм-off control, различающий two-vacancy case и негативный/abstention case |

Focused tests проверяют контракты и составной Hydra→HTTP→SERP→artifact путь;
HTTP fixtures не исполняют BM25, tokenizer, embedding или Qdrant exact/HNSW.
Для E5 loop нужны controlled point scores и cases повторных calls, а не
тест, возвращающий сразу готовую document SERP.

### Приёмка рабочего notebook

| ID | Evidence образа результата |
| --- | --- |
| N01 | Setup по инструкции первого запуска, variant/filter/source declarations; no search/inference/write/export при instantiate |
| N02 | Preview и compare получают одинаковые query/variants/effective filters; ошибка binding/type до внешних calls |
| N03 | Повторная входная ячейка меняет query/city/variants без build; static AND остаётся явным, старые per-call filters не накапливаются |
| N04 | Rank lists и union matrix сохраняют final order; reference/deltas, top-k absence, пустой variant и несопоставимые sources показаны честно |
| N05 | Card содержит полные fields/description и имеющуюся provenance; malicious HTML экранирован; render/card/diagnostics не делают HTTP/inference |
| N06 | Неудача нового compare не оставляет старый current result; old output подписан snapshot, partial artifact недоступен для save; следующий вызов возможен |
| N07 | Hydra parameter change + compose/instantiate даёт новую identity; прежние snapshots неизменны, совместимые generations не перестраиваются |
| N08 | Explicit save фиксирует выбранный run_id/inputs, не делает search; no overwrite/partial publication/credentials |
| N09 | Основной сценарий раздела 15.5 и повторное выполнение cells проходят composed local HTTP path; Run All default flags блокируют writes/batch/audit |

Локальные cell tests проверяют данные и отсутствие лишних calls; удобство
реального Jupyter layout проверяется отдельно. Ручная проверка выбранного
notebook frontend на целевой машине нужна для G09; fixed HTML strings не заменяют её.

### Целевая машина

| ID | Необходимое live evidence |
| --- | --- |
| G01 | GPU/driver/image TEI и G01–G04 008: реальное encoding/token policy/Qdrant/persistence |
| G02 | Все F variants в реальном search-only proxy; effective BM25 similarity scored fields, analyzers/DSL support, полные ids+fields |
| G03 | City/active и объявленные optional equality filters в обоих источниках и E5 iterations |
| G04 | Реальный common cohort, whole/title/body/chunk representations, E2/E3 same generation, E5 M read-back |
| G05 | Ручная сессия всех десяти, positive/negative contrasts, full SERP и winner passage |
| G06 | Relevance judgments/held-out evidence отдельно от ANN document-level recall |
| G07 | N/M/token/truncation/chunk/calls и RAM/VRAM/disk, build cost и warm/cold durations |
| G08 | Для каждого основного механизма — реальный active-path case и observed admission/order evidence против контроля |
| G09 | Notebook в JupyterLab или VS Code: сессия 15.5, оба вида сравнения, полные карточки, all-ten navigation, смена query/filters и explicit save |

Ни один G criterion не закрывается локальным fixture. Качество не считается
улучшенным из факта существования preset, корректного parse или успешного HTTP.
До live evidence речь идёт о подготовленной исследовательской матрице.
G08 требует продемонстрировать механизм, а не обязательное улучшение метрики:
может оказаться, что policy систематически ухудшает качество. Это валидный
исследовательский вывод. HTTP stub ranks и cosine oracles не закрывают G08.

## 21. Порядок возможностей и условия переноса

**Первый milestone:** ручной filtered search/compare и diagnostics, F1/F2/E1,
общий cohort audit, strict-core и пять основных filter contrasts.
Exit evidence — исследователь повторяет query/filters и читает полные карточки
через настоящий composition path с локальным HTTP; уже доступны notebook setup,
rank lists/union matrix, snapshot card и явное сохранение. В milestones выбирается
реализованный набор явно, отсутствующие варианты не подменяются существующими.

**Второй milestone:** F3/F4/F5/E2/E3/E4, soft/decomposition policies,
веточная provenance, общий encode E4 и complete comparison/batch export.
Exit evidence — девять вариантов работают, сессия 15.5 проходит локально и обе
fusion families проверены на отказах.

**Третий milestone:** E5 source v2/build/chunk audit/iteration/max diagnostics.
Exit evidence — все десять доступны; concentration/Unicode/filter/publication
boundaries и общие gates проходят. Только это закрывает локальную поставку 009.

На целевой машине пользователь предоставляет реальные URL/auth/mapping/ids,
scalar values, corpus/cohort, выбранную GPU, pinned совместимый image,
внешний Qdrant и storage/cache. Ни точная GPU, ни SLA сейчас не выдумываются.
Перед builds/downloads требуется disk budget, учитывающий минимум whole/title/
body generations и M chunk points с повторённым payload, а также выбранные recipe
абляции. E1/E2/E3 переиспользуют whole generation. Строить все одновременно не обязательно;
последовательность и coexistence сохраняемых generations фиксируются в evidence.

На текущем VPS при подготовке spec свободно около 4.9 GiB, занято 96%.
Реализация проверяется локальными Python/HTTP fixtures без установки dependencies,
model/image downloads, реального indexing или live measurements. Локальные
синтетические builds не являются запуском OpenSearch, Qdrant или TEI.
