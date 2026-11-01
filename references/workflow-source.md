# Исходный workflow — проверенный источник

Репозиторий: https://github.com/asaf1n/AI-Orchestrated-Workflow
Проверка: `main`, commit `acd88df32f035cf0416d15a95204d895555b6881` (1 ноября 2026).

Прочитаны: корневые `README.md`, `AGENTS.md` и ссылки из `skills/orchestrate/references/connection.md`, `ownership.md`.

Подтверждено: роли `orchestrate`, `planner`, `implementer`, `reviewer`, формы `spec.md`/`result.md`/`review.md`; в новых проектах нормативный workflow не копируется, создаётся только `orchestrate_workflow/method.md`. Для Antigravity необходимо подтвердить, что глобальный skill обнаруживается и вызывает фактический runtime. Push отдельно разрешается пользователем.

При реальном подключении нового проекта агент перечитывает установленную актуальную версию; ссылка на этот commit нужна для трассируемости исходного задания, а не для принудительного отката всех навыков.
