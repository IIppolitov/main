# Вертикальный срез ИИ-ассистента в админке

| | |
|---|---|
| **Версия** | 0.2 |
| **Дата** | 2026-10-01 |
| **Владелец** | Ипполитов Иван, директор департамента информационных технологий |
| **Статус** | draft — эскиз к задаче; постановка — в [CRM-2154](https://tracker.yandex.ru/CRM-2154) |

Первый срез канала ИИ в [pbeadmin](../../../systems/pbeadmin.md): несколько
операций насквозь, от сервиса до ответа ассистента в текстовом чате, от имени
пользователя и с его правами. Решения, из которых он следует, описаны в паспорте
проекта, раздел [«Порядок работы над ИИ»](README.md#порядок-работы-над-ии-договорённость-30092026).

**Объём, критерии приёмки и «вне объёма» берутся из задачи, а не отсюда.** Здесь
только то, чего в задаче нет: почему выбраны боты, что показала сверка с кодом и
эскиз кода. Код ниже нужен для постановки, это не реализация. Изменения в
`pbeadmin` идут через задачу.

## Решения 01.10.2026

Решения переписаны в задачу (комментарий от 01.10):

1. **Предмет среза — боты (модуль Bots), а не врачи.** У раздела простые права,
   готовые сервисы статистики, есть пользовательская документация, и ассистент
   сразу полезен тем, кто настраивает ботов. Вынос `PersonQueryService` из среза
   убран.
2. **Таблицы ассистента лежат в базе подсистемы LLM** (соединение `Llm`), отдельной
   базы нет. Довод v0.1 остаётся в силе: переписка содержит ПДн, поэтому ей нужен
   доступ отдельно от `crmAdmin`. База LLM этому условию отвечает.
3. **Лимиты токенов** (пользователь / роль / должность / инстанс) — общий
   механизм подсистемы LLM, делаются в этой задаче.
4. **Ассистент — потребитель подсистемы LLM**, как Insights. Системный промпт —
   версионируемый сценарий `assistant`. Вызов по переписке с инструментами —
   расширение слоя вызова LLM ([CRM-2153](https://tracker.yandex.ru/CRM-2153)).

Сырые заметки, из которых выросли «следующие срезы», лежат в первом комментарии
к задаче.

## Что изменилось по сравнению с v0.1

- **Срез больше не образец рефакторинга (Аг-12).** В v0.1 срез заодно выносил
  логику видимости врачей из `PersonController::index` в сервис и тем самым
  показывал, как переводить контроллеры. У ботов сервисы уже есть
  (`BotStatsService`, `BotRepository`), выносить нечего. Срез проверяет
  канал ИИ: реестр, исполнитель, цикл, лимиты. Перевод контроллеров в сервисы
  остаётся без образца, и первый такой перевод придётся делать отдельно.
- **Риск видимости уходит из среза.** У врачей видимость строится по группам,
  `affectQuery` и флагам инстанса, а у ботов её нет вовсе: право
  `bots.flow.view` открывает всё. Критерий «те же данные, что на странице»
  выполнить легко, но и проверяет он меньше.
- **Объём вырос.** В срез вошли лимиты с экраном настройки (п. 5 задачи), а это
  механизм подсистемы LLM, который задевает и боевой путь Insights на планшете.

## Сверка с кодом (01.10.2026)

- **Права.** Страницы ботов (`BotController`, `BotLogController`) закрыты
  `bots.flow.view`. Секреты отдаются только при `bots.flow.edit` (`BotController::index`,
  строка 67). Построчных ограничений нет.
- **Секреты.** `ApiToken`, `WebhookSecret`, `CallbackSecret` — encrypted-колонки,
  в модели они в `$hidden` (CRM-1808). Массовая сериализация их не отдаёт.
  Инструмент всё равно собирает ответ из явного списка полей, а не из `toArray()`.
- **У бота один сценарий** (`Fields.FlowId`). В таблице задачи у `list_bots`
  написано «сколько сценариев», вернее — «какой сценарий подключён».
- **`BotStatsService::stats()`** считает по всем ботам сразу и отдаёт строки
  `bot_id, name, channel, enabled, subscribed, unsubscribed,
  unsubscribed_command, unsubscribed_blocked, active`. Фильтра по боту нет,
  `bot_stats{bot_id}` фильтрует результат. В выдачу попадают удалённые боты
  с подписчиками и строка «Бот не определён». Это то же, что на вкладке
  статистики, поэтому критерий приёмки сходится. Причины отписки — перечисление
  (`command` / `blocked`), свободного текста и ПДн нет.
- **`BotStatsService::bots()`** отдаёт только неудалённых ботов (`id, name,
  channel`). Это готовая основа для `list_bots`, в которую надо добавить
  `enabled` и сценарий.
- **Справка.** `modules/Bots/docs/user-guide.md`, 536 строк, разделы
  пронумерованы: сценарий, шаги, действия, песочница, подключение бота,
  особенности Telegram / VK / MAX, файлы, логи. Темы для `read_help` удобно
  резать по этим разделам.
- **Insights и лимиты.** По контракту (`modules/Insights/docs/API_CONTRACT.md`,
  §4) приложение разбирает у отказа только HTTP-код. 429 в контракте уже есть,
  а 401/403 отдавать запрещено: приложение разлогинит пользователя. Ответ на
  открытый вопрос задачи — ниже.

## Эскиз

### 1. Инструмент — тонкая обёртка над готовым сервисом

```php
// modules/Bots/App/Assistant/Tools/BotStatsTool.php

class BotStatsTool implements AssistantTool
{
    public function __construct(private BotStatsService $stats) {}

    public function name(): string  { return 'bot_stats'; }
    public function right(): string { return 'bots.flow.view'; }
    public function writes(): bool   { return false; }

    public function description(): string
    {
        return 'Метрики подписок: сколько диалогов, активных, отписанных и по какой '
             . 'причине (command — сам отписался, blocked — заблокировал бота). '
             . 'bot_id не указан — по всем ботам. Если не ясно, о каком боте речь, — переспроси.';
    }

    public function parameters(): array
    {
        return [
            'type'       => 'object',
            'properties' => ['bot_id' => ['type' => 'integer']],
        ];
    }

    /** Агрегаты, а не строки tBotChatState: подписчики модели не уходят. */
    public function handle(array $args, AssistantContext $ctx): array
    {
        $rows = $this->stats->stats();

        if (isset($args['bot_id']))
            $rows = array_values(array_filter($rows, fn ($r) => $r['bot_id'] === $args['bot_id']));

        return ['bots' => $rows];
    }
}
```

`list_bots` и `read_help` устроены так же. Регистрация — в
`BotsServiceProvider`:

```php
$this->app->make(AssistantToolRegistry::class)->add(ListBotsTool::class, BotStatsTool::class, ReadHelpTool::class);
$this->app->make(AssistantHelpRegistry::class)->topics('bots', __DIR__.'/../../docs/assistant', right: 'bots.flow.view');
```

### 2. Цикл — поверх слоя вызова LLM

В v0.1 цикл сам ходил в шлюз. Теперь он вызывает подсистему LLM, а проверку
требований к модели, лимит, журнал и учёт токенов делает она.

```php
// app/Services/Assistant/ChatService.php

public function reply(Conversation $conv, string $text, User $user): Message
{
    $ctx = new AssistantContext(user: $user, conversation: $conv, channel: 'web');
    $conv->messages()->create(['Role' => 'user', 'Content' => $text]);

    $settings = $this->llm->settings('assistant');               // maxSteps, historySize

    for ($step = 0; $step < $settings['maxSteps']; $step++) {
        $response = $this->llm->converse('assistant', [          // п. 1 задачи
            'user'     => $user,                                  // для лимита и журнала
            'messages' => $this->history->forModel($conv, $settings['historySize']),
            'tools'    => $this->tools->schemasFor($user),
        ]);                                                       // TokenLimitExceeded — до шлюза

        if (! $response->hasToolCalls()) {
            return $conv->messages()->create(['Role' => 'assistant', 'Content' => $response->text()]);
        }

        $conv->messages()->create(['Role' => 'assistant', 'ToolCalls' => $response->toolCalls()]);

        foreach ($response->toolCalls() as $call) {
            $result = $this->executor->run($call, $ctx);         // право, журнал, writes() → отказ
            $conv->messages()->create([
                'Role' => 'tool', 'ToolCallId' => $call->id, 'Content' => json_encode($result),
            ]);
        }
    }

    throw new TooManyStepsException();
}
```

### 3. Лимит — в слое вызова, один на все сценарии

```php
// app/Services/Llm/Call/TokenLimiter.php

public function check(?User $user): void
{
    $limits = $this->limits->forInstance()                         // действует всегда
        ->merge($user ? $this->limits->strictestFor($user) : []);  // свой / роль / должность — самый строгий

    foreach ($limits as $limit) {
        if ($this->usage->spent($limit) >= $limit->Tokens)
            throw new TokenLimitExceeded($limit);                  // «лимит исчерпан до …»
    }
}
```

Расход считается по журналу вызовов, то есть по уже завершённым запросам.
Поэтому лимит мягкий: запрос, начатый до порога, может его перешагнуть.
Для среза этого достаточно.

## Открытые вопросы — предложение

1. **Лимиты на боевом пути Insights.** Предлагаю так: лимиты действуют на все
   сценарии, Insights тоже, иначе лимит инстанса ничего не ограничивает. Планшету
   при отказе отдаётся **429** с телом `{"code": "token_limit_exceeded", ...}`.
   Приложение разбирает только HTTP-код, а 429 уже есть в контракте, так что
   приложение покажет «Агент вернул ошибку (429)», и правка на их стороне не
   нужна. Заголовок `Retry-After` — конец периода лимита. Строку добавить в §4
   `API_CONTRACT.md` и показать команде приложения до релиза.
2. В комментарии к задаче упомянуты «два открытых вопроса», а в описании остался
   один. Второй надо вернуть в описание или поправить комментарий.

## Ограничения среза

- **Воркер PHP-FPM занят весь цикл**, то есть несколько обращений к модели
  подряд. Для среза это приемлемо: Insights работает так же, под троттлингом.
  Для приложения и голоса позже понадобятся очередь и стриминг.
- **Канал выключен по умолчанию** (`ASSISTANT_ENABLED` и `LLM_ENABLED`) и
  включается по клиенту: передача данных фармклиентов в модель требует их
  согласия.
- **Только чтение.** Запись подтверждается кнопкой в чате, это следующий срез.
