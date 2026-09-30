# Вертикальный срез ИИ-ассистента в админке

| | |
|---|---|
| **Версия** | 0.1 |
| **Дата** | 2026-09-30 |
| **Владелец** | Ипполитов Иван, директор департамента информационных технологий |
| **Статус** | draft — эскиз, основа задачи в Трекере |

Первый срез канала ИИ в [pbeadmin](../../../systems/pbeadmin.md): одна операция
насквозь — от сервиса до ответа ассистента в текстовом чате, от имени
пользователя и с его правами. Образец, по которому потом переводятся остальные
контроллеры (Аг-12). Решения, из которых он следует, — в паспорте проекта,
раздел [«Порядок работы над ИИ»](README.md#порядок-работы-над-ии-договорённость-30092026).

Код ниже — эскиз для постановки, а не готовая реализация. Изменения в
`pbeadmin` идут задачей в Трекере.

## Сценарий

Пользователь на странице чата пишет: «сколько у меня врачей?».

1. Страница отправляет `POST /assistant/{conversation}/messages` под обычной
   web-сессией (guard `web`, драйвер `pbe`). `Auth::user()` — этот пользователь.
2. `ChatService` отправляет шлюзу модели системный промпт, историю переписки
   из БД и схемы инструментов, на которые у пользователя есть право. Данных
   в запросе нет, токена пользователя — тоже.
3. Модель отвечает вызовом `count_persons{group: "personal"}` — или
   переспрашивает, какую группу имеет в виду пользователь.
4. Исполнитель инструментов проверяет право, вызывает сервис от имени
   пользователя, пишет журнал. Результат — `{count: 412}`.
5. Результат уходит модели, модель отвечает: «В вашем личном списке 412 врачей».
6. Ответ сохраняется в БД и возвращается на страницу.

**Модель никуда не ходит сама.** Она только просит вызвать инструмент;
выполняет его админка в том же запросе. Сессия пользователя остаётся в
админке, модель состояния не хранит: на каждом шаге админка отправляет ей
переписку целиком. Так же уже устроен модуль Insights: админка сама
обращается к OpenAI-совместимому шлюзу с серверным ключом.

## Что есть в админке сейчас

- **Логика видимости врачей — внутри контроллера.**
  `PersonController::index` (`app/Http/Controllers/Person/PersonController.php`,
  строки 133–398) сам строит запрос: группы «личный список» (`tStaffPerson`),
  «территория» (`tStaffTerritoryPerson`), «таргет текущего цикла»
  (`promo_cycles.tPromoCycleStaffPerson`), ограничения права
  `Gate::raw('person.view')->affectQuery()`, флаги `HIDE_ARCHIVED_CONTACTS`,
  страна, `HIDE_NATIONAL`. Сервиса нет, переиспользовать нечего.
- **`PersonVisibilityService`** проверяет одного врача (`isVisibleFor`) и
  используется только модулем Consent. Списка он не строит.
- **API прилаги `getPerson`** (`app/Http/Controllers/API/PersonController.php`,
  строки 1200–1268) проверяет только `person.view` и отдаёт всех врачей — без
  групп и без `affectQuery`. Возможно, намеренно (прилага держит базу
  офлайн), возможно, дыра; для агента этот путь не годится в любом случае.
- **Шлюз модели** — `GatewayClient` модуля Insights. Ядро от модуля зависеть
  не должно: клиент шлюза поднимается в ядро (отдельная задача — хранилище
  промптов).

## Эскиз

### 1. Сервис — выносится из контроллера

```php
// app/Services/Person/PersonQueryService.php

/**
 * Врачи, видимые пользователю. Вынесено из PersonController::index —
 * одна логика видимости для страницы, API и ассистента.
 */
class PersonQueryService
{
    public const GROUPS = ['personal', 'territory', 'target', 'all'];

    public function visibleTo(User $user, string $group): Builder
    {
        $gate = Gate::forUser($user);
        $gate->authorize('person.view');

        $query = Person::query();
        $gate->raw('person.view')->affectQuery($query);   // RawWhere / Where из права

        match ($group) {
            'personal'  => $this->onlyPersonal($query, $user),   // tStaffPerson
            'territory' => $this->onlyTerritory($query, $user),  // tStaffTerritoryPerson
            'target'    => $this->onlyTarget($query, $user),     // текущий цикл
            'all'       => $gate->authorize('person.view.all'),
        };

        // HIDE_ARCHIVED_CONTACTS, страна, HIDE_NATIONAL — сюда же из index()
        return $query;
    }
}
```

Контроллер страницы после этого — `$persons->visibleTo(Auth::user(), $group)`
плюс фильтры формы и пагинация. Поведение страницы не меняется — это
проверяется до подключения агента.

### 2. Инструмент — тонкая обёртка над сервисом

```php
// app/Services/Assistant/Tools/CountPersonsTool.php

class CountPersonsTool implements AssistantTool
{
    public function __construct(private PersonQueryService $persons) {}

    public function name(): string  { return 'count_persons'; }
    public function right(): string { return 'person.view'; }
    public function writes(): bool   { return false; }

    public function description(): string
    {
        return 'Сколько врачей видит пользователь. group: personal — мой список, '
             . 'territory — моя территория, target — таргет текущего цикла, '
             . 'all — все доступные. Если не ясно, какая группа, — переспроси.';
    }

    public function parameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => ['group' => ['type' => 'string', 'enum' => PersonQueryService::GROUPS]],
            'required'   => ['group'],
        ];
    }

    /** Модели уходит число, не строки: ПДн врачей наружу не отдаём. */
    public function handle(array $args, AssistantContext $ctx): array
    {
        return [
            'group' => $args['group'],
            'count' => $this->persons->visibleTo($ctx->user, $args['group'])->count(),
        ];
    }
}
```

Модули регистрируют свои инструменты в своём ServiceProvider
(`$registry->add(ConsentStatusTool::class)`) — так же, как Insights
регистрирует свои права. Ядро внутрь модулей не лезет.

### 3. Цикл — в админке

```php
// app/Services/Assistant/ChatService.php

public function reply(Conversation $conv, string $text, User $user): Message
{
    $ctx = new AssistantContext(user: $user, conversation: $conv, channel: 'web');
    $conv->messages()->create(['Role' => 'user', 'Content' => $text]);

    for ($step = 0; $step < self::MAX_STEPS; $step++) {
        $response = $this->gateway->send([
            'model'    => $conv->Model,
            'messages' => $this->history->forModel($conv),   // вся переписка из БД
            'tools'    => $this->tools->schemasFor($user),   // только то, на что есть право
        ]);

        if (! $response->hasToolCalls()) {
            return $conv->messages()->create(['Role' => 'assistant', 'Content' => $response->text()]);
        }

        $conv->messages()->create(['Role' => 'assistant', 'ToolCalls' => $response->toolCalls()]);

        foreach ($response->toolCalls() as $call) {
            // право, журнал; для writes() — только «подготовить», не выполнить
            $result = $this->executor->run($call, $ctx);
            $conv->messages()->create([
                'Role' => 'tool', 'ToolCallId' => $call->id, 'Content' => json_encode($result),
            ]);
        }
    }

    throw new TooManyStepsException();
}
```

**Третий вход** — это исполнитель инструментов, а не новый guard: он знает,
что вызов пришёл через ИИ, проверяет право, пишет журнал, для записи
разрешает только «подготовить». Отдельный guard с делегированным токеном
понадобится, только если цикл агента уедет из админки.

## Хранение

Отдельная база на том же сервере, по образцу Insights и Consent — со своим
соединением. Причины: тексты чатов содержат ПДн («еду к Петровой из 5-й
поликлиники») и требуют другого доступа, чем `crmAdmin`; у переписки и
журнала разные сроки хранения; рост не смешивается с мастер-данными.
Внешних ключей на `tUser` нет — `UserId` хранится значением.

| Таблица | Что | Срок |
|---|---|---|
| `tAssistantConversation` | `UserId`, `Channel` (web / app / voice), `Model`, заголовок, даты | как у сообщений |
| `tAssistantMessage` | `ConversationId`, `Role` (user / assistant / tool), `Content`, `ToolCalls`, `ToolCallId`, токены | короткий, чистится |
| `tAssistantToolCall` | журнал: кто, какой инструмент, аргументы, итог **без данных**, статус, длительность | длинный |

Переписку можно удалять, журнал — нет.

## Ограничения среза

- **Воркер PHP-FPM держится весь цикл** — несколько обращений к модели
  подряд. Для среза приемлемо (Insights живёт так же, под троттлингом); для
  прилаги и голоса позже — очередь и стриминг.
- **Канал выключен по умолчанию** и включается по клиенту: передача данных
  фармклиентов в модель требует их согласия.
- **Только чтение.** Запись в два шага (подготовить → подтвердить) — следующий
  срез.
- **Системный промпт ассистента** — сразу в хранилище промптов, если оно
  готово к началу среза; иначе в коде с переносом.
