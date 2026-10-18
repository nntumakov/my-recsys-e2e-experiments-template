# 006. Пять готовых экспериментов: ranking и Query Understanding

Статус: **реализована локально; OpenSearch/Painless не запускались**.
Свидетельства приёмки — в [development.md](../development.md).
Редакция: 2026-09-29, реализация под русскоязычный поиск рабочих профессий.

Обязательная часть: [006A — наполненный профиль blue-collar/ru](006-blue-collar-content.md).
Он задаёт контент первого запуска, новые slots, примеры и минимальные объёмы.
Это проектная поисковая таксономия, не официальная классификация профессий.

## 1. Результат и граница поставки

После реализации исследователь и продакт открывают готовый notebook, вводят
подключение, реальные названия полей и выбранные boosts, переключают пять presets
и сравнивают выдачи рабочих вакансий на русском языке. Новый research notebook
по умолчанию использует `blue-collar/ru`; старые RU/EN/IT presets остаются регрессиями.
В состав входят код, конфиги, Painless, словари, диагностика и примеры.
Для запуска на своём индексе нужно задать подключение и source fields.

**Тестирование на OpenSearch не входит в обязательную поставку.** Не требуется
поднимать локальный кластер, создавать индекс или иметь production-доступ.
Локальные unit/contract tests, настоящий HTTP к fixture-серверу, Hydra/CLI/notebook,
форматирование и type checking обязательны. Они доказывают локальную готовность,
но не исполнение Painless, remote compatibility и качество поиска. Эти границы
указываются в handoff, а отсутствие live smoke не оставляет реализацию незавершённой.

### Что изменилось относительно первой редакции

| Было | Теперь |
| --- | --- |
| Отдельные freshness и quality experiments | Один C2 с общей формулой и отдельными абляциями |
| Отдельный phrase rescore experiment | Дополнительный готовый режим C2, а не шестой эксперимент |
| Priority tiers в C4 | C3 — строгая Painless-сортировка |
| C4/C5 про ранжирование | C4 — entity/slot understanding; C5 — восстановление формы запроса |
| Неопределённый будущий diagnostic contract | Обязательный отдельный research artifact и готовый notebook |
| Live evidence в критериях поставки | Только необязательное последующее исследование в реальном окружении |

Первые три направления сохранены: текстовая точность, мягкое многосигнальное
ранжирование и строгий порядок категорий. Последние два исследуют **смысл запроса**
и **ошибки его записи**. Это разные задачи, не два названия одного fuzzy search.

## 2. Основания и готовность до реализации

- [Painless: проверка механизмов и исходников OpenSearch 2.19.0](../research/2026-09-29-painless-ranking.md).
- [Query Understanding: практики и ограничения](../research/2026-09-29-query-understanding.md).
- [Лексический поиск](../research/2026-09-26-opensearch-lexical.md).
- [Архитектура](../architecture.md), [статус](../development.md),
  [spec 005: notebook и batch](005-search-research.md).

Источники подтверждают механизмы, но не эффективность предложенных весов.
Все стартовые коэффициенты ниже — исследовательские гипотезы.
Версия, mapping и script permissions реального окружения неизвестны.

До реализации существовали parser со стадиями role/skill/seniority/work_mode/location,
73 записи RU/EN, protected spans, правила неоднозначности, literal/concept paths,
AND-filters, bounded fuzzy, RRF, notebook, CLI, batch и NDCG.
Необходимо переиспользовать их, а не строить второй parser с нуля.

На исходной точке отсутствовали configurable Painless/sort/rescore, role-specific field routing,
словарного исправления запроса, раскладки и полной диагностики сравнения.
Нынешний `parsed` уже распознаёт сущности: один его rename не считается C4.
Нынешний fuzzy сопоставляет термы в индексе: включить его недостаточно для C5.

## 3. Матрица экспериментов

Существующий C0 `baseline` остаётся дополнительным контролем, не новым экспериментом.

| Preset | Что проверяет | Изменение допуска/порядка | Необходимые поля |
| --- | --- | --- | --- |
| C1 `title-precision` | Достаточны ли веса и фразы | Weighted lexical + phrase | Text title и text fields |
| C2 `bounded-signals` | Помогают ли свежесть и качество | Тот же C1 + bounded Painless score; отдельный rescore mode | Date публикации и numeric quality |
| C3 `priority-tiers` | Нужен ли строгий продуктовый порядок | Тот же C1 + `_script` sort | Keyword category и stable id |
| C4 `concept-slots` | Какой ролью являются слова запроса | Сущности, контролируемые эквиваленты, поля по смыслу, coverage | Те же text fields; отдельное skills-поле необязательно |
| C5 `conservative-rewrite` | Что пользователь мог ошибочно набрать | Словарные исправления + раскладка + curated transliteration | Те же text fields; словари поставляются локально |

По умолчанию C4/C5 используют только relevance ranking без C2/C3: сначала измеряется
именно QU. Скриптовые режимы совместимы с их query через ту же композицию, но
их включение — явная абляция. Никаких автоматических переходов C4→C5 после zero hits.
У каждого preset один search HTTP на запрос, включая C2 rescore и C5 alternatives.
RRF не входит в новую стартовую матрицу; его прежние presets продолжают работать.

Hard filters и исходный `QueryFilter` сохраняются. Дополнительные интерпретации
живут в локальном immutable результате разбора, не подменяют filters или context.
Все кандидаты возвращают полные документы, score и порядок.

## 4. C1 — title-precision

### Гипотеза и фактура

Заголовок «Комплектовщик» — более прямое свидетельство профессии, чем упоминание
комплектовщиков в описании вакансии грузчика. Но заголовок «Работник склада» может
быть слишком общим: сборка заказов и ТСД указаны только в описании. C1 даёт управляемый компромисс.

`best_fields` берёт сильнейший field score и долю остальных через tie_breaker.
Дополнительный phrase проверяет порядок проанализированных токенов, а не буквальное
равенство всего заголовка. MSM применяется по полю: слова, разнесённые между полями,
могут не пройти порог. Это важный контрпример для C4. [O1, O2]

Старт: title weight=4, description=1, tie_breaker=0.1,
MSM=`2<75%`, title phrase boost=2, slop=0. Aliases и fuzzy выключены.

```yaml
retrieval:
  type: best_fields
  field_weights: {title: 4.0, description: 1.0} # Пример mapping
  tie_breaker: 0.1
  minimum_should_match: "2<75%"
  title_phrase_boost: 2.0
  phrase_slop: 0
ranking:
  mode: relevance
```

Это иллюстрация новой конфигурационной поверхности, не текущий рабочий YAML.
Физические имена задаются source profile; реальные presets используют интерполяции.

Запрос — обязательный multi_match плюс optional title phrase, с AND-filters в
внешнем bool. Переиспользуются нынешние guards неподдержанного синтаксиса: если они
отключают phrase/structured path, диагностика объясняет причину. Их семантика для
старых presets не меняется.

### Что поставляется и как обсуждать

Готовый preset и абляции `no-phrase`, `title-2`, `title-8`, `coverage-strict`.
На встрече сравнить «комплектовщик ТСД», «водитель погрузчика», «повар горячего цеха»:
точное название, разрозненные слова, название только в description, стек вне title.
Менять один параметр за раз; source, filters и final_k сохраняются.

Риски: переусиление короткого поля, потеря межполевых совпадений, неудачный анализатор.
Не обещается восстановление различий C/C++/C#, потерянных индексом.
C1 служит объяснимым контролем остальных кандидатов, а не заведомо слабым baseline.

## 5. C2 — bounded-signals: объединённые freshness, quality и rescore

### Основной готовый режим

`S0` — score C1, F — свежесть, Q — насыщенный quality signal:

```text
age = max(0, (now_ms - published_ms) / 86_400_000)
F = 2^(-age / half_life_days)
Q = max(0,x) / (max(0,x) + quality_pivot)
S2 = S0 × (1 + alpha × F + beta × Q)
```

Старт: alpha=0.20, beta=0.20, half_life_days=14.
Общий множитель находится в [1,1.4]. Ограничивается сумма бонусов, а не каждый
независимый множитель: сигналы не усиливают друг друга скрытым перемножением.
При возрасте 14 дней и x=pivot имеем F=Q=0.5, множитель 1.2.
Вакансия с S0=10 не обгонит S0=15 только этими бонусами.

На условных документах видно, какое решение принимает формула:

| Документ | S0 | Возраст / quality | Итог S2 |
| --- | --- | --- | --- |
| A: самое точное старое совпадение | 12 | 28 дней / missing | 12.6 |
| B: близкое новое совпадение | 11 | 0 дней / x=pivot | 14.3 |
| C: слабое новое совпадение | 7 | 0 дней / x=9×pivot | 9.66 |

B обгоняет A, но C остаётся ниже: именно такой обмен свежести/качества на текст
предлагается обсудить. S0 здесь задан вручную для объяснения арифметики, не измерен
в поисковом индексе. При alpha=beta=0 исходный порядок восстанавливается.

`quality_pivot=10` допустим только для демонстрационного поля. Для реального
показателя в [0,1] пользователь вводит значение той же шкалы, например 0.5.
Пресет fixture заполнен, external source требует осмысленный pivot и поля.

```yaml
ranking:
  mode: script_score
  script:
    lang: painless
    source: |
      double fresh = 0.0;
      if (doc.containsKey(params.date_field) && doc[params.date_field].size() != 0) {
        long published = doc[params.date_field].value.toInstant().toEpochMilli();
        double age = Math.max(0.0, (params.now_ms - published) / 86400000.0);
        fresh = Math.exp(-Math.log(2.0) * age / params.half_life_days);
      }
      double x = 0.0;
      if (doc.containsKey(params.quality_field) && doc[params.quality_field].size() != 0) {
        x = Math.max(0.0, (double)doc[params.quality_field].value);
      }
      double quality = 1.0 - params.quality_pivot / (x + params.quality_pivot);
      return _score * (1.0 + params.alpha * fresh + params.beta * quality);
    params:
      date_field: ${jobboard.source.ranking.published_at}
      quality_field: ${jobboard.source.ranking.quality}
      now_ms: ${jobboard.session.now_ms}
      half_life_days: 14.0
      quality_pivot: 10.0
      alpha: 0.20
      beta: 0.20
```

В поставке source лежит непосредственно в Hydra YAML, не в Python-константе.
Параметры времени, полей и коэффициентов передаются через params, без конкатенации
пользовательского запроса с source. Изменение params не меняет ключ компиляции source. [O3, O6]

Missing date/quality даёт соответствующий нулевой бонус. Будущая дата получает
F=1 и помечается в диагностике; отрицательный quality трактуется как 0 и тоже
помечается. `containsKey` проверяет mapping, `size` — значение; ни один guard не
исправляет неверный тип или отсутствие doc values. Поля single-valued. [O8]

Параметры конечны, half_life/pivot>0, alpha,beta≥0, alpha+beta≤0.5 для этого семейства.
Поле публикации не должно означать техническое обновление. Сырые clicks/applications
не считаются quality без определения экспозиции и смещения по позиции.
Существующий готовый numeric signal нужен; придумывать его из произвольного `_source`
или автоматически добавлять runtime field не требуется и не разрешается молча.

### Абляции и дополнительные готовые режимы

Поставляются `text-only`, `freshness-only`, `quality-only`, `balanced`,
а также `phrase-rescore`. Отключённый сигнал не требует поля: соответствующий
source не читает его. Значение alpha=0 само по себе не гарантирует этого в произвольном
отредактированном script; готовая абляция должна действительно убрать обращение.

В `phrase-rescore` первая стадия — C1 без phrase bonus. На W=200 документов
**каждого shard** выполняется query rescore с `script_score(query=match_phrase(title,text))`:

```text
P = score вложенного match_phrase, slop=1
H = P / (P + phrase_pivot)
R = H × (1 + alpha × F + beta × Q)
Srescore = S0 + lambda × R
query_weight=1, rescore_query_weight=lambda, score_mode=total
```

Старт: phrase_pivot=2, lambda=2, alpha=beta=0.2, бонус ≤2.8.
Внутри Painless `_score=P`, **не S0**. За пределами W и при phrase nonmatch
бонуса нет; дата сама не поднимает документ без phrase match. Полный script source
с теми же missing guards, params и готовым rescore DSL обязателен в поставке,
а не оставляется упражнением пользователю. [O3, O7]

Режим нужен для сравнения точности и стоимости сложного сигнала. При полном запросе
«комплектовщик ТСД без опыта Москва» phrase может не встретиться в title вообще: это
ожидаемый предел, который C4 адресует разбором на роли и атрибуты.
Rescore не возвращает кандидатов, пропущенных первым запросом; W не равен size.
Custom sort, включая вторичный id, с этим режимом запрещён. Tie-break между
равными scores не гарантируется; W=50/200/500 — готовые overrides для исследования.

### Практическая сессия

Показать пары «сильная старая / слабая новая», missing/future date,
x=0/pivot/очень большое, хороший текст с низким quality и новые вакансии без истории.
Начинать с isolated freshness/quality, потом balanced; rescore — отдельное сравнение
с тем же retrieval и phrase-only абляцией. Native decay/field_value_factor доступны
в OpenSearch, но новая поставка использует Painless ради редактируемых формул,
а не обещания большей производительности. [O4]

## 6. C3 — priority-tiers: явный строгий порядок

### Гипотеза и отличие от C2

Продукт может требовать «сначала предпочитаемый формат работы, затем остальные».
Это не мягкая прибавка к score: категория более высокого приоритета всегда раньше,
даже если текстовая релевантность ниже. Такая гарантия выражается лексикографически:

```text
(tier ascending, lexical score descending, stable keyword id ascending)
```

Пример: day=0, shift_2_2=1, night=2, other/unknown/missing=3.
Это mutually exclusive категории демонстрационного поля, а не универсальная модель
графиков: реальная дневная смена может одновременно иметь график 2/2 (см. 006A). Категория выбирается явным
параметром сессии. Упоминание города или удалёнки в тексте не включает C3 автоматически.
Коммерческое продвижение не вводится под видом этого preset.

```yaml
ranking:
  mode: script_sort
  script:
    lang: painless
    source: |
      if (!doc.containsKey(params.category_field) || doc[params.category_field].size() == 0) {
        return params.other_tier;
      }
      String value = doc[params.category_field].value;
      return params.tiers.containsKey(value) ? params.tiers[value] : params.other_tier;
    params:
      category_field: ${jobboard.source.ranking.category}
      tiers: {day: 0, shift_2_2: 1, night: 2}
      other_tier: 3
  type: number
  order: asc
  tie_break_field: ${jobboard.source.ranking.stable_id}
  track_scores: true
```

Компилятор формирует `sort=[{_script:...},{_score:desc},{<stable_id>:asc}]`.
`tie_break_field` — локальный config key, не OpenSearch-параметр.
Категория — single-valued keyword; stable id — уникальный keyword с doc values,
присутствующий у всех документов. Внутренний `_id` не предполагается sortable.
Tier — малое целое 0..1000; unknown tier не меньше максимального объявленного.

В ответе `_score` остаётся lexical, script key находится в `hit.sort`.
Оба сохраняются отдельно и показываются в notebook. [O5, O6]
Поставляются профили `day-first`, `night-first`, `shift-2-2-first` с заполненными
maps, а не пустой интерфейс. Реальные значения категорий пользователь отображает
конфигом; значения fixture не выдаются за production-коды.

Проверочный спор с продактом: допустимо ли показать слабую дневную вакансию выше
точной ночной? Если нет, C3 отвергается как политика, а не «чинится» незаметным
смешиванием со score. Готовая абляция `no-tiers` возвращает C1.
Rescore и клиентский RRF с C3 не соединяются.

## 7. C4 — concept-slots: понимать роль слов в запросе

### Практика и гипотеза

Entity linking/slot filling раскладывает запрос на профессиональные понятия,
а controlled expansion добавляет только эквивалентные формы того же понятия.
Профессия, оборудование, квалификация, опыт, график и город не должны одинаково
участвовать в обязательном bag-of-words. Полезность особенно заметна в русскоязычных
запросах с падежами, сокращениями, условиями работы
и когда смысл распределён по полям. ESCO описывает неканонические обозначения
понятий, включая synonyms, spelling variants, declensions и abbreviations.
В нашем словаре связанный навык не считается синонимом профессии. [Q4, Q5]

Пример: `комплектовщик ТСД без опыта 2/2 Москва`:

| Фрагмент | Slot | Интерпретация | Использование |
| --- | --- | --- | --- |
| комплектовщик | role | Комплектовщик / курируемые эквиваленты | Тематическая группа |
| ТСД | skill | Терминал сбора данных | Тематическая группа |
| без опыта | experience | Разрешённая цельная фраза | Мягкий bonus |
| 2/2 | schedule | График, не дробь и не стаж | Мягкий bonus |
| Москва | location | Город | Мягкий bonus |

В концептуальной ветке нужны роль и ТСД; опыт, график и город не превращаются
в автоматические hard filters. Явный `TermFilter(city=...)` сохраняется во всех ветках.
Для рабочих профессий 006A добавляет `experience`, `schedule`, `employment`,
`qualification`, `work_condition` с отдельными правилами. Квалификация сохраняется
в обязательных группах structured path, а не теряется при округлении coverage.
Это часть domain/parser/compiler реализации, не переименование существующего seniority.

### Точный поток и новый результат относительно нынешнего parsed

1. Существующая нормализация NFKC/casefold, protected spans и syntax gate.
2. Существующие модульные стадии и curated lexicons; детерминированное разрешение
   перекрытий с longest-match внутри категории и abstention при межкатегорийном конфликте.
3. Каждое unambiguous понятие образует группу `literal + equivalents/translations`.
   Родственная роль, более широкая профессия и соседний skill не являются equivalent.
4. Role-группа ищется по настроенным role text fields (старт: title^4, description^1),
   skill — по skill text fields (старт: title^2, description^1, optional skills^3).
   Каждая группа сама multi-field: роль может совпасть в title, навык — в description.
5. Неопознанный остаток сохраняется как обязательные/учитываемые coverage-группы;
   исключение — конечный whitelist вводных фраз из 006A с явными nonsemantic spans.
   Неоднозначный фрагмент сохраняется literal без expansion. Нельзя выбросить `КПП`,
   неизвестное название оборудования или важное нераспознанное слово как «шум».
6. Полный literal path исходного запроса сохраняется. Structured path содержит
   тематические группы и residual, optional attribute boosts. Они объединяются
   `dis_max(tie_breaker=0)`; одинаковые альтернативы одного понятия не суммируются.
7. Общий topical gate требует хотя бы одно подтверждённое role/skill при их наличии,
   как в текущем parsed. При no-topic запросе нет поиска только по bonuses:
   остаётся literal path, а structured path требует хотя бы одну residual-группу.

Стартовая coverage: все группы при n≤2, ceil(0.75*n) при n>2. Alias weight=0.7;
Location и мягкие attributes из 006A имеют boosts=0.2; квалификации не являются
бонусами. Role phrase строится по распознанному span,
а не по всему запросу, вместе с курируемыми многословными формами. Skills не обязаны
стоять рядом с role. Это главный отличимый от C1 эффект.

Для `комплектовщик ТСД 2/2` концептуальная ветка имеет такую смысловую форму
(условные обозначения, не буквальный готовый OpenSearch JSON):

```text
outer bool:
  filter: все явные hard filters
  must:
    topical_gate: хотя бы один подтверждённый role/skill
    dis_max(tie_breaker=0):
      literal_query("комплектовщик ТСД 2/2")
      bool:
        must:
          role_group(комплектовщик | сборщик заказов)
          skill_group(ТСД | терминал сбора данных)
        should:
          schedule_bonus(2/2 equivalents)
          role_phrase_bonus(комплектовщик forms)
```

Для длинных запросов `must` тематических групп заменяется coverage-группой с
оговорённым MSM. Внутри role_group многословные forms уже phrase clauses;
дополнительный role_phrase_bonus не должен дублировать ту же clause: compiler
применяет усиление в одном месте. Coverage относится к structured path; literal
path может допустить документ по своему MSM. Поэтому C4 не обещает, что каждый
документ во всей выдаче обязательно содержит все распознанные skills.

Новый scope относительно существующего `parsed`: **полевая маршрутизация по slot,
готовые phrase/coverage presets, диагностический trace и расширенный контент с
контрпримерами**. Переиспользуются QueryHint/ParsedQuery и stages; требуемые расширения
их смысла фиксируются в canonical docs при реализации. Нельзя скрыто изменить
существующий parsed preset: новые правила включаются новым конфигом.

```yaml
understanding:
  mode: concept_slots
  role_field_weights: {title: 4.0, description: 1.0}
  skill_field_weights: {title: 2.0, description: 1.0}
  alias_weight: 0.7
  concept_coverage: 0.75
  role_phrase_boost: 2.0
  attribute_boosts: {experience: 0.2, schedule: 0.2, employment: 0.2, work_condition: 0.2, location: 0.2}
  max_concept_groups: 16
  max_forms_per_concept: 4
  max_forms_total: 48
  max_query_leaves: 128
```

### Начальный контент

Поставляется новый полный `blue-collar/ru` из [006A](006-blue-collar-content.md):
64 профессии, ≥320 recognition forms, ≥40 skill/equipment, ≥16 qualifications,
≥32 conditions/experience/schedule/employment, ≥20 городов. Существующий RU/EN seed
сохраняется отдельно. Начальный словарь включён в конфигурацию.
Recognition forms и expansion alternatives разделены; полная морфология не обещается.

Обязательные демонстрации: «комплектовщик ТСД без опыта», «водитель категории CE»,
«оператор ЧПУ» против «наладчик ЧПУ», «повар горячего цеха», «ищу работу грузчиком
в Москве», «охранник без лицензии», «вахта 30/30 с проживанием», «грузчик не вахта».
Ожидаемые interpretations, counterexamples и 64-query ядро заданы в 006A.
IT-примеры C++/C#/PM остаются отдельными регрессиями, не стартовым showcase.

Обязательные variants: `slots-default`, `no-aliases`, `no-attributes`,
`strict-coverage`, `shared-fields` (отключает routing, сохраняя остальные параметры).
Сравнивать C4 с существующим parsed и C1, иначе нельзя приписать результат новым правилам.

### Риски

Неверный dictionary link меняет смысл; слишком широкие aliases дают query drift.
Coverage может потерять узкие запросы с неизвестным словом; trace показывает остаток.
При неподдержанном отрицании/OR structured path выключается целиком по прежней
политике. Это **не** обещание исполнить пользовательское «не продажи» как must_not.
Abstention — явный результат понимания, не транспортный fallback после ошибки.

## 8. C5 — conservative-rewrite: восстановить запись, не придумать намерение

### Практика и отличие от C4/fuzzy

Spelling correction, keyboard-layout correction и curated transliteration создают
проверяемые варианты исходного текста. C4 понимает корректно написанные понятия;
C5 восстанавливает неизвестную форму **до** смыслового разбора и показывает замену.
OpenSearch fuzzy расширяет термы по словарю индекса, но не возвращает надёжную
«исправленную строку» и не исправляет раскладку как отдельную операцию. [Q1, Q3]

Нужен локальный детерминированный алгоритм без LLM, внешнего spellcheck, query logs,
нового сервиса, trained model, `_suggest`/`_analyze` и загрузок во время запуска.
Применяются поставляемые словари, RU/EN keyboard map и явные rewrite rules.

### Порядок и правила принятия

1. Сохраняется original query, нормализованная строка и позиции. Protected technologies,
   известные словарные формы, короткие слова <5 букв, mixed-script и буквенно-цифровые
   обозначения и uppercase acronyms не исправляются автоматически.
2. Если syntax gate обнаружил неподдержанный OR/negation, нет автоматических rewrites
   всего запроса. Диагностика показывает abstention, original literal исполняется.
3. Для неизвестного eligible token строятся кандидаты независимо: точное curated
   rewrite-rule; однократная RU↔EN конверсия раскладки; словарное исправление одной
   буквы (вставка/удаление/замена/соседняя перестановка). Цели — известные
   unambiguous role/skill recognition forms, не произвольные слова из интернета.
4. Curated transliteration — конечные пары вроде `razrabotchik → разработчик`,
   не универсальное транслитерирование любого имени. Ни исправленная форма, ни alias
   не проходит второй круг исправлений. Нет цепочек spelling→layout→translation.
5. Дедупликация ведётся по canonical concept, затем target form. Если несколько
   алгоритмов дают один concept — это один кандидат с несколькими evidence reasons.
   Разные правдоподобные concepts хотя бы у одного span → **abstain всего rewrite**,
   даже если другой span однозначен. Не выбирать по алфавиту/случайному score.
   Все кандидаты рассматриваются до решения о единственности: обрезание списка
   не должно превращать неоднозначность в кажущуюся уверенность. При одном concept
   конкретная target form выбирается по фиксированному порядку curated→layout→spelling,
   затем меньшей distance и лексикографическому порядку normalized target.
   Этот tie-break выбирает только форму уже подтверждённого понятия, не его смысл.
6. Принимается не более одной концептуальной замены на eligible span; длина≤64,
   edit-distance=1, минимальная длина=5, максимальное число принятых spans=2.
   До двух замен применяются вместе к одному полному rewritten query.
   Если более двух spans однозначно требуют замены, весь rewrite отклоняется с
   `rewrite_budget_exceeded`; нельзя исправить случайные первые два.
7. Новый вариант сохраняет все остальные слова, порядок и явные filters. Он получает
   собственные spans и запускает C4 parser. Старые offsets нельзя переносить на новую строку.
   Пересечение replacements запрещено. После разбора действуют те же DSL budgets.

Distance=1 реализуется bounded алгоритмом standard library/Python без новой dependency.
Для масштабов starter lexicon достаточно ограниченного словаря, индексированного
по длине; не требуется строить поисковый сервис для словаря.
Поведение исправлений не определяется частотой документа в индексе.

```yaml
understanding:
  mode: conservative_rewrite
  spelling: {enabled: true, min_length: 5, max_length: 64, max_distance: 1}
  keyboard_layout: {enabled: true, directions: [ru_to_en, en_to_ru]}
  curated_rewrites: ${jobboard.lexicons.rewrites}
  max_replaced_spans: 2
  max_rewritten_queries: 1
  rewrite_weight: 0.65
  on_ambiguity: abstain
```

Один rewritten query — сознательное ограничение, исключающее комбинаторный рост
нескольких исправлений. Показанные кандидаты и причины отказа доступны до HTTP.
Default не включает OpenSearch fuzzy; это отдельная существующая контрольная линия.

### Как альтернативы становятся поиском

Две ветки в **одном search body**: C4(original) и C4(rewritten) с boost=0.65,
объединённые `dis_max(tie_breaker=0)`. Каждая ветка имеет собственные корректные
topic groups; общий внешний bool применяет одинаковые исходные hard filters.
Если принятого rewrite нет, исполняется ровно C4(original), без пустой второй ветки.
Не создаётся второй QueryFilter и не меняется original Request.

Исходная ветка всегда сохраняется. Это не гарантия «все original hits выше corrected»:
BM25 двух разных queries имеет разные шкалы; boost=0.65 лишь начальная гипотеза.
Требование абсолютного приоритета original было бы другим ranking contract.
Максимальный общий leaf budget остаётся 128 на весь запрос, не по 128 на каждую ветку;
при превышении — ошибка до HTTP, а не незаметное отбрасывание ветки.

### Наполненный starter pack и примеры

Основной набор — [006A, раздел 6](006-blue-collar-content.md): ≥40 направленных
правил, полная RU/EN keyboard map и словарь профессий/оборудования. Пары включают
`вадитель → водитель`, `кладовшик → кладовщик`, `rkfljdobr → кладовщик`,
`gruzchik → грузчик`. Неизвестное имя компании не исправляется без подтверждения.

Пунктуационные keyboard keys разрешены только layout channel по правилу 006A:
`e,jhobr офиса → уборщик офиса`. Это не отменяет alphabetic-only spelling и защиты
`.NET`, категорий B/C/CE, ЧПУ, ТСД, разрядов, зарплатных чисел и графиков 2/2.
Curated target обязан пройти контекстную неоднозначность: «уборщик» без объекта
не превращается автоматически в уборщика помещений.

Для phrase recognition подтверждается целая наиболее длинная известная фраза
той же категории; слово «горячего» само по себе не является профессией.
При конфликте concepts одинаковой длины — abstention. Recognition vocabulary
строится из выбранного lexicon, не второй вручную расходящейся копии.
Для каждой пары обязательны положительный и запрещающий контрпример.
Старые `pyhton`, `знерщт`, protected technologies остаются регрессиями RU/EN-профиля.

Обязательные variants: `rewrite-default`, `no-spelling`, `no-layout`,
`no-curated-rewrites`, `rewrite-off`, `rewrite-weight-0.4`.
Переключатели независимы: отключение семейства действительно исключает его кандидатов.
`rewrite-off` должен совпасть с C4 при одинаковых параметрах.

### Риски и наблюдаемость

C5 может исправить редкий бренд в распространённую технологию; поэтому нужны
protected/custom denylist, короткая дистанция и отказ при неоднозначности.
Curated rewrite направленный: разрешение `кладовшик→кладовщик` не разрешает обратное.
Однократная раскладка не применяется ко всему смешанному запросу без анализа spans.
Нельзя объявлять deterministic rule match «уверенностью 0.95»: показываются
правило, edit distance, кандидат, признаки защиты и причина принятия/отказа.

## 9. Полный состав контента и интерфейсов поставки

Пять presets — семейства опытов, а перечисленные варианты — их готовые абляции,
не дополнительные верхнеуровневые эксперименты. Все новые groups располагаются
в `configs/jobboard/lexical/` с существующими Hydra wrappers, без нового toolchain.

| Обязательный deliverable | Содержимое, которое нельзя отложить |
| --- | --- |
| Source profiles | Заполненный fictional fixture; external template с одной картой полей |
| Пять presets | Рабочая Hydra composition, все targets, default параметры и понятные имена |
| Ranking | Полные Painless source/params для combined, single-signal, tiers, phrase-rescore |
| QU | Реальные algorithms, включённые словари, keyboard map, rewrite pairs, guards и trace |
| Абляции | Все явно перечисленные варианты, без заглушек и ручных Python edits |
| Query pack | ≥200 русскоязычных запросов с ожидаемым parse/rewrite outcome, reason и контрпримерами; состав в 006A |
| Fixture pack | ≥96 вымышленных рабочих вакансий, ≥32 контрастных группы, все ranking metadata и крайние случаи |
| Notebook | Сборка, изменение параметров, offline preview, один запрос во всех presets, таблицы, batch/export |
| Research artifact | Безопасный request, parse/rewrite trace, hits/sort, timing и variant identity |
| CLI и docs | Готовые команды для каждого preset, смены source/boost/script и batch |
| Проверки | Локальные gates и регрессии прежних contracts, без запуска OpenSearch |

Распределение query pack по C4/C5/guards/ranking cases задано в 006A;
минимальные объёмы считаются без дублирования между группами. У C2/C3 query cases дополняются явно указанными
парами документов и математическим ожидаемым порядком; это не computed BM25.
Fixture не требуется индексировать: HTTP stub возвращает контролируемые hits.
Синтетические grades не переносятся на реальные ids; для QU ожидаемый parse не
равен доказательству релевантности поисковой выдачи.

Все правила и content работают offline из checkout после обычного setup проекта.
Нет незаполненного «добавьте словарь», «напишите script» или внешнего download на запуске.
Разрешены только неизбежные site-specific inputs: URL/auth, index/mapping,
названия/типы/значения реальных полей, желаемые coefficients и timestamp сессии.

### Возможности источника

Text fields обязательны. Ranking fields необязательны для проекта в целом:
C1/C4/C5 работают без них. Полный C2 требует date+quality, C3 category+stable id;
single-signal C2 требует только активное поле. Если реальное поле отсутствует,
соответствующий режим явно недоступен; это ограничение данных, не незавершённый код.

Skill field в C4 необязателен: поставляется вариант routing по title/description;
если skill field объявлен, его вес настраивается. Новое поле не создаётся в индексе.
Всем scripts нужны подтверждённые single-valued typed fields/doc values.
Профиль декларативный; локальная проверка не называется проверкой remote mapping.

## 10. Готовый notebook и research artifact

Ячейки notebook поставляются без сохранённых outputs и допускают повторный запуск:

1. Один блок source/field map, session timestamp, variant list и коэффициенты.
2. Повторная Hydra compose/instantiate после изменения конфигурации.
3. До HTTP: original/normalized, slots, residual, aliases, rejected candidates,
   accepted rewrite, reason codes, budgets и окончательный DSL каждого варианта.
4. Один вызов для сравнения C1–C5 на одинаковом запросе; недоступность preset
   выявляется при preflight до начала сравнения, без молчаливого пропуска.
5. Таблица variant/rank/id/title/score/sort, используемые metadata и перемещения
   документов между вариантами; по выбранному документу — полный `_source`.
6. CSV-набор запросов → те же presets → атомарный research export.
   Отдельный выбор одного варианта продолжает поддерживать прежний CSV spec 005.

Технические diagnostics не смешиваются с fields вакансии. В новой поставке
добавляется **отдельный** JSON research artifact `schema_version=1`:

```text
session: fixed_now_ms, variants, configured_source_id
results[]:
  query_id, original_text, variant_id
  request_body                       # точный JSON отправленного search без connection/auth
  understanding:
    normalized_text, hints[], residual[], syntax_enabled
    rewrite_candidates[], accepted_rewrite, decisions[], budget_usage
  timing: client_elapsed_ms, server_took_ms
  hits[]: id, rank, score, sort_values, metadata, fields
```

`hints[]` содержит kind/span/surface/canonical/rule_id/status/alternatives.
`rewrite_candidates[]` содержит source_span/source_text/target_text/concept_id/
mechanisms/edit_distance/decision/reason; edit_distance=null для не-spelling.
`decisions[]` содержит code/span/details для защиты, ambiguity, syntax и budget.
Spans hints относятся к явно обозначенному normalized text; rewrite source_span —
к original-normalized. Переписанный текст имеет отдельный parse, не чужие offsets.
Поэтому `understanding` включает отдельный `rewritten_parse` при accepted rewrite.

`metadata` содержит `values`, `flags` и `origin=returned_source_not_doc_values`:
это локальная диагностика возвращённых полей для активных ranking signals.
Отсутствие поля в `_source` не доказывает его отсутствие в doc values.

`sort_values=[]` при отсутствии sort; optional server_took=null только если допустимый
ответ действительно не содержит took. Поля numeric валидируются; данные snapshot-ятся.
`configured_source_id` обозначает индекс/профиль; неизменность снимка индекса он не подтверждает.
Request может содержать тексты/фильтры исследования, но не URL/Authorization.

Прежние searches.json/comparison.json/CSV не меняются. Новая модель результата
хранит technical metadata явно на границе search result; она не протаскивается
через user context, документ fields или глобальный mutable «последний ответ».
Точный Python тип — тактический выбор реализации, но этот observable contract обязателен.
Search preview и execution используют один compiler; notebook не копирует формулы
и не вызывает HTTP напрямую в обход поискового владельца.

Для script_score показывается итоговый script score, для tiers — lexical score
и отдельные sort keys, для rescore — финальная оценка. Составляющие F/Q можно
посчитать локально по возвращённым metadata с пометкой «локальный расчёт».
Phrase score P не восстанавливается из финального score и не показывается как измеренный.
Explain не обязателен для поставки; выдуманных explain-компонент быть не должно.

## 11. Ownership, совместимость и ошибки

QU algorithms остаются в `src/components/query/jobboard`, значения разбора —
в `src/domain/request`, query/ranking compilation — в существующем OpenSearch
transformer/compiler, HTTP/result conversion — в integrations. Notebook/experiment
владеет сценарием, reporting — новым артефактом. Всё Python-поведение находится в src.
Новые словари/правила/source/boosts живут в Hydra. Нет отдельного backend, DI layer,
универсального query templating language или dynamic arbitrary JSON merge поверх filters.

Существующие parsed/fuzzy и main default сохраняют поведение. Новый C4/C5 включается
явным preset. Код может переиспользоваться без навязывания новой политики старым опытам.
Canonical architecture/CONTEXT/development/README обновлены вместе с реализацией.
Команды и соответствие presets конфигурационным группам находятся в
[руководстве по внешним сервисам](../usage.md).

До HTTP проверяются missing required config, types, budgets, scripts JSON params,
field capabilities и несовместимость sort/rescore. Painless source локально
не компилируется: invalid source и запреты контекстов выявит реальное окружение.
`script_score` может быть запрещён expensive-query policy; score и number_sort
могут иметь разные разрешения. Настройки кластера не меняются автоматически. [O3, O6]

Неоднозначность rewrite → явный abstention до HTTP с original path; это предусмотренное
поведение QU. Ошибка parser/compiler/script/HTTP/partial response → ошибка поиска,
не abstention и не fallback на baseline. В batch/compare ошибка прекращает run без
публикации частичного research artifact. Предыдущие read-only searches не откатываются;
уже показанные notebook rows не объявляются успешно завершённым batch.
No-overwrite export и independent snapshots сохраняются.

## 12. Acceptance evidence без OpenSearch

### Локальная корректность алгоритмов

- C4: spans/longest-match, ambiguity, original preservation, per-kind fields,
  multiword aliases, residual coverage, no inferred filters, unsupported syntax.
- C5: каждая edit operation, RU/EN layout в обе стороны, curated directionality,
  phrase membership, candidate dedup, abstention при разных concepts, protected
  spans, длины/budgets, отсутствие cascade, неизменность original QueryFilter.
- Metamorphic checks: alpha=beta=0 возвращает C1 score по математической модели;
  no-tiers возвращает C1 query; rewrite-off совпадает с C4 DSL; no-aliases не оставляет
  скрытой canonical expansion. Разные variants не мутируют общие словари/payload.
- Формулы проверяются на известных числах и bounds. Python-проверка математической
  модели **не выдаётся за исполнение Painless** и не заменяет передачу полного source.

### Составной путь и контент

- Реальные Hydra presets и все абляции instantiate из checkout.
- Preview DSL совпадает с перехваченным HTTP body, scripts/source/params передаются
  без потери строк, filter сохраняется во всех QU ветках и ranking wrappers.
- Реальный HTTP client → контролируемый ответ → SERP/research diagnostics → JSON,
  включая null score, sort arrays, missing metadata, пустую выдачу, malformed/failure.
- CLI и notebook code cells выполняются с локальным HTTP stub; исходный notebook
  без outputs. Поставляемые команды проверены, secrets отсутствуют в diagnostics.
- Наполненность словарей/query pack/fixtures проверяется содержательными cases,
  не только счётчиком записей. Все примеры разделов C4/C5 имеют local evidence.
- Parser/compiler failure до downstream call, network/script-error response без
  partial mutation/export, старый файл сохраняется, повторный ручной поиск возможен.
- Ruff check/format, ty, pytest, Markdown links и git diff checks. При недоступном
  Poetry допустим существующий project environment с явно названным ограничением.

**Локальный exit:** все пять семейств и их контент реализованы, запуск не требует
дописывать Python/словари/scripts, все доступные локальные gates пройдены.
OpenSearch не устанавливается, Painless не исполняется, реальное качество и latency
не заявляются проверенными. На этом граница поставки считается достигнутой.

## 13. Последующая сессия с продактом — использование, не блокер разработки

После переноса вводятся реальные поля и credentials, выполняется разрешённый search.
C1→C2/C3 сравнивают порядок при том же тексте; C1/старый parsed→C4 — интерпретацию;
C4→C5 — восстановление записи. Затем можно совместить удачный QU с выбранным ranking.
Зафиксировать timestamp, config и запросы. Если cluster запрещает script context,
показать ограничение и обсуждать доступ, не менять настройки молча.

Первые вопросы: какие перестановки полезны, где C4 переобобщает роль, какие исправления
C5 ошибочны, приемлем ли строгий tier, что значит missing metadata для продукта.
После ручного выбора собрать pool, разметить, разделить tuning/held-out и измерить
NDCG@10. Missing judgment не равен нулю. Пересечение выдач и rank shifts — диагностика,
не метрики качества. Cold compilation и warm latency измеряются отдельно.

Эти измерения требуют реальных данных и выполняются отдельно
от локальной проверки реализации.

## 14. Принятые границы реализации

Реализованы описанные defaults, типы QU и diagnostic contract: ограниченный metadata bonus, строгая tier-политика,
консервативные rewrites/aliases и отдельный research JSON. Коэффициенты остаются
стартовыми гипотезами для сессии с продактом; обязательный starter content поставлен.

Не входят LLM/embeddings/LTR, query-log learning, automatic morphology,
полный ESCO import, индексирование/reindex, analyzer changes, stored scripts,
server search pipelines, arbitrary DSL editor, pagination и live OpenSearch tests.
Словари и scripts редактируемы через конфиги в рамках явно описанных механизмов.

Поставлены общий profile/diagnostics, C1–C5, routing/rewrite/content,
notebook/CLI/batch. Локальные проверки подтверждают contracts и передачу DSL,
но не исполнение Painless и не преимущество поиска на реальных данных.

## 15. Первичные источники

Источники прочитаны для research notes 2026-09-29; `/latest/` меняется со временем.
Подробное подтверждение Painless контекстов и version-pinned source — в
[записке Painless](../research/2026-09-29-painless-ranking.md), QU — в
[записке Query Understanding](../research/2026-09-29-query-understanding.md).

- **O1:** [OpenSearch Multi-match](https://docs.opensearch.org/latest/query-dsl/full-text/multi-match/).
- **O2:** [OpenSearch Match phrase](https://docs.opensearch.org/latest/query-dsl/full-text/match-phrase/).
- **O3:** [OpenSearch Script score](https://docs.opensearch.org/latest/query-dsl/specialized/script-score/).
- **O4:** [OpenSearch Function score](https://docs.opensearch.org/latest/query-dsl/compound/function-score/).
- **O5:** [OpenSearch Sort](https://docs.opensearch.org/latest/search-plugins/searching-data/sort/).
- **O6:** OpenSearch 2.19.0: [ScriptService](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/ScriptService.java), [ScriptCache](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/ScriptCache.java), [ScriptSortBuilder](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/search/sort/ScriptSortBuilder.java).
- **O7:** [OpenSearch Rescore](https://docs.opensearch.org/latest/query-dsl/rescore/).
- **O8:** [OpenSearch 2.19.0 ScriptDocValues](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/index/fielddata/ScriptDocValues.java).
- **Q1:** [OpenSearch 2.19 Match](https://docs.opensearch.org/2.19/query-dsl/full-text/match/).
- **Q2:** [OpenSearch 2.19 Multi-match](https://docs.opensearch.org/2.19/query-dsl/full-text/multi-match/).
- **Q3:** [OpenSearch 2.19 Fuzzy](https://docs.opensearch.org/2.19/query-dsl/term/fuzzy/).
- **Q4:** [LinkedIn — Building the next generation of job search](https://www.linkedin.com/blog/engineering/ai/building-the-next-generation-of-job-search-at-linkedin).
- **Q5:** [European Commission / ESCO — Non-preferred term](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/non-preferred-term).

Рекомендованные алгоритмы и starter rules — проектные решения, не гарантия
опубликованного улучшения. Подробная граница переноса практик — в исследовании QU.
