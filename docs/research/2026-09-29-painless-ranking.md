# Пять кандидатов ранжирования: OpenSearch и Painless

Эта записка исследует пять механизмов первоначального предложения. В актуальной
[spec 006](../specs/006-query-and-painless-candidates.md) они сгруппированы в C1–C3:
freshness/quality/rescore входят в C2, tiers — в C3. C4/C5 теперь посвящены
[Query Understanding](2026-09-29-query-understanding.md). Нумерация механизмов ниже
не является нумерацией актуальных экспериментов; технические выводы сохраняются.

Дата чтения источников: **2026-09-29**. Исследование предшествовало реализации
spec 006; теперь код и конфиги поставлены, локальные свидетельства находятся в
[development.md](../development.md). Скрипты на OpenSearch не исполнялись.
Официальная документация
`/latest/` — изменяемый источник; важные детали дополнительно проверены в исходном
коде **OpenSearch 2.19.0**. Версия, mapping, разрешённые script contexts и возможности
прокси целевого окружения неизвестны. Ссылки ниже подтверждают механизм, а не
преимущество формулы на реальных вакансиях.

## Вывод

Для совместной сессии полезны пять различимых гипотез: сильный текстовый контроль,
ограниченный бонус свежести, ограниченный бонус числового качества, строгие
категориальные приоритеты и дорогой бонус точности на ограниченном окне. Первые
три сохраняют основным сигналом текст, четвёртая намеренно ставит продуктовый
приоритет выше текста, пятая проверяет trade-off качества и стоимости.

Это не пять доказанно лучших формул. Начальные коэффициенты — управляемые
гипотезы; полнотекстовый score зависит от запроса, индекса и статистики shard.
Сравнение имеет смысл при одинаковых filters, профиле полей, тексте запроса и
зафиксированном времени. Подробнее о lexical DSL — в
[исследовании от 26 сентября](2026-09-26-opensearch-lexical.md).

## 1. Title-led lexical: необходимый контроль

`multi_match` с явными `fields` и boosts, `type=best_fields`, отдельный optional
`match_phrase` по title. `best_fields` берёт лучший field score, `tie_breaker`
добавляет заданную долю остальных. Phrase — совпадение проанализированных термов
с позициями, а не буквальное равенство исходной строки; `slop=0` сохраняет точную
последовательность, перестановка двух термов требует как минимум `slop=2`. [S1, S2]

**Фактура:** повышать короткое название роли над случайным упоминанием в длинном
описании можно понятными полями и boosts, без новых признаков индекса. Отдельный
phrase signal отличает «data engineer» от двух разрозненных слов. Контроль нужен,
чтобы не приписать Painless выигрыш от одновременно изменённых текстовых весов.

Обязательный текст должен оставаться в `must` либо в группе с явным
`minimum_should_match`: у `bool` при наличии `must`/`filter` default MSM равен 0.
Optional phrase boost не должен превращаться в дополнительный обязательный фильтр.
`best_fields` применяет operator/MSM по полю; слова, распределённые по разным полям,
могут не пройти строгую coverage. [S1, S3]

## 2. Ограниченная свежесть через script_score

Пусть `S0` — score того же lexical query, `age=max(0,now-published_at)` в днях,
`h>0` — период полураспада. Рекомендуемая гипотеза:

```text
F = 2^(-age/h)
S = S0 * (1 + alpha * F), alpha >= 0
```

При `alpha=0.3` множитель лежит в `[1,1.3]`: новая вакансия получает до 30%, через
`h` дней — до 15%, через `2h` — до 7.5%. Это математическое ограничение вклада,
не гарантия отсутствия нежелательной перестановки. Вакансия со score 8 может
обойти score 10 при максимальном бонусе; score 6 — уже нет.

**Фактура:** явная цена свежести, отсутствие полного вытеснения текста датой,
объяснимый полураспад и обратимость при `alpha=0`. Дата публикации должна означать
публикацию вакансии, а не техническое обновление индекса: иначе легко награждать
перепубликацию. Отсутствующая дата даёт `F=0`; будущая дата при clamp даёт `F=1`
и должна быть видна как проблема данных. Это предлагаемая продуктовая политика.

`script_score` оборачивает обязательный lexical query; `_score` внутри скрипта —
score **его вложенного query**. Новая оценка должна быть неотрицательной.
`search.allow_expensive_queries=false` запрещает этот query. [S4, S7]

Дата в doc values OpenSearch 2.19 представлена `JodaCompatibleZonedDateTime`;
`doc[field].value.toInstant().toEpochMilli()` поддерживается исходным типом и
Painless allowlist. Время сравнения передавать `params.now_ms`, фиксируя на всю
сессию: это рекомендация воспроизводимости, а не требование API. [S9, S10]

## 3. Насыщаемый числовой сигнал качества

Для неотрицательного числового признака `x` и `pivot>0`:

```text
Q = x / (x + pivot)
S = S0 * (1 + beta * Q), beta >= 0
```

OpenSearch документирует Painless `saturation(value,pivot)` с этой формулой.
При `x=0` вклад нулевой; `x=pivot` даёт половину максимального бонуса;
`x=9*pivot` — 90%. При `beta=0.3` никакое большое `x` не увеличит lexical score
более чем в 1.3 раза. [S4]

**Фактура:** устойчивость к длинному хвосту числового признака и понятный параметр
«сколько считается достаточно». В отличие от прямого `S0*x`, крупный выброс не
получает неограниченную власть. Missing и отрицательные значения можно трактовать
как `x=0`; ненулевой bonus нужно заслужить известным корректным значением.

Нужен осмысленный готовый признак: полнота карточки, подтверждённое качество,
согласованный бизнес-показатель. Сырые клики/отклики не являются автоматически
качеством: накопленные counts смешивают экспозицию, возраст и привлекательность.
Исследование API не доказывает причинный смысл такого сигнала. Параметр pivot
выбирается по реальному диапазону; число без знания шкалы не является настройкой.

## 4. Категориальные tiers через _script sort

Порядок сортировки:

```text
_script(type=number, order=asc, source=category -> tier, params=field+mapping)
_score desc
stable_keyword_id asc
```

`_script`, поля `script`/`type`, number/string types и направление сортировки
подтверждены `ScriptSortBuilder` OpenSearch 2.19. Числовой sort имеет контекст
`number_sort`; родитель `AbstractSortScript` предоставляет `doc`, `params`,
`_score`. Это отдельный ключ сортировки, а не изменение `_score`. [S11]

**Фактура:** продукт может сформулировать жёсткий порядок «сначала категория A,
затем B, затем остальные». Внутри категории сохраняется текстовая релевантность;
в отличие от большого boost, более низкая категория никогда не перескочит более
высокую из-за высокого BM25. Это полезная и одновременно сильная политика:
слабое текстовое совпадение категории A обгонит отличное совпадение категории B.
Обязательный lexical retrieval всё равно ограничивает допустимых кандидатов.

Missing/unlisted category получает явный default tier. Поле должно иметь
согласованную единственную категорию; `.value` для массива не означает «основная
категория из _source». Stable ID — реальное уникальное keyword-поле с doc values,
а не обещание сортировать по внутреннему `_id`.

Search response содержит массив `hit.sort` в порядке sort clauses; его необходимо
сохранять отдельно от `_score`. При сортировке по полю score по умолчанию не
вычисляется; `track_scores=true` явно запрашивает его. Для этой вариации он нужен
для анализа вместе с ключами сортировки. [S5]

## 5. Phrase-aware Painless rescore ограниченного окна

Первая стадия: тот же lexical retrieval со score `S0`. Вторая стадия:
`rescore.query.rescore_query = script_score(query=match_phrase(title,text),script=...)`.
Пусть `P` — **phrase score**, `k>0`, `F,Q` — сигналы выше в `[0,1]`:

```text
R = P / (P + k) * (1 + beta*F + gamma*Q)
S = S0 + lambda*R
query_weight = 1
rescore_query_weight = lambda
score_mode = total
window_size = 200
```

При неотрицательных beta/gamma/lambda бонус ограничен сверху
`lambda*(1+beta+gamma)`. Начальное `lambda=2` — гипотеза в единицах score: это
**абсолютный**, а не относительный бонус, поэтому чувствителен к шкале lexical
score конкретного запроса. Phrase nonmatch не получает бонус; документ вне окна
тоже сохраняет `S0`. Это следует из `QueryRescorer`: при nonmatch возвращается
`firstPassScore*queryWeight`, при match комбинируются взвешенные scores. [S6, S8]

**Фактура:** добавляет точность последовательности слов только перспективным
кандидатам, а свежесть/качество усиливают этот доказанный текстовый сигнал.
Карточка без phrase match не поднимается только из-за даты или бизнеса. Насыщение
`P/(P+k)` ограничивает дополнительный вклад phrase score. Это содержательная
гипотеза для точных названий ролей, но full-query phrase на «python senior remote»
может почти никогда не совпасть в title: полезность зависит от состава запросов.

Ключевые ограничения:

- `_score` внутри rescore script — `P`, **не S0**; first-pass score комбинирует
  внешний `rescore`. Подмена этой семантики даёт другую формулу. [S7, S8]
- Окно считается **на каждом shard**, до объединения. При W=200 и 10 shards
  верхний budget — 2000 кандидатов второй стадии, не 200 на весь поиск. [S6]
- Rescore не возвращает пропущенные retrieval документы. W — качество/стоимость,
  его нужно сравнить, например 100/200/500, при одинаковых остальных параметрах.
- Запрещена явная сортировка кроме `_score desc`: нельзя добавить даже secondary
  ID sort. Общий tie-break с предыдущими кандидатами здесь не гарантирован. [S6]
- При пагинации W должен быть неизменным. Нельзя считать rescore rerank уже
  полученной клиентской RRF выдачи. [S6]

Это обычный **query rescore с вложенным script_score**. Не требуется предполагать
наличие отдельного нового script-rescorer API или ML/rerank pipeline.

## Общий контракт Painless и конфигурации

1. **Два разных missing.** `doc.containsKey(field)` проверяет наличие mapping;
   `doc[field].size()>0` — значение у конкретного документа. Только после обоих
   проверяется `.value`. `ScriptDocValues.get(0)` при отсутствующем значении
   бросает исключение, включая даты. Комментарий в исходнике Dates про epoch при
   missing устарел относительно исполняемого метода. [S9, S12]
2. **Проверки missing не исправляют mapping.** Неверный тип, отсутствие пригодных
   doc values/fielddata, неожиданная многозначность остаются ошибками профиля.
   Не читать большой `_source` на каждом scoring как молчаливый fallback.
3. **Source отдельно от params.** YAML содержит `lang: painless`, многострочный
   `source`, JSON-совместимые `params`; поля/коэффициенты/фиксированное время идут
   параметрами. `ScriptCache` 2.19 использует lang, code, context и options как
   ключ компиляции, без runtime params. Изменение коэффициента не требует нового
   исходника; редактирование source может потребовать компиляции и попасть под
   rate limit. Это cache компиляции, не cache результатов поиска. [S13]
4. **Права проверяются на реальном search.** `script.allowed_types` и
   `script.allowed_contexts` могут запретить inline и нужный контекст; score и
   number_sort — разные contexts. Разрешение search не гарантирует разрешение
   `_scripts/painless/_execute` или stored-script API. Не предлагать изменение
   админ-настроек как скрытую часть эксперимента. [S11, S14, S15]
5. **Ошибка скрипта — ошибка эксперимента.** Не подменять запрет/compilation error
   пустой выдачей или обычным lexical ranking; иначе сравнение теряет смысл.
6. **Evidence разделяется.** Локально проверяются Hydra→DSL→HTTP, params и сохранение
   scores/sort values. Только настоящий OpenSearch подтверждает компиляцию,
   исполнение Painless, mapping и порядок. Только просмотр/разметка вакансий
   подтверждает пригодность продуктовой гипотезы.

## Программа живой проверки

Сначала зафиксировать версию, поля и их cardinality, доступные contexts; выполнить
каждый режим на малом наборе запросов. Для freshness проверить missing/future/old;
для quality — missing/zero/pivot/outlier; для tiers — missing/unknown и конфликт
приоритета с релевантностью; для rescore — phrase match/nonmatch и границу W.

Сохранять итоговый DSL, resolved script params без secrets, фиксированный now,
порядок/ID/score/sort, server took и отдельную client latency, failures/shard status.
Скрипт и параметры сами по себе не объясняют реальное качество. Сравнивать C1 с
каждой вариацией на одинаковом source snapshot насколько это доступно, размечая
перестановки; оценивать latency отдельно для холодной компиляции и повторов.
Это предлагаемые evidence, а не выполненные испытания.

## Первичные источники

Все перечисленные страницы/файлы прочитаны 2026-09-29. Целевой endpoint,
credentials и реальные вакансии не использовались.

- **S1:** [OpenSearch Multi-match](https://docs.opensearch.org/latest/query-dsl/full-text/multi-match/) — поля, boosts, best_fields, operator/MSM, tie_breaker.
- **S2:** [OpenSearch Match phrase](https://docs.opensearch.org/latest/query-dsl/full-text/match-phrase/) — analysis, позиции, slop.
- **S3:** [OpenSearch Bool](https://docs.opensearch.org/latest/query-dsl/compound/bool/) — eligibility и default MSM.
- **S4:** [OpenSearch Script score](https://docs.opensearch.org/latest/query-dsl/specialized/script-score/) — вложенный query, nonnegative scores, saturation и expensive-query restriction.
- **S5:** [OpenSearch Sort](https://docs.opensearch.org/latest/search-plugins/searching-data/sort/) — последовательность sort, hit.sort и track_scores.
- **S6:** [OpenSearch Rescore](https://docs.opensearch.org/latest/query-dsl/rescore/) — per-shard window, weights/modes, запрет sort и pagination.
- **S7:** [OpenSearch 2.19.0 ScoreScript](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/ScoreScript.java) — get_score как score inner query.
- **S8:** [OpenSearch 2.19.0 QueryRescorer](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/search/rescore/QueryRescorer.java) — match/nonmatch комбинация и topN.
- **S9:** [OpenSearch 2.19.0 ScriptDocValues](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/index/fielddata/ScriptDocValues.java) — dates, size и missing exceptions.
- **S10:** [JodaCompatibleZonedDateTime](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/JodaCompatibleZonedDateTime.java) и [Painless java.time allowlist](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/modules/lang-painless/src/main/resources/org/opensearch/painless/spi/java.time.txt) — toInstant/toEpochMilli.
- **S11:** [ScriptSortBuilder](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/search/sort/ScriptSortBuilder.java), [NumberSortScript](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/NumberSortScript.java), [AbstractSortScript](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/AbstractSortScript.java) — _script и sort context; OpenSearch 2.19.0.
- **S12:** [OpenSearch 2.19.0 LeafDocLookup](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/search/lookup/LeafDocLookup.java) — containsKey и ошибка unmapped field.
- **S13:** [OpenSearch 2.19.0 ScriptCache](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/ScriptCache.java) — ключ cache компиляции и compilation limit.
- **S14:** [OpenSearch 2.19.0 ScriptService](https://github.com/opensearch-project/OpenSearch/blob/2.19.0/server/src/main/java/org/opensearch/script/ScriptService.java) — allowed types/contexts и отказ исполнения.
- **S15:** [OpenSearch Execute inline script](https://docs.opensearch.org/latest/api-reference/script-apis/exec-script/) — отдельный execute API, score context и context_setup.
