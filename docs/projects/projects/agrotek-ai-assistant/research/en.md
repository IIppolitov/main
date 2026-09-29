# Голосовые ИИ-ассистенты (голос + LLM-агент с инструментами): практика по англоязычным источникам

Дата: 2026-09-29
Статус: draft, исследование
Метод: только англоязычные источники, каждый открыт и прочитан (через WebFetch). Где источник — маркетинг вендора, это помечено «(вендор)»; цифры из таких источников — ориентир, а не факт.

> Поправка от 29.09.2026: утверждение о голосовых заметках к сделке у Microsoft (раздел 8) по первоисточнику не подтвердилось — см. [salesforce.md](salesforce.md#что-не-подтвердилось).

Контекст отбора: голосовой ассистент на смартфоне для менеджера по продажам за рулём (сельхозклиенты), запись в Bitrix24 и 1С через prepare → голосовое подтверждение, RAG по базе знаний, плохая связь, роли, журнал.

---

## 1. Распознавание речи: шум, доменный словарь, имена, числа

**Проблема: шум салона сильно и неравномерно бьёт по качеству.** Скорость, открытое окно, обороты, покрытие дороги, дождь меняют уровень шума; разница между поставщиками ASR на корпусе AVICAR растёт с шумом (4 п.п. в тишине → 8 п.п. на 55 mph с открытым окном).
→ Как решали: выбирать ASR по замерам на собственном шумном аудио, а не по бенчмаркам на чистой речи.
Источник: [Speechmatics — Accurate speech-to-text is AI's solution to vehicle safety](https://www.speechmatics.com/company/articles-and-news/accurate-speech-to-text-is-ais-solution-to-vehicle-safety) (вендор).

**Проблема: Bluetooth-гарнитура/громкая связь машины режет полосу.** При открытии микрофона через Bluetooth HFP звук переходит в моно 8 кГц (CVSD) или 16 кГц (mSBC) — это узкополосный «телефонный» канал, заметно хуже для ASR.
→ Как решали: по возможности писать с микрофона телефона/широкополосного канала (HFP 1.6 WBS 16 кГц), тестировать ASR именно на том пути звука, который будет в машине.
Источник: [обсуждение деградации HFP/HSP, GitHub issue](https://github.com/Shadowolf7/Vayu-Viewer/issues/89) (найдено в поиске, первичный стандарт не читался — `TODO: уточнить` на реальных телефонах/машинах).

**Проблема: «почистить» звук шумоподавлением перед ASR — обычно хуже, а не лучше.** Систематическое исследование: на 4 современных ASR (Whisper, Parakeet, Gemini Flash 2.0 и др.) и 10 условиях шума денойзинг ухудшил semWER во всех 40 конфигурациях, на 1,1–46,6 п.п. AssemblyAI и Deepgram приходят к тому же: артефакты шумоподавления путают модель сильнее, чем исходный шум. Нюанс Deepgram: для turn-taking (VAD) шумоподавление может помогать, для транскрипции — вредит.
→ Как решали: отдавать ASR «сырой» сигнал; шумоподавление, если нужно, — только в ветке VAD/перебивания.
Источники: [arXiv 2512.17562 — When De-noising Hurts](https://arxiv.org/abs/2512.17562); [AssemblyAI — Transcription accuracy on hard, noisy audio](https://www.assemblyai.com/blog/async-transcription-accuracy-hard-audio) (вендор); [Deepgram — The noise reduction paradox](https://deepgram.com/learn/the-noise-reduction-paradox-why-it-may-hurt-speech-to-text-accuracy) (вендор).

**Проблема: редкие термины и имена собственные ломаются первыми.** «Когда аудио шумное или у говорящего акцент, модель откатывается к частым похожим по звучанию словам — поэтому редкие дорогие термины страдают больше всего».
→ Как решали — contextual biasing / keyterm prompting:
- Deepgram Nova-3/Flux: до 100 терминов, 500 токенов на запрос, рекомендация — держаться 20–50 самых важных; избегать общих слов; фонетически близкие термины (например, конкурирующие названия препаратов) требуют ручной курации и фраз с контекстом. [Deepgram docs — Keyterm prompting](https://developers.deepgram.com/docs/keyterm); [Deepgram — Large vocabulary speech recognition](https://deepgram.com/learn/large-vocabulary-speech-recognition).
- AssemblyAI: два этапа — смещение в реальном времени + фонетическая (metaphone) правка после каждой реплики; до 100 терминов в стриминге; **список можно менять посреди сессии**; совет — «начните без терминов и добавляйте только те, что модель стабильно пропускает», длинный список даёт ложные замены. [AssemblyAI — Keyterm prompting](https://www.assemblyai.com/blog/keyterm-prompting-real-time-accuracy) (вендор).
- Speechmatics: custom dictionary до 1000 слов с `sounds_like` — подсказкой произношения для нестандартных имён. [Speechmatics — Custom dictionary](https://www.speechmatics.com/company/articles-and-news/speechmatics-launches-custom-dictionary) (вендор).
- Каскад позволяет подать живой контекст разговора в ASR; AssemblyAI заявляет −10,2% WER от этого. [AssemblyAI — Speech-to-speech vs cascaded](https://www.assemblyai.com/blog/speech-to-speech-for-voice-agents) (вендор).

**Проблема: LLM-постправка не умеет «придумать» сущность, которой нет в гипотезах ASR.** Generative Error Correction хорошо чинит обычные слова, но на именах собственных упирается в отсутствие знания.
→ Как решали: retrieval-augmented correction — к гипотезе ASR подмешиваются кандидаты-сущности из базы (у нас — клиенты, контакты, населённые пункты, номенклатура из CRM/1С); DARAG даёт −8…30% WER в домене и −10…33% вне домена.
Источники: [arXiv 2410.13198 — DARAG, Failing Forward](https://arxiv.org/abs/2410.13198); [arXiv 2506.07510 — DeRAGEC](https://arxiv.org/pdf/2506.07510) (найдено в поиске, прочитана аннотация в выдаче).

**Проблема: коды, артикулы, цифры — WER их прячет.** «96,6% точности по словам может означать всего 77% точного совпадения идентификатора»; буквы и цифры акустически путаются (P/B, M/N, O/0).
→ Как решали: (1) словарь только из ~1000 активных позиций, а не весь каталог — полный каталог топит модель низковероятными строками; (2) сверка с каталогом, доступным в сессии, fuzzy-match с порогами: уверенно — автоисправление, неуверенно — переспрос; (3) зачитывать клиенту не код, а название и атрибуты («синий, размер 10»); (4) мерить exact match по сущностям и false accept, а не WER.
Источник: [Speechmatics — Alphanumeric speech recognition: why voice assistants mangle SKUs](https://www.speechmatics.com/company/articles-and-news/alphanumeric-speech-recognition-why-voice-assistants-mangle-skus-and-how-to-fix-it) (вендор, но рецепт общий).

**Проблема: галлюцинации ASR на паузах и неречевом звуке.** Whisper выдумывал целые фразы в ~1,4% транскриптов; чаще там, где больше пауз и неречевого звука; 38% выдумок — вредные (ложные имена, связи, утверждения).
→ Как решали: обрезать тишину (VAD перед ASR), не отдавать в ASR длинные неречевые куски, выбирать модели/настройки с меньшей «генеративностью».
Источники: [arXiv 2402.08021 — Careless Whisper](https://arxiv.org/html/2402.08021v2); [arXiv 2501.11378 — Whisper hallucinations induced by non-speech audio](https://arxiv.org/pdf/2501.11378) (аннотация по выдаче).

## 2. Латентность и диалог

**Проблема: суммарная задержка каскада.** Разговор начинает казаться медленным после ~800 мс; основной вклад — ASR и TTFT модели, а не сеть. На практике в проде P50 1,4–1,7 с, P95 4,3–5,4 с (Hamming).
→ Как решали: стримить каждый этап (токены LLM сразу в TTS); выбирать модели по time-to-first-token; маршрутизировать простые запросы на малую модель; держать ASR/LLM/TTS в одном регионе; мерить свой пайплайн, а не бенчмарки вендоров.
Источники: [WebRTC.ventures — The voice AI latency budget](https://webrtc.ventures/2026/09/voice-ai-latency-budget/); [Hamming — Voice agent evaluation metrics](https://hamming.ai/resources/voice-agent-evaluation-metrics-guide) (вендор); [HN — Building an AI voice agent from scratch](https://news.ycombinator.com/item?id=46946705).

**Проблема: что именно мерить.** OpenAI рекомендует мерить «сколько пользователь ждёт полезного ответа» и раскладывать время по точкам: начало запроса к бэкенду, первый полезный результат, начало/конец вызова инструмента, прибытие аудио, начало воспроизведения.
Источник: [OpenAI — Voice agents guide](https://developers.openai.com/api/docs/guides/voice-agents).

**Проблема: конец реплики (endpointing).** VAD по тишине режет людей, которые думают вслух; таймаут тишины 800 мс добавляет почти секунду к каждому ответу. Перебивания ассистентом особенно часты на структурированных данных (телефон, адрес).
→ Как решали: семантический детектор конца реплики поверх транскрипта (LiveKit: модель 0,5B, дистиллированная из 7B, −39% ложных перебиваний, в т.ч. −38% для русского; модель ждёт окончания номера/адреса, выводя ожидаемый формат из промпта). Новые версии слушают аудио напрямую (просодия + смысл).
Источники: [LiveKit — Improved end-of-turn model cuts interruptions 39%](https://livekit.com/blog/improved-end-of-turn-model-cuts-voice-ai-interruptions-39); [LiveKit — Turn detection: VAD, endpointing, model-based](https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection).

**Проблема: перебивание (barge-in) и ложные перебивания.** Эхо собственного голоса агента, кашель, шум, короткие «ага/угу» ошибочно останавливают ответ.
→ Как решали: отменять TTS и LLM за доли секунды; использовать частичные гипотезы ASR; эхоподавление на клиенте (WebRTC AEC); минимальная длительность/число слов для перебивания; авто-возобновление речи, если после «перебивания» транскрипта не появилось; разные политики для разных типов сообщений (при диктовке номеров — «терпеливый» endpointing). Метрики: доля ложных и пропущенных перебиваний, успешность возобновления.
Источники: [LiveKit — Configuring turn detection and interruptions](https://livekit.com/blog/turn-detection-and-interruption-handling); [Hamming — Interruption handling runbook](https://hamming.ai/resources/voice-agent-interruption-handling-runbook) (вендор); [HN — Building an AI voice agent from scratch](https://news.ycombinator.com/item?id=46946705).

**Проблема: speech-to-speech или каскад.** S2S быстрее и естественнее, но: нет текстового слоя для проверок и аудита, труднее отлаживать, сложнее управлять инструментами, стоимость растёт с длиной разговора. В бенчмарке Full-Duplex-Bench-v3 (реальная речь с оговорками, многошаговые вызовы API) лучший — GPT-Realtime с Pass@1 всего 0,60; самокоррекция говорящего («нет, не во вторник, в среду») остаётся слабым местом у всех.
→ Как решали: для корпоративных сценариев с записью в системы и аудитом — каскад как основа (консенсус AssemblyAI, Coval, Deepgram), S2S — на контролируемый эксперимент на части трафика.
Источники: [AssemblyAI — Speech-to-speech vs cascaded](https://www.assemblyai.com/blog/speech-to-speech-for-voice-agents) (вендор); [Coval — S2S vs cascaded](https://www.coval.ai/blog/speech-to-speech-vs-cascaded-voice-ai-which-architecture-should-you-deploy/) (вендор); [arXiv 2604.04847 — Full-Duplex-Bench-v3](https://arxiv.org/abs/2604.04847); [OpenAI — Voice agents guide](https://developers.openai.com/api/docs/guides/voice-agents).

**Проблема: длинный диалог — модель «теряется».** Все топовые LLM в многоходовом диалоге с постепенным уточнением теряют в среднем 39% качества против одного хода; «свернув не туда, не возвращаются». Pipecat наблюдает «context rot»: инструкции из начала истории забываются, модель путает задачи и досрочно выходит из сценария.
→ Как решали: структурировать разговор узлами (Pipecat Flows) — на каждом шаге свой короткий промпт и свой набор инструментов, доступ к «дорогим» инструментам открывается только после нужного шага; подтверждения — явные шаги сценария.
Источники: [arXiv 2505.06120 — LLMs get lost in multi-turn conversation](https://arxiv.org/abs/2505.06120); [Daily — Why your voice agent needs structure with Pipecat Flows](https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/).

## 3. Надёжность tool calling и подтверждения

**Проблема: в голосе агенты заметно хуже, чем в тексте, на тех же задачах.** τ-voice (Sierra): одни и те же 278 задач и оценка по итоговому состоянию БД; в августе 2025 голос держал 45% от текстового качества, к апрелю 2026 — ~79%; в реалистичном аудио (шум, акценты, сжатие) хуже у всех. Главные причины: ошибки распознавания сущностей (имена, e-mail, ID), потеря контекста/исправлений между репликами, неверный выбор инструмента под шумом.
Источник: [Sierra — τ-voice](https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks).

**Проблема: сбор сущностей голосом.** τ-Elicitation: текстовый агент — 100%, голосовые конфигурации — 14–41% точного успеха; агенты перепроверяют, но исправляют лишь 24–37% найденных ошибок. Структурированная процедура (спеллинг → зачитка → исправление → подтверждение) даёт +14…31 п.п., но стоит +21…28 с на звонок.
→ Вывод: сущности нужно не «выспрашивать» у модели, а резолвить по справочнику и подтверждать.
Источник: [arXiv 2609.13602 — τ-Elicitation](https://arxiv.org/abs/2609.13602).

**Как решали — вызов инструмента как запрос, а не разрешение.** OpenAI: узкие, явные инструменты; валидация всех аргументов на сервере; «относиться к function call модели как к запросу, а не к разрешению»; для рискованных операций — шаг подтверждения; не полагаться только на голос — по возможности дублировать подтверждение в UI; проверять, что озвученное подтверждение совпадает с реально выполненным действием.
Источники: [OpenAI — Voice agents guide](https://developers.openai.com/api/docs/guides/voice-agents); [OpenAI — Prompting realtime models](https://developers.openai.com/api/docs/guides/voice-prompting).

**Как зачитывать (read-back).** OpenAI: числовые идентификаторы — по цифрам («8… 3… 5… 2… 1, верно?»), «чтение целым числом прячет ошибки»; e-mail — по буквам; при неразборчивом аудио — переспросить и **не звать инструменты**, не достраивать слова. ElevenLabs: подтверждать конкретику («оформляю возврат на [сумма]… верно?»); в описании параметров инструмента давать пример формата, т.к. ASR отдаёт «устную» форму (at → @). Google Conversation Design: явное подтверждение — для трудно отменяемых действий и дорогих ошибок (имена, адреса, сообщения от имени пользователя); подтверждать сами параметры, а не «я услышал…»; дать исправить один параметр без перезапуска.
Источники: [OpenAI — Prompting realtime models](https://developers.openai.com/api/docs/guides/voice-prompting); [ElevenLabs — Prompting guide](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide) (вендор); [Google — Conversation design: confirmations](https://developers.google.com/assistant/conversation-design/confirmations).

**Проблема: ложное «да».** Бэкчэннелы («да», «ага», «угу», «окей») звучат как согласие, но часто им не являются; общий вопрос «подтвердить?» приучает соглашаться рефлекторно.
→ Как решали: backchannel не считать подтверждением, если сценарий явно не ждёт подтверждения; зачитывать действие с реальными значениями («отменить заказ 1041»), а не «подтвердить действие?»; для деструктивных действий — отдельный, отличимый токен подтверждения, «никогда не голое да»; проверка — структурный шаг сценария, а не опциональный диалог.
Источники: [Hamming — Interruption handling runbook](https://hamming.ai/resources/voice-agent-interruption-handling-runbook) (вендор); [Verb — Confirming destructive actions in an AI agent](https://askverb.com/blog/confirming-destructive-actions-ai-agent) (найдено в поиске, по выдаче); [Munder Difflin — Voice is a control plane](https://munderdiffl.in/blog/voice-as-a-control-plane-for-agent-fleets/) (по выдаче).

**Проблема: «успех в транскрипте» ≠ успех в системе.** Вызов инструмента может выглядеть удачным в диалоге, а в системе запись не создана или создана не та.
→ Как решали: для каждого инструмента проверять, что нижележащая система получила правильный запрос; τ-bench/τ-voice оценивают по итоговому состоянию БД.
Источники: [Sierra — τ-voice](https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks); поисковая выдача по Pipecat-тестированию (Cekura/Hamming, вендоры).

## 4. Безопасность

**Проблема: prompt injection через данные.** Модель следует любым инструкциям, попавшим в контекст, — из письма, документа, карточки клиента, ответа API. Уильсон: «летальная тройка» — доступ к приватным данным + недоверенный контент + канал наружу; гардрейлы на «95% атак» в безопасности — провал; «после того как агент прочитал недоверенный ввод, он должен быть ограничен так, чтобы этот ввод не мог вызвать значимое действие».
→ Как решали: разрывать тройку архитектурно; OWASP: считать недоверенными все внешние данные (включая ответы API и документы RAG), отделять инструкции от данных, отдельные проверки для недоверенного контента.
Источники: [Simon Willison — The lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/); [OWASP — AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html).

**Проблема: права агента.** OWASP: минимальный набор инструментов на задачу, раздельные права read/write, allowlist операций, классификация действий по риску, превью действия до выполнения, step-up аутентификация для финансовых/деструктивных операций, журнал всех решений и вызовов с метаданными, adversarial-тесты на обход подтверждения.
Источник: [OWASP — AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html).

**Проблема: «от имени пользователя» — confused deputy.** Если сервис агента просто пробрасывает токен пользователя дальше, личности агента и пользователя сливаются, в журнале не видно, что действие сделал агент; если агент ходит со своими широкими правами — injection превращается в эскалацию.
→ Как решали: token exchange (RFC 8693) с claim `act`: итоговый токен — пересечение прав пользователя, агента и целевого API («never the union»); агент — отдельный субъект в журнале, его можно отозвать отдельно; токены привязывать к пользователю, сессии, цели.
Источники: [Christian Posta — Explaining OAuth delegation, on-behalf-of and agent identity](https://blog.christianposta.com/explaining-on-behalf-of-for-ai-agents/); поисковая выдача по MCP/confused deputy (FlowHunt, Arcade — вендоры).

**Проблема: утечки через RAG.** Если retrieval не повторяет права источника, пользователь получает содержимое документа, который ему не положено видеть; фильтрация после генерации не спасает — модель уже пересказала. ConfusedPilot показал на Copilot for M365 и injection через документы, и утечку через кэширование в RAG.
→ Как решали: permission-aware retrieval — ACL/метка доступа на каждом чанке при индексации, фильтр на этапе поиска, до попадания в промпт.
Источники: [arXiv 2408.04870 — ConfusedPilot](https://arxiv.org/abs/2408.04870); [TianPan — Permission-aware retrieval](https://tianpan.co/blog/2026-05-04-permission-aware-retrieval-enterprise-rag-access-control) (по выдаче).

**Проблема: утечка через кэш промптов.** Глобально общий кэш промптов у 7 API-провайдеров (включая OpenAI на момент исследования) даёт тайминговый канал: по скорости ответа можно узнать, что другой пользователь уже отправлял этот префикс.
→ Как решали: кэш на пользователя/тенанта, нормализация таймингов; у себя — не класть в общий префикс данные конкретного клиента/пользователя, ключ кэша — с учётом пользователя и роли.
Источник: [arXiv 2502.07776 — Auditing prompt caching in LLM APIs (ICML 2025)](https://arxiv.org/abs/2502.07776).

## 5. Плохая связь, офлайн, on-device

**Проблема: транспорт на мобильной сети.** WebSocket работает поверх TCP: при потерях — стопоры на ретрансмиссии, звук «замерзает» и приходит пачкой. WebRTC (UDP) даёт jitter buffer, маскировку потерь, эхоподавление; Opus FEC держит разборчивость примерно до 10% потерь (+15–20% трафика), DRED — через длинные пачки потерь. Без AEC — выше ошибки ASR.
→ Как решали: медиа по WebRTC, управление — по WebSocket/HTTP.
Источники: [CallMissed — WebRTC for voice AI](https://www.callmissed.com/blog/webrtc-for-voice-ai-a-practical-primer) (вендор); поисковая выдача (GetStream — media resilience).

**Проблема: полный офлайн.** Облачный голос без связи не работает вовсе.
→ Как решали: on-device ASR. Apple SpeechAnalyzer (iOS 26) — полностью на устройстве, длинные записи, лучше на дальних микрофонах, но **нет custom vocabulary** и ~10 языков (Argmax); WhisperKit/Argmax SDK — 100 языков и custom vocabulary; Moonshine — быстрее Whisper на слабом железе при сравнимой/лучшей точности. На Android — офлайн-пакеты распознавания.
Источники: [Argmax — Apple SpeechAnalyzer and WhisperKit](https://www.argmaxinc.com/blog/apple-and-argmax) (вендор); [arXiv 2410.15608 — Moonshine](https://arxiv.org/html/2410.15608v1) (по выдаче); [Apple Developer — Bringing advanced speech-to-text to your app](https://developer.apple.com/documentation/Speech/bringing-advanced-speech-to-text-capabilities-to-your-app) (по выдаче).

**Проблема: запись в системы без связи.** Microsoft Field Service Mobile работает offline-first: локальная БД, фоновые синки, статус синхронизации всегда на экране; конфликты — на уровне записи целиком, а не поля; по умолчанию офлайн-изменения полевого сотрудника перезаписывают изменения диспетчера.
→ Вывод для нас: офлайн-очередь «сырых» диктовок (аудио + локальный транскрипт) и отложенный prepare/подтверждение, когда связь вернулась, — а не офлайн-запись в CRM/1С. Политику конфликтов задать явно.
Источник: [Microsoft Learn — Configure offline data synchronization (Field Service)](https://learn.microsoft.com/en-us/dynamics365/field-service/mobile/offline-data-sync).

## 6. Voice UX для водителя

**Проблема: «hands-free — не risk-free».** AAA Foundation (Strayer, Univ. of Utah): голосовые системы в машинах и смартфонах повышают когнитивную нагрузку до потенциально опасной; отвлечение держится **до 27 секунд** после окончания голосовой задачи. Отправка текстов голосом — самая тяжёлая задача.
Источник: [AAA Newsroom — New hands-free technologies pose hidden dangers](https://newsroom.aaa.com/2015/10/new-hands-free-technologies-pose-hidden-dangers-for-drivers/).

**Проблема: от чего зависит нагрузка.** Порядок систем по числу ошибок взаимодействия совпал с порядком по отвлечению: **точность распознавания важнее числа шагов**. Рекомендации: минимизировать длительность задачи и число обменов; меню — не больше 4–5 пунктов; принимать свободные формулировки; синтетический голос против живого — без разницы; высоконагрузочные функции (сочинение сообщений) ограничивать.
Источник: [AAA Foundation — Measuring cognitive distraction II](https://aaafoundation.org/research/measuring-cognitive-distraction-automobile-ii-assessing-vehicle-voice-based-interactive-technologies/).

**Проблема: а LLM-ассистент?** Полевое исследование Gemini Live (32 водителя, реальная езда): нагрузка одно- и многоходовых диалогов — на уровне разговора по громкой связи, между низким и высоким эталоном; взгляд на экран — ниже порогов.
Источник: [arXiv 2601.15034 — Visual and cognitive demands of an LLM in-vehicle agent](https://arxiv.org/abs/2601.15034).

**Проблема: молчание во время долгих многошаговых операций.** CHI 2026 (N=45): промежуточная обратная связь («планирую…, нашёл…») повышает ощущение скорости и доверие и снижает нагрузку; пользователи хотят адаптивности — подробно вначале, короче по мере роста доверия, в зависимости от ставок.
Источник: [arXiv 2602.15569 — "What are you doing?"](https://arxiv.org/abs/2602.15569). Там же OpenAI: короткие преамбулы только когда идёт реальная работа («проверяю заказ»), без «дайте подумать». [OpenAI — Prompting realtime models](https://developers.openai.com/api/docs/guides/voice-prompting).

**Регуляторика.** NHTSA выпустила руководства только по визуально-мануальным интерфейсам (фазы 1–2: встроенные и портативные устройства); фаза 3 — аудио-вокальные интерфейсы — не опубликована. Для смартфона за рулём применима фаза 2 (визуально-мануальная): любое взаимодействие с экраном должно укладываться в короткие взгляды.
Источник: [Federal Register — Visual-Manual NHTSA Driver Distraction Guidelines](https://www.federalregister.gov/documents/2013/04/26/2013-09883/visual-manual-nhtsa-driver-distraction-guidelines-for-in-vehicle-electronic-devices) (по выдаче; статус фазы 3 — из выдачи и arXiv 2601.15034).

**Длина ответа, списки, цифры.** ElevenLabs: до 3 предложений, если не просят подробно; OpenAI: прямой ответ — 1–2 коротких предложения, результат инструмента — сначала итог, потом одно следующее действие; списки — не больше 3 вариантов, дальше «есть ещё» (Alexa guide в пересказе); цифры, даты, суммы, единицы нормализовать в словесную форму **в своём коде** до TTS, не полагаясь на модель TTS — цифры и символы вызывают ошибки произношения и «галлюцинации голоса».
Источники: [ElevenLabs — Prompting guide](https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide); [ElevenLabs — TTS best practices](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices) (по выдаче); [OpenAI — Prompting realtime models](https://developers.openai.com/api/docs/guides/voice-prompting); [TYPENORM — VUI design](https://typenorm.com/articles/voice-user-interface-design) (по выдаче).

## 7. Оценка и тестирование

**Проблема: WER и «приятный диалог» не показывают, выполнена ли задача.**
→ Как решали:
- OpenAI — «Crawl / Walk / Run»: синтетическая речь на одиночных запросах → записи реальных людей → многоходовые диалоги с симулированным пользователем; мерить task success, P50/P95 времени ответа, плюс слушать глазами человека. [OpenAI — Voice agents guide](https://developers.openai.com/api/docs/guides/voice-agents).
- τ-voice: оценка по итоговому состоянию БД, симулятор пользователя с персонами, шумом, телефонным сжатием и выпадением кадров — чистое vs реалистичное аудио как отдельный срез. [Sierra — τ-voice](https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks).
- Метрики по сущностям: exact match и false accept по идентификаторам, а не WER. [Speechmatics — Alphanumeric](https://www.speechmatics.com/company/articles-and-news/alphanumeric-speech-recognition-why-voice-assistants-mangle-skus-and-how-to-fix-it).
- Ориентиры Hamming: WER <5% «enterprise», 5–10% «хорошо»; barge-in recovery >90%; галлюцинации <1%; регрессионный прогон после каждого изменения, продовые сбои → тест-кейсы. [Hamming — Evaluation metrics](https://hamming.ai/resources/voice-agent-evaluation-metrics-guide) (вендор).
- Для перебиваний — отдельные метрики ложных/пропущенных перебиваний и возобновлений; менять один порог за раз. [Hamming — Interruption runbook](https://hamming.ai/resources/voice-agent-interruption-handling-runbook).

## 8. Внедрение: почему не приживается

**Прецедент: Salesforce Einstein Voice Assistant (2019–2020)** — показан на Dreamforce, так и не вышел из беты, закрыт летом 2020. Аналитик: это была «разговорная обёртка над существующими приложениями», «скорее фокус, чем настоящий ассистент»; распознавание — товар, ценность — в глубине платформы. Голос в Salesforce выжил там, где он встроен в процесс: анализ звонков, коучинг, контакт-центр.
Источники: [TechTarget — Salesforce pulls plug on Einstein Voice Assistant](https://www.techtarget.com/searchcustomerexperience/news/252487869/Salesforce-pulls-plug-on-Einstein-Voice-Assistant); [VentureBeat — Why Salesforce is killing off Einstein Voice Assistant](https://venturebeat.com/2020/07/21/why-salesforce-is-killing-off-einstein-voice-assistant/).

**Вторая волна (2025–2026) — узкий сценарий «после визита».** Microsoft Sales agent в Outlook mobile: голосом надиктовать заметку к сделке в Dynamics 365/Salesforce — «сказал, подтвердил, сохранил»; сделку определяет по частичному названию и контексту, при нескольких совпадениях переспрашивает. Salesforce Agentforce Field Voice: голосовые сводки перед визитом и диктовка заметок/статусов/задач после.
Источники: [Microsoft — What's new in Copilot for Sales, April 2025](https://www.microsoft.com/en-us/dynamics-365/blog/it-professional/2025/05/01/whats-new-in-copilot-for-sales-april-2025/) (по выдаче); [Microsoft Learn — Capture opportunity notes using voice](https://learn.microsoft.com/en-us/copilot/release-plan/2026wave1/copilot-sales/capture-opportunity-notes-using-voice-sales-agent) (страница перенаправляет на roadmap; описание — по выдаче); [Salesforce — Dreamforce 2026 announcements](https://www.salesforce.com/blog/dreamforce-2026-announcements/) (упоминание field voice for mobile workers).

**Причины неудач и рецепты rollout** (вендорские, но согласуются с прецедентом Einstein): голос как «ещё один инструмент» не приживается — должен стать способом по умолчанию для одного самого болезненного сценария (для полевых — логирование после визита); менеджеры должны пользоваться этими данными на разборах воронки, иначе торговые бросят; первые ~30 дней — калибровка, а не ожидание идеала; два страха с первого дня — «это точно?» и «оно меня всё время слушает?» — снимаются превью «агент готовит — человек утверждает» и включением только по инициативе пользователя. Внедрения «застревают» не на голосе, а на интеграции с CRM: поля теряются, записи не к тем контактам.
Источники: [aiOla — Voice AI for field sales](https://aiola.ai/blog/voice-ai-for-field-sales/) (вендор, цифры экономии — маркетинговые); поисковая выдача (SPOTIO, CloudTalk).

**Ожидания руководства.** Gartner (2025): ИИ экономит продавцу ~4,8 ч/нед, но 72% организаций не реинвестируют это время в продажи — эффект не наступает сам.
Источник: [Gartner press release, 2026-05-19](https://www.gartner.com/en/newsroom/press-releases/2026-05-19-gartner-survey-finds-ai-saves-sellers-nearly-five-hours-per-week-yet-seventy-two-percent-of-sales-organizations-fail-to-reinvest-time-in-high-value-activities) (страница вернула 403, цифры — из поисковой выдачи; `TODO: уточнить` по первоисточнику).

## 9. Стоимость на пользователя

**Цены компонентов (сентябрь 2026):**
- OpenAI gpt-realtime-2.1: аудио вход $32 / выход $64 за 1М токенов; mini — $10 / $20. Транскрипция от $0,0045/мин, whisper $0,006/мин, стриминговые транскрайберы $0,017/мин. [OpenAI — Pricing](https://developers.openai.com/api/docs/pricing).
- В пересчёте: realtime ≈ $0,019/мин слушания и ≈ $0,077/мин говорения; каскад — Deepgram Nova-3 стриминг $0,0077/мин + дешёвая LLM <1 цента за несколько ходов + TTS ElevenLabs Flash $0,05 за 1000 символов. [Layer3labs — OpenAI Realtime API pricing](https://www.layer3labs.io/guides/openai-realtime-api-pricing) (по выдаче).
- Себестоимость на своих GPU (Cerebrium): ~$0,029/мин, главный рычаг — конкурентность; STT/TTS тарифицируют время открытого соединения, а не работу. [Cerebrium — What a voice AI agent really costs per minute](https://cerebrium.ai/resources/voice-ai-agent-cost-per-minute) (вендор).
- Каскад — предсказуемая цена ($0,0095–0,17/мин), S2S — разброс в 182 раза и рост с длиной разговора (контекст перечитывается каждый ход). [Gradium / сводка поиска](https://gradium.ai/content/cascaded-voice-agent-vs-speech-to-speech-2026) (по выдаче); LLM — самая быстрорастущая статья из-за истории. [Retell — pricing breakdown](https://www.retellai.com/blog/ai-voice-agent-pricing-full-cost-breakdown-platform-comparison-roi-analysis) (по выдаче).

**Собственная оценка (не из источника, для порядка величин):** менеджер, 21 рабочий день, ~30 мин активного голоса в день (≈ 2/3 — говорит человек, 1/3 — ассистент) → ~630 мин/мес.
- Каскад: ASR ~$5 + TTS ~$10 (≈190 тыс. символов) + LLM $2–10 → **≈ $15–25 в месяц на пользователя**.
- Realtime S2S: ~$8 (слушание) + ~$16 (говорение) + рост контекста → **≈ $25–50 в месяц**, mini — примерно втрое дешевле.
Главные множители: длина ответов ассистента (TTS — самая дорогая строка), длина истории в контексте, конкурентность при собственном хостинге. Телефонии у нас нет — её строку (~$0,01/мин) можно не считать.

---

## Что особенно важно для нашего кейса

1. **Каскад, а не speech-to-speech, как основа.** Нам нужен текстовый слой: валидация, журнал, prepare-превью, роли. Консенсус источников — для записи в системы и аудита каскад; S2S — эксперимент позже. ([AssemblyAI](https://www.assemblyai.com/blog/speech-to-speech-for-voice-agents), [Coval](https://www.coval.ai/blog/speech-to-speech-vs-cascaded-voice-ai-which-architecture-should-you-deploy/), [Full-Duplex-Bench-v3](https://arxiv.org/abs/2604.04847))
2. **Сущности резолвить по справочнику, а не доверять распознаванию.** Фамилии, хозяйства, сёла, номенклатура: динамический keyterm-список на сессию из плана дня (20–50 терминов, меняется при переходе к визиту) + retrieval-коррекция по CRM/1С + fuzzy-match с порогами. Голос даёт 14–41% точности сбора сущностей против 100% в тексте. ([τ-Elicitation](https://arxiv.org/abs/2609.13602), [DARAG](https://arxiv.org/abs/2410.13198), [AssemblyAI keyterms](https://www.assemblyai.com/blog/keyterm-prompting-real-time-accuracy), [Speechmatics SKU](https://www.speechmatics.com/company/articles-and-news/alphanumeric-speech-recognition-why-voice-assistants-mangle-skus-and-how-to-fix-it))
3. **Наш prepare → подтверждение клиентом совпадает с лучшей практикой — усилить тремя деталями:** зачитывать действие с реальными значениями (клиент, позиции, количества, сумма), а не «подтвердить?»; бэкчэннелы и голое «да» не считать подтверждением заказа в 1С — нужна отличимая фраза или кнопка; превью всегда дублировать на экране для проверки после остановки. ([OpenAI voice agents](https://developers.openai.com/api/docs/guides/voice-agents), [Hamming](https://hamming.ai/resources/voice-agent-interruption-handling-runbook), [Google](https://developers.google.com/assistant/conversation-design/confirmations))
4. **Итоги визита — один монолог → структурированный черновик, а не длинный диалог с уточнениями.** Многоходовые диалоги теряют ~39% качества; уточнять только то, что не резолвилось, по одному параметру. Сценарий — узлами с разными наборами инструментов. ([arXiv 2505.06120](https://arxiv.org/abs/2505.06120), [Pipecat Flows](https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/))
5. **Шум: не чистить звук перед ASR, выбирать ASR на своих записях из машины** (УАЗ/пикап на грунтовке, окно открыто, Bluetooth-громкая связь). Сделать эталонный корпус из 50–100 реальных диктовок до выбора поставщика. ([When De-noising Hurts](https://arxiv.org/abs/2512.17562), [Speechmatics in-car](https://www.speechmatics.com/company/articles-and-news/accurate-speech-to-text-is-ais-solution-to-vehicle-safety))
6. **Плохая связь: WebRTC для медиа + офлайн-очередь диктовок.** Без связи — записать аудио и локальный транскрипт, сделать prepare и подтверждение, когда связь вернулась; в CRM/1С офлайн не писать. Учесть, что Apple SpeechAnalyzer без custom vocabulary и русского может не быть в списке — `TODO: уточнить` поддержку русского. ([CallMissed WebRTC](https://www.callmissed.com/blog/webrtc-for-voice-ai-a-practical-primer), [Argmax](https://www.argmaxinc.com/blog/apple-and-argmax), [MS Field Service offline](https://learn.microsoft.com/en-us/dynamics365/field-service/mobile/offline-data-sync))
7. **Безопасность: токен пользователя — через token exchange с `act`, а не простым пробросом;** RAG — ACL на уровне чанка при индексации; кэш промптов — без общих префиксов с данными клиентов; данные из CRM/писем/документов — недоверенные: после их чтения модель не может сама выполнить запись, только предложить prepare. ([Posta OBO](https://blog.christianposta.com/explaining-on-behalf-of-for-ai-agents/), [Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/), [ConfusedPilot](https://arxiv.org/abs/2408.04870), [Prompt caching audit](https://arxiv.org/abs/2502.07776))
8. **За рулём: короткие ответы и точное распознавание важнее «умности».** Отвлечение держится до 27 с после задачи; ошибки распознавания — главный фактор нагрузки. Ответ — 1–2 предложения, списки — до 3 пунктов, цифры нормализовать в коде до TTS, оформление заказа по возможности — на остановке. ([AAA II](https://aaafoundation.org/research/measuring-cognitive-distraction-automobile-ii-assessing-vehicle-voice-based-interactive-technologies/), [AAA 27 s](https://newsroom.aaa.com/2015/10/new-hands-free-technologies-pose-hidden-dangers-for-drivers/), [Gemini Live study](https://arxiv.org/abs/2601.15034))
9. **Оценка — по итоговому состоянию CRM/1С, а не по транскрипту.** Свой набор из реальных сценариев визитов, симуляция с шумом салона, метрики: exact match сущностей, false accept подтверждений, task success, P50/P95 задержки. ([τ-voice](https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks), [OpenAI crawl/walk/run](https://developers.openai.com/api/docs/guides/voice-agents))
10. **Внедрение — один сценарий по умолчанию и руководитель продаж, который смотрит эти данные.** Einstein Voice умер как «обёртка»; выжили узкие сценарии «после визита». Начать с голосового итога визита, а не с «ассистента на всё». ([TechTarget](https://www.techtarget.com/searchcustomerexperience/news/252487869/Salesforce-pulls-plug-on-Einstein-Voice-Assistant), [aiOla](https://aiola.ai/blog/voice-ai-for-field-sales/))

---

## Полный список источников

Открыты и прочитаны:
1. OpenAI — Voice agents guide — https://developers.openai.com/api/docs/guides/voice-agents
2. OpenAI — Prompting realtime models — https://developers.openai.com/api/docs/guides/voice-prompting
3. OpenAI Cookbook — Realtime prompting guide — https://developers.openai.com/cookbook/examples/realtime_prompting_guide
4. OpenAI — API Pricing — https://developers.openai.com/api/docs/pricing
5. Deepgram docs — Keyterm prompting — https://developers.deepgram.com/docs/keyterm
6. Deepgram — The noise reduction paradox — https://deepgram.com/learn/the-noise-reduction-paradox-why-it-may-hurt-speech-to-text-accuracy
7. AssemblyAI — Keyterm prompting — https://www.assemblyai.com/blog/keyterm-prompting-real-time-accuracy
8. AssemblyAI — Transcription accuracy on hard, noisy audio — https://www.assemblyai.com/blog/async-transcription-accuracy-hard-audio
9. AssemblyAI — Speech-to-speech vs cascaded — https://www.assemblyai.com/blog/speech-to-speech-for-voice-agents
10. Speechmatics — Alphanumeric speech recognition — https://www.speechmatics.com/company/articles-and-news/alphanumeric-speech-recognition-why-voice-assistants-mangle-skus-and-how-to-fix-it
11. Speechmatics — In-car speech-to-text and vehicle safety — https://www.speechmatics.com/company/articles-and-news/accurate-speech-to-text-is-ais-solution-to-vehicle-safety
12. ElevenLabs — Agents prompting guide — https://elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide
13. LiveKit — Turn detection: VAD, endpointing, model-based — https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection
14. LiveKit — Improved end-of-turn model cuts interruptions 39% — https://livekit.com/blog/improved-end-of-turn-model-cuts-voice-ai-interruptions-39
15. LiveKit — Configuring turn detection and interruptions — https://livekit.com/blog/turn-detection-and-interruption-handling
16. Daily/Pipecat — Pipecat Flows — https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/
17. WebRTC.ventures — Voice AI latency budget — https://webrtc.ventures/2026/09/voice-ai-latency-budget/
18. Hamming — Voice agent evaluation metrics — https://hamming.ai/resources/voice-agent-evaluation-metrics-guide
19. Hamming — Interruption handling runbook — https://hamming.ai/resources/voice-agent-interruption-handling-runbook
20. Coval — S2S vs cascaded — https://www.coval.ai/blog/speech-to-speech-vs-cascaded-voice-ai-which-architecture-should-you-deploy/
21. Cerebrium — Voice AI agent cost per minute — https://cerebrium.ai/resources/voice-ai-agent-cost-per-minute
22. CallMissed — WebRTC for voice AI — https://www.callmissed.com/blog/webrtc-for-voice-ai-a-practical-primer
23. Argmax — Apple SpeechAnalyzer and WhisperKit — https://www.argmaxinc.com/blog/apple-and-argmax
24. Sierra — τ-voice — https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks
25. arXiv 2609.13602 — τ-Elicitation — https://arxiv.org/abs/2609.13602
26. arXiv 2604.04847 — Full-Duplex-Bench-v3 — https://arxiv.org/abs/2604.04847
27. arXiv 2505.06120 — LLMs get lost in multi-turn conversation — https://arxiv.org/abs/2505.06120
28. arXiv 2410.13198 — DARAG / Failing Forward — https://arxiv.org/abs/2410.13198
29. arXiv 2512.17562 — When De-noising Hurts — https://arxiv.org/abs/2512.17562
30. arXiv 2402.08021 — Careless Whisper — https://arxiv.org/html/2402.08021v2
31. arXiv 2510.09236 — Automotive microphone characteristics and ASR — https://arxiv.org/abs/2510.09236
32. arXiv 2601.15034 — Visual and cognitive demands of LLM in-vehicle agent — https://arxiv.org/abs/2601.15034
33. arXiv 2602.15569 — Intermediate feedback of agentic in-car assistants (CHI 2026) — https://arxiv.org/abs/2602.15569
34. arXiv 2408.04870 — ConfusedPilot — https://arxiv.org/abs/2408.04870
35. arXiv 2502.07776 — Auditing prompt caching in LLM APIs — https://arxiv.org/abs/2502.07776
36. OWASP — AI Agent Security Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
37. Simon Willison — The lethal trifecta — https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/
38. Christian Posta — On-behalf-of and agent identity — https://blog.christianposta.com/explaining-on-behalf-of-for-ai-agents/
39. Google — Conversation design: confirmations — https://developers.google.com/assistant/conversation-design/confirmations
40. Amazon — Alexa Voice Design Guide announcement — https://developer.amazon.com/en-US/blogs/alexa/post/ea99c8a1-36fa-4778-bbc3-56a6cee6e3b9/announcing-the-amazon-alexa-voice-design-guid
41. AAA Foundation — Measuring cognitive distraction II — https://aaafoundation.org/research/measuring-cognitive-distraction-automobile-ii-assessing-vehicle-voice-based-interactive-technologies/
42. AAA Newsroom — Hands-free technologies pose hidden dangers (27 s) — https://newsroom.aaa.com/2015/10/new-hands-free-technologies-pose-hidden-dangers-for-drivers/
43. Microsoft Learn — Field Service offline data sync — https://learn.microsoft.com/en-us/dynamics365/field-service/mobile/offline-data-sync
44. TechTarget — Salesforce pulls plug on Einstein Voice Assistant — https://www.techtarget.com/searchcustomerexperience/news/252487869/Salesforce-pulls-plug-on-Einstein-Voice-Assistant
45. VentureBeat — Why Salesforce is killing off Einstein Voice Assistant — https://venturebeat.com/2020/07/21/why-salesforce-is-killing-off-einstein-voice-assistant/
46. Salesforce — Dreamforce 2026 announcements — https://www.salesforce.com/blog/dreamforce-2026-announcements/
47. aiOla — Voice AI for field sales (вендор) — https://aiola.ai/blog/voice-ai-for-field-sales/
48. Hacker News — Building an AI voice agent from scratch — https://news.ycombinator.com/item?id=46946705

Использованы по поисковой выдаче (страница не открылась или прочитана только аннотация — перед цитированием перепроверить):
- Gartner press release 2026-05-19 (403) — https://www.gartner.com/en/newsroom/press-releases/2026-05-19-gartner-survey-finds-ai-saves-sellers-nearly-five-hours-per-week-yet-seventy-two-percent-of-sales-organizations-fail-to-reinvest-time-in-high-value-activities
- Microsoft Learn — Capture opportunity notes using voice (редирект) — https://learn.microsoft.com/en-us/copilot/release-plan/2026wave1/copilot-sales/capture-opportunity-notes-using-voice-sales-agent
- Microsoft — What's new in Copilot for Sales, April 2025 — https://www.microsoft.com/en-us/dynamics-365/blog/it-professional/2025/05/01/whats-new-in-copilot-for-sales-april-2025/
- Federal Register — NHTSA Visual-Manual Guidelines — https://www.federalregister.gov/documents/2013/04/26/2013-09883/visual-manual-nhtsa-driver-distraction-guidelines-for-in-vehicle-electronic-devices
- Speechmatics — Custom dictionary — https://www.speechmatics.com/company/articles-and-news/speechmatics-launches-custom-dictionary
- Deepgram — Large vocabulary speech recognition — https://deepgram.com/learn/large-vocabulary-speech-recognition
- arXiv 2506.07510 — DeRAGEC — https://arxiv.org/pdf/2506.07510
- arXiv 2501.11378 — Whisper hallucinations from non-speech audio — https://arxiv.org/pdf/2501.11378
- arXiv 2410.15608 — Moonshine — https://arxiv.org/html/2410.15608v1
- Apple Developer — SpeechAnalyzer — https://developer.apple.com/documentation/Speech/bringing-advanced-speech-to-text-capabilities-to-your-app
- ElevenLabs — TTS best practices — https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
- Layer3labs — OpenAI Realtime API pricing — https://www.layer3labs.io/guides/openai-realtime-api-pricing
- Gradium — Cascaded vs S2S 2026 — https://gradium.ai/content/cascaded-voice-agent-vs-speech-to-speech-2026
- Retell — AI voice agent pricing breakdown — https://www.retellai.com/blog/ai-voice-agent-pricing-full-cost-breakdown-platform-comparison-roi-analysis
- TianPan — Permission-aware retrieval — https://tianpan.co/blog/2026-05-04-permission-aware-retrieval-enterprise-rag-access-control
- Verb — Confirming destructive actions — https://askverb.com/blog/confirming-destructive-actions-ai-agent
- Munder Difflin — Voice is a control plane — https://munderdiffl.in/blog/voice-as-a-control-plane-for-agent-fleets/
- TYPENORM — VUI design — https://typenorm.com/articles/voice-user-interface-design
- GitHub issue — Bluetooth HFP degradation — https://github.com/Shadowolf7/Vayu-Viewer/issues/89
