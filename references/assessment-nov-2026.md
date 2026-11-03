# Kenshi-хроника с ИИ: какой инструмент для какой задачи (состояние на ноябрь 2026)

**Главный вывод: ведите кампанию в Claude Code. Сделайте из неё локальный git-репозиторий, где лежат конвертер сейва, JSON-снимки, markdown-справочники и скриншоты. Числа, справочные файлы и визуализации пусть генерируются скриптами из JSON и проверяются через `git diff`, а не переписываются моделью «по памяти».** Claude Projects на claude.ai и ChatGPT в этой схеме — вспомогательные «окна» для художественной хроники и второго мнения. NotebookLM (ныне Gemini Notebook) подходит для справок по лору с цитатами.

## TL;DR
- **Числа и справочники → Claude Code (или Codex).** Только агент, который сам запускает ваш Python-конвертер, читает JSON и правит файлы точечными диффами, решает две главные проблемы: галлюцинации в числах и «дрейф» справочников при перезаписи. Чат-проекты (ChatGPT Projects, Claude Projects) этого принципиально не гарантируют.
- **Хроника на русском и интерактив → Claude.** Литературный текст лучше писать в Claude: в Claude Code или в чате/Cowork с подключённой папкой проекта. Сортируемые таблицы и схемы маршрутов надёжнее генерировать как самодостаточные HTML-файлы скриптом из JSON. Artifacts в claude.ai и Sites в ChatGPT удобны для показа, но не как источник истины.
- **ChatGPT — запасной и вспомогательный.** После переезда держать там основную кампанию нет смысла: Canvas убран, лимит 25 файлов на проект в Plus, Instant-контекст 54K токенов. Полезен как второе мнение, для Data analysis по JSON и для Sites. Для лора добавьте Gemini Notebook (50 источников бесплатно, до 300 на Pro).

## Key Findings

### Сравнительная таблица (по сценарию «Kenshi-хроника»)

| Критерий | ChatGPT Projects (+ Work/desktop) | Claude Projects / Cowork (claude.ai + Desktop) | Claude Code | OpenAI Codex | Gemini (Gems / Notebook / CLI) |
|---|---|---|---|---|---|
| (а) Точность чисел из сейва | Средне: Data analysis запускает Python по JSON, но ответы в чате не сверяются автоматически | Средне: Cowork исполняет код, но файлы проекта — RAG-фрагменты | **Высоко**: запускает конвертер, пишет проверочные скрипты, хуки | **Высоко** (аналогично, AGENTS.md, `codex exec`) | Notebook — цитаты, но без вычислений; CLI — как агент |
| (б) Много скриншотов | Лимит неявный: до 100 изображений на чат, включая страницы PDF (по FileUploadGPT, не официально), 20 МБ/шт. | 20 изображений на сообщение, 10 МБ/шт. | Читает файлы с диска пакетами, можно размечать субагентами | Аналогично | Длинный контекст 1M, Notebook принимает изображения |
| (в) Русская проза | Хорошо (GPT-5.6/6) | **Очень хорошо** (Opus 5.5, Fable 5.1) | Те же модели Claude, но интерфейс терминала | Ориентирован на код | Хорошо, но менее стабильный стиль (оценка) |
| (г) Согласованность справочников | Слабо: Sources в облачном проекте нельзя перезаписать программно; память — непрозрачная | Средне: память проекта видна и редактируется; правки файлов через Cowork | **Лучшее**: git, диффы, CLAUDE.md/AGENTS.md, auto memory | **Сильно** (git, AGENTS.md) | Слабо (Gems — 10 файлов, ручное обновление) |
| (д) Интерактивные визуализации | Code-block Preview (HTML/React), Sites (бета, Plus+) | Artifacts, Claude Design/Docs, inline-визуалы | Генерирует HTML-файлы, проверяет в браузере (Claude in Chrome) | Генерирует файлы | Ограниченно |
| (е) Цена (США, в месяц) | Go $8, Plus $20, Pro $100/$200/$500 | Pro $20, Max $100, Ultra $200 | Входит в Pro/Max/Ultra, общий лимит с чатом | [codeagentswarm](https://www.codeagentswarm.com/en/guides/claude-code-plans-and-pricing) [suprmind](https://suprmind.ai/hub/chatgpt/pricing/) Входит в планы ChatGPT | Notebook бесплатно (50 источников); Gemini CLI теперь платный |

### Рекомендации «инструмент → задача»

| Задача | Основной инструмент | Почему | Запасной |
|---|---|---|---|
| Запуск конвертера, парсинг JSON, блок изменений между сейвами | **Claude Code** (`/goal`, хуки) | Реальное исполнение Python, проверка вывода, детерминизм | Codex CLI |
| Обновление «Отряда», «Глоссария», «Справки по выгрузке» | **Claude Code**: таблицы генерирует скрипт, прозу правит модель диффами | Исключает дрейф: всё видно в `git diff` | Codex |
| Разбор ~280 скриншотов | **Claude Code + субагенты**: однократная разметка в `screenshots/index.md` (кто, где, дословные реплики) | Не упирается в лимит 20 изображений на сообщение; разметка делается один раз | claude.ai пакетами по ≤20 |
| Художественная хроника на русском | **Claude** (Opus 5.5 / Fable 5.1) в чате или Cowork с подключённой папкой | Сильнейшая проза; хроника читает факты из сгенерированных файлов | ChatGPT (GPT-6 Sol) как второе перо |
| Сортируемые таблицы, схемы маршрутов | **Скрипт → самодостаточный HTML** в репозитории; Claude Artifacts — для быстрых прототипов | Числа берутся прямо из JSON, а не из «памяти» модели | ChatGPT Sites (бета) |
| Лор Kenshi, вики-справки | **Gemini Notebook** с вики-страницами как источниками | Ответы с цитатами, отдельно от данных сейва | Claude Project с RAG |
| Второе мнение по тактике | ChatGPT (Plus) | Независимая модель ловит ошибки | — |

## Details

### 1. ChatGPT Projects (OpenAI)
- **Файлы проекта.** По справке OpenAI на 8 октября 2026: 5 файлов на проект на Free, 25 на Go и Plus, 40 на Pro, Business, Edu и Enterprise. [otio](https://otio.ai/blog/chatgpt-project-limits-research-workflow) Другие лимиты:
  - 512 МБ на файл; [chatgptlimit](https://chatgptlimit.com/chatgpt-file-upload-limit/)
  - 2 млн токенов на текстовый документ; [otio](https://otio.ai/blog/chatgpt-project-limits-research-workflow)
  - около 50 МБ на таблицу; [otio](https://otio.ai/blog/chatgpt-project-limits-research-workflow)
  - 20 МБ на изображение; [ai-toolbox](https://www.ai-toolbox.co/chatgpt-management-and-productivity/chatgpt-file-upload-limits-2026)
  - 80 загрузок за 3 часа. [otio](https://otio.ai/blog/chatgpt-project-limits-research-workflow)
  
  Для ваших ~280 скриншотов и десятка md-файлов 25 слотов Plus — жёсткое ограничение. Скриншоты придётся склеивать или выносить.
- **Память проекта.** Есть два режима: default и project-only. [openai](https://help.openai.com/en/articles/10169521-projects-in-chatgpt) С 14 августа 2026 режим можно переключить у существующего неопубликованного проекта. [felloai](https://felloai.com/chatgpt-projects/) Shared-проекты всегда работают в project-only. [openai](https://help.openai.com/en/articles/10169521-projects-in-chatgpt) ChatGPT Work недоступен в проектах с project-only памятью. [openai](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) Детального списка «что помнит проект» нет, и сторонние обзоры прямо называют это проблемой контроля. [omidsaffari](https://omidsaffari.com/blog/chatgpt-projects)
- **Локальные файлы.** С 9 июля 2026 новый ChatGPT desktop app объединяет Chat, Work и Codex. Work и Codex могут открывать локальную папку, читать и менять файлы в ней. [stashbase](https://stashbase.ai/blog/can-chatgpt-access-local-files/)
  - Облачный Project при этом **не даёт доступа к папке**: по документации OpenAI, «A ChatGPT project doesn't provide direct access to a folder on your computer». [chatgpt](https://learn.chatgpt.com/docs/projects)
  - Перезаписать Sources облачного проекта программно нельзя. Пользователи сообщают о багах при попытке создать локальный Work-чат внутри облачного проекта. [github](https://github.com/openai/codex/issues/34499) [openai](https://community.openai.com/t/codex-could-not-use-this-project-for-a-local-chat-repair/1392960)
  - Прежний ChatGPT agent, по данным Википедии, удалён в начале августа 2026 в пользу Work. [Wikipedia](https://en.wikipedia.org/wiki/OpenAI_Operator) Независимого подтверждения этому я не нашёл.
- **Data analysis.** Поддерживает .json, .md, .csv. Код выполняется в stateful Jupyter без доступа в сеть. OpenAI сама просит проверять сгенерированный код и выводы. [openai](https://help.openai.com/en/articles/8437071-code-interpreter)
- **Canvas.** Release notes OpenAI от 28 мая 2026: «canvas will no longer be available in GPT-5.5 Instant or GPT-5.5 Thinking». [openai](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) Вместо него появились writing/code blocks с Preview для HTML и React («may include» — не гарантировано). [openai](https://help.openai.com/en/articles/20001246-working-with-writing-blocks-and-code-blocks-in-chatgpt)
- **Sites.** Публичная бета запущена 9 июля 2026: интерактивные сайты и дашборды по URL. [openai](https://openai.com/index/chatgpt-for-your-most-ambitious-work/) Доступна на Plus, Pro, Business и Enterprise, на Free и Go — нет. [openai](https://help.openai.com/en/articles/20001339-creating-and-using-chatgpt-sites)
- **Модели и контекст.**
  - GPT-5.6 Sol выпущен 9 июля 2026. [openai](https://help.openai.com/en/articles/9624314-model-release-notes)
  - GPT-5.5 выведен из ChatGPT 14 октября 2026. [openai](https://help.openai.com/en/articles/9624314-model-release-notes)
  - По сторонним сводкам, в сентябре 2026 вышло семейство GPT-6. [runbear](https://runbear.io/posts/gpt-5-explained)
  - Контекст на Plus — 54K токенов в Instant и 256K в reasoning; на Pro — 128K и 400K. [geotoolbox](https://geotoolbox.ai/blog/chatgpt-pricing) [scriptbyai](https://www.scriptbyai.com/token-limit-openai-chatgpt/) Длинная кампания в одном чате на Plus быстро теряет ранние сообщения. [ai-toolbox](https://www.ai-toolbox.co/chatgpt-models/chatgpt-pricing)
- **Изображения.** OpenAI не публикует лимит изображений на сообщение. [slimsnap](https://slimsnap.ai/blog/chatgpt-image-limit) По FileUploadGPT (обновлено 20 сентября 2026), ChatGPT выдаёт сообщение «you can add at most 100 images, including PDF pages, to a chat». Лимит действует на разговор, а не на сообщение; страницы PDF тоже считаются. Источник сторонний.

### 2. Claude Projects на claude.ai (Anthropic)
- **Project knowledge.** До 30 МБ на файл, без лимита на число файлов. При приближении к контексту включается RAG-режим, который увеличивает ёмкость «до 10x». [fast](https://fast.io/resources/claude-project-knowledge-limit/) [grandlinux](https://www.grandlinux.com/en/blogs/claude-projects-knowledge.html)
  - Минус для вашего сценария: в RAG-режиме модель видит фрагменты, а не весь «Отряд» целиком. Это прямой источник ошибок в числах. [sentra](https://www.sentra.app/articles/claude-projects)
  - Справки Anthropic противоречат друг другу в том, на каких планах доступен RAG. [prompt-architects](https://prompt-architects.com/blog/390-claude-projects-prompting-with-project-knowledge)
- **Память.**
  - 10 июля 2026 память стала набором категоризированных записей. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 25 августа 2026 она стала общей для чата и Cowork; записи видны и редактируются в Settings › Memory. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 17 сентября 2026 вышел редизайн Projects (бета, пока в Claude Code): общая память, в которую пишет каждый «thread», и библиотека файлов и артефактов. Существующие проекты переведут позже. [claude](https://claude.com/resources/articles/projects-redesigned) [memorylake](https://www.memorylake.ai/en/blogs/claude-projects-thread-memory)
- **Artifacts.**
  - 12 марта 2026: интерактивные графики и диаграммы прямо в ответах. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 12 июня 2026: точечная правка черновиков. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 16 сентября 2026: Claude Design, Slides и Docs («живые документы») в любом разговоре, на всех планах, включая Free. [claude](https://support.claude.com/en/articles/12138966-release-notes)
- **Desktop / Cowork.**
  - 12 января 2026: research preview. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 9 апреля 2026: GA на macOS и Windows. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 7 июля 2026: веб и мобильные устройства, удалённые сессии. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - 16 сентября 2026: «Cowork comes to every conversation» для Pro и Max. [claude](https://support.claude.com/en/articles/12138966-release-notes)
  - С 6 октября 2026 новые сессии на Pro и Max идут в облаке Anthropic. К файлам из подключённых папок Claude обращается через desktop app и только пока приложение открыто. [claude](https://support.claude.com/en/articles/15520349-use-claude-cowork-on-web-desktop-and-mobile) [stashbase](https://stashbase.ai/blog/can-claude-desktop-access-local-files/) Это удобно, но «локальный» доступ фактически означает отправку файлов в облако. [windowsforum](https://windowsforum.com/news/claude-cowork-on-windows-folder-access-is-not-local.441791/)
- **Skills / plugins.** Skills появились в октябре 2025, открытый стандарт Agent Skills — 18 декабря 2025, публикация плагинов в каталог — 25 сентября 2026. [claude](https://support.claude.com/en/articles/12138966-release-notes)
- **Изображения.** По документации Vision: 20 изображений на сообщение в claude.ai, до 10 МБ и 8000×8000 px каждое. [claude](https://platform.claude.com/docs/en/build-with-claude/vision) Справочный центр пишет «20 files per chat», при этом сторонний тест уместил 101 изображение в один чат. [claudeimagine](https://claudeimagine.com/claude-image-upload-limit) На практике отправляйте пакеты по ≤20.
- **Длинные чаты.** С 24 ноября 2025 работает автоматическое сжатие (compaction): ранние сообщения заменяются сводкой. Нужен включённый code execution. Длинные чаты тратят больше лимита, а точные числа и формулировки в сводке могут упроститься. [claude-for](https://claude-for.com/guides/claude-compacting-our-conversation) [getequerry](https://getequerry.app/guides/claude-compacting-conversation)
- **Модели.** Fable 5.1 (1 сентября 2026), Opus 5.5 (22 сентября; release notes Anthropic: «It performs at the level of Claude Fable 5.1 on most work and costs 40% less to run than Opus 5»; цена в API — $4/$20 за 1M токенов против $5/$25 у Opus 5, по данным Quartz), Sonnet 5.5 (28 сентября), Haiku 5.5 (7 октября).

### 3. Claude Code
- **Инструкции.** Claude Code читает CLAUDE.md. С v2.1.277 (сентябрь 2026) он читает AGENTS.md напрямую, если CLAUDE.md нет; иначе AGENTS.md подключают строкой `@AGENTS.md`. [deployhq](https://www.deployhq.com/blog/ai-coding-config-files-guide) [claude](https://code.claude.com/docs/en/memory) [eesel](https://www.eesel.ai/blog/claude-code-agents-md) Для кампании это значит, что один AGENTS.md («правила хрониста», стиль, глоссарий) будет общим для Claude Code, Codex и Gemini.
- **Auto memory.** Заметки хранятся в `~/.claude/projects/<project>/memory/` в виде обычного markdown, их можно редактировать командой `/memory`. [Substack](https://aimaker.substack.com/p/anthropic-claude-updates-q1-2026-guide)
- **Субагенты.** Это Markdown-файлы с YAML-фронтматтером в `.claude/agents/`. Встроены Explore, Plan и general-purpose. [claude](https://code.claude.com/docs/en/subagents-and-plugins.md) Удобно завести отдельных субагентов: «аудитор чисел» (сверяет любой текст с JSON) и «разметчик скриншотов». [huggingface](https://huggingface.co/learn/context-course/unit4/using-subagents)
- **Работа до достижения цели.**
  - `/goal <условие>` — это обёртка над session-scoped Stop-хуком: после каждого хода быстрая модель проверяет условие. Нужна v2.1.139+. [pinggy](https://pinggy.io/blog/claude_code_loop_codex_goal_long_horizon_tasks/) [claude](https://code.claude.com/docs/en/goal)
  - `/goal` работает и headless: `claude -p "/goal ..."`. [claude](https://code.claude.com/docs/en/goal)
  - `/loop` повторяет задачу по таймеру. [claude](https://code.claude.com/docs/en/goal)
  - Stop-хук, который блокирует остановку больше 8 раз подряд, принудительно завершается. [claude](https://code.claude.com/docs/en/changelog)
  - Пример: `/goal скрипт validate.py проходит без ошибок и Отряд.md совпадает с snapshot.json`.
- **Хуки и headless.** Хуки на события (например, PostToolUse → прогнать `validate.py` после каждой правки md) и `claude -p` позволяют собрать конвейер «новый сейв → конвертер → дифф → обновление справочников → отчёт». [kunalganglani](https://www.kunalganglani.com/blog/loop-engineering-agent-loops)
- **Ограничение.** Лимит общий с чатом и Cowork на том же аккаунте. [ssdnodes](https://www.ssdnodes.com/learn/claude-max-plan-100-vs-200) Проза в терминале менее удобна для чтения, но файл можно открыть в любом markdown-просмотрщике.

### 4. Альтернативы
- **OpenAI Codex.**
  - CLI, IDE и облако; с 9 июля 2026 встроен в ChatGPT desktop app. [Wikipedia](https://en.wikipedia.org/wiki/OpenAI_Codex_%28AI_agent%29)
  - Читает AGENTS.md, есть `codex exec` для неинтерактивного режима. [github](https://github.com/devmruAI/codex)
  - Goal Mode (`/goal`) — GA с 21 мая 2026 (CLI 0.133), рассчитан на задачи «на часы и дни». Текст цели — до 4000 символов. [openai](https://developers.openai.com/codex/cli/reference) [pinggy](https://pinggy.io/blog/claude_code_loop_codex_goal_long_horizon_tasks/) [codersera](https://codersera.com/blog/ai-coding-agents-complete-guide-2026/)
  - Функционально близок к Claude Code, поэтому подходит как запасной «движок состояния» при подписке ChatGPT.
- **Google.**
  - NotebookLM переименован в Gemini Notebook в июле 2026. Лимиты: 50 источников на блокнот бесплатно, 100 на Plus, 300 на Pro, до 600 на Ultra; до 500 000 слов или 200 МБ на источник. [fast](https://fast.io/resources/notebooklm-size-limit/) [fast](https://fast.io/resources/notebooklm-max-sources/) [geminiomniprompts](https://geminiomniprompts.org/blog/notebooklm-limits/) С 2 сентября 2026 квота обновляется каждые 5 часов, с недельным потолком. [elephas](https://elephas.app/blog/notebooklm-source-limits)
  - Gems ограничены 10 файлами, но могут подключать блокноты Notebook. [aifire](https://www.aifire.co/p/build-a-pro-knowledge-base-gemini-notebooklm-2026) [remio](https://www.remio.ai/post/the-gemini-notebooklm-integration-turning-300-sources-into-a-custom-brain)
  - Gemini CLI: GEMINI.md, контекст 1M. 19 мая 2026 Google объявил в Google Developers Blog («An important update: transitioning Gemini CLI to Antigravity CLI»), что с 18 июня 2026 Gemini CLI «stops serving Google AI Pro, Ultra, and free Gemini Code Assist for individuals users» (цитата по Digital Applied); их переводят на Antigravity CLI. Бесплатные 1000 запросов в день действовали только до 18 июня. Проверьте перед выбором.
- **Cursor, GitHub Copilot.** Оба есть в официальном списке совместимых инструментов на agents.md («Cursor», «Coding agent from GitHub Copilot»). По документации GitHub, Copilot читает AGENTS.md в любом месте репозитория, и приоритет у ближайшего файла. Возможности те же, что у Claude Code и Codex, но они ориентированы на IDE. Для хроники преимуществ нет, если вы не работаете в VS Code постоянно. Отдельно их в этом исследовании не проверяли.
- **RAG и вики.** Для вопросов по лору (фракции, NPC, механики модов) нужен отдельный грунтованный корпус — Notebook или собственный индекс вики-страниц. Его нельзя смешивать с данными сейва, иначе модель будет путать «канон» с вашим отрядом.

### Известные проблемы и как их обойти
- **Дрейф проектных файлов при перезаписи.** Когда модель переписывает файл целиком, она молча сокращает, пересказывает или «улучшает» записи. В чат-проектах это хуже всего: правки нельзя проверить диффом, а Sources в ChatGPT вообще не перезаписываются программно. Решения:
  - разделить файлы на **генерируемые** (таблицы отряда, навыки, раны — только скриптом из JSON) и **авторские** (хроника, глоссарий — только точечными правками);
  - сделать хронику append-only;
  - коммитить каждое обновление и смотреть `git diff`;
  - запустить хук, который проверяет, что число персонажей и все ID из JSON присутствуют в «Отряде».
- **Потеря контекста в длинных чатах.**
  - Claude сжимает историю автоматически. Это лучше обрыва, но детали теряются. [unmarkdown](https://unmarkdown.com/blog/claude-compacting-explained) [dev](https://dev.to/agentic-engineer/taming-context-windows-disable-auto-compact-for-better-ai-4gbm)
  - У ChatGPT Plus Instant-окно всего 54K. [ai-toolbox](https://www.ai-toolbox.co/chatgpt-models/chatgpt-pricing)
  - Решение: «один сейв — одна сессия». Состояние живёт в файлах, а не в чате; в начале сессии загружаются только AGENTS.md, последний снимок и сводка изменений. [golev](https://golev.com/post/claude-saves-tokens-forgets-everything/) [dev](https://dev.to/agentic-engineer/taming-context-windows-disable-auto-compact-for-better-ai-4gbm)
- **Лимиты на изображения за сообщение.**
  - Claude.ai — 20 на сообщение; при больших пакетах в API действует понижение разрешения до 2000 px. [claude](https://platform.claude.com/docs/en/build-with-claude/vision) [slimsnap](https://slimsnap.ai/blog/claude-image-count-limit)
  - ChatGPT — до 100 изображений на чат, включая страницы PDF (по FileUploadGPT; OpenAI официального числа не публикует).
  - Решение: разметить скриншоты один раз в текстовый индекс и дальше ссылаться на него, а изображения прикладывать точечно.

### Предлагаемая структура репозитория
- `AGENTS.md` — роль хрониста/советника, стиль русской прозы, правило «числа только из JSON». Плюс `CLAUDE.md` со строкой `@AGENTS.md`. [deployhq](https://www.deployhq.com/blog/ai-coding-config-files-guide)
- `tools/convert_save.py`, `tools/validate.py`, `tools/render_tables.py` (генерирует md и HTML с сортировкой).
- `data/snapshots/*.json`, `data/diffs/*.json`.
- `docs/Хроника.md` (append-only), `docs/Отряд.md` (генерируется), `docs/Глоссарий.md`, `docs/Справка_по_выгрузке.md`.
- `screenshots/` и `screenshots/index.md`.
- `viz/*.html` — сортируемые таблицы и схемы маршрутов (например, SVG по координатам из сейва, если конвертер их выдаёт).
- Синхронизация с claude.ai: подключите эту же папку в Cowork. Тогда хронику можно писать в чате, читая те же файлы, без ручной загрузки в project knowledge.

### Ключевые даты релизов
| Дата | Событие |
|---|---|
| 24.11.2025 | Автосжатие длинных чатов в приложениях Claude | [getequerry](https://getequerry.app/guides/claude-compacting-conversation)
| 18.12.2025 | Открытый стандарт Agent Skills | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 12.01.2026 | Cowork research preview (macOS, Max) | [Wikipedia](https://en.wikipedia.org/wiki/Claude_%28language_model%29) [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 12.03.2026 | Интерактивные визуалы в ответах Claude | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 09.04.2026 | Cowork GA (macOS/Windows) | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 21.05.2026 | Codex Goal Mode GA | [techjacksolutions](https://techjacksolutions.com/ai-brief/openai-codex-goal-mode-reaches-general-availability-what-the/)
| 28.05.2026 | Canvas убран из GPT-5.5 | [openai](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)
| 18.06.2026 | Gemini CLI перестаёт обслуживать бесплатных и Google AI Pro/Ultra пользователей, перевод на Antigravity CLI (объявлено Google 19.05.2026) |
| 09.07.2026 | ChatGPT Work, GPT-5.6 Sol, Sites (бета), слияние Codex с ChatGPT desktop | [geotoolbox](https://geotoolbox.ai/blog/chatgpt-pricing) [MacRumors](https://www.macrumors.com/2026/07/09/openai-chatgpt-work/)
| 10.07.2026 | Новая память Claude (категоризированные записи) | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 14.08.2026 | Переключение памяти существующих проектов ChatGPT | [felloai](https://felloai.com/chatgpt-projects/)
| 25.08.2026 | Общая память чата и Cowork, редактируемые Topics | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 02.09.2026 | Новая квота Gemini Notebook (каждые 5 часов) | [geminiomniprompts](https://geminiomniprompts.org/blog/notebooklm-limits/)
| 16.09.2026 | Cowork в каждом разговоре; Claude Design/Slides/Docs | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 17.09.2026 | Редизайн Claude Projects (бета в Claude Code) | [bosio](https://bosio.digital/articles/claude-projects-redesign)
| 22.09.2026 | Claude Opus 5.5 | [claude](https://support.claude.com/en/articles/12138966-release-notes)
| 06.10.2026 | Сессии Cowork на Pro/Max переходят в облако | [stashbase](https://stashbase.ai/blog/give-claude-access-to-local-files/)
| 14.10.2026 | GPT-5.5 выведен из ChatGPT | [openai](https://help.openai.com/en/articles/9624314-model-release-notes)
| 30.10.2026 | Claude Max 5x → Max, Max 20x → Ultra | [biggo](https://finance.biggo.com/news/fbf12069-c58e-4695-98a1-1a78083456ed) [toolcolumn](https://www.toolcolumn.com/learn/claude-max-vs-ultra)

## Recommendations
1. **Сейчас.** Перенесите проект в git-репозиторий и запустите Claude Code в нём. Первая задача — `/goal`: «validate.py подтверждает, что Отряд.md совпадает с последним снимком». Это сразу выявит накопленный дрейф.
2. **План.** Начните с Claude Pro за $20. Если пакетная разметка скриншотов и регулярные прогоны `/goal` упираются в пятичасовой лимит, переходите на Max за $100: лимит общий для чата, Code и Cowork. Ultra за $200 нужна только при многочасовой автономной работе. [ssdnodes](https://www.ssdnodes.com/learn/claude-max-plan-100-vs-200) [codeagentswarm](https://www.codeagentswarm.com/en/guides/claude-code-plans-and-pricing) [tokenkarma](https://tokenkarma.app/blog/anthropic-usage-limits-explained-2026/)
3. **ChatGPT.** Не продлевайте Pro ради этой кампании. Plus за $20 оправдан, только если вам ценно второе мнение или нужны Sites для публичного дашборда. [nocode](https://www.nocode.mba/articles/chatgpt-pricing)
4. **Лор.** Соберите бесплатный блокнот Gemini Notebook из вики-страниц по фракциям и модам. Задавайте туда вопросы «что это за NPC/предмет», а ответы переносите в Глоссарий точечными правками.
5. **Правило для всех инструментов.** Ни одно число не попадает в хронику или справочник, если его не сгенерировал скрипт или не проверил аудитор чисел по JSON.

## Caveats
- Некоторые лимиты (изображения на чат в ChatGPT, файлы на чат в Claude) подтверждены только сторонними тестами и сводками. Базовые лимиты ChatGPT подтверждены официально: статья OpenAI Help Center «Uploading files and audio to ChatGPT» указывает «Images have a limit of 20 MB per image… You can upload up to 80 files every 3 hours. Free users are limited to 3 file uploads per day» и 25 ГБ хранилища на пользователя. Перед покупкой сверьтесь со счётчиками в приложении.
- По Opus 5.5 / Fable 5.1 и GPT-5.6/6 нет независимых сравнений качества русской художественной прозы. Оценка «Claude сильнее в прозе» основана на общей репутации и рассчитана на ваш собственный A/B-тест на одной главе.
- Функции в бете (Sites, редизайн Claude Projects, облачные сессии Cowork) меняются ежемесячно. Таблица отражает состояние на 1 ноября 2026.
- Cowork и ChatGPT Work отправляют содержимое локальных файлов на серверы вендора. [windowsforum](https://windowsforum.com/news/claude-cowork-on-windows-folder-access-is-not-local.441791/) [stashbase](https://stashbase.ai/blog/can-chatgpt-access-local-files/) Для игровых данных это обычно неважно, но учитывайте это.