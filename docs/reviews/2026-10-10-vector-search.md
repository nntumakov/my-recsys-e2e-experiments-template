# Ревью поставки 008: векторный поиск

Дата: 2026-10-10. Baseline: `e15aa3b`.
Проверены tracked изменения и authored untracked файлы поставки, включая код,
Hydra, notebook, tests и текущую документацию. Пустой history diff не использовался
как свидетельство отсутствия изменений.

Два GPT-6 Astra субагента независимо проверили Standards/architecture и соответствие
[spec 008](../specs/008-vector-search.md). Третий проверил достаточность локального
acceptance evidence. Ревью охватывает L01–L21; реальная приёмка G01–G06 не выполнена.

## Standards и specification

Оба ревью выявили P2: `qdrant.vector.size/distance` были объявлены в Hydra, но не
передавались владельцу поведения. Overrides `384/Dot` успешно собирали объекты,
которые продолжали использовать `768/Cosine`. Это нарушало проверку конфигурации
и делало сохранённый конфиг неверным описанием выполненного эксперимента.

Исправление: `qdrant.source.vector` связан с `${qdrant.vector}`; QdrantSource
отвергает расхождение с dimension embedding profile и фиксированной Cosine schema
до HTTP. Проверки в `tests/test_vector_configuration.py` подтверждают отказ
для неправильной dimension, bool вместо dimension, неподдерживаемой distance,
null и list вместо обязательного object. Профиль остаётся источником истины:
материализованная декларация проверяется, отдельно в source не хранится.
Specification reviewer повторно проверил исправление; исходное замечание закрыто.
Других actionable замечаний Standards/architecture и specification не осталось.

## Достаточность локальной приёмки

Дополнительно выявлены недостатки evidence, а не новые runtime defects:

- L04: collision UUID проверялся в search response, но не в index preflight.
- L06: не было отдельной проверки corpus digest на порядок записей и точность ids.
- L14: маленькие fixtures не исполняли вторую порцию полного read-back.

В `tests/test_vector_workflow.py` добавлены семь проверок: collision до первого HTTP,
успешный read-back десяти документов порциями 8+2, missing/duplicate/payload/vector
повреждения во второй порции и golden digest с перестановками/изменениями данных.
Отказы не публикуют ready и receipt. Все семь проверок прошли; адресные Ruff,
formatting и type checking прошли. Код production для этих проверок не менялся.

Общие gates и границы внешней приёмки записаны в
[development](../development.md). Local HTTP fixtures подтверждают protocol и
orchestration; они не доказывают GPU execution, Qdrant HNSW/filtering, persistence
или качество поиска на реальном корпусе.

## Повторное ревью после публикации

По следующему запросу пользователя два независимых GPT-6 Astra ревью проверили
`git diff e15aa3b...d608c8f` (feature `45095f3`, merge `d608c8f`). Working tree
в начале был чистым. Источник требований — spec 008 и локальные архитектурные
правила; внешние issues и дополнительные services не создавались.

### Standards

Найден один P2: readiness deadline задавал socket inactivity timeout, но не
останавливал чтение непрерывно поступающего ответа. TEI `/health` мог завершиться
после startup deadline, а Qdrant health wait мог принять поздний `green` и
продолжить публикацию. Это нарушало общий deadline из architecture и spec 008.

Исправлено в `integrations/http_response.py`: ограниченное по bytes чтение body
прерывается socket shutdown по оставшемуся deadline. Timer отменяется и завершается
в `finally`; read остаётся в вызывающем потоке. Обе интеграции используют этого
владельца чтения, Qdrant collection получает абсолютный deadline, а index experiment
проверяет expiry перед принятием готовности. Это также прерывает чтение chunk framing.
DNS и initial headers сохраняют socket semantics urllib; эта граница описана в spec.

### Spec

Найден один P2: при уменьшенном `embedding.limits.batch_bytes` запрос мог пройти
prepare и вызвать Qdrant, прежде чем encoding отвергал размер полного JSON body.
Ошибка недоступного endpoint маскировала локальную ошибку запроса; §4/L05 требуют
local validation до HTTP. `TextEmbedder.prepare_query` теперь проверяет полный
singleton body, включая prefix, UTF-8, JSON escaping и flags.

### Проверка исправлений

Оба замечания закрыты независимым повторным review. Четыре теста query budget
проверяют точную границу, escaping и composed zero-HTTP failure. Ещё пять тестов
проверяют непрерывный Content-Length body, chunk-extension framing для TEI/Qdrant
и поздний `green` без ready PATCH/receipt. Reviewer независимо исполнил шесть
deadline cases, включая прежнюю проверку timeout propagation: все прошли.
Результаты общего прогона зафиксированы в development. Live GPU/Qdrant/HTTPS
execution не проверялось, G01–G06 остаются невыполненными.

Итого: Standards — 1 P2 исправлен; Spec — 1 P2 исправлен; открытых замечаний нет.
