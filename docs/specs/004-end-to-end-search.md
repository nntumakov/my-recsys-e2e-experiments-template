# 004. Jobboard: модульный разбор запроса и поиск поверх BM25

Статус: **локальная реализация поставлена; внешняя проверка индекса открыта**.
Дата исследования и реализации: 2026-09-26. Код, заполненные Hydra-профили и
контрактные HTTP/CLI-тесты находятся в репозитории. Spec уточняет прежний
проект 004: цель — исследовать поиск вакансий, а не довести весь framework до
абстрактной «полноценности». Specs 001–003 и текущие defaults сохраняют свой смысл.

Основные материалы:

- [004A: обязательный контент поставки](004-jobboard-content.md) — starter lexicons,
  правила, профили, синтетические вакансии, queries/judgments и свидетельства полноты.
- [Исследование современного job search](../research/2026-09-26-jobboard-search.md).
- [Проверка возможностей OpenSearch/Lucene](../research/2026-09-26-opensearch-lexical.md).
- [Текущая архитектура](../architecture.md) и [готовность проекта](../development.md).

### Перед продолжением после сброса контекста

Границы локальной реализации:
**код раскладывается по существующим слоям `src`, а поставка включает наполненный
контент, а не только интерфейсы и пустые конфиги**. Прежняя идея складывать весь
сопутствующий код в `src/experiments/jobboard/...` отменена. Ограничение «один
эксперимент» относится к продуктовой области и выбору конфигурации, не к месту
хранения всех реализаций.

Реализация прошла локальные проверки.
Продолжение работы должно читать эту spec **вместе с 004A**, AGENTS.md и architecture.
Не нужно заново исследовать базовые OpenSearch-механизмы: evidence и ссылки сохранены.
Открыты реальные версия/mapping/credentials/доступ к индексу и production judgments;
локальные fixtures не подтверждают совместимость и качество на этом индексе.
RU/EN — предварительный профиль; он заменяется конфигом, не захардкожен в общих слоях.

Рабочий Git baseline исследования — `f9f7f77`, `master`, ahead 1 относительно
локальной ссылки origin/master. Документы этой работы пока не закоммичены;
перед продолжением проверить фактический status, не считать это разрешением commit/push.
Ранее в этой сессии прошли 95 tests/Ruff/ty из `.venv`; Poetry отсутствовал в PATH.
Этот исторический результат не заменяет gates после новой реализации.

## 1. Outcome и пределы эксперимента

На существующем полнотекстовом индексе вакансий можно сравнить обычный поиск
с поиском, который разбирает запрос на профессиональные понятия, добавляет
контролируемые варианты формулировок и использует содержательные lexical-сигналы.
Состав разбора, словари, поля, веса и способы получения кандидатов выбираются Hydra.
Одинаковые pipelines доступны интерактивно и на размеченном наборе запросов.

Эксперимент отвечает на конкретные вопросы:

1. Помогает ли выделение профессий, навыков и уточнений пользовательского запроса?
2. Какие aliases, phrase boosts и настройки покрытия слов улучшают выдачу?
3. Даёт ли объединение разных lexical-выдач выигрыш относительно лучшей одиночной?
4. Где ограничение связано с нашим запросом, а где — с анализатором и данными индекса?

Это **одна папка** `configs/experiments/vacancy-search/jobboard/lexical/` и связанные
группы в `configs/jobboard/lexical/`. В репозитории используется `configs/`, а не
`config/`. Код раскладывается по существующим владельцам в `src`: доменные значения
в `domain/request`, разбор в `components/query`, DSL и поисковый transformer в
`core/transformers/opensearch`, HTTP остаётся в `integrations/opensearch`.
`experiments` отвечает за сценарий и orchestration; складывать туда всю реализацию нельзя.

Не меняются глобальная точка входа, default experiment, базовый payload и общий
смысл filters. Jobboard-специализации имеют явные имена и тематические подкаталоги,
как существующий Vacancy; их наличие внутри `domain`/`core` не делает этот сценарий
обязательным для остальных экспериментов. Направление зависимостей остаётся прежним:
домен не зависит от обработки, интеграций и экспериментов; общие модули не импортируют
конкретный эксперимент. Новая реализация размещается по роли, а не по предполагаемому
числу будущих consumers. Детальная карта владельцев закреплена в разделе 9.0.

## 2. Что здесь означает SOTA

Современные системы job search используют несколько стадий: понимание запроса,
получение кандидатов, ранжирование и оценку пользовательского результата. В частности,
LinkedIn описывает semantic retrieval, query understanding и model-based reranking;
Indeed — обучаемое matching/ranking и несколько уровней оценки. Это не доказательство,
что любой hybrid или LLM автоматически улучшает данный индекс [JB-1, JB-1b, JB-2].

| Вывод из исследования | Решение этого эксперимента |
| --- | --- |
| Современный поиск учитывает смысл запроса и несколько сигналов | Ввести явное типизированное представление разбора и измеряемые lexical-сигналы |
| Название должности, профессия и skill — разные понятия | Различать категории hints; не превращать все совпадения словаря в универсальные синонимы |
| Семантические модели требуют данных, обучения/адаптации и инфраструктуры | Dense retrieval, embeddings, LLM и cross-encoder оставить следующими отдельными опытами |
| Поведение людей полезно для обучения, но наблюдения содержат selection/position bias | Первую оценку делать по явной разметке; не выдавать клики за чистую релевантность |
| ESCO различает occupation, labels и skills, содержит альтернативные названия | Использовать как источник курируемых отношений и идентификаторов; не загружать всю таксономию в query expansion |
| Сравнение проводится на конкретной задаче и данных | Называть результат SOTA-informed lexical baseline; превосходство доказывать на своём наборе |

Исследовательские результаты поиска людей для рекрутера не переносятся численно
на поиск вакансий соискателем. Универсального доказанного «лучшего BM25 для jobboard»
нет. Все начальные веса и лимиты ниже — проверяемые гипотезы, не опубликованный optimum.

## 3. Возможности исходного индекса

### 3.1. Минимальный профиль

Обязательны search-доступ к одному индексу, хотя бы одно известное полнотекстовое
поле, `_source`, уникальный в этом индексе `_id` и стандартный ответ OpenSearch.
Предпочтительны отдельные поля заголовка и описания: например `title` и `description`.
Это примеры, реальные названия задаются конфигурацией. Если поле одно, профиль
использует его; отдельный title boost недоступен и не имитируется.

Наличие BM25, типы полей и существующие анализаторы подтверждаются владельцем индекса.
Один HTTP 200 этого не доказывает. Не предполагаются доступ к `_mapping`, `_analyze`,
`_settings`, установка plugins, изменение схемы, reindex или создание search pipelines.
Версия OpenSearch должна быть записана перед внешней приёмкой: `/latest/` в источнике
не доказывает наличие всех возможностей на конкретном сервере.

| Capability | Что можно делать | Чего не подразумевать |
| --- | --- | --- |
| Одно/несколько `text`-полей | match, multi_match, phrase, lexical boosts | Точные категории, географию и гарантированное сохранение пунктуации |
| Отдельное поле title | Усиливать title и phrase в title | Keyword-подполе `title.keyword` без подтверждения mapping |
| Подтверждённое keyword-поле | Явный term filter или opt-in exact-title бонус | Использование term по analyzed text как точного совпадения всей строки |
| Подтверждённые numeric/date-поля | Отдельная проверяемая гипотеза freshness/quality | Salary/date parsing и числовые boosts на произвольных строках `_source` |
| Несколько физических индексов | Только после решения об identity | Слияние одинаковых `_id` разных вакансий через текущий RRF |

Профиль возможностей декларативный. Если выбранный сигнал требует отсутствующего
поля, конфигурация отклоняется до HTTP; обязательное условие нельзя молча опустить.
Неиспользуемые дополнительные возможности не запрашиваются и не проверяются сетью.

Минимальный контракт `SourceProfile`: `index`, непустой список `fields` с разрешёнными
text-полями, optional `title_field` из этого списка и `term_fields` — mapping имени
в подтверждённый тип `keyword | boolean | integer | float`. В text-only профиле
`term_fields={}`. Будущие exact-title/date signals требуют отдельно объявленной
capability и не включаются в basic профиль. Это декларация владельца, не результат
автоматической инспекции сервера: локальная проверка доказывает согласованность
конфига, но не истинность mapping удалённого индекса.

`LexicalQueryConfig` задаёт weights разрешённых text-полей, best_fields tie_breaker,
literal MSM, concept coverage, `aliases_enabled`, alias weight, phrase boost/slop, веса категорий soft
hints, `topical_gate: any_confirmed_topic | off` и budgets. Основной parsed-профиль
выбирает `any_confirmed_topic`, ablation — `off`. Boosts конечны и неотрицательны; 0 отключает соответствующий
сигнал. Tie breaker находится в [0, 1], coverage ratio — в (0, 1], slop — целое
неотрицательное, budgets — целые положительные. Fuzzy settings присутствуют только
в выбранном fuzzy-профиле. Search analyzer override в первой реализации отсутствует.
Неизвестное имя параметра не игнорируется; произвольный JSON overlay не принимается.

### 3.2. Ограничения, которые query-код не исправит

BM25 `k1` управляет насыщением term frequency, `b` — нормализацией длины поля.
В стандартном Lucene BM25 defaults — `k1=1.2`, `b=0.75`; действительный индекс может
использовать другие настройки. Это параметры similarity индекса, не поля `_search`
для произвольной подстройки на каждый запрос [OS-1].

Если анализатор при индексировании потерял различие `C`, `C++` и `C#`, новый parser
не восстановит отсутствующие postings. Сохранить обозначение в клиенте необходимо,
но недостаточно. Аналогично query-time stemming не восстанавливает произвольную
совместимость с index-time analysis. По умолчанию используется search analyzer поля;
override допускается только для подтверждённого совместимого профиля [OS-2].

Качество описаний, boilerplate, дублей и токенизации — отдельные ограничения данных.
Не маскировать их бесконечным повышением boosts или расширением словарей.

## 4. Отправная точка текущего кода

Существующие registry, PipelineTransformer, BranchTransformer, RRF, TopK,
SearchExperiment, CompareExperiment и OpenSearchIntegration достаточны для внешнего
исполнения. Две реальные HTTP-ветки → RRF → top-k → EOF/export уже проверены локально.

Текущий OpenSearchTransformer строит только `multi_match` с fields/size и AND term
filters. Расширять его jobboard-условиями не нужно: отдельный JobSearchTransformer в `core/transformers/opensearch/jobboard`
использует существующую интеграцию `search(index, body)` и конвертацию ответа.

Текущие ограничения не следует забывать:

- FieldScoreExtractor меняет score, но не сортирует; TopK только срезает порядок.
  Этот эксперимент ранжирует через OpenSearch scores или RRF, а не предполагаемую
  сортировку после extractor.
- RRF использует позиции, а не величину исходного score; все ветки имеют равный вес.
- CompareExperiment требует grade для каждого документа в оцениваемом top-k.
- CLI сейчас скрывает подробности errors. Полная общая переработка диагностики
  не входит в этот эксперимент; локальные ошибки получают безопасные категории
  и проверяются программно. Улучшение общего отображения ошибок — отдельная узкая задача.

## 5. Архитектура разбора: локальная и модульная

```text
SearchExperiment / CompareExperiment
        ↓ существующий registry и PipelineData
        ├─ baseline leaf → существующий OpenSearchTransformer
        └─ jobboard leaf
             исходный QueryFilter
                    ↓
             normalizer + token view
                    ↓
             настроенные независимые detectors
                    ↓
             merge hints + bounded expansion
                    ↓
             ParsedQuery → чистый DSL compiler
                    ↓
             существующая OpenSearchIntegration → SERP
        ↓ необязательное Branch → RRF → TopK
        ↓ существующие artifacts/exporters
```

`ParsedQuery` — локальное immutable значение одного вызова JobSearchTransformer.
Он не записывается в `PipelineData.context`, `branches`, private global cache или
новый производный filter. QueryFilter сохраняет исходный текст и не переписывается.
Разбор — интерпретация запроса; он не является новым обязательным условием payload.

Ветки могут повторять дешёвый детерминированный разбор. Shared state, query cache
и изменение общего payload ради устранения этого повторения не вводятся.

### 5.1. Предлагаемый интерфейс

Основные реализованные интерфейсы (полный контракт — в соответствующих модулях):

```python
@dataclass(frozen=True)
class QueryHint:
    kind: Literal["role", "skill", "seniority", "work_mode", "location"]
    span: tuple[int, int]  # полуинтервал в normalized_text
    surface: str
    canonical: str
    alternatives: tuple[str, ...]
    rule_id: str
    status: Literal["unambiguous", "ambiguous"]


@dataclass(frozen=True)
class ParsedQuery:
    original_text: str
    normalized_text: str
    hints: tuple[QueryHint, ...]
    structured_enabled: bool = True
    protected_spans: tuple[tuple[int, int], ...] = ()


class BaseQueryStage(ABC):
    @abstractmethod
    def parse(self, normalized_text: str) -> tuple[QueryHint, ...]: ...


class ModularQueryParser:
    def parse(self, raw_text: str) -> ParsedQuery: ...


# Чистая функция: без HTTP, изменения payload и чтения окружения.
def compile_query(
    parsed: ParsedQuery,
    request: Request,
    context: Mapping[str, JSONValue],
    source: SourceProfile,
    config: LexicalQueryConfig,
    size: int,
) -> dict[str, JSONValue]: ...
```

Normalizer, stages, lexicons и query settings передаются Hydra напрямую.
Один маленький stage interface оправдан реальной подстановкой детекторов.
Второй registry, orchestration DSL, plugin loader или каскад forwarding wrappers не нужны.
`QueryHint` и `ParsedQuery` принадлежат `domain/request/jobboard.py`; они не добавляют
новых полей общему PipelineData и не меняют контракты остальных request types.

Порядок одного вызова:

1. Проверить один непустой QueryFilter и ограничения размера входа.
2. Нормализовать текст один раз; каждый detector читает тот же текст.
3. Объединить hints по детерминированным правилам; построить ограниченные alternatives.
4. Проверить явные filters/context и скомпилировать DSL.
5. Выполнить search, полностью конвертировать ответ и только затем заменить `data.items`.

Детекторы не редактируют текст друг друга и не выполняют сеть. Поэтому отключение
location-stage не меняет смысл входа skill-stage. Ошибка стадии прекращает обработку;
отсутствие совпадений — нормальный результат, а не ошибка.

### 5.2. Нормализация и protected lexemes

Базовая нормализация ограничена Unicode NFKC, согласованным регистром и пробелами.
Сохраняются значимые `+`, `#`, `.`, `/`, `-` и цифры в профессиональных обозначениях.
Обозначения `C++`, `C#`, `.NET`, `Node.js`, `1C`/`1С`, `Go`, `R`, `QA`, `ML`
обрабатываются до общего распознавания границ слов. Не применять ко всему тексту
правило «удалить пунктуацию» или «оставить только буквы».

Offsets hints относятся к нормализованному тексту, не к original_text после Unicode
преобразований. Исходная строка остаётся доступной отдельно. Один parser не должен
выдавать span, не соответствующий своему surface.

Лемматизатор, перевод, транслитерация и исправление раскладки не включаются незаметно.
Первый профиль предполагает RU/EN и курируемые словари; язык — настройка эксперимента,
а не ограничение всего проекта. Русскую морфологию выполняет подтверждённый analyzer
или явные ограниченные формы словаря. Автоматическая замена любого `е`/`ё` и смешанных
алфавитов допускается только отдельной измеряемой конфигурацией, сохраняя literal-вариант.

### 5.3. Модули и их ответственность

| Модуль | Результат | Ограничение |
| --- | --- | --- |
| role-stage | Спаны профессий и названий должностей, каноническая роль, одобренные aliases | `product manager` и `project manager` не становятся одной ролью по общему `PM` |
| skill-stage | Языки, технологии, инструменты и навыки | `Java` ≠ `JavaScript`; `C` ≠ `C++` ≠ `C#`; generic word `go` неоднозначен |
| seniority-stage | Явные junior/middle/senior/lead, internship и «без опыта» | Не выводит seniority из возраста, зарплаты или стажа без отдельного контракта |
| work-mode-stage | remote/удалённо, hybrid, office из явных фраз | Удалённость не означает любую страну, график или тип договора |
| location-stage | Только известные однозначные формы топонимов | «Москва» не доказывает наличие `city.keyword`; неоднозначный топоним остаётся ambiguous |
| Bounded expansion | Направленные варианты распознанного понятия | Не обходит таксономию и не добавляет все связанные occupations/skills |

Normalizer плюс `stages: []` — полноценный профиль без entity parsing для ablation.
Детекторы можно включать и заменять через конфиг. Для первой реализации достаточно
детерминированных правил и небольших словарей; нейросетевой parser может позже
реализовать тот же локальный интерфейс, но его latency, privacy и failure semantics
потребуют отдельной spec.

### 5.4. Словари, неоднозначность и отношения

Каждая запись содержит стабильный id, категорию, canonical label, поверхности
распознавания, разрешённые поисковые alternatives, язык и источник/версию курирования.
Словарь не содержит произвольный DSL или исполняемые выражения.

Например, смысл записи из обязательного starter content (локальное курирование,
не импорт из ESCO; полный набор и правила материализации — в приложении 004A):

```text
id: local.role.backend_developer
kind: role
canonical: backend developer
surfaces:
  - {text: backend developer, language: en}
  - {text: бэкенд разработчик, language: ru}
alternatives:
  - {text: backend engineer, language: en, relation: curated_equivalent}
  - {text: разработчик бэкенда, language: ru, relation: curated_translation}
source: local-curation
```

В конфиге также фиксируется версия всего словаря. Окончательный YAML-контейнер
соответствует constructor parser; смысл полей выше является контрактом.
`related_skill` и broader/narrower relations не входят в допустимые retrieval
alternatives. Одно surface может соответствовать нескольким ids: это ambiguity,
а не основание выбрать первую запись. До запуска проверяются уникальность ids,
непустые формы/labels, допустимые категории и relations, версия и лимиты записей.
При заимствовании реальных данных сохраняются их attribution и условия использования;
здесь не предполагается наличие готового русского ESCO-словаря.

ESCO может подсказать occupation labels и связи, но occupation не равен конкретному
job title. Более широкое понятие, смежная профессия, навык профессии и взаимозаменяемый
alias — разные отношения [JB-3]. В частности:

- `software engineer` → `разработчик ПО` — гипотеза alias для конкретного домена;
  её полезность проверяется на RU/EN корпусе.
- `frontend developer` → `JavaScript` — связь role–skill, не синоним; автоматически
  расширять так retrieval нельзя.
- `PM`, `BA`, `аналитик`, `Go` без достаточного контекста не получают единственную
  уверенную интерпретацию. Допустим literal-поиск без расширения.
- Cross-language alternatives только из курируемых записей; запроса к переводчику нет.

Сначала выделяются protected/multiword spans, затем решаются перекрытия. Для одной
категории предпочтителен самый длинный подтверждённый span; одинаковые записи
дедуплицируются по стабильному ключу. Противоречивые трактовки получают ambiguous
status и не запускают уверенное expansion. Межкатегорийные перекрытия не должны
удваивать один и тот же retrieval-концепт; compiler выбирает literal либо согласованную
интерпретацию. Порядок YAML задаёт только стабильный tie-break, не скрытую семантику.

Лимит alternatives выбирает варианты в явном порядке: literal, canonical, aliases
по порядку словаря. Дубликаты удаляются после нормализации. Новая версия словаря
не должна недетерминированно менять запрос из-за обхода set.

### 5.5. Неизвестные слова, связки и отрицание

Неизвестные слова сохраняются. Parser не вырезает часть запроса только потому, что
она не распознана в словаре. Остаточные фрагменты участвуют в lexical evidence.

`python или java`, `не продажи`, `без ночных смен`, диапазоны зарплаты и произвольные
булевы выражения не получают выдуманную семантику от regex на одном слове.
Первый профиль распознаёт только перечисленные полные фразы вроде «без опыта».
Если связка/отрицание не входит в поддержанный набор полных фраз, **concept path,
entity expansion и inferred-attribute boosts отключаются для всего запроса**;
остаётся literal path. Та же политика применяется к fuzzy-профилю: он не пытается
исправлять такой ввод. Распознанные hints можно сохранить как ambiguous для проверки
parser, но compiler не использует их как положительные сигналы. Это консервативная
семантика первого профиля, а не fallback после исключения. Набор маркеров включает
отдельные слова `или`, `or`, `не`, `not`, `no`, `без`, `without`, `кроме`, `except`;
whitelist полных фраз вроде «без опыта» распознаётся раньше. Логический парсер
произвольных выражений не обещается.

В первой версии нет обещания строгого natural-language constraint following.
Например, «не продажи» остаётся текстовым запросом без гарантии исключения sales.
Такие запросы входят в отдельный evaluation slice; если они обязательны для задачи,
нужен следующий конкретный модуль, а не заявление, что базовый parser уже понимает их.

## 6. Разделение lexical signals и обязательных условий

Распознанные seniority, location и work mode по умолчанию добавляют **мягкие**
поисковые сигналы. Они не создают `bool.filter`/`must_not` и не становятся обязательными
структурированными ограничениями. Role/skill aliases расширяют способы текстового
совпадения, но не объявляют документ носителем гарантированного атрибута.

Явные `TermFilter` и `UserPropertyFilter` из payload сохраняют нынешнюю AND-семантику:
значение свойства должно существовать и быть поддержанным scalar; неизвестный тип
filter, null или несовместимое поле — ошибка до HTTP. QueryFilter ровно один.

Правила компиляции не допускают arbitrary JSON overlay, заменяющий обязательные
filters. Пользовательский текст передаётся значением `match`, `multi_match` или
`match_phrase`; `query_string`, wildcard query из raw ввода и исполнение синтаксиса
пользователя не используются.

Если нужны настоящие условия salary/date/geo, сначала подтверждаются mapping и
доменная семантика. Текстовое совпадение «Москва» в description нельзя выдавать
за структурированный geo filter. Нет нужной capability — соответствующий профиль
не запускается, а не выполняет более широкий поиск.

## 7. Query compiler и полезные приёмы для BM25

### 7.1. Поля и буквальный baseline

B0 — существующий OpenSearchTransformer с тем же индексом, глубиной кандидатов
и явно заданными fields/boosts. Это контрольная группа, не изменяемая при добавлении
parser. Не использовать `fields: ["*"]`: перечень полей известен и ограничен.

Для нового lexical-профиля отправная точка — `best_fields`: заголовок имеет больший
вес, длинное описание меньший, `tie_breaker` даёт небольшой вклад дополнительных
полей. `most_fields` полезен главным образом для нескольких analyzed-представлений
одного содержания; это не универсальный способ суммировать всё содержимое вакансии.
`cross_fields` нельзя назначать автоматически weighted title+body: он группирует
поля по analyzer, имеет ограничения boosts и не поддерживает fuzziness [OS-3].

Важно: `operator` и `minimum_should_match` у `best_fields` применяются внутри
field-oriented query. Совпадение профессии в title, а навыка в description не должно
ошибочно считаться неполным только из-за этого. Structured path ниже строит покрытие
по понятиям, каждое из которых может совпасть в любом разрешённом поле.

### 7.2. Покрытие понятий и controlled aliases

Compiler строит два вида lexical evidence:

1. **Literal:** полнотекстовый запрос по исходному нормализованному тексту.
2. **Concept:** группы однозначно распознанных role/skill понятий и остаточных
   содержательных фрагментов. Внутри группы literal/canonical/aliases альтернативны;
   между группами действует явное правило покрытия.

Несколько слов одного job title не превращаются в несколько независимых synonyms.
Multiword alias сопоставляется как phrase или согласованная conjunction внутри
одного поля; первый профиль выбирает phrase со slop=0. Остаточные слова не теряются.
Остаточные группы получаются из незанятых spans: protected lexeme — одна группа,
иначе последовательность Unicode букв/цифр — одна группа. Общего удаления stopwords
на клиенте нет; анализ выполняется сервером. Внутренний lexer не объявляется копией
OpenSearch analyzer. Неоднозначные hints не расширяются.
Seniority/work mode/location могут быть вынесены в soft clauses только когда
остаётся хотя бы один topical role/skill/residual anchor; иначе поиск идёт по
literal-тексту, например для запроса «удалённо».

Literal и concept paths объединяются `dis_max` с `tie_breaker=0`, чтобы альтернативные интерпретации
не суммировали бесконтрольно один и тот же смысл. Внутри alias-группы `tie_breaker=0`;
повтор одного alias не увеличивает score. При отсутствии применимого разбора
concept path отсутствует, остаётся literal path [OS-4].

При наличии однозначных положительных role/skill hints добавляется общий topical
anchor: хотя бы одна такая группа должна совпасть текстом в разрешённых полях.
Он применяется и к literal, и к concept path. Так запрос `senior python remote Moscow`
не проходит только по senior/remote/Moscow, пропустив Python. Gate не добавляет
score (например, `bool.should` с MSM=1 и `boost=0` в обязательном `must`);
ранжирование по-прежнему выполняют scoring clauses. Это проверяемая эвристика,
а не доказательство семантического соответствия: роль может совпасть, а часть навыков — нет.
Ambiguous hints в такой gate не включаются; другие однозначные hints того же запроса
можно использовать. При unsupported syntax или отсутствии подтверждённых topical
hints gate отсутствует. Отдельная ablation измеряет потери recall от этого условия.

Это lexical evidence находится в обязательном `must`. Отдельные phrase и attribute
boosts в `should` не могут самостоятельно пустить в выдачу документ без текстового
основания. Все критичные `minimum_should_match` задаются явно: у `bool` наличие
`filter` или `must` меняет default для `should` на 0 [OS-5].

Начальная гипотеза покрытия concept groups: для 1–2 групп нужны все, для 3 и более —
не меньше `ceil(0.75 * n)`. Считаются группы, а не количество aliases. Это правило
compiler, отдельное от analyzer-dependent `minimum_should_match` внутри multi_match.
Для literal path исходная гипотеза — выражение `2<75%`; сравнить с более мягким OR.
Покрытие — tuning-параметр конкретного профиля, а не новый глобальный default.

У OpenSearch положительный процент округляется **вниз**: literal MSM для трёх
analyzed terms допускает два, а наш concept coverage для трёх групп требует три.
Различие намеренное: альтернативный literal path сохраняет более мягкий recall [OS-12].
Concept coverage действует только внутри concept path, не на весь dis_max;
общий topical gate, когда применим, действует на оба пути.

Итоговая форма compiler, а не ещё один конфигурационный язык:

```text
bool
  filter: явные TermFilter / UserPropertyFilter
  must:
    - topical gate с нулевым score, если применим
    - dis_max(literal, concept если доступен, fuzzy только в fuzzy-профиле)
  should:
    - дедуплицированные title/full-query phrase signals
    - допустимые soft attributes
  minimum_should_match: 0
```

### 7.3. Phrase и title boosts

Exact analyzed phrase в title получает дополнительный вес; допустим небольшой
`slop` как отдельная ablation. `match_phrase` использует analyzer и позиции, поэтому
не означает байтовое равенство всей строки. Whole-title exact bonus допустим только
при подтверждённом keyword-поле и правилах его normalizer [OS-6].

Различать сигналы: роль в title, skill в любом текстовом поле, полная фраза запроса,
seniority рядом с ролью. Не усиливать все слова во всех полях несколькими одинаковыми
clauses. Если запрос целиком совпал с одной ролью, одинаковые phrase clauses
дедуплицируются до отправки.

### 7.4. Опечатки — отдельная ограниченная гипотеза

Fuzzy-профиль по умолчанию **не выбран**. Он применяется только к разрешённым
длинным буквенным фрагментам, не к `C++`, `C#`, `Go`, `R`, `.NET`, ids, цифрам,
аббревиатурам и подтверждённым словарным labels.

Начальный вариант: edit distance 1 для неизвестных буквенных слов длиной от 5,
`prefix_length=1`, `max_expansions=25`. Ошибка в первой букве при этом может остаться
неисправленной — это намеренное ограничение стоимости. AUTO допускается отдельным
опытом: его пороги не равны утверждению, что fuzziness включён по умолчанию [OS-7].

Fuzzy не добавляется к `phrase`/`cross_fields` и не размножается поверх всех aliases.
Он сохраняет обязательные filters и ограничение размера кандидатов. Высокий расход
на fuzzy нельзя скрывать успешным средним временем коротких запросов.

В fuzzy-профиле fuzzy path — альтернативный путь **внутри обязательного lexical
evidence**, а не optional should при неизменном strict must. В этом пути только
допустимые неизвестные фрагменты получают analyzed `match` с fuzziness; protected
и известные слова остаются strict. Иначе опечатки не возвращали бы потерянных
кандидатов. Общий topical gate по подтверждённым понятиям сохраняется.
Если кластер запрещает expensive queries или прокси не принимает DSL, это ошибка;
запрос не переисполняется молча без fuzzy [OS-7].

Fuzzy может быть отдельным кандидатом для сравнения, но низкий boost его `_score`
не даёт низкого веса в текущем RRF: RRF игнорирует величину score. Автоматическое
добавление такой третьей равноправной ветки не входит в основной профиль.

### 7.5. Что не включать в первый профиль

- `synonym_graph` требует настроенного analyzer; словарь в YAML клиента не создаёт
  его на сервере. Application expansion через clauses не равен token graph [OS-8].
- BM25 `k1/b`, новые analyzers, keyword subfields и исправления данных требуют
  работ с индексом; сейчас это рекомендации владельцу, не исполнение через search.
- Rescore работает в per-shard окне и не возвращает кандидатов, потерянных retrieval.
  Если станет нужен, window, стоимость и ограничения sort задаются отдельным опытом [OS-9].
- Freshness/quality через function_score возможны только на известных date/numeric
  полях. Свежесть не равна профессиональной релевантности; нужны отдельная ablation,
  обработка missing values и ограничение влияния. В базовом text-профиле выключено [OS-10].
- DFS, server-side hybrid/RRF, vector search и plugins не являются prerequisites.
  Client-side RRF уже имеется и соответствует доступу только к search [OS-11].

## 8. Профили и ограничение стоимости

| Имя pipeline | Смысл | HTTP searches на query |
| --- | --- | --- |
| `baseline` | Нынешний raw multi_match → TopK | 1 |
| `lexical` | Новый compiler, normalizer, без entity detectors → TopK | 1 |
| `parsed` | Compiler + модульный разбор + bounded aliases/phrases → TopK | 1 |
| `fused` | Независимые lexical и parsed с полной candidate depth → RRF → TopK | 2 |
| `fuzzy` | Отдельный ограниченный typo-профиль, opt-in ablation | 1 |

На первом шаге сравниваются baseline, lexical и parsed. Fused сохраняется как
явно выбираемый опыт; наличие RRF само по себе не делает его лучшим.
Ветки lexical/parsed коррелированы: дополнительный HTTP должен окупиться измеренным
улучшением. Для fused возможны свои candidate-depth и rank-constant ablations.

Начальные значения для обсуждения и измерения, не production-рекомендации:

| Параметр | Начальное значение | Почему и что проверять |
| --- | --- | --- |
| title / description boost | 4 / 1 | Усилить название роли относительно длинного текста; проверить другие отношения |
| best_fields tie_breaker | 0.1 | Малый вклад второго поля, сравнить с 0 |
| title phrase boost / slop | 2 / 0 | Точная analyzed phrase; отдельно проверить slop=1 |
| alias boost внутри понятия | 0.7 относительно literal | Не считать expansion столь же надёжным, как исходная форма |
| soft attribute boost | 0.2 | Уточнение не должно перекрывать профессиональный смысл |
| candidate_depth / final_k | 100 / 20 | Получать запас до усечения, отдельно измерить latency и recall на pool |
| NDCG cutoff | 10 | Одинаков для всех вариантов и не больше final_k |
| RRF rank_constant | 60 | Существующий default, не доказанный optimum |
| max input characters | 512 | Ошибка до HTTP при превышении, без молчаливого усечения запроса |
| max concept groups | 16 | Ограничить сложность запроса; oversized input — явная ошибка |
| max alternatives per concept | 4, включая literal | Контролируемое расширение вместо всей таксономии |
| max alternatives total / query leaves | 48 / 128 | Проверить компилированный DSL до HTTP |

Малый boost — коэффициент, а не верхняя граница вклада. Редкий alias с весом 0.7
может получить больший score, чем literal; attribute с весом 0.2 может изменить
профессиональный порядок из-за IDF и norms. Столбец «почему» описывает гипотезу,
которую проверяют ablations, а не гарантии этих чисел.

Новые budgets/capability checks относятся к jobboard compiler-профилям.
Исторический B0 сохраняет свой нынешний контракт, включая отсутствие этих лимитов
и implicit lexical coverage. Сравнительный набор подбирается в общей допустимой
области: известные поля, поддержанные явные filters и входы в пределах новых budgets.
Нельзя назвать B0 защищённым новой проверкой, которую он не выполняет.

Лимит query leaves считает порождённые клиентом запросные clauses. Внутреннее
расширение analyzer/fuzzy зависит от сервера; этот лимит не является доказательством,
что серверный max-clause limit никогда не сработает. Такие отказы остаются ошибками.

Исходный literal не удаляется ради помещения aliases в лимит. Лишние aliases
ограничиваются детерминированно; превышение лимита исходных концептов/DSL вызывает
ошибку до поиска. Empty/zero-token query не превращается в match_all:
`zero_terms_query=none`, если параметр применим [OS-7].

Обе RRF-ветки используют один индекс и одинаковые явные filters. Глубина кандидатов
не равна final_k: обрезать ветки до итогового k нельзя незаметно. Parent filters
не меняются; branches очищаются после успешного fusion. Никаких скрытых retries,
условного «попробуем другой запрос после ошибки» и shared retrieval cache.

## 9. Hydra: отдельная папка эксперимента и сопутствующие группы

Реализованная структура основных владельцев:

```text
configs/
  experiments/vacancy-search/jobboard/lexical/
    001-interactive.yaml
    002-comparison.yaml
  jobboard/lexical/
    profile.yaml
    source/basic.yaml
    lexicons/ru-en.yaml       # manifest категорий; заполнение — 004A
    parsing/rules.yaml
    parsing/no_entities.yaml
    parsing/roles_skills.yaml
    retrieval/lexical.yaml
    retrieval/parsed.yaml
    retrieval/fuzzy.yaml
    graph/lexical.yaml
    graph/fuzzy.yaml          # заполненный opt-in graph
    cases/synthetic.yaml
    cases/fixture-smoke.yaml
src/
  domain/request/jobboard.py
  components/query/
    normalization.py
    jobboard/
      abstract.py
      lexicon.py
      parser.py
  core/transformers/opensearch/jobboard/
    query.py
    search.py
  integrations/opensearch/ # существующие client.py и domain.py
  experiments/             # существующие direct/search.py и testing/compare.py
```

Это карта ответственности, а не разрешение оставить пустой scaffold. Каждый новый
файл должен владеть содержательным поведением либо данными. Близкие функции и types
сохраняются рядом; класс-маркер или wrapper ради отдельного файла не создаётся.
Словари и параметры принадлежат конфигурации; Python определяет их смысл.
Runtime зависимостей сверх существующего stack для базового варианта не требуется.
ESCO не скачивается при запуске.

### 9.0. Владельцы, направления зависимостей и гранулярность

| Владелец | Конкретное содержимое | Что ему не принадлежит |
| --- | --- | --- |
| `src/domain/request/jobboard.py` | Frozen QueryHint/ParsedQuery и конечные категории hints; значения и инварианты spans | Hydra, DSL, HTTP, registry, загрузка словарей |
| `src/components/query/normalization.py` | NormalizeText: Unicode/пробелы/защищённые обозначения и согласованные границы | Query expansion, mapping индекса, поиск вакансий |
| `src/components/query/jobboard/abstract.py` | BaseQueryStage.parse как интерфейс заменяемого анализа | Фиктивный base manager, registration/bind lifecycle |
| `src/components/query/jobboard/lexicon.py` | LexiconQueryStage и типизация записи словаря; matching, ambiguity, проверка конечного набора правил | Общий taxonomy service, внешний API ESCO, DSL |
| `src/components/query/jobboard/parser.py` | ModularQueryParser: normalizer, вызовы независимых stages, merge/overlap, лимиты разбора | Исполнение registry, хранение payload, HTTP |
| `src/core/transformers/opensearch/jobboard/query.py` | SourceProfile/LexicalQueryConfig; compile_query, lexical/concept/topical/phrase/fuzzy clauses и budgets | Чтение конфигов/окружения, network, экспериментальный цикл |
| `src/core/transformers/opensearch/jobboard/search.py` | JobSearchTransformer: Request → parse → compile → integration → полная конвертация → присваивание items | Собственный HTTP transport и копия SearchExperiment |
| `src/integrations/opensearch/` | Существующий POST search и преобразование hits | Jobboard parser, словари и правила boosts |
| `src/experiments/direct/search.py`, `testing/compare.py` | Существующие сценарии ввода, сравнения и вызова exporters | Знание конкретного lexicon или ручная сборка DSL |
| `configs/jobboard/lexical/` | Все значения, словари, варианты profiles и cases из 004A | Скрытые Python-константы с production настройками |

Для одинакового алгоритма matching используется один LexiconQueryStage с разными
`kind`/entries. Пять отдельно конфигурируемых стадий не требуют пяти пустых наследников
RoleParser/SkillParser и так далее. Новый concrete stage/file появляется при отличающемся
алгоритме, а не ради категории данных. Близкие dataclasses и функции владельца лежат
рядом: не выделять каждый enum, alias и проверку в отдельный файл. Сохраняются принятые
глубокие каталоги и короткие смысловые имена; `utils`, `manager`, `service` не вводятся.

Зависимости направлены от orchestration/transformers к components/domain и интеграции.
`domain` не импортирует components/core; parser не импортирует OpenSearch transformer;
ни один из новых нижележащих модулей не импортирует `experiments`. Интерфейс transform
и тип общего PipelineData остаются прежними. Общие `__init__.py` не начинают eagerly
импортировать весь jobboard-набор ради регистрации.

Сопутствующие Python targets в этой spec — контракт размещения и исполняемый
API. Constructor принимает параметры выбранного YAML:
LexiconQueryStage(kind, entries), ModularQueryParser(normalizer, stages),
JobSearchTransformer(integration, parser, source, query, size). Source/query —
типизированные значения, созданные Hydra; parsing rows нормализуются в immutable
entries один раз при создании стадии. Никакого чтения файлов из transform/parse.

Если появилась реальная общая обработка TermFilter/UserPropertyFilter в старом
и новом DSL builder, допустимо выделить её в тематический модуль рядом с
`core/transformers/opensearch/search.py`, сохранив старый search_body и его поведение.
Нельзя вызывать старый builder и затем вырезать части его JSON ради повторного
использования или вводить новый общий слой заранее.

### 9.1. Wrappers переиспользуют существующие эксперименты

`001-interactive.yaml`:

```yaml
defaults:
  - /jobboard/lexical@_global_: profile
  - _self_

_target_: src.experiments.direct.search.SearchExperiment
registry: ${jobboard.registry}
pipeline: parsed
context: {}
filters: []
exporters:
  - _target_: src.components.reporting.exporters.json.JSONExporter
    path: ${hydra:runtime.output_dir}/searches.json
```

`002-comparison.yaml`:

```yaml
defaults:
  - /jobboard/lexical@_global_: profile
  - /jobboard/lexical/cases@_global_.jobboard.cases: synthetic
  - _self_

_target_: src.experiments.testing.compare.CompareExperiment
registry: ${jobboard.registry}
pipelines:
  baseline: baseline
  lexical: lexical
  parsed: parsed
cases: ${jobboard.cases}
metrics:
  ndcg:
    _target_: src.components.evaluation.metrics.listwise.ndcg.ListWiseNDCG
    k: ${jobboard.evaluation_k}
exporters:
  - _target_: src.components.reporting.exporters.json.JSONExporter
    path: ${hydra:runtime.output_dir}/comparison.json
```

### 9.2. Композиция supporting configs

`profile.yaml`:

```yaml
defaults:
  - /jobboard/lexical/source@jobboard.source: basic
  - /jobboard/lexical/lexicons@jobboard.lexicons: ru-en
  - /jobboard/lexical/parsing@jobboard.parser: rules
  - /jobboard/lexical/retrieval@jobboard.retrieval.lexical: lexical
  - /jobboard/lexical/retrieval@jobboard.retrieval.parsed: parsed
  - /jobboard/lexical/graph@jobboard.registry: lexical
  - _self_

jobboard:
  candidate_depth: 100
  final_k: 20
  evaluation_k: 10
```

`source/basic.yaml` содержит явный index, перечень текстовых полей и, если доступно,
роль title-поля. URL/authorization/timeout берутся из существующего `connection`.
Названия реального индекса и полей остаются обязательными; `.keyword`, skills,
salary и published_at не придумываются. Неподтверждённые optional capabilities выключены.

Минимальный пример источника (названия полей заполняет пользователь):

```yaml
_target_: src.core.transformers.opensearch.jobboard.query.SourceProfile
index: ???
fields: ???
title_field: null
term_fields: {}
```

При `title_field: null` title-only bonuses отсутствуют. Введение role/title веса
требует явно выбрать title_field либо задать согласованные field weights в retrieval.
Список fields содержит имена, а boosts хранятся в retrieval-параметрах; compiler
и baseline-конфиг формируют из этого допустимое представление OpenSearch.
Retrieval configs также создают типизированное значение через
`_target_: src.core.transformers.opensearch.jobboard.query.LexicalQueryConfig`.
В graph leaf получает `source: ${jobboard.source}`, соответствующий `query`,
`size: ${jobboard.candidate_depth}` и integration/parser через `_target_`.
Hydra выполняет instantiate этой вложенной конфигурации вместе с экспериментом;
не передавать обычный dict туда, где Python Interface обещает SourceProfile.

`parsing/rules.yaml` иллюстрирует реальную заменяемость модулей:

```yaml
_target_: src.components.query.jobboard.parser.ModularQueryParser
normalizer:
  _target_: src.components.query.normalization.NormalizeText
  protected_terms: ${jobboard.lexicons.protected.terms}
stages:
  - _target_: src.components.query.jobboard.lexicon.LexiconQueryStage
    kind: role
    entries: ${jobboard.lexicons.roles.entries}
  - _target_: src.components.query.jobboard.lexicon.LexiconQueryStage
    kind: skill
    entries: ${jobboard.lexicons.skills.entries}
  - _target_: src.components.query.jobboard.lexicon.LexiconQueryStage
    kind: seniority
    entries: ${jobboard.lexicons.seniority.entries}
  - _target_: src.components.query.jobboard.lexicon.LexiconQueryStage
    kind: work_mode
    entries: ${jobboard.lexicons.work_modes.entries}
  - _target_: src.components.query.jobboard.lexicon.LexiconQueryStage
    kind: location
    entries: ${jobboard.lexicons.locations.entries}
```

`no_entities.yaml` сохраняет normalizer и задаёт `stages: []`. Специфичные detectors
можно заменить другим `_target_`; это не требует правки core registry. Состав списка
меняется целиком либо готовым parser-профилем, без неявных merge-by-index ожиданий.

Функциональные названия role-stage/skill-stage в разделе 5 обозначают конфигурируемые
экземпляры LexiconQueryStage, а не требование к пяти отдельным классам. Разные алгоритмы
могут реализовать BaseQueryStage; одни лишь разные данные не требуют наследования.
Формат entries, protected terms и содержимое каждого config group определены в 004A.
Оставить `entries: []` в shipped rules-профиле нельзя; пустой список осмыслен только
в явно выбранном ablation no_entities.

`graph/lexical.yaml` собирает существующий BasicTransformerRegistry. Он содержит
leaf targets baseline/lexical/parsed, завершённые pipelines с top-k и, при выборе
fused, BranchTransformer → RRF → TopK. Baseline получает существующий
OpenSearchTransformer; новые leaf получают parser/config/integration в конструкторе.
Lexical leaf всегда использует no-entities parser независимо от parser-профиля parsed.
Общий `${jobboard.parser}` не должен нечаянно включить entities и в контрольной ветке.

Hydra interpolation переиспользует описание конфигурации, но не гарантирует identity
Python-объектов. Это нормально: parser stateless, интеграция закрывает response
после каждого search. Общего скрытого состояния между экземплярами нет.

### 9.3. Выбор эксперимента

Команда сохраняет нынешний `main.yaml` и явно заменяет experiment:

```sh
poetry run python -m src.run \
  '~experiments/vacancy-search/interactive@experiment' \
  '+experiments/vacancy-search/jobboard/lexical@experiment=001-interactive' \
  connection.url=https://search.example.org/opensearch \
  jobboard.source.index=vacancies \
  'jobboard.source.fields=[title,description]' \
  jobboard.source.title_field=title
```

Для сравнения выбирается `002-comparison`; для ablation parser:

```text
jobboard/lexical/parsing@jobboard.parser=no_entities
```

Default cases нового comparison wrapper — **synthetic**, полностью заполненный
набор приложения 004A для controlled source. Это не production judgments. Для
реального индекса выбирается отдельный user cases group с его ids/оценками;
не переносить grade синтетической вакансии на случайный реальный документ.
Локальный CLI acceptance подставляет fixture URL/source и cases/fixture-smoke,
не требует доступного реального OpenSearch и не запускает его автоматически.

Эксперимент не использует обязательные поля прежнего `search` блока, если они ему
не нужны; не следует заставлять пользователя заполнять чужой конфиг.
Секрет остаётся через `OPENSEARCH_AUTHORIZATION`, не через CLI overrides.

Упаковка Hydra defaults, `_global_`/package overrides, переключение wrappers и parser
проверены через instantiate поставленных targets и subprocess CLI с локальным HTTP.
Канонический override cases имеет вид `jobboard/lexical/cases@jobboard.cases=fixture-smoke`,
хотя wrapper использует `_global_` при объявлении defaults.
Семантика packages/defaults сверена с документацией Hydra 1.3 [HY-1, HY-2].

## 10. Проверяемые примеры разбора

Примеры задают ожидаемое поведение на курируемом тестовом lexicon; они не объявляют
готовым словарь для всего рынка труда.

| Запрос | Ожидаемый разбор/запрос | Чего нельзя делать |
| --- | --- | --- |
| `Senior Python developer remote` | Role developer, skill Python, seniority и work mode как soft hints; literal сохранён | Требовать несуществующие поля seniority/remote |
| `разработчик C++ Москва` | Protected C++; роль; известная Москва как мягкое уточнение | Превращать C++ в C; исключать вакансии через выдуманный geo filter |
| `C# .NET backend` | Раздельные protected skills, bounded aliases для backend при наличии записи | Fuzzy по C#/.NET; отбрасывать punctuation |
| `Java разработчик` | Java и роль | Расширять Java в JavaScript |
| `PM` | Неоднозначный literal | Выбирать project либо product manager без основания |
| `1С разработчик` | Согласованная запись для кириллической/латинской формы из словаря | Глобально заменять похожие буквы во всём вводе |
| `data scientist` | Один multiword role span и одобренные aliases | Путать с любой вакансией, содержащей data |
| `без опыта аналитик` | Entry-level hint, role если известна | Интерпретировать `без` как универсальный must_not |
| `удалённо` | Literal anchor без профессиональных hints | Отправлять match_all, потому что весь текст распознан как attribute |
| `python или java` | Literal и ambiguous coordination без принудительного AND skills | Объявлять распознанный OR, если его семантика не реализована |
| `разработчки python` | Literal в основном профиле; отдельная bounded fuzzy ablation | Исправлять известные технологии или запускать скрытый повторный поиск |
| Неизвестная профессия | Обычный lexical path с сохранением слов | Ошибка «нет в словаре» или обнуление запроса |
| Только пробелы / превышение лимита | Пропуск пустого ввода существующим Search либо явная ошибка границы | Молчаливое усечение/широкий запрос |

## 11. Оценка качества и ablations

### 11.1. Данные и единица сравнения

Единица — case с query, явными context/filters и relevance mapping по id.
Одинаковые входы и index применяются ко всем pipelines. Контексты разных cases
не смешиваются; в интерактивном Search контекст фиксирован на сессию, поэтому разные
контексты требуют отдельных запусков.

Начать с небольшого development-набора и отдельных held-out запросов; затем увеличивать
coverage. Число запросов само по себе не является доказательством достаточности.
Обязательно покрыть RU/EN, короткие/длинные, role-only, skill-heavy, multiword titles,
неоднозначность, protected technologies, опечатки, seniority, location/work mode,
неизвестные профессии и запросы со сложной логикой.

Словари и boosts настраиваются на development split. Held-out не используется для
ручного добавления aliases «под правильный ответ». Похожие перефразировки одного
намерения по возможности принадлежат одному split, иначе оценка завышается.

### 11.2. Разметка

1. Получить успешные выдачи выбранных вариантов до оценки и сохранить через EOF.
2. Собрать по каждому query объединённый pool кандидатов, скрыть происхождение
   pipeline при разметке и по возможности перемешать порядок.
3. Оценить соответствие профессиональному намерению и явным ограничениям, пользуясь
   полной вакансией, а не только совпадением слов заголовка.
4. Передать один relevance mapping всем pipelines case. Grade 0 — явная оценка
   нерелевантности; отсутствующий grade не равен нулю.

Рекомендуемая шкала для первого опыта: 0 — нерелевантно; 1 — слабое/частичное
соответствие; 2 — релевантно с оговорками; 3 — сильное соответствие запросу.
Примеры границ шкалы и разбор спорных случаев фиксируются вместе с набором.
Judge получает исходный запрос, а не предсказанные parser hints в роли ground truth.
На части набора нужна независимая повторная разметка с разбором расхождений:
иначе ошибка parser или различное понимание seniority могут самоподтвердиться в grades.
Не выводить пригодность кандидата или демографические свойства: это поиск вакансий,
а не автоматическое решение о найме.

Минимальный pool покрывает union оцениваемых top-k всех вариантов. Более глубокий
pool полезен для анализа retrieval; IDCG считается по всем переданным judgments,
включая не найденные данным вариантом документы. Pool может быть неполным относительно
индекса: нельзя объявить полученный recall полнотой поиска по всему корпусу.

### 11.3. Метрики и стоимость

Основная автоматическая метрика — существующая NDCG@10 с одинаковыми final_k и cutoff.
Дополнительно анализируются per-query изменения, worst regressions, пустые выдачи,
нарушения смысла запроса и поведение slices. Улучшение среднего не должно скрывать
провал коротких запросов или путаницу C++/C#.

Число HTTP, глубина кандидатов, размеры DSL/expansion, end-to-end latency p50/p95
и затраты parser измеряются в контролируемом прогоне. Это требования к evidence,
а не повод добавить общую telemetry platform. Бюджет latency фиксируется по целевой
среде перед внешним сравнением; заранее неизвестный SLO не придумывается.

Последовательность ablations:

- B0: нынешний raw multi_match.
- B1: lexical с контролируемыми полями/boosts, coverage и title phrase, без entities;
  отдельный вариант B1 без phrase выделяет вклад этого сигнала.
- B2: плюс role/skill parsing и aliases; отдельно выключить общий topical gate.
- B3: плюс soft attribute signals; по одному классу сигналов.
- B4: fused lexical/parsed против лучшего одиночного.
- B5: fuzzy как отдельный opt-in профиль.

Не менять все веса одновременно и не считать выигрыш на двух специально выбранных
запросах улучшением продукта. Без online A/B вывод ограничивается offline relevance;
количество откликов, качество отклика и найм не заявляются улучшенными [JB-2].

### 11.4. Стабильность источника и артефакты

Compare делает новые поиски. Изменения индекса и tie ordering могут принести новый
неразмеченный id: NDCG завершится ошибкой, comparison.json не публикуется. Ранее
успешные retrieval-артефакты остаются; нужно дополнить judgments и повторить опыт.
Это не replay. Snapshot индекса не вводится без отдельного решения.

Сохраняются нынешние форматы searches.json/comparison.json. Hydra sidecar описывает
выбранную композицию, но environment interpolations не фиксируют автоматически все
фактические значения. Для evidence записываются версия словаря, безопасные параметры,
версия источника если известна, дата и выбранные cases. Credentials и личные данные
не переносятся в Git. Полный runtime trace разбора по умолчанию не сохраняется.

## 12. Failure semantics и проверка до side effects

Следующие новые проверки относятся к jobboard leaves; B0 сохраняет legacy-контракт,
как оговорено в разделе 8.

- Невалидный lexicon, неоднозначная конфигурация stage, отрицательный/нечисловой boost,
  превышение лимитов, неизвестные fields/capabilities и некорректные filters
  отклоняются до HTTP. Ошибочные словарные данные не становятся произвольным DSL.
- Empty detector output корректен. Исключение parser/compiler не вызывает скрытый
  baseline/fuzzy/fallback и не меняет payload.
- Ответ полностью преобразуется до замены items. Malformed/partial response не
  превращается в успешную пустую выдачу.
- Ошибка ветки не публикует частичные branches и не запускает downstream fusion/export.
  Уже выполненные read-only HTTP не обещается откатить.
- EOF/ошибка/Ctrl-C и atomic no-overwrite export сохраняют прежние contracts.
  Между несколькими exporters общей транзакции нет.
- Имена стадий и безопасные коды ошибки доступны программно. Не печатать raw exceptions,
  заголовки, server body, пользовательский context или полный resolved config.

## 13. Acceptance evidence

### 13.1. Локальная реализация

| Область | Проверяемый результат |
| --- | --- |
| Изоляция эксперимента | Старые конфиги и tests проходят без jobboard-параметров; общий payload не получил query-plan. Новые доменные типы размещены в тематическом модуле, core не импортирует experiments |
| Parser interface | Реальная замена/отключение detector через Hydra меняет hints, не исходный QueryFilter; все detectors читают один normalized_text |
| Нормализация | C++/C#/.NET/1С и multiword spans сохранены; offsets проверены на Unicode; неизвестные слова не теряются |
| Неоднозначность | PM/Go, overlap и unsupported coordination не превращаются в уверенные expansion/hard filters |
| DSL | В новых compiler-профилях обязательный lexical anchor, explicit minimum_should_match и bounded aliases; общий topical gate не пропускает attributes-only match. Во всех вариантах сохраняются явные filters; raw текст не попадает в query_string |
| Atomicity | Ошибки parser/filter/compiler дают ноль HTTP и сохраняют прежний payload; malformed response не публикует новые items |
| Бюджеты | Проверяются лимиты исходного запроса, групп, alternatives, DSL; fuzzy не применяется к protected/коротким tokens |
| Composition | Поставляемый Hydra-конфиг исполняет настоящий subprocess CLI и HTTP; несколько веток получают разные bodies, полный source сохраняется |
| RRF | Candidate depth больше final_k; кандидат ниже раннего cutoff влияет на fused top-k; ties детерминированы по текущему контракту |
| Comparison | Минимум два case, разные rankings, заранее вычисленные NDCG и сводка; missing judgment запрещает export |
| Ablation | baseline и lexical не меняются при переключении parser parsed; no_entities отключает entity clauses; unknown option не игнорируется |
| Starter content | Все строки lexicons/rules, 20 вакансий, 24 полных case judgments и профили из 004A материализованы; tests читают поставляемый контент, не отдельный урезанный словарь |
| Совместимость | Ruff check, Ruff format, ty и pytest проходят каноническими командами; документационные ссылки и diff проверены |

Тесты DSL проверяют смысл запроса и failure boundary, не порядок ключей JSON или
дословное prose. Локальный сервер проверяет запросы/ответы и composition, но **не
исполняет анализатор, BM25, fuzzy или phrase matching**. Это не evidence search quality.

### 13.2. Настоящий OpenSearch

Безопасный live smoke использует только разрешённый search API:

1. Подтвердить профиль индекса и версии, auth/TLS, непустой результат по известной
   вакансии и сохранение `_source`. Действие term/user-property фильтра проверяется,
   если профиль объявляет совместимое поле. Для text-only профиля filters пусты;
   локальный тест нового leaf отклоняет неподдержанное явное условие до HTTP.
2. На контрольных вакансиях проверить title/body matching, phrase, aliases,
   C++/C#/Go, RU/EN и отсутствие неожиданного расширения выдачи из-за bool defaults.
3. Получить и разметить pool; выполнить baseline/parsed и выбранные ablations.
4. Зафиксировать качество и latency, провалы analyzer/mapping отделить от ошибок compiler.
5. Fuzzy/fused рекомендовать только после их собственного evidence; результатом
   опыта допустимо решение оставить более простой pipeline.

Если реальное подключение недоступно, можно завершить локальную реализацию, но нельзя
объявить подтверждёнными mapping compatibility, качество и преимущество нового поиска.

## 14. Граница локальной поставки и открытые данные

Локальная поставка включает типизированные query values, модульный parser и lexical/parsed
compiler у владельцев, определённых разделом 9.0, **заполненные** RU/EN словари,
supporting Hydra groups, wrappers поиска и сравнения, cases, judgments и contract tests.
Контент приложения 004A — обязательная часть той же поставки, а не follow-up. Fused использует существующие абстракции;
fuzzy остаётся отдельно выбираемой ablation. Начинать с поля/phrase baseline и добавлять
модули по одному, не строить сразу taxonomy engine.

Перед внешней приёмкой нужны версия OpenSearch, реальные text/keyword поля и
analyzers, несколько разрешённых примеров вакансий, целевые языки, запросы и judges.
До получения этих данных RU/EN и title/description — предварительные предположения;
ни один production mapping или словарь всей отрасли не считается известным.

Вне текущей spec: изменение индекса, нормализация корпуса, администрирование,
векторная инфраструктура, model serving/обучение, автоматический перевод и LLM,
персонализация, salary/geo search без подтверждённой схемы, автодополнение, UI разметки,
полный replay, распределённое исполнение, retries/resume и общая перестройка framework.
Это возможные независимые эксперименты, а не скрытые критерии завершения этого.

### 14.1. Одна локальная волна: поведение вместе с контентом

| Пакет исполнения | Законченный результат | Проверяемая граница |
| --- | --- | --- |
| 1. Query values и parsing | Доменные значения и parser у владельцев 9.0; весь manifest и entries A2–A5, protected/syntax, rules/no_entities/roles_skills | Из authored config получается содержательный ParsedQuery; ambiguity, Unicode/spans, неизвестные слова, whitelist и unsupported syntax проверены без HTTP |
| 2. DSL и retrieval | JobSearchTransformer, SourceProfile/LexicalQueryConfig, filled lexical/parsed/fuzzy/ablation settings A6 | Реальные bodies меняются от выбранного модуля/сигнала; все новые проверки до HTTP, baseline semantics сохранены |
| 3. Hydra и scenarios | Общий registry с полными leaf и конечными pipelines, wrappers 001/002, basic/fixture sources, все A7/A8 данные | Переключаются parser/graph/query/cases; ссылки и instantiate реальных targets проходят, старый main default не изменён |
| 4. Составная проверка | CLI → local HTTP → SERP/RRF → Search/Compare artifacts, ожидаемые scores/grades A9 | Успешные runs и failure paths; проверка candidate depth, protection от missing judgments, no partial export |
| 5. Handoff | Актуальные architecture/CONTEXT для действительно реализованных понятий, README с испробованными командами, development с evidence и ограничениями | Канонические gates выполнены либо точное ограничение указано; контент A10 полный, выполненные live gates указаны отдельно |

Пакеты задают порядок ближайшего исполнения, а не разрешают назвать первый пакет
полной поставкой. Для каждого сразу пишутся содержательные конфиги и данные:
нельзя сначала объявить framework законченным, а наполнение отложить вне волны.
Временный task plan реализации может уточнять порядок файлов, не менять эти exits.

Работа с живым индексом — отдельная внешняя проверка после локальной волны.
Она не является поводом оставить пустые starter dictionaries или незавершённый
fuzzy compiler. Настоящих ids/credentials/mapping сейчас нет: их не следует выдумывать.

### Детали принятой реализации

- `ParsedQuery.structured_enabled` явно передаёт политику неподдержанного синтаксиса;
  `protected_spans` удерживает protected обозначения и исключает из fuzzy неизвестные
  uppercase-сокращения исходного текста. Offsets всегда относятся к normalized_text.
- `LexicalQueryConfig.field_weights` задаёт overrides известных text-полей;
  `title_field_weight=4` и `default_field_weight=1` позволяют использовать произвольные
  имена полей без Hydra resolver и без предположения о production mapping.
- `profile.yaml` независимо собирает `jobboard.lexical_parser` и `jobboard.parser`.
  Query settings живут в `jobboard.retrieval.{lexical,parsed,fuzzy}`; fuzzy settings
  добавляются только выбранным `graph/fuzzy.yaml`. Все бюджеты видны в YAML.
- Lexicon parser сначала сохраняет literal каждой группы, затем распределяет
  оставшийся бюджет alternatives детерминированно. Compiler отдельно ограничивает
  группы, alternatives и число DSL leaves; превышение неизбежной сложности — ошибка.
- Конечные registry targets называются baseline/lexical/parsed/fused/fuzzy, поисковые
  листья имеют суффикс `_leaf`. Fused ссылается непосредственно на полные листья.
- Исполняемое evidence: `tests/test_jobboard_parser.py`, `test_jobboard_search.py`,
  `test_jobboard_content.py`, `test_jobboard_workflow.py`. Это контрактная локальная
  проверка; OpenSearch не разворачивается, синтетические ранги не объявляются BM25.

### 14.2. Критерий handoff следующему исполнителю

В начале проверить Git status и прочитать AGENTS/architecture/development, затем
004 и 004A. Расположение экспериментов определяется текущим деревом `src/experiments/`.
Существующие несвязанные изменения сохраняются. Актуальные проверки и внешние
ограничения записаны в development.md; targets и YAML проверяются по текущему коду.

После реализации показать фактические новые владельцы поведения, проверенные команды,
coverage контента, полученные локальные артефакты и состояние внешнего smoke.
Минимальные gates: Poetry Ruff check, Ruff format --check, ty check, pytest, проверка
Markdown-ссылок и `git diff --check`. Если Poetry недоступен, отдельно назвать проверку
из существующего `.venv`; не объявлять этим проверенной новую Poetry installation.
Перед установкой/тяжёлой сборкой применяется дисковый gate из AGENTS.md.
Commit/push/PR не входят в эту спецификацию автоматически.

## 15. Источники

Ссылки и ограничения источников подробнее разобраны в двух research notes в начале.
Источники подтверждают механизм или существующий подход; конкретные boosts, словари,
пороги и выбор профиля в этой spec являются нашими экспериментальными гипотезами.

- [JB-1: LinkedIn — Building the next generation of job search, 2025-05-29](https://www.linkedin.com/blog/engineering/ai/building-the-next-generation-of-job-search-at-linkedin).
- [JB-1b: LinkedIn — Reimagining LinkedIn’s search stack, 2026-01-21](https://www.linkedin.com/blog/engineering/search/reimagining-linkedins-search-stack).
- [JB-2: Indeed — Normalized Entropy or Apply Rate?, 2025-11-11](https://engineering.indeedblog.com/blog/2025/11/normalized-entropy-or-apply-rate-evaluation-metrics-for-online-modeling-experiments/).
- [JB-3: ESCO — Occupation](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/occupation),
  [Preferred term](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/preferred-term),
  [Non-preferred term](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/non-preferred-term).
- [OS-1: Lucene 10.3.1 — BM25Similarity](https://lucene.apache.org/core/10_3_1/core/org/apache/lucene/search/similarities/BM25Similarity.html)
  и [OpenSearch — Similarity](https://docs.opensearch.org/latest/im-plugin/similarity/).
- [OS-2: OpenSearch search analyzers](https://docs.opensearch.org/latest/analyzers/search-analyzers/).
- [OS-3: Multi-match query](https://docs.opensearch.org/latest/query-dsl/full-text/multi-match/).
- [OS-4: Disjunction max query](https://docs.opensearch.org/latest/query-dsl/compound/disjunction-max/).
- [OS-5: Boolean query](https://docs.opensearch.org/latest/query-dsl/compound/bool/).
- [OS-6: Match phrase query](https://docs.opensearch.org/latest/query-dsl/full-text/match-phrase/).
- [OS-7: Match query, zero terms и fuzziness](https://docs.opensearch.org/latest/query-dsl/full-text/match/).
- [OS-8: Synonym graph token filter](https://docs.opensearch.org/latest/analyzers/token-filters/synonym-graph/).
- [OS-9: Rescore](https://docs.opensearch.org/latest/query-dsl/rescore/).
- [OS-10: Function score query](https://docs.opensearch.org/latest/query-dsl/compound/function-score/).
- [OS-11: Score ranker processor / server RRF](https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/).
- [OS-12: Minimum should match](https://docs.opensearch.org/latest/query-dsl/minimum-should-match/).
- [HY-1: Hydra 1.3 — Packages](https://hydra.cc/docs/1.3/advanced/overriding_packages/).
- [HY-2: Hydra 1.3 — Defaults List](https://hydra.cc/docs/1.3/advanced/defaults_list/).
