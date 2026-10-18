# 004A. Обязательный контент jobboard-эксперимента

Статус: **нормативное приложение к [spec 004](004-end-to-end-search.md)**.
Дата: 2026-09-26. Начальное содержимое материализовано в Hydra YAML и test fixtures;
проверки читают поставляемые файлы. Это нормативное описание стартовой версии. Нельзя принять волну, реализовав parser с пустым lexicon, demo на
одном слове или комментарий «пользователь потом наполнит» вместо содержимого ниже.

Все записи — локально курируемый starter `jobboard-seed-v1`, не выгрузка ESCO,
не production-таксономия и не статистически проверенный benchmark. Чувствительные
данные и придуманные production ids отсутствуют. Реальный mapping и реальные
judgments по-прежнему предоставляет доступный источник; локальная поставка
наполняется независимо от него.

## A1. Материализация и единый источник данных

```text
configs/jobboard/lexical/
  lexicons/
    ru-en.yaml                       # manifest/version + defaults категорий
    roles/ru-en.yaml
    skills/ru-en.yaml
    seniority/ru-en.yaml
    work_modes/ru-en.yaml
    locations/ru-en.yaml
    protected/default.yaml
    syntax/conservative.yaml
  parsing/
    rules.yaml
    no_entities.yaml
    roles_skills.yaml
  retrieval/
    lexical.yaml
    parsed.yaml
    fuzzy.yaml
    no_phrase.yaml
    no_aliases.yaml
    no_topical_gate.yaml
    strict_coverage.yaml
  source/
    basic.yaml
    fixture.yaml
  cases/
    synthetic.yaml
    fixture-smoke.yaml
```

Файлы содержат полные данные и исполняемые `_target_` из карты владельцев 004.
В tests остаются ожидаемые результаты и HTTP-сценарии, а не вторая независимая
копия словаря. Синтетические вакансии можно хранить как authored fixture в
`tests/fixtures/jobboard/vacancies.json`; контент fixtures не является runtime
реализацией. Все Python-реализации, в отличие от тестов, остаются в `src`.

В `ru-en.yaml` manifest содержит `version: jobboard-seed-v1`, `source: local-curation`
и Hydra defaults по категориям. Каждая категория содержит `entries`. Одна запись:

```yaml
id: role.backend_developer
canonical: backend developer
surfaces:
  - backend developer
  - backend engineer
  - бэкенд разработчик
  - разработчик бэкенда
alternatives:
  - text: backend engineer
    relation: curated_equivalent
  - text: бэкенд разработчик
    relation: curated_translation
ambiguous_surfaces: []
requires_any: []
```

Литерал исходного совпадения и canonical добавляет parser; alternatives — только
дополнительные значения. Поля строки явно заданы; не использовать string→Any bag
с произвольными ключами. В каждой группе `kind` задаётся в конфиге стадии, а не
угадывается из имени класса. Допустимы только категории spec 004.

### Как читать таблицы ниже

Каждая строка обязательна. ID имеет префикс категории. Canonical и все формы
распознавания входят в surfaces; повторения после нормализации удаляются. В первых
двух таблицах формы разделены на RU и EN. Материализация alternatives выполняется
детерминированно: остальные EN forms как `curated_equivalent`, затем RU forms как
`curated_translation` для EN canonical; при RU canonical порядок обратный.
Это именно вручную утверждаемые гипотезы starter-набора, не автоматический перевод.
Canonical/literal не дублируются в alternatives; неоднозначные сокращения из A4
никогда не становятся expansion targets.

Связанные профессии/навыки отсутствуют в alternatives: из backend не выводится Python,
из data scientist — data engineer, из Java — JavaScript. Дополнительные формы можно
добавлять только с тем же смыслом записи и новой версией контента при его изменении.
Размер YAML не является самоцелью: обязательны перечисленные значения и edge cases.

## A2. Начальные профессии и роли

| ID | Canonical | RU forms | EN forms |
| --- | --- | --- | --- |
| role.backend_developer | backend developer | бэкенд разработчик; разработчик бэкенда | backend engineer; back-end developer |
| role.frontend_developer | frontend developer | фронтенд разработчик; разработчик фронтенда | frontend engineer; front-end developer |
| role.fullstack_developer | full stack developer | фулстек разработчик; fullstack разработчик | fullstack developer; full-stack developer |
| role.android_developer | android developer | андроид разработчик; разработчик android | android engineer |
| role.ios_developer | ios developer | ios разработчик; разработчик ios | ios engineer |
| role.devops_engineer | devops engineer | devops инженер; девопс инженер | devops |
| role.sre | site reliability engineer | инженер по надежности сервисов; инженер по надёжности сервисов | sre |
| role.data_engineer | data engineer | дата инженер; инженер данных | data engineering specialist |
| role.data_scientist | data scientist | специалист по data science; дата сайентист | data science specialist |
| role.data_analyst | data analyst | аналитик данных | data analytics specialist |
| role.business_analyst | business analyst | бизнес аналитик; бизнес-аналитик | business analysis specialist |
| role.system_analyst | systems analyst | системный аналитик | system analyst |
| role.manual_qa | manual qa engineer | тестировщик ручного тестирования; ручной тестировщик | manual tester; manual testing engineer |
| role.automation_qa | test automation engineer | инженер по автоматизации тестирования; автоматизатор тестирования | qa automation engineer; automation tester |
| role.security_engineer | information security engineer | инженер информационной безопасности; инженер по информационной безопасности | security engineer; cyber security engineer |
| role.sysadmin | system administrator | системный администратор; сисадмин | sysadmin; systems administrator |
| role.project_manager | project manager | менеджер проектов; руководитель проектов | project management specialist |
| role.product_manager | product manager | продакт менеджер; менеджер продукта | product management specialist |
| role.ux_designer | ux designer | ux дизайнер; дизайнер пользовательского опыта | user experience designer |
| role.support | technical support specialist | специалист технической поддержки; специалист техподдержки | technical support engineer; helpdesk specialist |
| role.accountant | accountant | бухгалтер | accounting specialist |
| role.recruiter | recruiter | рекрутер; специалист по подбору персонала | recruitment specialist |
| role.sales_manager | sales manager | менеджер по продажам | sales representative |
| role.electrician | electrician | электрик; электромонтер; электромонтёр | electrical technician |
| role.nurse | nurse | медицинская сестра; медицинский брат; медсестра; медбрат | registered nurse |
| role.driver | driver | водитель | professional driver |
| role.warehouse_worker | warehouse worker | работник склада; сотрудник склада | warehouse operative |

У некоторых названий может различаться scope у работодателей. Например, sales
manager не обязательно people manager, registered nurse зависит от лицензии страны.
Starter не делает из aliases условий лицензии/допуска; такие расхождения обязательно
попадают в ручной review перед production применением. Нельзя заменять этот review
ссылкой на SOTA-статью.

Короткие «разработчик», «инженер», «аналитик», «менеджер» не привязываются к одной
узкой роли. Они остаются literal/residual. `backend` и `frontend` без полного title
можно добавить отдельными recognition forms в этот seed с соответствующей ролью;
для этого минимального набора такие сокращения **включены** в две первые записи.
Они не добавляют связанные навыки. В отличие от них одиночное `full stack` без
слов developer/разработчик остаётся обычным текстом в v1.

## A3. Начальные навыки и технологии

| ID | Canonical | RU/написание | EN/дополнительные формы |
| --- | --- | --- | --- |
| skill.python | Python | питон; пайтон | python |
| skill.java | Java | джава | java |
| skill.javascript | JavaScript | джаваскрипт | javascript; js |
| skill.typescript | TypeScript | тайпскрипт | typescript; ts |
| skill.c | C | язык си | c |
| skill.cpp | C++ | си плюс плюс | c++ |
| skill.csharp | C# | си шарп | c#; c sharp |
| skill.dotnet | .NET | дотнет | .net; dotnet |
| skill.go | Go | голанг | go; golang |
| skill.r | R | язык r | r |
| skill.sql | SQL | эс ку эль | sql |
| skill.postgresql | PostgreSQL | постгрес | postgresql; postgres |
| skill.mysql | MySQL | май эскьюэль | mysql |
| skill.redis | Redis | редис | redis |
| skill.mongodb | MongoDB | монгодб | mongodb; mongo db |
| skill.docker | Docker | докер | docker |
| skill.kubernetes | Kubernetes | кубернетес | kubernetes; k8s |
| skill.linux | Linux | линукс | linux |
| skill.git | Git | гит | git |
| skill.kafka | Apache Kafka | кафка | apache kafka; kafka |
| skill.airflow | Apache Airflow | эйрфлоу | apache airflow; airflow |
| skill.spark | Apache Spark | апач спарк | apache spark |
| skill.tensorflow | TensorFlow | тензорфлоу | tensorflow |
| skill.pytorch | PyTorch | пайторч | pytorch |
| skill.react | React | реакт | react; react.js |
| skill.vue | Vue.js | вью джей эс | vue.js; vuejs |
| skill.angular | Angular | ангуляр | angular |
| skill.nodejs | Node.js | нод джей эс | node.js; nodejs |
| skill.excel | Microsoft Excel | эксель | microsoft excel; excel |
| skill.onec | 1С | 1с; один эс | 1c |

Таблица — стартовая поисковая гипотеза, включая разговорные формы. Перечисленные
формы обязательны для теста границ/alias, но не гарантируют полезности в каждом
корпусе. Например, общий `spark` без Apache в v1 не расширяется, чтобы не смешивать
другие значения; SQL не расширяется в PostgreSQL и наоборот. Канонический skill
не означает доказанную квалификацию вакансии.

Направление translation в таблице с RU canonical `1С` — специальный объявленный
вариант `1c` ↔ `1с`; **только в этой записи**. Нельзя глобально менять похожие буквы
C/С во всём тексте. Стартер не обещает поддержку любых похожих Unicode-глифов.

## A4. Неоднозначные сокращения, границы и protected terms

Добавить recognition forms и ambiguity rules к указанным записям:

| Surface | Записи | Правило v1 |
| --- | --- | --- |
| pm | role.project_manager, role.product_manager | Всегда ambiguous для сокращения; не выбирать по порядку YAML |
| ba | role.business_analyst | Ambiguous без полной фразы business analyst/бизнес аналитик в том же запросе |
| qa | role.manual_qa, role.automation_qa | Ambiguous; QA не доказывает manual либо automation |
| go | skill.go | Unambiguous только при одном из developer, engineer, programmer, разработчик, программист, backend; иначе literal |
| r | skill.r | Unambiguous только при data, analyst, scientist, аналитик, статистика, язык, programming; иначе literal |
| c | skill.c | Unambiguous только при developer, programmer, разработчик, программист, язык; «водитель категории C» не включает skill C |
| ts | skill.typescript | Unambiguous только при typescript, developer, frontend, разработчик; иначе literal |
| js | skill.javascript | Unambiguous только при javascript, developer, frontend, разработчик; иначе literal |

`requires_any` — конечный список literal tokens/phrases в **том же normalized query**.
Проверяются границы, а не substring; это не произвольный regex и не результат другого
детектора. Для forms без ambiguity requirement контекст не требуется. Конфликт
нескольких ids на одном span имеет приоритет над таким уточнением: общий `pm`/`qa`
остаётся ambiguous даже рядом с developer. Полная фраза рядом распознаётся отдельно.

`protected/default.yaml` содержит в объявленном порядке:

```text
C++, C#, .NET, Node.js, Vue.js, React.js, 1C, 1С,
C, Go, R, SQL, QA, ML, AI, JS, TS, SRE, K8s
```

Normalization сохраняет смысловые символы; сравнение forms учитывает согласованный
регистр. Protected не означает «unambiguous» и не создаёт hint само по себе.
Все перечисленные формы и числовые/смешанные ids исключаются из fuzzy.
Обязательные отрицательные примеры: Go не матчится внутри Google, C# не равен C++,
Java не равна JavaScript, `1С` не приводит к замене всех кириллических С на C.

`syntax/conservative.yaml` содержит unsupported standalone markers:
`или`, `or`, `не`, `not`, `без`, `without`, `кроме`, `except`.
Whitelist: `без опыта`, `без коммерческого опыта`, `without experience`,
`no experience required`. Последняя фраза распознаётся положительно как entry level;
`no` дополнительно включается в unsupported markers вне whitelist. Сначала применяется
полная whitelist-фраза, затем проверяются оставшиеся markers. Для unsupported syntax
отключаются concept path, expansions, inferred attributes и fuzzy **целиком**.

Это сохраняет literal-ввод, но не исполняет логическое отрицание. В частности,
`без ночных смен` не превращается в положительный hint про ночные смены.

## A5. Уровень, формат работы, география

| ID | Canonical | Все формы распознавания/варианты |
| --- | --- | --- |
| level.intern | intern | intern; internship; стажер; стажёр; стажировка |
| level.trainee | trainee | trainee; ученик |
| level.junior | junior | junior; jr; джуниор; младший |
| level.middle | middle | middle; mid-level; мидл; миддл |
| level.senior | senior | senior; sr; сеньор; синьор; старший |
| level.lead | lead | lead; team lead; тимлид; ведущий |
| level.entry | entry level | entry level; без опыта; без коммерческого опыта; without experience; no experience required |
| mode.remote | remote | remote; удаленно; удалённо; удаленка; удалёнка; удаленная работа; удалённая работа |
| mode.hybrid | hybrid | hybrid; гибрид; гибридный формат |
| mode.office | office | office; onsite; on-site; офис; работа в офисе |
| place.moscow | Москва | Москва; в Москве; Moscow |
| place.saint_petersburg | Санкт-Петербург | Санкт-Петербург; Санкт Петербург; в Санкт-Петербурге; СПб; Saint Petersburg; St Petersburg |
| place.kazan | Казань | Казань; в Казани; Kazan |
| place.novosibirsk | Новосибирск | Новосибирск; в Новосибирске; Novosibirsk |
| place.berlin | Berlin | Berlin; Берлин; в Берлине |
| place.london | London | London; Лондон; в Лондоне |

Aliases этих категорий участвуют только как разрешённые soft text signals.
Записи levels не сливаются: junior ≠ «без опыта», lead ≠ гарантированный people
manager. Для entry фразы с отрицанием разрешены только как whitelist целиком.
У remote нет неявной страны, часов пояса, employment type или разрешения на работу.
Location map не содержит coordinates и не исполняет geo distance.

Москва/Лондон не означают mapping `city.keyword`. Неизвестный топоним остаётся текстом.
Языковое разнообразие dictionary не является разрешением хранить locale-specific
состояние в пользовательском context без задания пользователя.

## A6. Заполненные retrieval и parsing profiles

Все значения — начальная гипотеза v1; они заданы в поставляемом YAML.
Параметры относительных весов не означают калиброванную вероятность или верхнюю
границу вклада в score. Значения из разных таблиц не должны дублироваться в Python.

| Значение | lexical | parsed | fuzzy |
| --- | --- | --- | --- |
| Parser | no_entities | rules | rules |
| Field weights | title=4, остальные разрешённые text=1; без title все=1 | те же | те же |
| best_fields tie_breaker | 0.1 | 0.1 | 0.1 |
| literal minimum_should_match | `2<75%` | `2<75%` | `2<75%` |
| concept coverage | path отсутствует | все при n≤2, ceil(0.75n) иначе | то же для strict concept path |
| dis_max interpretation/alias tie_breaker | 0 / 0 | 0 / 0 | 0 / 0 |
| alias weight | не применяется без hints | 0.7 | 0.7 |
| title phrase boost / slop | 2 / 0 | 2 / 0 | 2 / 0 |
| full-query phrase на прочих полях | 0 | 0 | 0 |
| seniority/work_mode/location weights | hints отсутствуют | 0.2 / 0.2 / 0.2 | 0.2 / 0.2 / 0.2 |
| topical_gate | off без подтверждённых hints | any_confirmed_topic | any_confirmed_topic |
| fuzzy.enabled | false | false | true |
| fuzzy.distance / minimum_length | не используется | не используется | 1 / 5 |
| fuzzy.prefix_length / max_expansions | не используется | не используется | 1 / 25 |

Изменения ablation относительно соответствующего полного профиля:

- `no_phrase`: lexical settings, title phrase boost=0.
- `no_aliases`: parsed settings, aliases выключены; literal и canonical распознанного
  понятия совпадают с исходной surface для retrieval, иначе canonical сам стал бы alias.
- `no_topical_gate`: parsed settings, topical_gate=off.
- `strict_coverage`: parsed settings, все concept groups обязательны. Literal path
  всё ещё альтернативен; название не обещает глобальный AND всего запроса.
- `roles_skills`: parser имеет только role/skill stages; normalization/protected terms
  те же. `no_entities`: stages пусты. Каждый ablation выключает ровно названный механизм.

Общие budgets: candidate_depth=100, final_k=20, evaluation_k=10;
max_input_characters=512, max_concept_groups=16, max_alternatives_per_concept=4
(включая literal), max_alternatives_total=48, max_query_leaves=128.
RRF rank_constant=60; равные веса веток, последовательное исполнение.

B0 baseline использует прежний OpenSearchTransformer и те же выбранные поля.
Для данного стартового сравнения его field boosts фиксируются 1 для всех полей;
B1 lexical вводит 4:1 и phrase/MSM. Дополнительная ablation `no_phrase` отделяет
phrase от остальных изменений; для измерения только поля добавить вариант weights=1.
Фиксировать фактические settings вместе с результатом, не переименовывать B0 в
«улучшенный baseline» после того, как его параметры подогнали под результат.

### Матрица выбора профилей и graph

`graph/lexical.yaml` содержит готовые именованные pipelines baseline, lexical,
parsed и fused. Fused получает **leaf** lexical/parsed с полной глубиной 100,
затем RRF и финальный top-k; нельзя сослаться на уже обрезанные pipelines.
`graph/fuzzy.yaml` — заполненный opt-in вариант с pipeline fuzzy и прежними целями.
Он добавляет fuzzy settings/leaf по явному defaults, а не держит скрыто включённую
третью RRF-ветку. Все имена/циклы проверяются существующим registry.

Default wrapper 001 выбирает parsed; wrapper 002 сравнивает baseline/lexical/parsed.
Fused/fuzzy — обязательные реализованные профили этой локальной волны с собственными
тестами, но необязательный выбор пользователя при запуске. «Выключен по умолчанию»
не означает «оставить TODO вместо реализации».

## A7. Синтетические вакансии: полный исходный набор

Ниже **вымышленные** документы `j01`–`j20`, не результаты реального поиска.
`_id` равен ID строки; `_source` содержит в точности title, description, city, remote.
City — keyword-код fixture mapping, remote — boolean «полностью удалённо»; гибрид=false.
Текст в таблице материализуется целиком, не заменяется lorem ipsum или одним словом.
Наличие этих полей в fixture не доказывает их наличие в реальном индексе.

| ID | title | description | city | remote |
| --- | --- | --- | --- | --- |
| j01 | Senior Python backend developer | Разработка серверных API на Python и PostgreSQL. Старший разработчик бэкенда, полностью удалённая работа, команда в Москве. | moscow | true |
| j02 | Java backend developer | Backend engineer для сервисов на Java и Apache Kafka. Middle, работа в офисе в Москве. | moscow | false |
| j03 | Junior JavaScript frontend developer | Разработка пользовательского интерфейса на JavaScript и React. Джуниор, удалённая работа из Санкт-Петербурга. | saint_petersburg | true |
| j04 | C++ developer | Разработка системного ПО на C++ и Linux. Офис в Санкт-Петербурге, требуется опыт промышленной разработки. | saint_petersburg | false |
| j05 | Senior C# .NET backend developer | Серверная разработка на C# и .NET, база PostgreSQL. Гибридный формат в Москве, старший специалист. | moscow | false |
| j06 | Golang backend developer | Разработка сервисов на Go, Docker и Kubernetes. Полностью удалённо, команда в Казани. | kazan | true |
| j07 | Project manager | Руководитель проектов внедрения корпоративного ПО. Планирование сроков и координация команды, офис в Москве. | moscow | false |
| j08 | Product manager | Менеджер продукта в финтехе. Исследование пользователей и приоритизация продуктовых задач, удалённая работа в Санкт-Петербурге. | saint_petersburg | true |
| j09 | Junior data analyst | Аналитик данных, SQL и Microsoft Excel. Рассматриваем без опыта, обучение в команде, удалённо из Москвы. | moscow | true |
| j10 | Бухгалтер 1С | Бухгалтерский учёт в 1С и сверка документов в Excel. Работа в офисе в Казани, нужен опыт ведения учёта. | kazan | false |
| j11 | Водитель категории C | Перевозка грузов по городу, необходимы водительские права категории C. Работа в Москве. | moscow | false |
| j12 | Медицинская сестра | Медицинская сестра процедурного кабинета. Нужен подтверждённый профессиональный допуск, работа в клинике в Москве. | moscow | false |
| j13 | Sales manager | Менеджер по продажам B2B, общение с клиентами. Полностью удалённая работа, команда в Москве. | moscow | true |
| j14 | Senior Python test automation engineer | Автоматизация тестирования на Python, поддержка тестов API. Офис в Москве, старший инженер по тестированию. | moscow | false |
| j15 | Senior recruiter | Подбор специалистов и проведение интервью. Удалённо, московская команда, старшая позиция. | moscow | true |
| j16 | Data scientist | Модели машинного обучения на Python и PyTorch. Удалённая работа из Москвы, фокус на исследованиях данных. | moscow | true |
| j17 | Technical support specialist | Специалист технической поддержки пользователей Linux. Гибридный формат в Берлине. | berlin | false |
| j18 | Электрик | Обслуживание электрического оборудования и диагностика неисправностей. Работа на объекте в Новосибирске. | novosibirsk | false |
| j19 | UX designer | Проектирование пользовательского опыта и исследование интерфейсов. Гибридный формат, команда в Лондоне. | london | false |
| j20 | Работник склада | Приёмка, размещение и сборка товаров. Можно без опыта, работа на складе в Казани. | kazan | false |

Source fixture имеет text fields title/description, title_field=title,
term_fields={city: keyword, remote: boolean}, index=fixture-vacancies. HTTP fixture
подставляет свой временный URL; production source/basic остаётся отдельным.
Fixture не создаёт индекс в OpenSearch и не делает такой side effect при pytest.

## A8. Query cases и полная разметка замкнутого корпуса

Все 24 cases ниже — synthetic contract cases. Они проверяют заданную семантику и
workflow, **не являются held-out benchmark реального качества**. Production dev/test
split и независимые judges описаны в 004 и нужны при работе с настоящим источником.

Обозначение таблицы: указанные пары ID:grade имеют явную оценку; **каждый другой
ID из фиксированного множества j01…j20 явно оценён в 0 автором этого fixture**.
Это компактная запись полной матрицы 24×20. При материализации `relevance` содержит
все 20 ids в каждом case. Никакого runtime default=0 для новых ids нет. `j21` или
реальный неизвестный id всегда остаётся missing judgment и должен дать ошибку.

Например, **полностью заполненная** запись q01 для списка `cases/synthetic.yaml`:

```yaml
- _target_: src.experiments.testing.compare.ComparisonCase
  key: q01
  data:
    _target_: src.core.transformers.data.PipelineData
    context: {}
    filters:
      - _target_: src.domain.request.filters.QueryFilter
        text: python backend developer
  relevance:
    j01: 3
    j02: 0
    j03: 0
    j04: 0
    j05: 0
    j06: 0
    j07: 0
    j08: 0
    j09: 0
    j10: 0
    j11: 0
    j12: 0
    j13: 0
    j14: 0
    j15: 0
    j16: 0
    j17: 0
    j18: 0
    j19: 0
    j20: 0
```

Этот список передаётся существующему CompareExperiment напрямую. Не добавлять
вместо него envelope с metadata, требующий нового loader. Остальные 23 записи
собираются в том же формате по следующей таблице; metrics/cutoff задаёт профиль.

| Case | Query | Grade > 0 | Что проверяет |
| --- | --- | --- | --- |
| q01 | python backend developer | j01:3 | Роль + skill, QA и data science не эквивалентны backend |
| q02 | java developer | j02:3 | Java отдельно от JavaScript |
| q03 | javascript frontend | j03:3 | Два совместимых hints и сокращённый role label |
| q04 | разработчик C++ | j04:3 | Значимая пунктуация |
| q05 | C# .NET backend | j05:3 | Два protected навыка |
| q06 | golang backend developer | j06:3 | Явная форма Go без ambiguity |
| q07 | project manager | j07:3 | Project отдельно от product |
| q08 | product manager | j08:3 | Product отдельно от project |
| q09 | аналитик данных без опыта | j09:3 | Whitelist без опыта, junior не выводится автоматически |
| q10 | бухгалтер 1С | j10:3 | Роль + точечный межалфавитный alias |
| q11 | водитель категории C | j11:3 | C не язык программирования в таком контексте |
| q12 | медицинская сестра Москва | j12:3 | Не-IT роль, мягкий location hint |
| q13 | менеджер по продажам удаленно | j13:3 | Многословная роль и work mode |
| q14 | python test automation | j14:3 | Автотесты отдельно от backend |
| q15 | senior recruiter remote Moscow | j15:3 | Атрибуты не заменяют профессию |
| q16 | data scientist python | j16:3 | Data scientist отдельно от analyst/engineer |
| q17 | technical support Linux | j17:3 | Роль в title, skill в description |
| q18 | электрик Новосибирск | j18:3 | Другая отрасль и русский location |
| q19 | UX designer London | j19:3 | RU/EN profile, многословная роль |
| q20 | работа на складе без опыта | j20:3 | Неизвестные residual слова сохраняются |
| q21 | PM | j07:2; j08:2 | Не выбирать одну из двух правдоподобных ролей |
| q22 | python | j01:3; j14:3; j16:3 | Skill-only запрос допускает несколько профессий |
| q23 | python remote | j01:3; j16:3; j14:1 | Мягкое предпочтение: office слабее remote, но grade задан явно |
| q24 | энтомолог | нет | Успешная пустая/нулевая релевантность, unknown role не ошибка parser |

Отдельные mutation-free parser/DSL cases, не выводимые из grades:

- `Go` и `R` без контекста — ambiguous/literal; `Go developer` допускает skill.go.
- `python или java` и `не продажи` отключают structured path целиком.
- `без опыта аналитик` допускает whitelist, `без ночных смен` её не допускает.
- `Senior Python developer remote Moscow` не допускает j15 через одни атрибуты.
- `JavaScript` не добавляет skill.java, `Google` не добавляет skill.go.
- `разработчки python` получает bounded fuzzy только в fuzzy-профиле.
- Только stopwords/пунктуация не превращаются в match_all.
- Запрос длиной 513 при budget 512 отклоняется до network в новых leaves.

Обязательный case явного фильтра: query=`python`,
filters=[QueryFilter("python"), TermFilter("remote", true)],
relevance={j01:3,j16:3,остальные из j01…j20:0}. Для UserPropertyFilter эквивалент:
context={selected_city: "moscow"}, filter(field="city", property="selected_city").
В поставленном `cases/explicit-filters.yaml` это два отдельных case: remote=true
имеет j01/j16=3, а city=moscow — j01/j14/j16=3 (j14 находится в Москве).
Остальные fixture ids явно оценены нулём в каждом case.
Это проверка объявленной fixture-схемы. Она не превращает parser-hint Москва
в обязательный filter и не вводит новые атрибуты в production mapping.

## A9. Полностью определённый composed HTTP smoke

`cases/fixture-smoke.yaml` содержит q01 и q02 с полной relevance из A8.
Для этого тестового профиля final_k=2, evaluation_k=2; server fixtures возвращают
следующие **контролируемые** списки и scores. Это не вычисленный BM25 и не эмуляция
анализатора. Цель — контракт HTTP/composition/ranking/evaluation/export.

| Case | lexical hits | parsed hits | Fused final ids |
| --- | --- | --- | --- |
| q01 | j14:5, j01:4 | j01:9, j16:7 | j01, j14 |
| q02 | j03:5, j02:4 | j02:9, j05:7 | j02, j03 |

Все hits содержат `_source` из A7; `timed_out=false`, `_shards.failed=0`.
Перед ответом fixture проверяет index path, общий query и явные filters; variant
отличает по структуре тела, а не по скрытому заголовку или production-only flag.
Не изобретать fake поле `variant` в запросе OpenSearch ради теста.

При rank_constant=60 документ на втором месте lexical и первом parsed получает
1/62+1/61, одиночный первый lexical — 1/61. Поэтому указанный fused порядок
определён независимо от оригинальных scores 5/9. Для обеих строк:

- lexical NDCG@2 = 1/log2(3) ≈ 0.6309297536;
- parsed NDCG@2 = 1;
- fused NDCG@2 = 1;
- среднее по двум cases совпадает с соответствующим значением, population stddev=0.

B0 проверяется отдельно с ожидаемым legacy body; не приписывать ему автоматически
lexical responses без проверки запроса. Дополнительный полностью заданный regression
для candidate depth>final_k использует q01, candidate_depth=3, final_k=2:

| lexical hits | parsed hits | Ожидаемый fused result |
| --- | --- | --- |
| j14:6, j16:5, j01:4 | j02:9, j05:8, j01:7 | j01, j14 |

У j01 score=2/63, у j14=1/61. j14 выигрывает tie с первым документом второй ветки
по порядку первого появления. Раннее усечение каждой ветки до 2 удалило бы j01
из обеих, изменив и итог, и NDCG. Bodies реально запрашивают depth=3; final_k=2
применяется только после RRF. Ответы этого fixture также проверяют композицию,
а не утверждают, что BM25 на данном тексте выдаст именно эти документы.

Failure variants того же сценария: второй HTTP=500; второй ответ timed_out=true;
malformed hit; неизвестный id `outside-fixture`; несуществующее context property;
unsupported term field; overflow бюджета. Проверяются отсутствие downstream вызовов,
отсутствие final artifact и сохранность ранее успешно опубликованного retrieval-файла.

## A10. Что считается наполненной поставкой

1. Все записи A2–A5 и protected/syntax rules материализованы и доступны из shipped
   Hydra profile. Нет пустого default dictionary, сетевого скачивания при запуске,
   несуществующих targets и путей на локальные файлы автора.
2. Профили A6 полностью заполнены, включая opt-in fuzzy/fused. Различия ablations
   наблюдаемы в DSL/поведении; переключатель без эффекта не является реализацией.
3. Все 20 вакансий A7, 24 cases A8, полная замкнутая разметка, отдельный filter case
   и два HTTP smoke cases A9 сохранены как контент проверки. Никакие реальные
   вакансии и оценки не объявляются проверенными этим fixture.
4. Content checks находят дубли ids, некорректные формы/relations, неопределённые
   ссылки lexicon/profile/case и нарушения budgets. Каждая категория покрыта
   положительными и отрицательными semantic examples; проверка одного лишь count
   не заменяет тесты смысла.
5. README/документация объясняют выбор source, dictionary, parser, retrieval и cases;
   отличие synthetic от live очевидно. Пользователю для локальной проверки не нужно
   сначала написать свой словарь, но для реального индекса нужны реальные judgments.
6. Новые defaults `main.yaml` не устанавливаются. Код разложен по карте владельцев
   004; старые tests и эксперименты работают без новых параметров.

Допустимые внешние незаполненные значения: URL/auth, реальный index, реальные поля
и подтверждение их типов, реальные queries/judgments для live evidence. Это данные
целевой среды. Алгоритмы, starter lexicons, синтетические cases, ablations и README
не могут оставаться заготовками под видом этих внешних ограничений.
