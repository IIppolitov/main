# Голосовые ИИ-ассистенты (голос + LLM-агент с инструментами): практика по англоязычным источникам

Метод: только англоязычные источники, каждый открыт и прочитан (через WebFetch). Где источник — маркетинг вендора, это помечено «(вендор)»; цифры из таких источников — ориентир, а не факт.

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

---

## 10. Реальные продукты

Что уже работает у вендоров CRM и полевых продаж, если смотреть на сценарии нашего ТЗ, а не на архитектуру. Отбор: pharma/life sciences CRM (наш рынок), крупные CRM с мобильным голосом, нишевые продукты для полевых продаж, агро. Salesforce разобран отдельно в [salesforce.md](salesforce.md) и здесь не повторяется. Каждый источник открыт и прочитан; где страница не открылась и факт взят из поисковой выдачи — помечено «(по выдаче)». Цифры с сайтов и из пресс-релизов вендоров — «(вендор)»: это ориентир, а не измерение. Отсутствие функции в таблице означает «в первоисточнике не нашёл», а не «точно нет».

### Veeva Vault CRM — Agentic Voice, Agentic Call Report, Account Summary

**Что и когда.** Прямой ориентир для нас: CRM для фармы, полевые медпредставители и MSL. В ноябре 2024 Veeva анонсировала CRM Bot (LLM «на выбор клиента» для pre-call planning, подсказок, контента) и Voice Control — «hands-free operation of CRM via spoken commands» через Apple Intelligence, оба на конец 2025, без доплаты. К выходу (3 декабря 2025) концепция распалась на отдельных агентов: Voice Agent, Pre-call Agent, Free Text Agent; «голосовое управление CRM» превратилось в диктовку с разбором в поля. Статус — GA, нужна лицензия Veeva AI.

**Как устроено (по справке Vault CRM):**
- «Agentic Voice uses AI to convert your words into text, then intelligently maps that text to fields and records in Vault CRM» — из голосовой заметки создаётся call report с предзаполненными полями; вход — Siri («hands-free access with Siri»), долгое нажатие на AI-иконку, диктовка в My Notes.
- Эволюция по релизам: 25R3 — только iPad; 26R1.0 — iPad и iPhone, автоматическое добавление участников визита («Names captured in the Call Voice Note are matched to person accounts»), места визита, Medical Insights для MSL, вкладка My Notes — очередь голосовых заметок, ещё не превращённых в запись; 26R1.3 — групповые визиты, продукты и ключевые сообщения по каждому участнику.
- Два приёма, прямо относящиеся к нашей задаче резолва сущностей: список возможных участников показывается **до** начала транскрипции, и агент опирается на «explicit product names rather than inferring them from context». Отдельные ошибки для таймаута LLM (120 с+), лимита токенов и нарушения политики.
- Подтверждение: пользователь просматривает предзаполненный call report и правит его перед сохранением/отправкой (по выдаче справки; открытая страница переадресует).
- Модель: «Vault AI agents use Anthropic's Claude as the LLM, hosted on AWS Bedrock». Оценка расхода: 30–50 тыс. токенов на запрос к Voice Agent, 10–15 тыс. — на Account Summary.
- Account Summary (бывший Pre-call Agent, переименован в 26R1.4): сводит данные CRM в «concise, actionable response» перед визитом; вход с карточки клиента и call report. «An internet connection is required» — офлайн нет. Голосового ввода для сводки справка не упоминает.
- Рядом — Free Text / Text Monitoring: проверка свободного текста заметок на комплаенс, в т. ч. ретроспективно для «home office» — ближайший аналог нашего журнала для руководства, но про содержание, а не про действия агента.

**Adoption.** Novo Nordisk: «Pre-call Agent and Voice Agent will drive efficiencies and allow the field to focus on the value parts of their jobs» (вендор, пресс-релиз). Отчёт за квартал до 31.07.2026: «a top 20 biopharma deployed Vault CRM and the Agentic Call Report across its full U.S. field team in the quarter»; Vault CRM live у 180+ клиентов. Числа по использованию голоса не раскрываются.

Источники: [Veeva — AI Agents now available (03.12.2025)](https://www.veeva.com/resources/veeva-ai-agents-now-available-to-increase-productivity-and-customer-centricity/); [PR Newswire — Veeva announces AI in Vault CRM (11.2024)](https://www.prnewswire.com/news-releases/veeva-announces-ai-in-vault-crm-302311077.html); [Vault CRM Help — Agentic Voice overview](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/VeevaAI/VoiceAgent/VoiceAgentOverview.htm); [Vault CRM Help — Veeva AI overview](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/VeevaAI/Overview.htm); [Account Summary](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/VeevaAI/PreCallAgent/PreCallAgentOverview.htm); [What's New 25R3.0](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/ReleaseNotes/25R3.0/NewIn25R3.0.htm); [26R1.0](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/ReleaseNotes/26R1.0/NewIn26R1.0.htm); [26R1.3](https://vaultcrmhelp.veeva.com/doc/Content/CRM_topics/ReleaseNotes/26R1.3/NewIn26R1.3.htm); [Veeva 8-K, Q2 FY2027](https://www.sec.gov/Archives/edgar/data/0001393052/000139305226000032/veev-20260731xex991.htm); [IntuitionLabs — Veeva AI roadmap](https://intuitionlabs.ai/articles/veeva-ai-roadmap-crm-bot-agents-2026) (обзор третьей стороны).

### IQVIA Field Force Agent (экосистема OCE)

**Что и когда.** Представлен 9 сентября 2025: ИИ-ассистент для представителей, KAM, MSL и менеджеров; работает поверх Veeva и Salesforce или отдельным мобильным приложением. Опирается на данные IQVIA (OneKey — 25 млн проверенных HCP в 118 странах). Статус — у первых клиентов, доступен как пилот.

**Сценарии.** Динамический таргетинг и территориальное планирование вместо «static, quarterly call plans», подготовка к визиту, next best action. Голос — только на выходе: «insights in audio, visual, and written formats (e.g., voice summaries, slides, and emails)». Голосового ввода итогов визита, записи в CRM и подтверждения на страницах продукта нет.

**Цифры (вендор):** «27% time savings in HCP call preparation and follow-up, 85% user satisfaction… 17% increase in multichannel equivalent calls».

Источники: [IQVIA — Introducing Field Force Agent](https://www.iqvia.com/blogs/2025/09/introducing-iqvia-field-force-agent); [IQVIA — Field Force Agent](https://www.iqvia.com/solutions/commercialization/commercial-analytics/omnichannel-engagement-and-insights/field-force-agent).

### Microsoft — Sales agent на мобильном (Outlook mobile, Microsoft 365 Copilot)

**Поправка к разделу 8 и [salesforce.md](salesforce.md#для-сравнения--microsoft):** голосовой сценарий у Microsoft первоисточником **подтверждается** — статья Microsoft Learn «Sales agent chat on mobile» (обновлена 01.07.2026). Прежние ссылки были не те.

**Что.** Sales agent в Outlook mobile и приложении Microsoft 365 Copilot, iOS и Android, пишет в Dynamics 365 Sales или Salesforce. Голосовые заметки в CRM — public preview (CMSWire, 27.04.2026); сам Sales agent объявлен GA.

**Как устроено — ближе всех к нашему prepare → подтверждение:**
- Заметка: распознавание — системная диктовка клавиатуры (в Outlook) или микрофон в приложении Copilot; пользователь начинает с намерения («I want to add a note to my Fabrikam opportunity»), **смотрит и правит транскрипт до отправки**, затем выбирает запись CRM и подтверждает.
- Изменение полей голосом («Change the deal amount for Fabrikam to $50,000»): агент находит сделку по контексту, при нескольких совпадениях переспрашивает, показывает предлагаемое изменение с именем поля и новым значением; допускаются несколько полей за разговор и исправление по ходу. «Confirmation is required before any update is saved to your CRM.» Только поля типов String, DateTime, Money, Integer, Decimal, Double, Picklist — связанные записи и позиции заказа голосом не меняются.
- Подготовка к встречам на мобильном: список предстоящих встреч, сводка записи CRM, «AI-generated insights»; итоги прошедших — из Teams.

Офлайн, язык, журнал и права в статье не описаны.

Источники: [Microsoft Learn — Sales agent chat on mobile](https://learn.microsoft.com/en-us/microsoft-sales-copilot/sales-agent-chat-mobile); [CMSWire — Microsoft adds voice agents across Dynamics 365](https://www.cmswire.com/customer-experience/microsoft-adds-voice-agents-across-dynamics-365/).

### SAP Sales Cloud mobile — Siri, Capture Meeting, Visit Insights

**Что.** Мобильное приложение SAP Sales Cloud (руководство от 16.09.2026). У SAP сразу три голосовых слоя — и ни один не «ассистент на всё»:
- **Siri Shortcuts / Voice Search (только iOS):** фиксированные фразы — «Log a call in SAP Sales Cloud», «Add a note…», поиск записи. Siri спрашивает тип и имя сущности, делает глобальный поиск, при нескольких совпадениях просит выбрать. Ограничение показательное: «Siri uses the exact business entity labels configured in your system. Alternate names or synonyms are not supported.»
- **Capture Meeting (только iOS):** запись и транскрипция личной встречи прямо в приложении, «Audio is transcribed on your device in real time»; по итогам — заметки, сводка, «Follow-up Suggestions», сводку можно сохранить **PDF-вложением** к визиту. Ограничения: только устройства с Apple Intelligence, «results may vary and should be verified by the user», транскрипты встреч — пока только английский, запись встаёт на паузу, если приложение ушло в фон.
- **Visit Insights (GenAI):** «pre visit and post-visit summaries» по задачам, заметкам, опросам визита, кнопка **Text-to-speech** — прослушать сводку.

Прочее: из визита можно создать связанный sales quote, навигация до клиента через картографическое приложение. В требованиях к устройствам: «There's no offline support for this application» (для части функций есть кэш Downloads). Голос в Joule (LiveKit, real-time) — Early Adopter Care, GA в H2 2026 (по выдаче: блоги SAP Community отдают 403). Партнёрский обзор описывает агента-брифинг, который собирает открытые сделки, тикеты и «last quarter's orders from S/4HANA» в одностраничную справку перед встречей — это пример кастомного агента в Joule Studio, а не штатная функция.

Источники: [SAP Help — SAP Sales Cloud Mobile App (PDF, 2026-09-16)](https://help.sap.com/doc/41fddcfe2a0c4e009fb5d08ec8bf5620/CSS_SHIP/en-US/CX_NG_CSS_SalesMobileApps_1.pdf); [Spadoom — What's new in SAP Sales Cloud V2 Q1 2026](https://www.spadoom.com/en/blog/whats-new-sap-sales-cloud-v2-q1-2026/) (партнёр); [SAP UX Q3/2026 Update](https://community.sap.com/t5/technology-blog-posts-by-sap/sap-ux-q3-2026-update-part-1-ai-joule-mobile-sap-build-work-zone/ba-p/14451534) (по выдаче).

### Oracle — Oracle Voice (2015) → Sales Assistant → вывод SaaS-скиллов (2026)

**Интересен как история, а не как продукт.** В феврале 2015 Oracle выпустила Oracle Voice для Sales Cloud Release 9 — голосового ассистента на смартфоне продавца: заметки, задачи, встречи, контакты, сделки; распознавание и синтез — Nuance, только США и английский. Заявлено: ввод голосом «three times faster than typing», «80% of Oracle field sales reps testing Oracle Voice said the product exceeded their expectations» (вендор). Дальше — Oracle Sales Assistant в CX Sales Mobile: чат-бот, «You can enter questions or dictate commands», просмотр и обновление записей. Сейчас готовые SaaS-скиллы Oracle Digital Assistant для Fusion Applications «entered maintenance mode in Release 25.10 and will be discontinued on November 21, 2026», переход — на AI Agent Studio. В уведомлении перечислены SCM, ERP, HCM «and so on»; распространяется ли оно на Sales Assistant явно, `TODO: уточнить`. Голосового ввода у новых агентов Oracle Sales (26A/26B) не нашёл.

Вывод из истории: голосовой командный интерфейс к CRM у Oracle прожил около десяти лет, хорошо тестировался, но так и не стал основным способом работы и уходит вместе с платформой.

Источники: [Cloud Communications — Oracle Voice… Release 9 (10.02.2015)](https://www.cloudcommunications.com/news/oracle-voice-brings-a-mobile-speech-enabled-virtual-assistant-to-the-oracle-sales-cloud-in-release-9-on-smart-phones-2); [Oracle Docs — Sales Assistant in CX Sales Mobile](https://docs.oracle.com/en/cloud/saas/sales/fasqa/what-conversational-interactions-can-i-have-with-oracle-sales-assistant-in-cx-sales-mobile.html); [Oracle Docs — Oracle Skills (уведомление о выводе)](https://docs.oracle.com/en/cloud/paas/digital-assistant/skills.html).

### SPOTIO DASH (и Badger Maps) — нишевые приложения для полевых продаж

**SPOTIO DASH** — запущен 1 апреля 2026, встроен в мобильное приложение SPOTIO (маршруты, территории, визиты). Четыре части: DASH IQ — сводки по клиенту, информация о продукте, подготовка к визиту из базы знаний компании; DASH Actions — голос → записи, follow-up, оцифровка визитки или документа по фото, черновики писем; DASH Go — «Reps talk to DASH to hear summaries and draft visit notes by voice — then confirm with a quick tap when it's safe»; DASH Connect — синхронизация с Salesforce, HubSpot, Pipedrive, SAP, Oracle, а также «works with the LLM of your choice, including Claude and ChatGPT». Каждое действие ИИ требует подтверждения человеком. Офлайн: «Download My Day» — до 24 ч доступа к записям и логирования, но «DASH's AI features require a connection». CEO: «Field sales has been underserved by AI because most tools were built for reps who sit at a desk.» По их же отчёту 2026, треть полевых команд не использует никаких ИИ-инструментов (вендор).

**Badger Maps (Badger AI):** ИИ-маршрут на неделю по давности визита, статусу и размеру возможности, брифинги перед встречей из CRM; «drop a voice note and Badger does the rest… populates the right fields in your CRM» — на странице помечено **«Coming soon»**. Список CRM включает Veeva.

Источники: [SPOTIO — DASH AI Co-Pilot](https://spotio.com/features/ai-field-sales/); [National Law Review — SPOTIO launches DASH (01.04.2026)](https://natlawreview.com/press-releases/spotio-launches-dash-ai-co-pilot-purpose-built-field-sales-teams) (пресс-релиз); [Badger Maps — Badger AI](https://www.badgermapping.com/badger-ai/).

### VoiceLine и bliro — голос после визита, европейские стартапы

**VoiceLine** (Мюнхен): после встречи сотрудник наговаривает заметку или звонит; ИИ превращает это в отчёт о визите, записи CRM, задачи и подготовку к следующей встрече и синхронизирует с «existing CRM, ERP, and enterprise systems». Каналы — приложение, телефонный звонок, кнопка на руле. Series A €10 млн (28.02.2026); 100+ корпоративных внедрений, «pilot success rate exceeding 95 percent», «up to 82% reduction in administrative effort» (вендор). Клиенты — промышленность и логистика (DACHSER, ABB, Knauf, KSB); следующие отрасли — фарма, медтех, еда и напитки. Агро среди клиентов не нашёл.

**bliro:** голосовые агенты, которым звонят обычным телефонным звонком из машины: «You dial the number right from your steering wheel — no opening apps or typing on your smartphone required». После встречи продавец рассказывает итоги — поля CRM заполняются; перед встречей агент зачитывает историю, открытые задачи и последние заметки. Работает по обычной телефонной сети, без мобильного интернета, аудио не хранится (GDPR). «6 to 8 hours per week», «+22 percent» конверсии (вендор, блог CEO от 11.08.2026).

Источники: [VoiceLine](https://www.voiceline.ai/); [Munich Startup — Voiceline Series A (28.02.2026)](https://www.munich-startup.de/en/117517/voiceline-series-a/); [bliro — CRM updates by phone](https://www.bliro.io/en/blog/crm-updates-by-phone-voice-ai-for-field-sales).

### Rilla и Siro — запись самого визита и коучинг

Другой класс: не диктовка после визита, а запись разговора с клиентом на телефон представителя. **Rilla** — запись → анализ → «virtual ridealongs» руководителя; явно предупреждает, что в ряде штатов США нужно уведомлять клиента о записи. Цифры с сайта: «+40% Average increase in close rates», руководитель делает «25 to 30 ridealongs a week» вместо 6–7 (вендор). **Siro** — «open the app and hit record», транскрипция, живые подсказки во время разговора, автозаметки и выгрузка данных в CRM; среди отраслей — medical devices и medical aesthetics; «80% of sales reps choosing daily AI coaching» (вендор). Оба продукта — про домашние услуги и ритейл; для B2B-визитов к хозяйствам и тем более для фармы запись визита требует согласия клиента, и у нас она ТЗ не предусмотрена. Интересны как образец «журнала для руководства», выросшего в отдельный продукт.

Источники: [Rilla](https://www.rilla.com/); [Siro](https://www.siro.ai/).

### Bayer E.L.Y. — агро-ассистент для продавцов и агрономов

**Что.** E.L.Y. (Expert Learning for You) — генеративный ассистент Bayer Crop Science, сделан с EY и Microsoft на Azure OpenAI и Azure AI Search (RAG); данные — регуляторика EPA, агрономические данные, результаты испытаний семян, дрон-снимки, обучающие видео, тикеты поддержки. Отвечает на вопросы вида «какие гибриды устойчивы к стеблевой гнили», программы защиты по региону. Прототип за 90 дней; 120 агрономов и 800 продавцов в контуре, активно пользуются «a few hundred» (EY); позже — «1,500 of our customer-facing employees — sales reps, agronomists, anyone working in the field across the U.S.» (VP Bayer, Future Farmer); пилоты с ритейлерами и фермерами.

**Внедрение:** проверка экспертами, затем четыре цикла с небольшими региональными группами, затем сотни пользователей в нескольких регионах. Экономия «10% of their time, four to five hours a week» у части пользователей (AgFunder, со слов участника; вендорская оценка). Голос и мобильный вывод — в планах на развивающиеся рынки, а не в текущем продукте. Сценарии CRM (визиты, заказы) не покрывает — это только база знаний.

Источники: [EY — How Bayer is unearthing agronomy's future with GenAI](https://www.ey.com/en_us/insights/consulting/how-bayer-is-unearthing-agronomy-future-with-generative-ai); [Microsoft Customer Stories — Bayer and EY (25.02.2025)](https://www.microsoft.com/en/customers/story/22209-bayer-azure-ai-foundry); [Future Farmer — Bayer brings E.L.Y.](https://futurefarmermag.com/bayer-brings-e-l-y-ai-to-farming-making-agriculture-smarter/); [AgFunder News (17.04.2025)](https://agfundernews.com/gen-ai-can-create-an-agronomist-on-steroids-says-white-paper-so-why-do-so-many-projects-fail-to-get-out-of-the-starting-blocks); цифры «60% быстрее ответ», «AgTech Breakthrough 2025» — [Bayer](https://www.bayer.com/en/us/news-stories/ely-wins-ai-based-agtech-solution-of-the-year) (по выдаче, 403).

### Syngenta Cropwise AI — ассистент продавцов семян

**Что.** Самый близкий к нам по предмету: ассистент для **продавцов семян** Syngenta в Северной Америке, подбор гибридов под условия хозяйства («What corn hybrids do you suggest for a dry, windy region?»), вопросы к агрономическим моделям (урожайность при разных нормах высева), погода и рынок в реальном времени. Встроен в мобильное приложение GHX 2.0. Интерфейс — текстовый чат; голоса, офлайна и записи в CRM в описании нет.

**Как устроено (AWS, 03.12.2024):** Amazon Bedrock Agents, модели Claude 3.5 Haiku/Sonnet и Llama 3.1 с заменой модели без правки кода; базы знаний на OpenSearch; Lambda-действия к рекомендательным моделям; Cognito. Ценная деталь для нашего раздела 4: «The user identity gets propagated over a secure side channel (session attributes) to the agent and action groups… The session attributes aren't shared with the LLM». Оценка — 100 пар вопрос-ответ, собранных у продавцов, ручная проверка плюс Ragas (релевантность, краткость, faithfulness). Результат: «generate recommendations with analytical models five times faster» (вендор).

Источник: [AWS ML Blog — Syngenta develops a generative AI assistant to support sales representatives](https://aws.amazon.com/blogs/machine-learning/syngenta-develops-a-generative-ai-assistant-to-support-sales-representatives-using-amazon-bedrock-agents).

### Кратко: HubSpot, Zoho, ServiceTitan

- **HubSpot Breeze Assistant (мобильный):** диктовка сообщения ассистенту (STT), сводка предстоящей встречи по контакту, компании, сделке, письмам и заметкам; «User permissions determine which actions can be performed with Breeze Assistant». Подтверждения записи в справке нет. [HubSpot KB](https://knowledge.hubspot.com/ai/use-breeze-assistant-on-the-hubspot-mobile-app).
- **Zoho Ask Zia:** текст и голос, действия — обновить поле, создать задачу или встречу; «Ask Zia supports the English language only»; один модуль и одна агрегатная функция на вопрос. [Zoho Help](https://help.zoho.com/portal/en/kb/crm/zia-artificial-intelligence/conversational-ai/articles/ask-zia).
- **ServiceTitan Atlas in Mobile (полевые техники, GA с Fall 2025):** голосом через voice-to-text — документация оборудования, калькуляторы, подсказки по процессу; про действия и подтверждения справка молчит. Это наш сценарий «справочные вопросы по базе знаний» в чистом виде. [ServiceTitan Release Hub — Technicians](https://help.servicetitan.com/release-hub/docs/fall-2025-release-st-75-technicians).

### Сводная таблица: продукт × сценарии ТЗ

«+» — подтверждено первоисточником; «±» — частично или косвенно; «−» — в первоисточнике нет; «?» — источник не говорит. Голос: «вв» — голосовой ввод, «выв» — голосовой вывод (TTS).

| Продукт | Голос | План дня | Сводка перед визитом | Итог визита → CRM | Заказ в ERP | Договор / печать | Скан в PDF | База знаний | Права | Журнал |
|---|---|---|---|---|---|---|---|---|---|---|
| Veeva Vault CRM | вв (Siri, диктовка) | ± | + | + (участники, место, продукты; правка перед сохранением) | − | − | − | ± (Agentic Media) | ? | ± (комплаенс-мониторинг текста) |
| IQVIA Field Force Agent | выв | + (таргетинг, территория) | + | ? | − | − | − | ? | ? | ? |
| Microsoft Sales agent (mobile) | вв (диктовка ОС) | ± (список встреч) | + | + (заметка и поля с подтверждением) | − | − | − | − | ? | ? |
| SAP Sales Cloud mobile | вв + выв | ± (визиты, навигация) | + (Pre Visit Insights, TTS) | + (Siri, Capture Meeting) | ± (quote из визита вручную) | ± (сводка встречи в PDF) | − | ? | ? | ? |
| Oracle Voice / Sales Assistant | вв | − | ? | ± (заметки, задачи, обновление записей) | − | − | − | − | ? | ? |
| SPOTIO DASH | вв + выв | ± (маршруты, Download My Day) | + (зачитывает) | + (подтверждение тапом) | − | − | ± (фото визитки/документа) | + (DASH IQ) | ? | ? |
| VoiceLine / bliro | вв + выв (bliro — звонок) | ? | + | + | ± (VoiceLine: синхронизация с ERP) | − | − | ? | ? | ? |
| Rilla / Siro | запись визита | − | − | ± (автозаметки) | − | − | − | − | ? | + (записи для руководителя) |
| Bayer E.L.Y. | − | − | − | − | − | − | − | + | ? | ? |
| Syngenta Cropwise AI | − (текст) | − | ± (подбор гибридов) | − | − | − | − | + | + (identity мимо LLM) | ? |

### Выводы для нашего кейса

1. **Рынок сошёлся на одном сценарии — «наговорил после визита → черновик записи → человек подтвердил».** Veeva, Microsoft, SAP, SPOTIO, VoiceLine, bliro делают именно это, и все требуют подтверждения до сохранения («Confirmation is required before any update is saved to your CRM»). Голосовое управление приложением как таковое не выжило ни у Oracle (2015–2026), ни у Salesforce, а анонсированный Veeva Voice Control дошёл до пользователя как диктовка с разбором в поля. Итог визита → Bitrix24 — правильное ядро первого этапа.
2. **Голосового заказа в ERP, договора с печатью и скана подписанного нет ни у кого из разобранных.** Даже Microsoft голосом меняет только скалярные поля сделки, а не позиции. Это одновременно наш дифференциатор и наша зона наибольшего риска: заказ в 1С и договор — отдельный этап, всегда с экранным превью и на остановке.
3. **Резолв сущностей — до распознавания, а не после.** Veeva показывает список возможных участников до транскрипции и требует явных названий продуктов; SAP Siri не понимает синонимов; Microsoft переспрашивает при нескольких совпадениях. Это подтверждает раздел 1: keyterm-список и кандидаты из плана дня (клиенты, контакты, номенклатура) — до записи, с переспросом по одному полю.
4. **Сводка перед визитом голосом — уже стандарт** (SAP Visit Insights с TTS, SPOTIO, bliro, IQVIA). Наше отличие — содержательное: история закупок, плановая маржа, ассортимент под севооборот. Ассортимент под севооборот — ровно задача Syngenta Cropwise AI, и у них это отдельные рекомендательные модели, вызываемые агентом как инструмент, а не «знание» LLM.
5. **ИИ-функции офлайн не даёт никто:** Veeva Account Summary — «internet connection is required», SAP — «no offline support», SPOTIO — «AI features require a connection» при 24-часовом кэше записей. Рабочие обходы — очередь голосовых заметок до появления связи (Veeva My Notes) и телефонный звонок агенту по обычной сети (bliro, VoiceLine) — второе стоит проверить для сёл, где голосовая связь есть, а мобильного интернета нет.
6. **Агро-гиганты сделали текстовые базы знаний, а не голосовые CRM-ассистенты.** Bayer E.L.Y. и Syngenta — RAG для продавцов и агрономов с поэтапным раскатом (эксперты → региональные группы → сотни пользователей) и оценкой на наборе реальных вопросов продавцов (100 пар у Syngenta). Для нашей базы знаний это готовый порядок пилота; у Syngenta же — готовый приём разграничения доступа: identity пользователя идёт в инструменты по боковому каналу и не попадает в LLM.
7. **Для Powbee как фарма-вендора Veeva задаёт планку.** Agentic Call Report развёрнут на всё полевое подразделение США у биофармы из топ-20; модель — Claude на Bedrock, ориентир расхода — 30–50 тыс. токенов на один голосовой запрос. В разделе 9 стоимость считалась по минутам голоса; этот ориентир стоит добавить в расчёт LLM-строки, она может оказаться не самой дешёвой. Agentic Voice — только iPad и iPhone; у Microsoft — iOS и Android.
8. **«Журнал для руководства» как журнал действий агента в продуктах почти не описан** — есть комплаенс-проверка текста заметок (Veeva) и записи визитов для коучинга (Rilla, Siro). Значит, формат журнала (кто, что сказал, что агент предложил, что подтверждено, что ушло в Bitrix24/1С) придётся проектировать самим; готового образца у вендоров нет.
