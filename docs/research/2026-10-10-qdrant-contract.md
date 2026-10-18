# Qdrant: контракт коллекции и векторного поиска вакансий

Дата проверки первичных источников: **2026-10-10**. Это исследование и предложение
для [spec 008](../specs/008-vector-search.md), а не свидетельство реализации или
live smoke. Qdrant, inference runtime и модель не запускались; образы, зависимости
и weights не устанавливались. Прочитаны текущие архитектура, доменные контракты,
статус поставки и владельцы search/payload/export в `src/`.

## Вывод для первой реализации

Рекомендуемый исходный контракт: **Qdrant 1.19.2**, одна новая физическая коллекция
на сборку, один unnamed dense float32 vector на вакансию, `Cosine`, полные исходные
domain id/`fields` в payload. Поиск использует native Query Points API и серверные
filters. Коллекция после успешной проверки не изменяется приложением. **Remote
collection metadata — источник готовности**, имя physical collection выбирает
generation. Локальный JSON-артефакт — receipt/evidence, не дополнительный search
gate. Ни alias, ни online updates, ни
автоматический подбор совместимого индекса для первого сценария не нужны.

Это минимальное предложение, а не автоматически принятая ready-spec. Пользователь
поднимает Qdrant сам; приложение работает с предоставленным endpoint и создаёт
свои коллекции. Управление сервером, существующими коллекциями, snapshots,
кластером и credentials не следует из разрешения загрузить новый корпус.

## Версия и источники истины

- На дату исследования GitHub Releases API возвращает **v1.19.2**, опубликованную
  **2026-10-05**, как последний release. В этой версии есть исправления filtered
  HNSW и persistence; это дополнительная причина выбрать конкретный исправленный
  release, а не старую минимальную версию. [S1]
- Query API документирован с **v1.10.0**. Collection metadata документированы с
  **v1.16.0**. Следовательно, `1.16.0` — документированный feature floor для
  предлагаемой схемы metadata, но не обещание совместимости всего приложения со
  всеми версиями начиная с него. Metadata также присутствуют в OpenAPI tag
  `v1.15.5`: утверждать, что код впервые появился строго в 1.16, нельзя. [S2, S3, S4]
- Предлагаемый support contract первой итерации — один **проверяемый pin 1.19.2**.
  Старые/новые releases добавляются после отдельной проверки. Это позволяет не
  смешивать разные API хранения памяти: в pinned 1.19.2 schema есть `memory`,
  а legacy `on_disk` и `on_disk_payload` уже deprecated. [S2]
- Фактическая wire schema берётся из **tag v1.19.2**, не из `master` или случайного
  примера SDK. При развёртывании пользователь фиксирует также digest образа и
  подтверждает версию сервера. Никакой digest в этой заметке не выдуман.

## Native запрос и ответ

Для обычного nearest-neighbor поиска выполняется
`POST /collections/{collection_name}/points/query`. Пример ниже иллюстрирует
wire shape; длина query должна равняться dimension реального профиля. [S2, S5]

```json
{
  "query": [0.12, -0.03, 0.91],
  "filter": {
    "must": [
      {"key": "fields.city", "match": {"value": "Москва"}},
      {"key": "fields.remote", "match": {"value": false}}
    ]
  },
  "params": {"exact": false, "indexed_only": false, "hnsw_ef": 128},
  "limit": 50,
  "with_payload": true,
  "with_vector": false
}
```

Нельзя опустить `query`: без query и prefetch API возвращает points по id, а не
semantic search. `limit` задаёт candidate depth; `final_k` применяется существующим
`TopKTransformer` после успешной конвертации. В vector-only пути не требуются
prefetch, fusion, recommendation, inference objects или пользовательский DSL.
`filter.must` входит в retrieval, а не применяется Python-кодом к уже выбранному
top-k. Qdrant выбирает physical plan с учётом payload indexes и cardinality. [S2, S5, S6]

Успешный ответ содержит **`result.points`**, а не старый `result: [...]` от Search
Points API. Например:

```json
{
  "result": {
    "points": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "version": 7,
        "score": 0.83,
        "payload": {
          "document_id": "vacancy:123",
          "fields": {"title": "Комплектовщик", "city": "Москва", "remote": false}
        }
      }
    ]
  },
  "status": "ok",
  "time": 0.002
}
```

UUID в иллюстрации не вычислен из приведённого domain id; реальный adapter обязан
проверять связь point id с domain id по принятому алгоритму. `usage` и другие
технические поля могут присутствовать. `with_payload` по умолчанию false, поэтому
его нужно включать явно. Поисковый запрос не возвращает vectors: это экономит
передачу без потери полей вакансии. Каноническое поле Query API — `with_vector`;
`with_vectors` также принимается как serde alias в pinned source. У методов
retrieve/scroll свои формы запроса: нельзя механически переносить имена из SDK. [S2, S5]

### Один vector и метрика

Создание unnamed vector использует `"vectors":{"size":D,"distance":"Cosine"}`;
upsert — `"vector":[...]`; query не передаёт `using`. Named vector использует
`"vectors":{"text":{"size":D,"distance":"Cosine"}}`, upsert
`"vector":{"text":[...]}` и query `"using":"text"`. Это разные wire contracts.
Первая итерация может закрепить unnamed vector и отклонять чужую named schema;
добавлять сразу два поддерживаемых режима не требуется. [S2, S3]

| Distance | Направление возвращаемого score | Обработка |
| --- | --- | --- |
| `Cosine` | Больше — ближе | Qdrant нормализует vectors и вычисляет dot product |
| `Dot` | Больше — ближе | Нормализация не следует из выбора Dot |
| `Euclid` | Меньше — ближе | Внешний score — Euclidean distance |
| `Manhattan` | Меньше — ближе | Внешний score — Manhattan distance |

Направления закреплены в `Distance::distance_order`; preprocessing cosine query
также виден в `MetricQueryScorer`. Нельзя считать все scores «чем больше, тем
лучше», пересортировывать ответ по универсальному правилу или интерпретировать
score как вероятность/grade. Возвращённый порядок сохраняется, включая равные
scores; стабильность ANN tie order между запусками не обещается. [S7]

Qdrant normalization не заменяет embedding profile. В нём должны совпадать
immutable model revision, tokenizer/pooling, query/document prefixes,
truncation policy, normalization и dimension. Выход inference проверяется на
число vectors, dimension, конечные числовые элементы, представимость float32 и
ненулевую норму. Нулевой vector Qdrant сам не обязан отклонять: source оставляет
его нулевым. Значит, отклонение нулевого embedding — явный invariant приложения. [S7]

## Payload, identity и filters

Payload envelope предлагается закрепить как `{"document_id": domain_id, "fields": original_fields}`.
`Vacancy.id` берётся из envelope, `Vacancy.fields` — независимый полный snapshot
`fields`, а score — из ScoredPoint. Служебные metadata не подмешиваются в original
fields. Это сохраняет существующий контракт проекта без фиксированного списка
`title`/`description` и без второй таблицы для восстановления документов.

Qdrant point id допускает uint64 или UUID; произвольная строка вакансии не является
валидным native id. Предложение с namespace корпуса:
`corpus_namespace = uuid5(FIXED_PROJECT_NAMESPACE, corpus_identity)`, затем
`point_id = uuid5(corpus_namespace, domain_id)`. Project namespace фиксируется
once в контракте, version алгоритма и оба входа namespace входят в recipe.
`corpus_identity` — стабильное логическое имя корпуса, не pathname и не checksum
каждой generation; одна вакансия сохраняет point id при изменении других записей.
Вложенные namespaces исключают неоднозначность конкатенации двух строк.
UUIDv5 namespace+name определяют стабильный UUID; это не криптографическое
доказательство отсутствия коллизий. [S8, S9]

Перед любыми writes проверяется весь корпус:

- id — непустая строка; исходная строка сохраняется без case folding, Unicode
  normalization или тихого `strip`. Можно отвергать whitespace-only id, сохраняя
  точные остальные значения; это правило reader должно быть зафиксировано.
- Повторный domain id запрещён даже для одинакового документа. Разные id,
  получившие одинаковый UUID, также запрещены: проверяется весь mapping.
- У каждой записи JSON-object fields, только JSON-compatible значения, конечные
  числа, валидные объявленные filter fields и детерминированно собранный текст.
- Recipe фиксирует reader, параметры представления, ID algorithm и fingerprint
  фактических id/fields. Один pathname корпуса не идентифицирует его содержимое.

«Полные fields» означает сохранение JSON-значений, не исходного whitespace или
порядка ключей файла. Для lossless contract нельзя полагаться на неограниченные
Python int: безопасная первая граница для всех JSON integers —
`[-2^63, 2^64-1]`, для float — finite float64; invalid Unicode также отклоняется
до HTTP. Qdrant использует `serde_json::Value`, а обычный Number без
`arbitrary_precision` ограничен i64/u64/f64. Превращать большое число в float
или строку молча нельзя. Это ограничение storage boundary, не изменение полей. [S16]

Qdrant upsert **перезаписывает** point с тем же id. Поэтому проверку duplicate ids
нельзя отдавать серверу: при ошибке reader Qdrant может успешно принять меньше
уникальных документов, чем было записей. Idempotence допускает только повтор
точно того же batch в той же owned generation; она не разрешает переиспользовать
чужую/готовую коллекцию. [S8]

### Сопоставление scalar filters

`QueryFilter` остаётся исходным текстом. Ровно один непустой QueryFilter;
`TermFilter` и разрешённые через context `UserPropertyFilter` компилируются в AND.
Проверка всего request, объявленных типов, missing user property и неизвестных
filters завершается **до inference и Qdrant search**.

| Объявленный тип поля | Допустимое значение условия | Native condition |
| --- | --- | --- |
| `keyword` | string | `{"match":{"value":"Москва"}}` |
| `bool` | именно bool | `{"match":{"value":true}}` |
| `integer` | именно int, signed int64 | `{"match":{"value":7}}` |
| `float` | конечное число по явной float-policy | `{"range":{"gte":0.7,"lte":0.7}}` |

`MatchValue` **не принимает float**: `ValueVariants` содержит только string,
int64 и bool. Source integer match использует `as_i64`, range — `as_f64`.
Предложение для float-поля: принимать конечные JSON int/float, исключая bool;
int, который нельзя без потери представить в float64, отклонять. Это numeric
равенство представленных двоичных чисел, без epsilon и без округления денег.
Для integer поля `7.0`, bool и числовая строка отклоняются; integer equality
не следует реализовывать float-range из-за потери точности больших int64. [S2, S10]

Missing/null значение поля в документе можно разрешить как отсутствие свойства:
positive scalar condition его не выбирает. Present non-null значение должно
соответствовать объявленному типу уже при preflight корпуса. Null в query term
не означает автоматически `is_null`; оно отвергается. Scalar-only declaration
не принимает массив вместо scalar, хотя Qdrant умеет matching элементов массива.
Payload index — средство поиска/оценки cardinality, не замена этой проверки данных. [S6, S10]

### Пути полей

`fields.location.city` — путь по вложенным объектам; он отличается от literal key
`"location.city"` внутри fields. Qdrant поддерживает quoted segments, индексы и
wildcards; например `fields."location.city"` обращается к literal key с точкой.
Pinned parser не поддерживает произвольные JSON escapes внутри quoted segment.
Нельзя унаследовать OpenSearch `.keyword` или правило diagnostics «сначала точный
верхнеуровневый ключ»: это другой контракт. [S11]

Практический минимальный source profile связывает публичное имя term-поля,
явный путь относительно original fields и scalar type. Один и тот же path
используется preflight-проверкой и Qdrant compiler. Можно начать с путей по
JSON-объектам с безопасными сегментами; неподдерживаемый filter path отвергается
в конфигурации, а произвольные непроиндексированные fields всё равно сохраняются.
Arrays-of-objects требуют отдельной nested-filter семантики: обычные AND-условия
могут совпасть на разных элементах массива. Их не следует незаметно добавлять к
текущему scalar contract. [S10, S11]

Все разрешённые term-поля получают типизированные payload indexes **до загрузки
points**. Qdrant рекомендует это, чтобы filter-aware edges строились вместе с
HNSW. Имена — `fields.<declared path>`, типы — keyword/bool/integer/float.
Indexing каждого поля полного документа избыточно. Strict mode сервера может
отвергать фильтры без index; клиент не должен обходить это ограничение. [S6]

## Durable generation и публикация

### Remote metadata

Qdrant metadata — часть collection config, синхронизируемая consensus в кластере.
Create принимает `metadata`, GET возвращает её в `result.config.metadata`, PATCH
обновляет её. Это подходящее место для небольшого remote build contract:

```text
metadata.recsys = {
  schema_version,
  generation_id,
  physical_collection_name,
  state: building | ready,
  expected_points,
  embedding_profile,
  embedding_profile_sha256,
  source_profile,
  document_recipe,
  recipe_sha256,
  corpus_identity,
  corpus_sha256,
  id_namespace,
  payload_schema_version
}
```

Названия — проектное предложение. Readiness metadata не являются native feature
Qdrant: **их значение определяет приложение**. Qdrant не доказывает истинность
model revision и не запрещает менять ready collection. Нужен единственный writer
этой generation; поиск использует read-only доступ, если контур его предоставляет.
Гарантия immutable относится к поведению приложения и договорённости с владельцем
сервера, а не к защите от любого out-of-band изменения.

Remote descriptor должен быть достаточен для проверки без локального receipt:
полные embedding/source/payload/ID contracts и document recipe доступны в metadata,
а крупные corpus records остаются points. Fingerprints дополняют descriptor,
не заменяют неизвестным digest необходимые поля контракта. Search проверяет,
что physical name из metadata совпадает с настроенным именем: это также не даёт
незаметно выбрать alias вместо конкретной generation.

PATCH metadata делает **merge верхнего уровня**, не recursive deep merge.
Null удаляет верхнеуровневый ключ; объект заменяет значение ключа целиком.
Поэтому `{"metadata":{"recsys":{"state":"ready"}}}` потеряет остальные поля
объекта `recsys`. Нужно посылать полный объект owned namespace и затем проверять
его чтением. Это подтверждено `update_metadata → Payload::merge → merge_map`. [S3, S12]

У PATCH коллекции другой acknowledgment: `?timeout=N` задаёт ожидание commit,
response — `{"status":"ok","result":true,...}`. У этого endpoint нет `wait`,
`ordering` или read-consistency parameters. `result.status=completed` относится
к points/index mutation, не к collection PATCH. REST handler ожидает
`submit_collection_meta_op`; после успешного metadata PATCH выполняется GET
read-back. Ошибка timeout означает неопределённость результата, а не rollback. [S2, S15]

Локальный build artifact хранит полную recipe/profile, fingerprints, generation,
physical collection name, non-secret source identity, server version, число
документов, принятые index settings и evidence проверки. Endpoint credentials
туда не входят. Полный resolved Hydra config нельзя бездумно сериализовать с
секретами. Profile fingerprint сравнивается вместе с version его serialization;
dimension сама по себе не подтверждает embedding space.

### Порядок операций

1. **Preflight без writes.** Проверить всю конфигурацию, корпус, IDs, JSON,
   filter declarations и document rendering. Проверить runtime identity/model
   revision и доступную версию Qdrant read-only вызовами. Подготовить fingerprint
   immutable input snapshot; повторное чтение изменившегося файла не продолжает
   ту же сборку. Проверить destination артефакта там, где он задан, не обещая,
   что предварительная проверка исключает все будущие filesystem failures.
2. **Новая generation.** Сгенерировать уникальные generation id/collection name.
   Создать коллекцию с metadata `building`, vector schema и явными настройками.
   Existing collection — ошибка; не delete/recreate, не adopt и не resume.
   Проверка exists может улучшить ошибку, но решающим остаётся успешное create:
   между exists и create возможна гонка.
3. **Создать declared payload indexes.** `PUT .../index?wait=true`; проверить
   completed responses. Конфигурация первой итерации не отключает HNSW временно:
   это оставляет меньше состояний, которые требуется восстанавливать.
4. **Encode и upload bounded batches.** Проверить весь inference response batch
   до upsert. `PUT .../points?wait=true` с deterministic ids и полным envelope;
   принять только transport success + `status=ok` + `result.status=completed`.
   `acknowledged` недостаточно. После ambiguous timeout остановить сборку;
   не объявлять, что сервер откатил write. Первая реализация может вообще не иметь
   автоматических retries. [S2, S8]
5. **Проверить данные.** Повторно прочитать collection schema и metadata;
   `POST .../points/count {"exact":true}` должен дать точное ожидаемое число.
   Не использовать `CollectionInfo.points_count` как exact count. Для полного
   round-trip evidence проверить retrieval всех ожидаемых ids ограниченными batches:
   каждый id присутствует один раз, native id соответствует domain id, payload
   совпадает с подготовленным snapshot, vector существует и имеет правильную
   dimension/finite values. Retrieve запрашивает vectors явно. Эта полная
   проверка стоит дополнительных O(N·D + payload) чтения/передачи; выбор лишь
   sample должен честно ослабить acceptance claim. Cosine/float32 storage не
   требует bitwise равенства исходному inference JSON. [S2, S3, S8]
6. **Проверить эксплуатационную готовность.** Bounded ожидание здоровой collection
   (`status=green`, `optimizer_status=ok`) и требуемых payload indexes;
   записать actual settings/counts. Timeout — незавершённая сборка. Маленький
   корпус законно может иметь `indexed_vectors_count=0`; нельзя ждать равенства
   этого счётчика N. Отдельный exact smoke проверяет retrieval/filter contract,
   а ANN performance/recall принимаются отдельным тестом ниже. [S3, S13]
7. **Remote ready.** PATCH полного metadata namespace с `state=ready` только
   после проверок, затем read-back schema/metadata. После этого приложение
   больше не пишет points/profile/schema этой generation.
8. **Artifact и receipt export.** `run()` возвращает полноценный build artifact;
   existing `_wrapped_run` вызывает один JSON exporter после успеха. Export
   использует существующий atomic no-overwrite механизм. Это свидетельство
   завершённой сборки; оно не определяет готовность remote collection.

У Qdrant и filesystem нет общей транзакции. Если upload/verify/ready завершились,
но exporter упал, remote collection остаётся **ready и доступной для поиска**,
а CLI сообщает ошибку экспорта receipt. Нельзя объявить всю команду успешно
завершённой, но нельзя и представить remote generation как незавершённую.
Search source выбирает точное physical name и проверяет metadata/profile/schema;
наличие локального artifact не требуется. Никакой default/alias автоматически
не переключается. Ready до export предотвращает receipt, рекламирующий ещё
неподтверждённую коллекцию. Повторный export receipt может быть отдельной ручной
операцией; сбой filesystem не откатывает уже опубликованную remote generation.

Ошибка до ready оставляет `building` или неопределённое при сетевом сбое remote
состояние, но не successful artifact. Не нужно пытаться PATCH `failed` в каждом
exception: это второй потенциальный failure, способный замаскировать исходную
ошибку. Путь/phase/generation можно сообщить безопасно, не публикуя success.

Если для большого корпуса выбран минимальный protocol verification вместо полного
read-back, acceptance нужно назвать точно: preflight + completed upserts + exact
count + schema + ограниченный sample retrieval. Это не проверка round-trip всех
fields каждого point. Дополнительный exact count с `filter.must:[{"has_vector":""}]`
проверяет наличие unnamed vector у N points; native `HasVectorCondition` входит
в pinned schema. Он не доказывает совпадение всех payload с исходным corpus. [S2]

### Visibility, consistency и ANN

`wait=true` означает завершение применённой операции и доступность points для
поиска после ответа. Запись сначала проходит WAL. У Qdrant нет необходимого
OpenSearch-style `refresh`, который надо угадывать после upsert. Это **не**
обещание, что HNSW optimization уже закончилась. Неиндексированные segments
доступны для поиска обычным способом. [S8, S13]

`indexed_only=true` может исключать ещё неиндексированные большие segments и
приводить к неполным результатам. Для принятого контракта используется false;
такой latency shortcut нельзя включать скрыто. `indexing_threshold` измеряется
в **kB vectors на segment**, не в числе вакансий. Маленький fixture может
обслуживаться exact scan даже при `params.exact=false`. [S2, S3, S13]

Для первого standalone контура разумны явные shard/replication settings с одной
репликой и один последовательный writer. `wait`, `ordering` и `consistency` —
разные механизмы. `ordering=weak|medium|strong` определяет порядок writes;
read `consistency` задаётся query parameter, а `write_consistency_factor` — в
collection config. На replicated контуре нельзя переносить standalone-гарантию
только добавлением `wait=true`: нужно отдельно определить quorum/consistency и
проверить reads на репликах. Поддержку кластера не следует подразумевать. [S2]

## Search boundary и размещение в текущем каркасе

| Владелец | Его контракт |
| --- | --- |
| Source profile | Physical collection и ожидаемый embedding profile, scalar field declarations, payload/vector schema; `fields` пути принадлежат этому source |
| Query profile | Candidate depth, `final_k`, HNSW search parameters; initial Cosine contract и отсутствие score threshold до исследования качества |
| Index profile | Batch sizes и bounded timeouts, vector/payload index settings, generation naming; не server administration |
| `integrations/qdrant` | HTTP create/get/index/upsert/retrieve/count/query/PATCH для нужного lifecycle; validation native response; внешний document/result adapter |
| `core/transformers/qdrant` | Snapshot Request → полный preflight → profile check → encode query → native query → полная conversion → присвоить `items` |
| `experiments/indexing` | Последовательность сборки и единственное владение generation; вернуть build artifact после verify/ready |
| `components/reporting/artifacts` | Immutable snapshot recipe/evidence; существующий JSON exporter публикует его |

`QdrantIntegration` может использовать standard-library HTTP по принятому в
проекте образцу, сохраняя явные методы. Новый общий transport framework,
`VectorDatabaseService`, factory hierarchy и изменение `BaseIntegration` для всех
backends не нужны. Текущий `integrations/backend` не является подходящим владельцем
native Qdrant. Registry, PipelineTransformer и TopK переиспользуются.

Embedding остаётся локальным типизированным значением вызова, исходный QueryFilter
не меняется. Ни vectors, ни connection/profile/build state не записываются в
пользовательский `PipelineData.context`, filters или branches. Collection/profile
проверяются перед допуском поиска; строгий простой вариант — GET перед каждым
search. Позднее caching требует явного session contract. Metadata не обнаружат
любую внешнюю mutation неизменённого payload без полной повторной проверки.

Весь search response конвертируется до замены `data.items`: валидны envelope,
`result.points`, каждый point id/version/finite non-bool score, nonempty domain id,
JSON fields, UUID mapping; duplicate native/domain ids запрещены. Порядок сохраняется.
При `with_vector=false` отсутствующий vector нормален; нельзя требовать его для
обычной выдачи или заявлять, что response проверил все stored embeddings. Если
дополнительный wire field используется как evidence, он проверяется отдельно.
HTTP failure, повреждённый второй hit и несовместимый profile оставляют прежние
items/context/filters/branches целыми. Успешный `points:[]` — пустая SERP; ошибка
не становится пустой выдачей или lexical fallback.

В действующей [архитектуре](../architecture.md) утверждение «система не владеет
состоянием удалённого индекса» описывает OpenSearch-proxy путь. Принятие spec 008
потребует уточнить его scope: Qdrant-generation пишет application-owned experiment,
а runtime/server остаётся user-owned. Это изменение durable contract, не повод
переписывать действующий search-only OpenSearch adapter.

## RAM, GPU и проверка производительности

Для `N` points, dimension `D`, float32 и replication factor `R` исходные dense
vectors занимают примерно **N × D × 4 × R bytes**. При D=768, R=1:

| Корпус | Только float32 vectors |
| --- | --- |
| 100 тысяч | 0.286 GiB |
| 1 миллион | 2.861 GiB |
| 10 миллионов | 28.610 GiB |

Это не общий RAM budget. Добавляются HNSW, id tracker, payload indexes, cached
payload, runtime и optimizer buffers, WAL/disk, исходный corpus в Python и
inference runtime. Текущий Qdrant capacity guide даёт грубые ориентиры:
HNSW ≈ `N × R × m × 2 × 4 × 1.2`, id tracker ≈ `N × R × 52 bytes`, плюс headroom.
Их следует проверять на representative corpus, а не выдавать за измерение.
Fresh generation временно сосуществует со старой, что умножает storage budget;
сборка также может требовать временных segments. [S14]

Pinned schema 1.19.2: original dense vectors поддерживают `memory=cached|cold`,
но не `pinned`; payload — `cached|cold`; данные persist на disk независимо от
memory tier. Default cached vectors/HNSW и cold payload подходят для первого
исследования на RAM-oriented машине; **новый API memory не требуется добавлять
в конфигурационный surface приложения**. User-owned server задаёт storage defaults,
приложение читает и фиксирует actual settings в artifact. Если выбран более старый сервер, нельзя
отправлять новые поля на основании current docs. [S2, S14]

GPU в данном проекте нужен прежде всего для embedding inference. Наличие GPU не
делает CPU/RAM vector-search latency известной и не отменяет sizing Qdrant.
GPU HNSW indexing — отдельная runtime возможность и настройка; первая итерация
не должна неявно занимать GPU одновременно Qdrant и inference. Quantization,
float16 storage и on-disk tuning меняют accuracy/performance и требуют отдельной
проверки; достаточный RAM позволяет начать без них.

План приёмки разделяет три независимых вопроса:

1. **Wire/domain correctness:** маленькая collection, реальные embeddings,
   exact query `params.exact=true`, filters каждого поддержанного типа,
   round-trip всех fields/ids, malformed responses, отсутствие partial mutation,
   restart persistence, несовместимый profile, ошибка exporter. Локальный HTTP
   fixture может проверить adapter/orchestration, но не исполнение Qdrant.
2. **ANN recall:** representative corpus достаточно велик для реального HNSW;
   same query vectors, filters, collection и k, baseline `exact=true`, ANN с
   выбранным `hnsw_ef`. Измерять overlap/recall@k относительно exact, отделяя
   genuine ties на границе k; зафиксировать query set, N/D, actual config и counts.
   Recall ANN относительно exact не является human relevance/NDCG.
3. **Latency/capacity:** после завершения build, отдельно warm/cold измерения,
   p50/p95, single/query-batch inference time, Qdrant time и end-to-end latency,
   filter selectivity, concurrency, RAM/disk. Threshold/ANN настройки берутся
   из принятой цели; чисел SLA и достигнутого качества это исследование не задаёт.

## Осталось подтвердить перед ready-spec/live acceptance

- Pin сервера и доступные API/permissions в конкретном user-owned endpoint;
  standalone versus replicated deployment, storage path/persistence.
- Фактические N, средние и максимальные размеры fields/text, term fields/types,
  supported source paths и политика missing/null; выбор полного post-upload
  read-back либо явно более слабого evidence.
- Точный embedding profile и identity attestation inference runtime; одинаковый
  D не подтверждает compatible model revision.
- Лимиты времени/размера запроса и batch, latency/ANN recall targets, hardware
  budget при coexistence generations. Ни один из этих результатов не измерен здесь.

## Первичные источники

[S1]: https://github.com/qdrant/qdrant/releases/tag/v1.19.2
[S2]: https://github.com/qdrant/qdrant/blob/v1.19.2/docs/redoc/master/openapi.json
[S3]: https://qdrant.tech/documentation/concepts/collections/
[S4]: https://github.com/qdrant/qdrant/blob/v1.15.5/docs/redoc/master/openapi.json
[S5]: https://github.com/qdrant/qdrant/blob/v1.19.2/lib/api/src/rest/schema.rs#L569
[S6]: https://qdrant.tech/documentation/concepts/indexing/
[S7]: https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/types.rs#L325
[S8]: https://qdrant.tech/documentation/manage-data/points/
[S9]: https://docs.python.org/3.12/library/uuid.html#uuid.uuid5
[S10]: https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/payload_storage/condition_checker.rs#L224
[S11]: https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/json_path/parse.rs
[S12]: https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/common/utils.rs#L43
[S13]: https://qdrant.tech/documentation/search/search/
[S14]: https://qdrant.tech/documentation/capacity-planning/
[S15]: https://github.com/qdrant/qdrant/blob/v1.19.2/src/actix/api/collections_api.rs#L137
[S16]: https://docs.rs/serde_json/latest/serde_json/struct.Number.html

- **S1:** release history; проверен также [GitHub latest release API](https://api.github.com/repos/qdrant/qdrant/releases/latest).
- **S2:** pinned OpenAPI: paths, request/response schemas, scalar types, consistency, memory.
- **S3–S4:** collection metadata/version floor, schema, approximate counts, named vectors.
- **S5:** pinned REST query definitions, defaults, `with_vectors` alias и `QueryResponse.points`.
- **S6:** payload types/indexes, filter-aware HNSW, порядок создания indexes.
- **S7:** distance direction; также [метрики и normalization](https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/spaces/simple.rs)
  и [query preprocessing](https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/vector_storage/query_scorer/metric_query_scorer.rs#L38).
- **S8–S9:** native ids, upsert/idempotence, count/retrieve, wait и UUIDv5.
- **S10–S11:** исполняемые scalar/path rules; дополнительно [filtering и nested arrays](https://qdrant.tech/documentation/concepts/filtering/).
- **S12:** shallow metadata merge; [collection update owner](https://github.com/qdrant/qdrant/blob/v1.19.2/lib/collection/src/collection/collection_ops.rs#L159).
- **S13–S14:** search visibility/indexed_only/exact и capacity estimates.
- **S15:** PATCH collection, timeout и ожидание metadata operation.
- **S16:** представление JSON numbers; [pinned Qdrant manifest](https://github.com/qdrant/qdrant/blob/v1.19.2/Cargo.toml)
  и [Payload type](https://github.com/qdrant/qdrant/blob/v1.19.2/lib/segment/src/types.rs#L2523).

Проверены доступность первичных источников и применимые source definitions.
Documentation links проверяются отдельно; runtime tests для этой заметки не
запускались. Ни release/schema research, ни предполагаемые проверки выше не
являются выполненным live evidence.
