/*
========================================================================
  СИД СЕРВИСНЫХ УЧЁТОК ИИ-АГЕНТОВ  (категория [AI Agent])

  Это ШАБЛОН. Настоящий файл — seed-ai-agents.sql рядом, он в .gitignore.
  Скопируй этот файл в seed-ai-agents.sql и подставь сгенерированные пароли.

  Запускать ПОСЛЕ init.sql — он создаёт dbo.spProvisionAiAgent.
  Идемпотентен: повторный прогон не плодит учётки, а обновляет их.

  Порядок работы:
    1. Прогнать этот файл — он только впишет строки в таблицы.
    2. EXEC dbo.spProvisionUsers @DryRun = 1;   — посмотреть план
    3. EXEC dbo.spProvisionUsers @DryRun = 0;   — создать логины и выдать права
    4. Раздать пароли людям через парольный менеджер. Не почтой и не в чат.
    5. Хранить пароли в парольном менеджере, а не в файле.

  После шага 3 spProvisionUsers затирает SqlPassword в таблице в NULL —
  в базе пароли открытым текстом не остаются. Этот файл остаётся
  единственным местом, где они есть, поэтому шаг 5 не откладывать.

  Технический директор (Улич Дмитрий) в списке есть, хотя организационно
  он в Администрации, а не в ИТ. Учётка ему нужна отдельная от рабочей:
  его собственная — категории [DB Admin], то есть db_owner на боевых базах.
  Агенту такие права давать нельзя, поэтому у него, как у всех, отдельный
  read-only логин. Разные учётки для человека и для агента — это не
  формальность: в аудите видно, что читал агент, а что делал человек.

  Кто НЕ получает учётку и почему:
    · бизнес-аналитики (Пономарев, Семков) — работают в ../pbeba,
      чекаута кода и доступа к БД у них нет;
    · информационная безопасность (Передельский), релиз-менеджер
      (Гаранжина) — команды /pbe-bug и /pbe-task вне их потока;
    · техподдержка и менеджеры клиента — другой департамент,
      выдача инструмента там не решена (долг З-9).

  Дата-инженеры учётку получают: она нужна для /pbe-task и разбора
  «цифра не бьётся». Учётные записи под витрины BI — отдельная история
  с изоляцией по контурам, см. Д-1 в долге.
========================================================================
*/

USE [DBAProvisioning];
GO

-- Ипполитов Иван — Директор департамента ИТ; консоль
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_ippolitov',
     @Password  = '<пароль-ippolitov>',
     @Notes     = 'ИИ-агент: Ипполитов Иван';

-- Улич Дмитрий — Технический директор (Администрация)
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_ulich',
     @Password  = '<пароль-ulich>',
     @Notes     = 'ИИ-агент: Улич Дмитрий, технический директор';

-- Селезнев Алексей — Разработка, рук. отдела «Админки»
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_seleznev',
     @Password  = '<пароль-seleznev>',
     @Notes     = 'ИИ-агент: Селезнев Алексей';

-- Воробьев Роман — Разработка админок
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_vorobyev',
     @Password  = '<пароль-vorobyev>',
     @Notes     = 'ИИ-агент: Воробьев Роман';

-- Гаврилов Андрей — Разработка админок
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_gavrilov',
     @Password  = '<пароль-gavrilov>',
     @Notes     = 'ИИ-агент: Гаврилов Андрей';

-- Клочков Денис — Разработка админок
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_klochkov',
     @Password  = '<пароль-klochkov>',
     @Notes     = 'ИИ-агент: Клочков Денис';

-- Ипполитова Светлана — Рук. отдела «Приложение»
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_petrovicheva',
     @Password  = '<пароль-petrovicheva>',
     @Notes     = 'ИИ-агент: Ипполитова Светлана';

-- Знайда Василий — Разработка приложения
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_znaida',
     @Password  = '<пароль-znaida>',
     @Notes     = 'ИИ-агент: Знайда Василий';

-- Бардюжина Мария — Тестирование
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_bardyuzhina',
     @Password  = '<пароль-bardyuzhina>',
     @Notes     = 'ИИ-агент: Бардюжина Мария';

-- Шуваев Владислав — Тестирование
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_shuvaev',
     @Password  = '<пароль-shuvaev>',
     @Notes     = 'ИИ-агент: Шуваев Владислав';

-- Маркграф Олег — Дата-инженер, рук. отдела
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_markgraf',
     @Password  = '<пароль-markgraf>',
     @Notes     = 'ИИ-агент: Маркграф Олег';

-- Корнеев Дмитрий — Дата-инженер
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_korneev',
     @Password  = '<пароль-korneev>',
     @Notes     = 'ИИ-агент: Корнеев Дмитрий';

-- Тырцов Ярослав — Дата-инженер
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_tirtsov',
     @Password  = '<пароль-tirtsov>',
     @Notes     = 'ИИ-агент: Тырцов Ярослав';

-- Яковлев Игорь — Дата-инженер
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_yakovlev',
     @Password  = '<пароль-yakovlev>',
     @Notes     = 'ИИ-агент: Яковлев Игорь';

GO

-- Применить: создать логины на сервере и разложить права по матрице.
EXEC dbo.spProvisionUsers @DryRun = 1;
-- EXEC dbo.spProvisionUsers @DryRun = 0;
GO

-- Проверить, что получилось:
SELECT LoginName, IsActive, Notes,
       CASE WHEN SqlPassword IS NULL THEN 'применён' ELSE 'ждёт прогона' END AS Пароль
FROM   dbo.tUserProvisioningList
WHERE  Category = 'AI Agent'
ORDER  BY LoginName;
