/*
========================================================================
  USER PROVISIONING SYSTEM FOR MSSQL  — v6
  Безопасна для многократного запуска (Idempotent)

  v6: категория [AI Agent] — сервисные учётки только на чтение под ИИ-агентов.
    Обычная категория матрицы: db_datareader на PROD и на DEV, то есть
    SELECT на все таблицы и вьюхи каждой базы из tDatabases, включая те,
    что появятся позже. EXECUTE не выдаётся — процедура умеет писать.
    В [sysadmin] такая учётка не попадает никогда (это только [DB Admin])
    и обязана быть SQL-логином (CHECK chk_upl_AiAgentIsSql).
    Модель — одна учётка на разработчика (svc_ai_<фамилия>), все контуры.
    Заводится процедурой [8] dbo.spProvisionAiAgent; подробности и порядок
    отзыва — «СЕРВИСНЫЕ УЧЁТКИ ДЛЯ ИИ-АГЕНТОВ» в конце файла.

    Попутно исправлено: пара «пользователь + база», закрытая исключением,
    больше не обрабатывается матрицей. Раньше каждый прогон сначала снимал
    права по матрице, а следом выдавал их заново по исключению — доступ
    моргал в середине прогона, а журнал распухал на ровном месте.

  v5: предохранитель на нехватку прав. Если базы [DBAProvisioning] нет,
    а права CREATE DATABASE у запускающего тоже нет, скрипт больше не
    сваливается в master, а останавливается целиком (SET NOEXEC ON):
    ни одного объекта не создаётся, в выводе — причина и что делать.
    См. «СОЗДАНИЕ БАЗЫ ДАННЫХ» сразу после шапки и «[10] ИТОГ ЗАПУСКА»
    в конце файла.

  v4: деактивация (IsActive=0) теперь реально отзывает доступ — не только
  исключает пользователя из будущих прогонов. При IsActive=0 скрипт:
    · отключает серверный логин (ALTER LOGIN ... DISABLE);
    · снимает членство в серверной роли [sysadmin], если оно было;
    · отзывает все управляемые роли/EXECUTE/VIEW DEFINITION во всех базах
      из dbo.tDatabases (то же самое происходит и при смене Category у
      активного пользователя — членство в [sysadmin] тоже теперь desired-state,
      а не "только добавляем").
  Обратное включение (IsActive=1) полностью восстанавливает доступ по матрице.

  МАТРИЦА ПРАВ (значения по умолчанию — меняй в tPermissionMatrix):
  ┌──────────────────────┬─────────┬───────────────────────────┬──────┬──────┐
  │ Категория            │ DbType  │ Роли                      │ EXEC │ VIEW │
  ├──────────────────────┼─────────┼───────────────────────────┼──────┼──────┤
  │ DB Admin             │ PROD    │ db_owner                  │  —   │  —   │
  │ DB Admin             │ DEV     │ db_owner                  │  —   │  —   │
  │ Dev Team             │ PROD    │ db_datareader|db_datawriter│  ✓   │  ✓   │
  │ Dev Team             │ DEV     │ db_owner                  │  —   │  —   │
  │ Data Engineer Team   │ PROD    │ db_datareader|db_datawriter│  ✓   │  ✓   │
  │ Data Engineer Team   │ DEV     │ db_datareader|db_datawriter│  ✓   │  ✓   │
  │ QA                   │ PROD    │ db_datareader              │  —   │  ✓   │
  │ QA                   │ DEV     │ db_datareader|db_datawriter│  —   │  ✓   │
  │ Support Senior       │ PROD    │ db_datareader              │  —   │  ✓   │
  │ Support Senior       │ DEV     │ db_datareader              │  —   │  ✓   │
  │ AI Agent             │ PROD    │ db_datareader              │  —   │  ✓   │
  │ AI Agent             │ DEV     │ db_datareader              │  —   │  ✓   │
  └──────────────────────┴─────────┴───────────────────────────┴──────┴──────┘

  EXEC = GRANT EXECUTE        — запуск всех процедур и функций в базе
  VIEW = GRANT VIEW DEFINITION — просмотр кода процедур, вьюх, функций
  DbType: PROD = базы без суффикса _dev  |  DEV = базы с суффиксом _dev
  Примечание: db_owner уже включает оба права, поэтому DB Admin и Dev Team DEV
              не нуждаются в явных EXEC/VIEW — они получают их через роль.
  AI Agent:   сервисные учётки ИИ-агентов. Права те же, что у Support Senior,
              но категория отдельная намеренно: за ней стоит не человек,
              а процесс, и отзывать/сужать её надо независимо от людей.

  ОБЪЕКТЫ (все создаются в базе [DBAProvisioning]):
    [0] dbo.fnQuoteLiteral         — безопасное экранирование строк для dynamic SQL
    [1] dbo.tUserProvisioningList  — список пользователей
    [2] dbo.tUserProvisioningLog   — аудит-журнал
    [3] dbo.tPermissionMatrix      — матрица прав (редактируемая)
    [4] dbo.tDatabases             — список баз для провижна (редактируемый)
    [5] dbo.tPermissionOverride    — исключения из матрицы (группа или юзер + база)
    [6] dbo.spProvisionUsers       — основная процедура (матрица + деprovision)
    [7] dbo.spProvisionOverrides   — процедура исключений (вызывается автоматически)
    [8] dbo.spProvisionAiAgent     — завести сервисную учётку ИИ-агента разработчика

  В конце init-скрипта разово выполняется очистка tDatabases от баз,
  которых нет на этом сервере.

  ЗАПУСК (из любого контекста):
    EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 1;
    EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;

  ЛОГИКА PROD vs QA:
    tProvisioningServerConfig УДАЛЕНА — она была избыточна.
    На PROD-сервере _dev баз физически нет → они скипаются автоматически
    через проверку EXISTS(sys.databases). Никакой доп. конфигурации не нужно.
    Добавить новую базу = один INSERT в dbo.tDatabases.
========================================================================
*/

-- ====================================================================
-- СОЗДАНИЕ БАЗЫ ДАННЫХ
-- Идемпотентно: пропускает если уже существует.
--
-- Предохранитель на случай нехватки прав. Все объекты [0]–[10] создаются
-- в текущей базе контекста, поэтому если [DBAProvisioning] нет и создать
-- её нельзя (нет CREATE ANY DATABASE), то без проверки USE молча упадёт,
-- а весь остаток скрипта развернётся в master. Здесь такой прогон
-- останавливается целиком: SET NOEXEC ON, ни одного объекта не создаётся,
-- в выводе — причина и что делать.
-- ====================================================================
SET NOCOUNT ON;

IF OBJECT_ID('tempdb..#ProvisioningTarget') IS NOT NULL
    DROP TABLE #ProvisioningTarget;

-- Временная таблица живёт до конца сессии — переживает GO и передаёт
-- решение о запуске всем последующим батчам скрипта.
CREATE TABLE #ProvisioningTarget
(
    TargetDb sysname       NOT NULL,
    Mode     NVARCHAR(20)  NOT NULL,   -- EXISTING | CREATED | ABORT
    Reason   NVARCHAR(400) NULL
);

DECLARE @TargetDb sysname = N'DBAProvisioning';

DECLARE @CanCreateDb BIT =
    CASE WHEN IS_SRVROLEMEMBER('sysadmin') = 1
           OR HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE ANY DATABASE') = 1
         THEN 1 ELSE 0 END;

IF DB_ID(@TargetDb) IS NOT NULL
BEGIN
    INSERT #ProvisioningTarget (TargetDb, Mode) VALUES (@TargetDb, N'EXISTING');
    PRINT 'SKIP: [DBAProvisioning] уже существует.';
END
ELSE IF @CanCreateDb = 1
BEGIN
    CREATE DATABASE [DBAProvisioning];
    INSERT #ProvisioningTarget (TargetDb, Mode) VALUES (@TargetDb, N'CREATED');
    PRINT 'OK: База данных [DBAProvisioning] создана.';
END
ELSE
BEGIN
    INSERT #ProvisioningTarget (TargetDb, Mode, Reason)
    VALUES (@TargetDb, N'ABORT',
            N'базы [DBAProvisioning] нет, а у ' + SUSER_SNAME()
          + N' нет права CREATE ANY DATABASE.');
    PRINT 'СТОП: базы [DBAProvisioning] нет, а прав на CREATE DATABASE у ' + SUSER_SNAME() + ' нет.';
    PRINT '      Скрипт остановлен, ни один объект не создан.';
    PRINT '      Попроси DBA создать базу [DBAProvisioning] и выдать в ней db_owner,';
    PRINT '      затем запусти этот скрипт заново — он идемпотентен.';
END

-- Дальше идти незачем: пропускаем USE и весь остаток скрипта.
IF EXISTS (SELECT 1 FROM #ProvisioningTarget WHERE Mode = N'ABORT')
    SET NOEXEC ON;
GO

USE [DBAProvisioning];
GO

-- Безусловно: батч под NOEXEC ON не выполняется, поэтому вернуть
-- выполнение можно только голым SET NOEXEC OFF, без IF.
SET NOEXEC OFF;
GO

-- Контроль контекста: объекты создаются в текущей базе, значит мы обязаны
-- стоять ровно в [DBAProvisioning]. Если USE не сработал (база есть, но
-- доступа к ней нет) — останавливаемся, чтобы объекты не уехали в master.
IF EXISTS (SELECT 1 FROM #ProvisioningTarget WHERE Mode = N'ABORT')
    SET NOEXEC ON;
ELSE IF EXISTS (SELECT 1 FROM #ProvisioningTarget WHERE TargetDb <> DB_NAME())
BEGIN
    PRINT 'СТОП: не удалось переключиться в [DBAProvisioning] — текущий контекст [' + DB_NAME() + '].';
    PRINT '      Нужен доступ к базе и db_owner в ней. Объекты не создаются.';
    UPDATE #ProvisioningTarget
    SET    Mode   = N'ABORT',
           Reason = N'USE не сработал, контекст остался [' + DB_NAME() + N'].';
    SET NOEXEC ON;
END
GO

-- ====================================================================
-- [0] СЛУЖЕБНАЯ ФУНКЦИЯ: безопасное экранирование строкового литерала
--     QUOTENAME(x, '''') официально работает только для строк ≤128
--     символов (для более длинных документированно возвращает NULL) —
--     он рассчитан на идентификаторы, а не на произвольные строки типа
--     пароля (SqlPassword NVARCHAR(256)) или длинных логинов.
--     Эта функция не имеет ограничения по длине.
-- ====================================================================
CREATE OR ALTER FUNCTION dbo.fnQuoteLiteral (@Value NVARCHAR(4000))
RETURNS NVARCHAR(4000)
WITH SCHEMABINDING
AS
BEGIN
    RETURN N'''' + REPLACE(ISNULL(@Value, N''), N'''', N'''''') + N'''';
END
GO

/*
========================================================================
  !! НАСТРОЙКА ОКРУЖЕНИЯ !!

  Переменная @Env задаётся ниже, в блоке заполнения tPermissionMatrix
  (ищи строку: DECLARE @Env NVARCHAR(10) = N'QA';)

    'QA'   → PROD + QA базы, полные права из матрицы
    'PROD' → все кроме DB Admin: db_datareader + VIEW DEFINITION
             (без EXECUTE и без записи на prod-базах)

  Значение влияет только на то, какие строки попадут в tPermissionMatrix
  при первом прогоне. Дальше права живут в таблице и меняются через UPDATE.
========================================================================
*/

-- ====================================================================
-- [1] ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ
-- ====================================================================
IF OBJECT_ID('dbo.tUserProvisioningList', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tUserProvisioningList
    (
        Id          INT           IDENTITY(1,1) PRIMARY KEY,
        LoginName   NVARCHAR(256) NOT NULL,
        LoginType   NVARCHAR(10)  NOT NULL DEFAULT 'SQL'
                        CONSTRAINT chk_upl_LoginType
                        CHECK (LoginType IN ('WINDOWS', 'SQL')),
        SqlPassword NVARCHAR(256) NULL,
        Category    NVARCHAR(50)  NOT NULL
                        CONSTRAINT chk_upl_Category
                        CHECK (Category IN (
                            'DB Admin',
                            'Dev Team',
                            'Data Engineer Team',
                            'QA',
                            'Support Senior',
                            'AI Agent'
                        )),
        IsActive    BIT           NOT NULL DEFAULT 1,
        Notes       NVARCHAR(500) NULL,
        CreatedAt   DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        -- Один логин = одна строка. Дубликаты приведут к двойному провижну.
        CONSTRAINT uq_upl_LoginName UNIQUE (LoginName),
        -- ИИ-агент — всегда SQL-логин. Доменной учётки у него быть не может:
        -- пароль живёт в конфиге агента, а не у человека, и отзывается
        -- сменой пароля этого логина, а не блокировкой в домене.
        CONSTRAINT chk_upl_AiAgentIsSql CHECK (
            Category <> 'AI Agent' OR LoginType = 'SQL'
        )
    );
    PRINT 'OK: Таблица dbo.tUserProvisioningList создана.';
END
ELSE
    PRINT 'SKIP: dbo.tUserProvisioningList уже существует.';
GO

-- Миграция v5 -> v6 для уже развёрнутых установок: категория 'AI Agent'.
-- Ограничение пересоздаётся только если в его определении ещё нет 'AI Agent',
-- поэтому блок безопасно запускать повторно.
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE  name             = 'chk_upl_Category'
      AND  parent_object_id = OBJECT_ID('dbo.tUserProvisioningList')
      AND  definition NOT LIKE '%AI Agent%'
)
BEGIN
    ALTER TABLE dbo.tUserProvisioningList DROP CONSTRAINT chk_upl_Category;
    ALTER TABLE dbo.tUserProvisioningList
        ADD CONSTRAINT chk_upl_Category CHECK (Category IN (
            'DB Admin', 'Dev Team', 'Data Engineer Team',
            'QA', 'Support Senior', 'AI Agent'));
    PRINT 'OK: chk_upl_Category расширен категорией AI Agent.';
END
ELSE
    PRINT 'SKIP: chk_upl_Category уже знает AI Agent.';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE  name             = 'chk_upl_AiAgentIsSql'
      AND  parent_object_id = OBJECT_ID('dbo.tUserProvisioningList')
)
BEGIN
    ALTER TABLE dbo.tUserProvisioningList
        ADD CONSTRAINT chk_upl_AiAgentIsSql CHECK (
            Category <> 'AI Agent' OR LoginType = 'SQL');
    PRINT 'OK: chk_upl_AiAgentIsSql добавлен.';
END
ELSE
    PRINT 'SKIP: chk_upl_AiAgentIsSql уже существует.';
GO

-- ====================================================================
-- [2] ТАБЛИЦА АУДИТ-ЛОГА
-- ====================================================================
IF OBJECT_ID('dbo.tUserProvisioningLog', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tUserProvisioningLog
    (
        Id        INT              IDENTITY(1,1) PRIMARY KEY,
        RunAt     DATETIME2        NOT NULL DEFAULT SYSDATETIME(),
        RunId     UNIQUEIDENTIFIER NOT NULL,
        LoginName NVARCHAR(256)    NOT NULL,
        Scope     NVARCHAR(256)    NOT NULL,
        Action    NVARCHAR(500)    NOT NULL,
        Status    NVARCHAR(20)     NOT NULL,   -- OK | SKIPPED | ERROR | DRY_RUN
        Details   NVARCHAR(MAX)    NULL
    );
    PRINT 'OK: Таблица dbo.tUserProvisioningLog создана.';
END
ELSE
    PRINT 'SKIP: dbo.tUserProvisioningLog уже существует.';
GO

-- ====================================================================
-- [3] МАТРИЦА ПРАВ
--     Редактируй строки в этой таблице — не в процедуре.
--     MERGE: создаёт строки если нет; НЕ перезаписывает уже существующие
--     (WHEN MATCHED закомментирован намеренно, чтобы не затирать ручные правки).
-- ====================================================================
IF OBJECT_ID('dbo.tPermissionMatrix', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tPermissionMatrix
    (
        Id           INT          IDENTITY(1,1) PRIMARY KEY,
        Category     NVARCHAR(50) NOT NULL
                         CONSTRAINT chk_pm_Category
                         CHECK (Category IN (
                             'DB Admin',
                             'Dev Team',
                             'Data Engineer Team',
                             'QA',
                             'Support Senior',
                             'AI Agent'
                         )),
        DbType       NVARCHAR(10) NOT NULL
                         CONSTRAINT chk_pm_DbType
                         CHECK (DbType IN ('PROD', 'DEV')),
        -- Роли через | например: 'db_datareader|db_datawriter'
        -- Пустая строка = никаких ролей (пользователь только создаётся)
        DbRoles             NVARCHAR(500) NOT NULL DEFAULT '',
        GrantExecute        BIT           NOT NULL DEFAULT 0,
        -- Позволяет видеть код процедур, вьюх и функций (sp_helptext, OBJECT_DEFINITION)
        -- Важно для read-only пользователей: без этого объекты «невидимы»
        GrantViewDefinition BIT           NOT NULL DEFAULT 0,
        Notes               NVARCHAR(500) NULL,
        UpdatedAt           DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT uq_pm_CategoryDbType UNIQUE (Category, DbType)
    );
    PRINT 'OK: Таблица dbo.tPermissionMatrix создана.';
END
ELSE
    PRINT 'SKIP: dbo.tPermissionMatrix уже существует — обновляем значения (MERGE).';
GO

-- Миграция v5 -> v6: категория 'AI Agent' в CHECK матрицы.
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE  name             = 'chk_pm_Category'
      AND  parent_object_id = OBJECT_ID('dbo.tPermissionMatrix')
      AND  definition NOT LIKE '%AI Agent%'
)
BEGIN
    ALTER TABLE dbo.tPermissionMatrix DROP CONSTRAINT chk_pm_Category;
    ALTER TABLE dbo.tPermissionMatrix
        ADD CONSTRAINT chk_pm_Category CHECK (Category IN (
            'DB Admin', 'Dev Team', 'Data Engineer Team',
            'QA', 'Support Senior', 'AI Agent'));
    PRINT 'OK: chk_pm_Category расширен категорией AI Agent.';
END
ELSE
    PRINT 'SKIP: chk_pm_Category уже знает AI Agent.';
GO

-- Миграция для уже существующих установок (v1 -> v2):
-- Добавляем колонку GrantViewDefinition если её ещё нет.
-- Безопасно запускать повторно.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE  object_id = OBJECT_ID('dbo.tPermissionMatrix')
      AND  name      = 'GrantViewDefinition'
)
BEGIN
    ALTER TABLE dbo.tPermissionMatrix
    ADD GrantViewDefinition BIT NOT NULL DEFAULT 0;
    PRINT 'OK: Колонка GrantViewDefinition добавлена в dbo.tPermissionMatrix.';
END
ELSE
    PRINT 'SKIP: GrantViewDefinition уже существует.';
GO

-- ════════════════════════════════════════════════════════════════════
-- !! ЗДЕСЬ МЕНЯЙ ОКРУЖЕНИЕ ПЕРЕД ПЕРВЫМ ЗАПУСКОМ: 'QA' или 'PROD' !!
-- ════════════════════════════════════════════════════════════════════
DECLARE @Env NVARCHAR(10) = N'QA';   -- ← 'QA' | 'PROD'
-- ════════════════════════════════════════════════════════════════════
--
-- MERGE идемпотентен: добавляет отсутствующие строки, НЕ трогает существующие.
--
-- ВНИМАНИЕ: при смене @Env на уже настроенном сервере существующие строки
-- НЕ обновятся (WHEN MATCHED закомментирован). Чтобы применить новое
-- окружение — раскомментируй WHEN MATCHED ниже, либо очисти таблицу:
--   DELETE FROM dbo.tPermissionMatrix;

MERGE INTO dbo.tPermissionMatrix AS T
USING (
    SELECT Category, DbType, DbRoles, GrantExecute, GrantViewDefinition, Notes
    FROM (VALUES
    -- Env     Категория              DbType  DbRoles                        EXEC VIEW  Комментарий
    -- ═══════════════════════════════════════════════════════════════════════════════════
    -- QA-сервер: полные права, PROD и DEV базы обе присутствуют
    -- ═══════════════════════════════════════════════════════════════════════════════════
    ('QA',   'DB Admin',           'PROD', 'db_owner',                     0,   0,  'Полный владелец'),
    ('QA',   'DB Admin',           'DEV',  'db_owner',                     0,   0,  'Полный владелец'),
    ('QA',   'Dev Team',           'PROD', 'db_datareader|db_datawriter',  1,   1,  'Чтение + запись + EXECUTE + VIEW'),
    ('QA',   'Dev Team',           'DEV',  'db_owner',                     0,   0,  'Полный владелец dev-баз'),
    ('QA',   'Data Engineer Team', 'PROD', 'db_datareader|db_datawriter',  1,   1,  'Чтение + запись + EXECUTE + VIEW'),
    ('QA',   'Data Engineer Team', 'DEV',  'db_datareader|db_datawriter',  1,   1,  'Чтение + запись + EXECUTE + VIEW'),
    ('QA',   'QA',                 'PROD', 'db_datareader',                0,   1,  'Только чтение + VIEW'),
    ('QA',   'QA',                 'DEV',  'db_datareader|db_datawriter',  0,   1,  'Чтение + запись + VIEW'),
    ('QA',   'Support Senior',     'PROD', 'db_datareader',                0,   1,  'Только чтение + VIEW'),
    ('QA',   'Support Senior',     'DEV',  'db_datareader',                0,   1,  'Только чтение + VIEW'),
    -- Сервисные учётки ИИ-агентов: чтение всех таблиц, ничего кроме чтения
    ('QA',   'AI Agent',           'PROD', 'db_datareader',                0,   1,  'Только чтение + VIEW'),
    ('QA',   'AI Agent',           'DEV',  'db_datareader',                0,   1,  'Только чтение + VIEW'),

    -- ═══════════════════════════════════════════════════════════════════════════════════
    -- PROD-сервер: все кроме DB Admin → только чтение + VIEW на prod-базах.
    -- DEV-строки оставлены на случай если dev-база всё же окажется на сервере.
    -- ═══════════════════════════════════════════════════════════════════════════════════
    ('PROD', 'DB Admin',           'PROD', 'db_owner',                     0,   0,  'Полный владелец'),
    ('PROD', 'DB Admin',           'DEV',  'db_owner',                     0,   0,  'Полный владелец'),
    ('PROD', 'Dev Team',           'PROD', 'db_datareader',                0,   1,  'PROD: только чтение + VIEW'),
    ('PROD', 'Dev Team',           'DEV',  'db_owner',                     0,   0,  'Полный владелец dev-баз'),
    ('PROD', 'Data Engineer Team', 'PROD', 'db_datareader',                0,   1,  'PROD: только чтение + VIEW'),
    ('PROD', 'Data Engineer Team', 'DEV',  'db_datareader|db_datawriter',  1,   1,  'Чтение + запись + EXECUTE + VIEW'),
    ('PROD', 'QA',                 'PROD', 'db_datareader',                0,   1,  'PROD: только чтение + VIEW'),
    ('PROD', 'QA',                 'DEV',  'db_datareader|db_datawriter',  0,   1,  'Чтение + запись + VIEW'),
    ('PROD', 'Support Senior',     'PROD', 'db_datareader',                0,   1,  'PROD: только чтение + VIEW'),
    ('PROD', 'Support Senior',     'DEV',  'db_datareader',                0,   1,  'Только чтение + VIEW'),
    -- Сервисные учётки ИИ-агентов: чтение всех таблиц, ничего кроме чтения
    ('PROD', 'AI Agent',           'PROD', 'db_datareader',                0,   1,  'PROD: только чтение + VIEW'),
    ('PROD', 'AI Agent',           'DEV',  'db_datareader',                0,   1,  'Только чтение + VIEW')
    ) AS v (Env, Category, DbType, DbRoles, GrantExecute, GrantViewDefinition, Notes)
    WHERE v.Env = @Env
) AS S (Category, DbType, DbRoles, GrantExecute, GrantViewDefinition, Notes)
ON T.Category = S.Category AND T.DbType = S.DbType
-- Раскомментируй WHEN MATCHED чтобы принудительно сбросить к умолчаниям
-- (нужно при смене ServerEnvironment на уже настроенном сервере):
-- WHEN MATCHED THEN
--     UPDATE SET DbRoles = S.DbRoles, GrantExecute = S.GrantExecute,
--                GrantViewDefinition = S.GrantViewDefinition,
--                Notes = S.Notes, UpdatedAt = SYSDATETIME()
WHEN NOT MATCHED THEN
    INSERT (Category, DbType, DbRoles, GrantExecute, GrantViewDefinition, Notes)
    VALUES (S.Category, S.DbType, S.DbRoles, S.GrantExecute, S.GrantViewDefinition, S.Notes);

PRINT 'OK: dbo.tPermissionMatrix синхронизирована для окружения ' + @Env + '.';
GO

-- ====================================================================
-- [4] СПИСОК БАЗ ДАННЫХ
--     Добавить новую базу = один INSERT. Процедура не меняется.
--     IsDev = 0 → правила DbType='PROD' из tPermissionMatrix
--     IsDev = 1 → правила DbType='DEV'
--     IsActive = 0 → база временно исключена из провижна
--     Базы которых нет на сервере — молча пропускаются (EXISTS sys.databases).
-- ====================================================================
IF OBJECT_ID('dbo.tDatabases', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tDatabases
    (
        Id        INT           IDENTITY(1,1) PRIMARY KEY,
        DbName    NVARCHAR(128) NOT NULL CONSTRAINT uq_db_DbName UNIQUE,
        IsDev     BIT           NOT NULL DEFAULT 0,
        IsActive  BIT           NOT NULL DEFAULT 1,
        Notes     NVARCHAR(500) NULL,
        CreatedAt DATETIME2     NOT NULL DEFAULT SYSDATETIME()
    );
    PRINT 'OK: Таблица dbo.tDatabases создана.';
END
ELSE
    PRINT 'SKIP: dbo.tDatabases уже существует — синхронизируем (MERGE).';
GO

-- Начальное заполнение / синхронизация списка баз.
-- Как и в tPermissionMatrix: добавляет новые, НЕ трогает существующие.
MERGE INTO dbo.tDatabases AS T
USING (
    VALUES
    -- DbName                          IsDev
    ('Address',                         0),
    ('Address_Id360',                   0),
    ('Address_OneKey',                  0),
    ('Bi',                              0), ('Bi_dev',                       1),
    ('Bots',                            0), ('Bots_dev',                     1),
    ('Consent',                         0), ('Consent_dev',                  1),
    ('ContentFactory',                  0), ('ContentFactory_dev',           1),
    ('crmAdmin',                        0), ('crmAdmin_dev',                 1),
    ('crmExtra',                        0),
    ('Export',                          0), ('Export_dev',                   1),
                                            ('ExportData_dev',               1),
    ('Id360',                           0), ('Id360_dev',                    1),
                                            ('LogDb',                        1),
    ('crmPbe',                          0), ('crmPbe_dev',                   1),
    ('CustomEvents',                    0), ('CustomEvents_dev',             1),
    ('CustomNotifications',             0), ('CustomNotifications_dev',      1),
    ('IntegrationExternalSite',         0), ('IntegrationExternalSite_dev',  1),
    ('Mindbox',                         0), ('Mindbox_dev',                  1),
    ('Omni',                            0), ('Omni_dev',                     1),
    ('OneKey',                          0), ('OneKey_dev',                   1),
    ('PersonalAccount',                 0), ('PersonalAccount_dev',          1),
    ('PharmacyChainContract',           0), ('PharmacyChainContract_dev',    1),
    ('PharmacyChainManagement',         0), ('PharmacyChainManagement_dev',  1),
    ('PharmacyStock',                   0), ('PharmacyStock_dev',            1),
    ('Planning',                        0), ('Planning_dev',                 1),
    ('Survey',                          0), ('Survey_dev',                   1)
) AS S (DbName, IsDev)
ON T.DbName = S.DbName
WHEN NOT MATCHED THEN
    INSERT (DbName, IsDev) VALUES (S.DbName, S.IsDev);
PRINT 'OK: dbo.tDatabases синхронизирована.';
GO

-- ====================================================================
-- [5] ТАБЛИЦА ИСКЛЮЧЕНИЙ
--     Переопределяет права из tPermissionMatrix для конкретной базы.
--
--     Два типа:
--       Групповое  — Category IS NOT NULL, LoginName IS NULL
--                    Применяется ко всем пользователям категории
--       Личное     — LoginName IS NOT NULL, Category IS NULL
--                    Применяется к конкретному пользователю
--
--     Приоритет: Личное исключение > Групповое исключение > Матрица
-- ====================================================================
IF OBJECT_ID('dbo.tPermissionOverride', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tPermissionOverride
    (
        Id                  INT           IDENTITY(1,1) PRIMARY KEY,
        -- Ровно одно из двух: Category (групповое) или LoginName (личное)
        Category            NVARCHAR(50)  NULL
                                CONSTRAINT chk_po_Category
                                CHECK (Category IN (
                                    'DB Admin', 'Dev Team', 'Data Engineer Team',
                                    'QA', 'Support Senior', 'AI Agent'
                                )),
        LoginName           NVARCHAR(256) NULL,
        DbName              NVARCHAR(128) NOT NULL,
        DbRoles             NVARCHAR(500) NOT NULL DEFAULT '',
        GrantExecute        BIT           NOT NULL DEFAULT 0,
        GrantViewDefinition BIT           NOT NULL DEFAULT 0,
        IsActive            BIT           NOT NULL DEFAULT 1,
        Notes               NVARCHAR(500) NULL,
        CreatedAt           DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        UpdatedAt           DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        -- Строго одно из двух: либо Category, либо LoginName
        CONSTRAINT chk_po_scope CHECK (
            (Category IS NOT NULL AND LoginName IS NULL)
            OR
            (Category IS NULL     AND LoginName IS NOT NULL)
        )
    );
    PRINT 'OK: Таблица dbo.tPermissionOverride создана.';
END
ELSE
    PRINT 'SKIP: dbo.tPermissionOverride уже существует.';
GO

-- Миграция v5 -> v6: категория 'AI Agent' в CHECK исключений.
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE  name             = 'chk_po_Category'
      AND  parent_object_id = OBJECT_ID('dbo.tPermissionOverride')
      AND  definition NOT LIKE '%AI Agent%'
)
BEGIN
    ALTER TABLE dbo.tPermissionOverride DROP CONSTRAINT chk_po_Category;
    ALTER TABLE dbo.tPermissionOverride
        ADD CONSTRAINT chk_po_Category CHECK (Category IN (
            'DB Admin', 'Dev Team', 'Data Engineer Team',
            'QA', 'Support Senior', 'AI Agent'));
    PRINT 'OK: chk_po_Category расширен категорией AI Agent.';
END
ELSE
    PRINT 'SKIP: chk_po_Category уже знает AI Agent.';
GO

-- Уникальность: одно исключение на группу+база, одно на юзера+база.
-- Обычный UNIQUE не работает с NULL — нужны фильтрованные индексы.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE  name      = 'uix_po_category_db'
                AND  object_id = OBJECT_ID('dbo.tPermissionOverride'))
    CREATE UNIQUE INDEX uix_po_category_db
        ON dbo.tPermissionOverride (Category, DbName)
        WHERE Category IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE  name      = 'uix_po_login_db'
                AND  object_id = OBJECT_ID('dbo.tPermissionOverride'))
    CREATE UNIQUE INDEX uix_po_login_db
        ON dbo.tPermissionOverride (LoginName, DbName)
        WHERE LoginName IS NOT NULL;
GO

-- ====================================================================
-- [6] ОСНОВНАЯ ХРАНИМАЯ ПРОЦЕДУРА
-- ====================================================================
CREATE OR ALTER PROCEDURE dbo.spProvisionUsers
    @DryRun BIT = 0
/*
  @DryRun = 1  →  только выводит что БЫЛО БЫ сделано, без изменений
  @DryRun = 0  →  применяет все изменения (по умолчанию)

  Обрабатывает ВСЕХ пользователей из tUserProvisioningList, не только
  активных: для IsActive=0 логин отключается, членство в [sysadmin] и все
  управляемые роли/EXECUTE/VIEW DEFINITION во всех базах — отзываются.
  Это реальный деprovision, а не просто исключение из будущих прогонов.

  Окружение (QA / PROD) задаётся один раз при первом прогоне init-скрипта
  и уже отражено в dbo.tPermissionMatrix — процедура просто читает матрицу.

  Запуск:
    EXEC dbo.spProvisionUsers @DryRun = 1;
    EXEC dbo.spProvisionUsers @DryRun = 0;

  Требования:
    · dbo.tDatabases должна быть заполнена
    · dbo.tUserProvisioningList должна быть заполнена
    · SQL Server 2016+ (используется STRING_SPLIT)
*/
AS
BEGIN
    SET NOCOUNT ON;

    -- ── Переменные ────────────────────────────────────────────────────
    DECLARE
        @LoginName   NVARCHAR(256),
        @LoginType   NVARCHAR(10),
        @SqlPassword NVARCHAR(256),
        @Category    NVARCHAR(50),
        @IsUserActive BIT,             -- IsActive из tUserProvisioningList: 0 = деактивирован, доступ отзывается
        @ShouldBeSysadmin BIT,
        @DbName      NVARCHAR(128),
        @IsDev       BIT,
        @DbType      NVARCHAR(10),
        @DbRoles      NVARCHAR(500),
        @GrantExec    BIT,
        @GrantViewDef BIT,
        @SQL          NVARCHAR(MAX),
        @RoleSQL      NVARCHAR(MAX),
        @CleanupSQL   NVARCHAR(MAX),   -- SQL для отзыва лишних прав (desired state)
        @PaddedRoles  NVARCHAR(520),   -- @DbRoles обёрнутый в | для точного CHARINDEX
        @HasOwner     BIT,             -- должен ли быть db_owner в этой базе
        @HasReader    BIT,             -- должен ли быть db_datareader
        @HasWriter    BIT,             -- должен ли быть db_datawriter
        @StateSQL     NVARCHAR(MAX),   -- запрос фактического состояния в целевой базе
        @UserExists   BIT,             -- есть ли уже database-принципал для логина
        @IsOrphaned   BIT,             -- orphaned user (SID не совпадает с логином)
        @ActualOwner  BIT,             -- фактическое членство в db_owner
        @ActualReader BIT,             -- фактическое членство в db_datareader
        @ActualWriter BIT,             -- фактическое членство в db_datawriter
        @ActualExec   BIT,             -- фактически выдан ли EXECUTE
        @ActualViewDef BIT,            -- фактически выдан ли VIEW DEFINITION
        @NeedsChange  BIT,             -- отличается ли факт от желаемого состояния
        @DesiredEmpty BIT,             -- по матрице в этой базе не положено вообще ничего
        @LogStatus    NVARCHAR(20),
        @Prefix       NVARCHAR(10),
        @RunId        UNIQUEIDENTIFIER;

    SET @RunId  = NEWID();
    SET @Prefix = CASE WHEN @DryRun = 1 THEN N'[DRY] ' ELSE N'' END;

    PRINT N'';
    PRINT N'=============================================================';
    PRINT @Prefix + N'spProvisionUsers  |  RunId: ' + CAST(@RunId AS NVARCHAR(40));
    PRINT N'=============================================================';

    -- ── Перебираем ВСЕХ пользователей (не только активных) ────────────
    -- Деактивированных (IsActive=0) тоже нужно пройти: их логин отключается,
    -- членство в sysadmin и роли во всех базах отзываются (реальный деprovision,
    -- а не просто "пропустить при следующих прогонах").
    DECLARE cur_Users CURSOR LOCAL FAST_FORWARD FOR
        SELECT LoginName, LoginType, SqlPassword, Category, IsActive
        FROM   dbo.tUserProvisioningList
        ORDER  BY LoginName;

    OPEN cur_Users;
    FETCH NEXT FROM cur_Users INTO @LoginName, @LoginType, @SqlPassword, @Category, @IsUserActive;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        PRINT N'';
        PRINT N'-------------------------------------------------------------';
        PRINT @Prefix + N'Пользователь: ' + @LoginName + N'  [' + @Category + N']'
            + CASE WHEN @IsUserActive = 0 THEN N'  ** ДЕАКТИВИРОВАН — доступ отзывается **' ELSE N'' END;
        PRINT N'-------------------------------------------------------------';

        -- =============================================================
        -- A. СЕРВЕРНЫЙ ЛОГИН: активным — создаём/включаем,
        --    деактивированным (IsActive=0) — отключаем.
        -- =============================================================
        BEGIN TRY
            IF @IsUserActive = 1
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM sys.server_principals
                    WHERE  name = @LoginName AND type IN ('U', 'S', 'G')
                )
                BEGIN
                    IF @LoginType = 'WINDOWS'
                        SET @SQL = N'CREATE LOGIN ' + QUOTENAME(@LoginName)
                                 + N' FROM WINDOWS WITH DEFAULT_DATABASE = [master];';
                    ELSE
                    BEGIN
                        IF ISNULL(@SqlPassword, N'') = N''
                            THROW 50001, 'SQL-логин требует непустой пароль в поле SqlPassword.', 1;
                        SET @SQL = N'CREATE LOGIN ' + QUOTENAME(@LoginName)
                                 + N' WITH PASSWORD     = N' + dbo.fnQuoteLiteral(@SqlPassword)
                                 + N'   , DEFAULT_DATABASE = [master]'
                                 + N'   , CHECK_EXPIRATION = OFF'
                                 + N'   , CHECK_POLICY     = ON;';
                    END

                    IF @DryRun = 0 EXEC sp_executesql @SQL;

                    SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                    -- Пароль SQL-логина НЕ пишем в лог — только тип и имя.
                    -- @SQL содержит WITH PASSWORD = N'...' в открытом виде.
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
                    VALUES (@RunId, @LoginName, N'SERVER', N'CREATE LOGIN', @LogStatus,
                            N'Type=' + @LoginType
                            + CASE WHEN @LoginType = 'WINDOWS'
                                   THEN N' | ' + @SQL
                                   ELSE N' | CREATE LOGIN ' + QUOTENAME(@LoginName)
                                      + N' WITH PASSWORD=***REDACTED***;'
                              END);
                    PRINT @Prefix + N'  [+] Логин создан (' + @LoginType + N').';

                    -- Пароль в открытом виде больше не нужен — он живёт в таблице
                    -- только до момента создания логина, дальше это лишний секрет.
                    IF @DryRun = 0 AND @LoginType = 'SQL'
                        UPDATE dbo.tUserProvisioningList
                        SET    SqlPassword = NULL
                        WHERE  LoginName = @LoginName;
                END
                ELSE
                BEGIN
                    -- Fix #8: ALTER LOGIN ENABLE только если логин реально выключен.
                    -- Вызов вхолостую на каждый прогон засоряет лог и создаёт DDL без нужды.
                    IF EXISTS (
                        SELECT 1 FROM sys.server_principals
                        WHERE  name = @LoginName AND is_disabled = 1
                    )
                    BEGIN
                        SET @SQL = N'ALTER LOGIN ' + QUOTENAME(@LoginName) + N' ENABLE;';
                        IF @DryRun = 0 EXEC sp_executesql @SQL;
                        INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                        VALUES (@RunId, @LoginName, N'SERVER', N'LOGIN WAS DISABLED -> ENABLED', N'OK');
                        PRINT @Prefix + N'  [!] Логин был отключён — включён.';
                    END
                    ELSE
                    BEGIN
                        INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                        VALUES (@RunId, @LoginName, N'SERVER', N'LOGIN EXISTS, ENABLED', N'SKIPPED');
                        PRINT @Prefix + N'  [=] Логин уже существует и включён.';
                    END
                END
            END
            ELSE  -- @IsUserActive = 0: доступ отзывается, логин должен быть отключён
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM sys.server_principals
                    WHERE  name = @LoginName AND type IN ('U', 'S', 'G') AND is_disabled = 0
                )
                BEGIN
                    SET @SQL = N'ALTER LOGIN ' + QUOTENAME(@LoginName) + N' DISABLE;';
                    IF @DryRun = 0 EXEC sp_executesql @SQL;
                    SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                    VALUES (@RunId, @LoginName, N'SERVER', N'DEACTIVATED -> LOGIN DISABLED', @LogStatus);
                    PRINT @Prefix + N'  [-] Пользователь деактивирован — логин отключён.';
                END
                ELSE
                BEGIN
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                    VALUES (@RunId, @LoginName, N'SERVER', N'DEACTIVATED, LOGIN ALREADY DISABLED/ABSENT', N'SKIPPED');
                    PRINT @Prefix + N'  [=] Пользователь деактивирован, логин уже отключён/отсутствует.';
                END
            END
        END TRY
        BEGIN CATCH
            INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
            VALUES (@RunId, @LoginName, N'SERVER', N'CREATE/ENABLE/DISABLE LOGIN', N'ERROR', ERROR_MESSAGE());
            PRINT N'  [!] ОШИБКА (логин): ' + ERROR_MESSAGE();
        END CATCH;

        -- =============================================================
        -- B. СЕРВЕРНАЯ РОЛЬ [sysadmin] — desired state.
        --    Должен быть членом только активный DB Admin. Для всех
        --    остальных (в т.ч. DB Admin, которого деактивировали или
        --    перевели в другую категорию) членство снимается —
        --    иначе право sysadmin переживало бы смену роли/увольнение.
        -- =============================================================
        SET @ShouldBeSysadmin = CASE WHEN @Category = N'DB Admin' AND @IsUserActive = 1 THEN 1 ELSE 0 END;

        BEGIN TRY
            IF @ShouldBeSysadmin = 1
            BEGIN
                IF ISNULL(IS_SRVROLEMEMBER('sysadmin', @LoginName), 0) = 0
                BEGIN
                    SET @SQL = N'ALTER SERVER ROLE [sysadmin] ADD MEMBER '
                             + QUOTENAME(@LoginName) + N';';
                    IF @DryRun = 0 EXEC sp_executesql @SQL;
                    SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                    VALUES (@RunId, @LoginName, N'SERVER', N'ADD TO [sysadmin]', @LogStatus);
                    PRINT @Prefix + N'  [+] Добавлен в серверную роль [sysadmin].';
                END
            END
            ELSE
            BEGIN
                IF ISNULL(IS_SRVROLEMEMBER('sysadmin', @LoginName), 0) = 1
                BEGIN
                    SET @SQL = N'ALTER SERVER ROLE [sysadmin] DROP MEMBER '
                             + QUOTENAME(@LoginName) + N';';
                    IF @DryRun = 0 EXEC sp_executesql @SQL;
                    SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                    VALUES (@RunId, @LoginName, N'SERVER', N'REMOVE FROM [sysadmin]', @LogStatus);
                    PRINT @Prefix + N'  [-] Снят из серверной роли [sysadmin] (категория/статус изменились).';
                END
            END
        END TRY
        BEGIN CATCH
            INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
            VALUES (@RunId, @LoginName, N'SERVER', N'SYNC [sysadmin]', N'ERROR', ERROR_MESSAGE());
            PRINT N'  [!] ОШИБКА (sysadmin): ' + ERROR_MESSAGE();
        END CATCH;

        -- =============================================================
        -- C. ПРОВИЖН В КАЖДОЙ БАЗЕ
        -- =============================================================
        DECLARE cur_DB CURSOR LOCAL FAST_FORWARD FOR
            SELECT DbName, IsDev
            FROM   dbo.tDatabases
            WHERE  IsActive = 1
            ORDER  BY DbName;

        OPEN cur_DB;
        FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;

        WHILE @@FETCH_STATUS = 0
        BEGIN
            -- ── База должна существовать и быть онлайн ────────────────
            -- На PROD-серверах _dev баз нет физически → скип происходит
            -- здесь автоматически. tProvisioningServerConfig не нужна.
            IF NOT EXISTS (
                SELECT 1 FROM sys.databases
                WHERE  name = @DbName AND state_desc = N'ONLINE'
            )
            BEGIN
                PRINT N'  [-] Не найдена / offline: ' + @DbName;
                FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
                CONTINUE;
            END

            -- ── Пара «пользователь + база» закрыта исключением? ───────
            --    Тогда матрица к ней не применяется: авторитет —
            --    tPermissionOverride, и spProvisionOverrides отработает эту
            --    пару сразу после основного прохода, под тем же RunId.
            --    Без пропуска каждый прогон моргал бы: сначала матрица
            --    снимает права, которых по ней быть не должно, следом
            --    исключение выдаёт их заново — лишние строки в журнале и
            --    окно, в котором доступа нет.
            --    Для деактивированных пропуска нет: spProvisionOverrides их
            --    не видит (JOIN ... IsActive = 1), отзыв делает этот проход.
            IF @IsUserActive = 1
               AND EXISTS (
                   SELECT 1
                   FROM   dbo.tPermissionOverride o
                   WHERE  o.IsActive = 1
                     AND  o.DbName   = @DbName
                     AND (o.LoginName = @LoginName OR o.Category = @Category)
               )
            BEGIN
                PRINT @Prefix + N'  [~] ' + @DbName
                    + N' -> ведётся исключением (tPermissionOverride), матрица пропущена.';
                FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
                CONTINUE;
            END

            BEGIN TRY
                -- ── Определяем тип базы для матрицы прав ────────────
                SET @DbType = CASE WHEN @IsDev = 1 THEN N'DEV' ELSE N'PROD' END;

                -- ── Читаем права из tPermissionMatrix (только для активных) ──
                -- ВАЖНО: сброс перед SELECT обязателен.
                -- Если строки нет — SQL Server НЕ обнуляет переменные сам,
                -- они остаются со значениями предыдущей итерации (stale data).
                SELECT @DbRoles = NULL, @GrantExec = 0, @GrantViewDef = 0;

                IF @IsUserActive = 1
                BEGIN
                    SELECT @DbRoles      = DbRoles,
                           @GrantExec    = GrantExecute,
                           @GrantViewDef = GrantViewDefinition
                    FROM   dbo.tPermissionMatrix
                    WHERE  Category = @Category AND DbType = @DbType;

                    IF @DbRoles IS NULL
                    BEGIN
                        INSERT INTO dbo.tUserProvisioningLog
                            (RunId, LoginName, Scope, Action, Status, Details)
                        VALUES (@RunId, @LoginName, @DbName, N'SKIP - NO RULE', N'SKIPPED',
                                N'Нет правила в tPermissionMatrix для ' + @Category + N' / ' + @DbType);
                        PRINT N'  [?] Нет правила в tPermissionMatrix: ' + @Category + N' / ' + @DbType;
                        FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
                        CONTINUE;
                    END
                END
                ELSE
                    -- Деактивирован: желаемое состояние = никаких управляемых прав.
                    -- CleanupSQL ниже построится так, что снимет всё лишнее.
                    SET @DbRoles = N'';

                -- ══════════════════════════════════════════════════════
                -- DESIRED STATE: сначала убираем всё лишнее,
                -- потом выдаём только то что в матрице.
                --
                -- Управляемые роли: db_owner, db_datareader, db_datawriter.
                -- Управляемые права: EXECUTE, VIEW DEFINITION.
                -- Всё остальное (кастомные роли и т.д.) — не трогаем.
                -- ══════════════════════════════════════════════════════

                -- Определяем какие управляемые роли ДОЛЖНЫ быть.
                -- Оборачиваем в | с обеих сторон для точного CHARINDEX:
                -- 'db_datareader|db_datawriter' → '|db_datareader|db_datawriter|'
                -- Это исключает ложные совпадения подстрок.
                SET @PaddedRoles = N'|' + ISNULL(@DbRoles, N'') + N'|';
                SET @HasOwner  = CASE WHEN CHARINDEX(N'|db_owner|',      @PaddedRoles) > 0 THEN 1 ELSE 0 END;
                SET @HasReader = CASE WHEN CHARINDEX(N'|db_datareader|',  @PaddedRoles) > 0 THEN 1 ELSE 0 END;
                SET @HasWriter = CASE WHEN CHARINDEX(N'|db_datawriter|',  @PaddedRoles) > 0 THEN 1 ELSE 0 END;

                -- По матрице в этой базе не положено ничего: ни роли, ни
                -- EXECUTE, ни VIEW DEFINITION. Тогда и заводить принципала
                -- незачем — пустой пользователь получил бы CONNECT и право
                -- подключиться к базе, из которой всё равно ничего не видно.
                -- Так выглядит любая строка матрицы с пустым DbRoles, а также
                -- сужение агента до одной базы через исключения (см. конец файла).
                -- Уже существующего пользователя это не трогает: если права
                -- у него есть, а по матрице их быть не должно, ниже сработает
                -- обычная сверка и @CleanupSQL их отзовёт.
                SET @DesiredEmpty = CASE WHEN @HasOwner = 0 AND @HasReader = 0
                                          AND @HasWriter = 0 AND @GrantExec = 0
                                          AND @GrantViewDef = 0
                                     THEN 1 ELSE 0 END;

                -- ══════════════════════════════════════════════════════
                -- Сверяем желаемое состояние с фактическим ДО того как
                -- что-либо строить/выполнять. Если всё уже совпадает —
                -- ни исполнения, ни записи в лог не будет: иначе на
                -- каждом прогоне по каждому юзеру x базе плодились бы
                -- строки "PROVISION / OK", даже когда права давно выданы
                -- и трогать нечего.
                -- ══════════════════════════════════════════════════════
                SET @UserExists = 0; SET @IsOrphaned = 0;
                SET @ActualOwner = 0; SET @ActualReader = 0; SET @ActualWriter = 0;
                SET @ActualExec = 0; SET @ActualViewDef = 0;

                SET @StateSQL =
                    N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                  + N'SELECT' + NCHAR(10)
                  + N'    @UserExists = CASE WHEN EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                  + N'                       WHERE name = N' + dbo.fnQuoteLiteral(@LoginName) + N' AND type IN (''U'',''S'',''G'')) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @IsOrphaned = CASE WHEN EXISTS (SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                  + N'                       WHERE dp.name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                  + N'                         AND dp.sid <> SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N')' + NCHAR(10)
                  + N'                         AND SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N') IS NOT NULL) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @ActualOwner  = ISNULL(IS_ROLEMEMBER(''db_owner'',      N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualReader = ISNULL(IS_ROLEMEMBER(''db_datareader'', N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualWriter = ISNULL(IS_ROLEMEMBER(''db_datawriter'', N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualExec = CASE WHEN EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                  + N'                       WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''EXECUTE''' + NCHAR(10)
                  + N'                         AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N')) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @ActualViewDef = CASE WHEN EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                  + N'                       WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                  + N'                         AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N')) THEN 1 ELSE 0 END;';

                EXEC sp_executesql @StateSQL,
                    N'@UserExists BIT OUTPUT, @IsOrphaned BIT OUTPUT, @ActualOwner BIT OUTPUT, @ActualReader BIT OUTPUT, @ActualWriter BIT OUTPUT, @ActualExec BIT OUTPUT, @ActualViewDef BIT OUTPUT',
                    @UserExists OUTPUT, @IsOrphaned OUTPUT, @ActualOwner OUTPUT, @ActualReader OUTPUT, @ActualWriter OUTPUT, @ActualExec OUTPUT, @ActualViewDef OUTPUT;

                SET @NeedsChange = CASE WHEN
                       (@IsUserActive = 1 AND @UserExists = 0 AND @DesiredEmpty = 0)
                    OR (@UserExists = 1 AND @IsOrphaned = 1)
                    OR (@ActualOwner   <> @HasOwner)
                    OR (@ActualReader  <> @HasReader)
                    OR (@ActualWriter  <> @HasWriter)
                    OR (@ActualExec    <> @GrantExec)
                    OR (@ActualViewDef <> @GrantViewDef)
                    THEN 1 ELSE 0 END;

                IF @NeedsChange = 0
                BEGIN
                    IF @IsUserActive = 1 AND @DesiredEmpty = 1 AND @UserExists = 0
                        PRINT @Prefix + N'  [ ] ' + @DbName + N' (' + @DbType + N') -> по матрице ничего не положено, пользователь не заводится.';
                    ELSE
                        PRINT @Prefix + N'  [=] ' + @DbName + N' (' + @DbType + N') -> уже соответствует желаемому состоянию, пропуск.';
                    FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
                    CONTINUE;
                END

                -- Строим @CleanupSQL: по каждому managed-объекту —
                -- если НЕ должен быть, но вдруг есть → отзываем.
                -- IS_ROLEMEMBER и EXISTS-check гарантируют идемпотентность:
                -- DROP/REVOKE не вызывается если права и так нет.
                SET @CleanupSQL =
                    N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                  + N'IF EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                  + N'           WHERE name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                  + N'             AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)

                  -- db_owner
                  + CASE WHEN @HasOwner = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_owner'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_owner] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- db_datareader
                  + CASE WHEN @HasReader = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datareader'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datareader] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- db_datawriter
                  + CASE WHEN @HasWriter = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datawriter'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datawriter] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- EXECUTE
                  + CASE WHEN @GrantExec = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'')' + NCHAR(10)
                      + N'                 AND permission_name = ''EXECUTE''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N'))' + NCHAR(10)
                      + N'        REVOKE EXECUTE FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- VIEW DEFINITION
                  + CASE WHEN @GrantViewDef = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'')' + NCHAR(10)
                      + N'                 AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N'))' + NCHAR(10)
                      + N'        REVOKE VIEW DEFINITION FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  + N'END';

                -- ── Строим SQL назначения ролей из DbRoles (через |) ─
                --    STRING_SPLIT требует SQL Server 2016+
                --    Для деактивированных (@IsUserActive=0) не строим и не
                --    выполняем — им ничего выдавать не нужно, только чистить
                --    существующее (см. @CleanupSQL, он выполняется ниже всегда).
                IF @IsUserActive = 1
                BEGIN
                    SET @RoleSQL = N'';

                    -- LTRIM(RTRIM()) вместо TRIM(): TRIM появился в SQL Server 2017,
                    -- STRING_SPLIT — в 2016. Используем совместимый вариант.
                    SELECT @RoleSQL += N'ALTER ROLE '
                        + QUOTENAME(LTRIM(RTRIM(value)))
                        + N' ADD MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    FROM   STRING_SPLIT(@DbRoles, N'|')
                    WHERE  LTRIM(RTRIM(value)) <> N'';

                    IF @GrantExec = 1
                        SET @RoleSQL += N'GRANT EXECUTE TO ' + QUOTENAME(@LoginName) + N';' + NCHAR(10);

                    -- GRANT VIEW DEFINITION:
                    --   Без этого read-only пользователь не видит код процедур, вьюх, функций.
                    --   sp_helptext и OBJECT_DEFINITION() возвращают NULL / ошибку.
                    --   Не даёт никаких прав на изменение или выполнение объектов.
                    IF @GrantViewDef = 1
                        SET @RoleSQL += N'GRANT VIEW DEFINITION TO ' + QUOTENAME(@LoginName) + N';';

                    -- ── Итоговый блок: переключение, создание юзера, права ─
                    SET @SQL =
                        N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)

                        -- Создаём пользователя если нет
                      + N'IF NOT EXISTS (' + NCHAR(10)
                      + N'    SELECT 1 FROM sys.database_principals' + NCHAR(10)
                      + N'    WHERE  name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                      + N'      AND  type IN (''U'', ''S'', ''G'')' + NCHAR(10)
                      + N')' + NCHAR(10)
                      + N'BEGIN' + NCHAR(10)
                      + N'    CREATE USER ' + QUOTENAME(@LoginName)
                      + N' FOR LOGIN ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                      + N'END' + NCHAR(10)

                      -- Фиксируем orphaned user (SID рассинхронизирован)
                      + N'ELSE IF EXISTS (' + NCHAR(10)
                      + N'    SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                      + N'    WHERE  dp.name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                      + N'      AND  dp.sid <> SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N')' + NCHAR(10)
                      + N'      AND  SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N') IS NOT NULL' + NCHAR(10)
                      + N')' + NCHAR(10)
                      + N'BEGIN' + NCHAR(10)
                      + N'    ALTER USER ' + QUOTENAME(@LoginName)
                      + N' WITH LOGIN = ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                      + N'END' + NCHAR(10)

                      -- Назначение ролей (из @RoleSQL)
                      + @RoleSQL;
                END

                -- Шаг 1: отзываем лишние права (desired-state cleanup) — всегда,
                --        в т.ч. для деактивированных (это и есть реальный деprovision)
                IF @DryRun = 0
                    EXEC sp_executesql @CleanupSQL;
                ELSE
                    PRINT N'    [CLEANUP] ' + @CleanupSQL;

                -- Шаг 2: создаём юзера и выдаём актуальные права из матрицы —
                --        только для активных пользователей
                IF @IsUserActive = 1
                BEGIN
                    IF @DryRun = 0
                        EXEC sp_executesql @SQL;
                    ELSE
                        PRINT N'    [GRANT]   ' + @SQL;
                END

                SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                INSERT INTO dbo.tUserProvisioningLog
                    (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, @DbName,
                        CASE WHEN @IsUserActive = 1 THEN N'PROVISION' ELSE N'DEPROVISION' END,
                        @LogStatus,
                        N'DbType=' + @DbType
                        + N' | Roles='   + ISNULL(@DbRoles, N'')
                        + N' | Exec='    + CAST(@GrantExec    AS NCHAR(1))
                        + N' | ViewDef=' + CAST(@GrantViewDef AS NCHAR(1)));

                IF @IsUserActive = 1
                    PRINT @Prefix + N'  [v] ' + @DbName
                        + N' (' + @DbType + N') -> ' + ISNULL(@DbRoles, N'—')
                        + CASE WHEN @GrantExec    = 1 THEN N' + EXECUTE'         ELSE N'' END
                        + CASE WHEN @GrantViewDef = 1 THEN N' + VIEW DEFINITION' ELSE N'' END;
                ELSE
                    PRINT @Prefix + N'  [-] ' + @DbName + N' (' + @DbType + N') -> доступ отозван';

            END TRY
            BEGIN CATCH
                INSERT INTO dbo.tUserProvisioningLog
                    (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, @DbName,
                        CASE WHEN @IsUserActive = 1 THEN N'PROVISION' ELSE N'DEPROVISION' END,
                        N'ERROR', ERROR_MESSAGE());
                PRINT N'  [!] ОШИБКА в [' + @DbName + N']: ' + ERROR_MESSAGE();
            END CATCH;

            FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
        END -- while cur_DB

        CLOSE cur_DB;
        DEALLOCATE cur_DB;

        FETCH NEXT FROM cur_Users INTO @LoginName, @LoginType, @SqlPassword, @Category, @IsUserActive;
    END -- while cur_Users

    CLOSE cur_Users;
    DEALLOCATE cur_Users;

    -- ── Применяем исключения под тем же RunId ────────────────────────
    --    spProvisionOverrides логирует в tUserProvisioningLog с тем же RunId,
    --    поэтому финальный SELECT ниже покажет и основной провижн, и исключения.
    EXEC dbo.spProvisionOverrides
        @DryRun      = @DryRun,
        @RunId       = @RunId,
        @PrintHeader = 0;

    -- ── Итоговый отчёт ────────────────────────────────────────────────
    PRINT N'';
    PRINT N'=============================================================';
    PRINT @Prefix + N'  ГОТОВО. Детали ниже и в dbo.tUserProvisioningLog';
    PRINT N'=============================================================';

    SELECT
        L.RunAt,
        L.LoginName,
        L.Scope,
        L.Action,
        L.Status,
        LEFT(ISNULL(L.Details, N''), 300) AS Details
    FROM   dbo.tUserProvisioningLog L
    WHERE  L.RunId = @RunId
    ORDER  BY L.Id;
END
GO

-- ====================================================================
-- [7] ПРОЦЕДУРА ИСКЛЮЧЕНИЙ
-- ====================================================================
CREATE OR ALTER PROCEDURE dbo.spProvisionOverrides
    @DryRun      BIT              = 0,
    @RunId       UNIQUEIDENTIFIER = NULL,
    @PrintHeader BIT              = 1
/*
  Standalone:
    EXEC dbo.spProvisionOverrides @DryRun = 1;
    EXEC dbo.spProvisionOverrides @DryRun = 0;

  Автоматически вызывается из spProvisionUsers с тем же RunId.
*/
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE
        @LoginName    NVARCHAR(256),
        @DbName       NVARCHAR(128),
        @DbRoles      NVARCHAR(500),
        @GrantExec    BIT,
        @GrantViewDef BIT,
        @OverrideType NVARCHAR(10),   -- 'USER' или 'GROUP'
        @SQL          NVARCHAR(MAX),
        @RoleSQL      NVARCHAR(MAX),
        @CleanupSQL   NVARCHAR(MAX),
        @PaddedRoles  NVARCHAR(520),
        @HasOwner     BIT,
        @HasReader    BIT,
        @HasWriter    BIT,
        @StateSQL     NVARCHAR(MAX),
        @UserExists   BIT,
        @IsOrphaned   BIT,
        @ActualOwner  BIT,
        @ActualReader BIT,
        @ActualWriter BIT,
        @ActualExec   BIT,
        @ActualViewDef BIT,
        @NeedsChange  BIT,
        @LogStatus    NVARCHAR(20),
        @Prefix       NVARCHAR(10),
        @OwnRun       BIT = 0,
        @UserCategory NVARCHAR(50),   -- справочно: категория пользователя (для диагностики)
        @IsDev        BIT;            -- признак dev-базы (для PROD-проверки)

    -- Standalone: генерируем свой RunId и показываем отчёт
    IF @RunId IS NULL
    BEGIN
        SET @RunId  = NEWID();
        SET @OwnRun = 1;
    END
    SET @Prefix = CASE WHEN @DryRun = 1 THEN N'[DRY] ' ELSE N'' END;

    IF @PrintHeader = 1
    BEGIN
        PRINT N'';
        PRINT N'=============================================================';
        PRINT @Prefix + N'spProvisionOverrides  |  RunId: ' + CAST(@RunId AS NVARCHAR(40));
        PRINT N'=============================================================';
    END
    ELSE
        PRINT N'--- Применяем исключения (tPermissionOverride) ---';

    -- ──────────────────────────────────────────────────────────────────
    -- Разворачиваем все активные исключения в плоский список
    -- (LoginName, DbName, DbRoles, ..., OverrideType).
    --
    -- Приоритет: личное исключение > групповое.
    -- Если у пользователя есть и то, и другое для одной базы —
    -- берём только личное (NOT EXISTS в ветке GROUP).
    -- ──────────────────────────────────────────────────────────────────

    -- Защита от повторного запуска в той же сессии после ошибки:
    -- если предыдущий вызов упал после CREATE TABLE но до DROP TABLE,
    -- таблица осталась бы висеть и следующий вызов падал бы с "already exists".
    IF OBJECT_ID('tempdb..#Overrides') IS NOT NULL
        DROP TABLE #Overrides;

    CREATE TABLE #Overrides (
        LoginName           NVARCHAR(256),
        DbName              NVARCHAR(128),
        DbRoles             NVARCHAR(500),
        GrantExecute        BIT,
        GrantViewDefinition BIT,
        OverrideType        NVARCHAR(10),
        UserCategory        NVARCHAR(50),   -- справочно, для диагностики
        IsDev               BIT             -- справочно, для диагностики
    );

    -- Личные исключения (наивысший приоритет)
    INSERT INTO #Overrides
    SELECT ul.LoginName, o.DbName, o.DbRoles, o.GrantExecute, o.GrantViewDefinition,
           N'USER', ul.Category, ISNULL(db.IsDev, 0)
    FROM   dbo.tPermissionOverride   o
    JOIN   dbo.tUserProvisioningList ul ON ul.LoginName = o.LoginName AND ul.IsActive = 1
    LEFT JOIN dbo.tDatabases         db ON db.DbName    = o.DbName
    WHERE  o.LoginName IS NOT NULL AND o.IsActive = 1;

    -- Групповые исключения — только если нет личного для той же базы
    INSERT INTO #Overrides
    SELECT ul.LoginName, o.DbName, o.DbRoles, o.GrantExecute, o.GrantViewDefinition,
           N'GROUP', ul.Category, ISNULL(db.IsDev, 0)
    FROM   dbo.tPermissionOverride   o
    JOIN   dbo.tUserProvisioningList ul ON ul.Category = o.Category AND ul.IsActive = 1
    LEFT JOIN dbo.tDatabases         db ON db.DbName   = o.DbName
    WHERE  o.Category IS NOT NULL AND o.IsActive = 1
      AND  NOT EXISTS (
               SELECT 1 FROM dbo.tPermissionOverride uo
               WHERE  uo.LoginName = ul.LoginName
                 AND  uo.DbName    = o.DbName
                 AND  uo.IsActive  = 1
           );

    -- ── Итерируем по развёрнутому списку ─────────────────────────────
    DECLARE cur_Ov CURSOR LOCAL FAST_FORWARD FOR
        SELECT LoginName, DbName, DbRoles, GrantExecute, GrantViewDefinition,
               OverrideType, UserCategory, IsDev
        FROM   #Overrides
        ORDER  BY OverrideType DESC, LoginName, DbName;

    OPEN cur_Ov;
    FETCH NEXT FROM cur_Ov INTO @LoginName, @DbName, @DbRoles, @GrantExec, @GrantViewDef,
                                @OverrideType, @UserCategory, @IsDev;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        -- База онлайн? Логин существует на сервере?
        -- Также проверяем tDatabases.IsActive — если база деактивирована там,
        -- override тоже не применяется (иначе деактивация в tDatabases не имела бы смысла).
        IF EXISTS (SELECT 1 FROM sys.databases WHERE name = @DbName AND state_desc = N'ONLINE')
        AND EXISTS (SELECT 1 FROM sys.server_principals WHERE name = @LoginName AND type IN ('U','S','G'))
        AND NOT EXISTS (SELECT 1 FROM dbo.tDatabases WHERE DbName = @DbName AND IsActive = 0)
        BEGIN
            BEGIN TRY
                -- ── Desired-state cleanup ─────────────────────────────
                SET @PaddedRoles = N'|' + ISNULL(@DbRoles, N'') + N'|';
                SET @HasOwner  = CASE WHEN CHARINDEX(N'|db_owner|',      @PaddedRoles) > 0 THEN 1 ELSE 0 END;
                SET @HasReader = CASE WHEN CHARINDEX(N'|db_datareader|',  @PaddedRoles) > 0 THEN 1 ELSE 0 END;
                SET @HasWriter = CASE WHEN CHARINDEX(N'|db_datawriter|',  @PaddedRoles) > 0 THEN 1 ELSE 0 END;

                -- ── Сверяем желаемое состояние с фактическим (тот же приём, что
                --    и в spProvisionUsers) — лог/выполнение только при реальном отличии ──
                SET @UserExists = 0; SET @IsOrphaned = 0;
                SET @ActualOwner = 0; SET @ActualReader = 0; SET @ActualWriter = 0;
                SET @ActualExec = 0; SET @ActualViewDef = 0;

                SET @StateSQL =
                    N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                  + N'SELECT' + NCHAR(10)
                  + N'    @UserExists = CASE WHEN EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                  + N'                       WHERE name = N' + dbo.fnQuoteLiteral(@LoginName) + N' AND type IN (''U'',''S'',''G'')) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @IsOrphaned = CASE WHEN EXISTS (SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                  + N'                       WHERE dp.name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                  + N'                         AND dp.sid <> SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N')' + NCHAR(10)
                  + N'                         AND SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N') IS NOT NULL) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @ActualOwner  = ISNULL(IS_ROLEMEMBER(''db_owner'',      N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualReader = ISNULL(IS_ROLEMEMBER(''db_datareader'', N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualWriter = ISNULL(IS_ROLEMEMBER(''db_datawriter'', N' + dbo.fnQuoteLiteral(@LoginName) + N'), 0),' + NCHAR(10)
                  + N'    @ActualExec = CASE WHEN EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                  + N'                       WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''EXECUTE''' + NCHAR(10)
                  + N'                         AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N')) THEN 1 ELSE 0 END,' + NCHAR(10)
                  + N'    @ActualViewDef = CASE WHEN EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                  + N'                       WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                  + N'                         AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N')) THEN 1 ELSE 0 END;';

                EXEC sp_executesql @StateSQL,
                    N'@UserExists BIT OUTPUT, @IsOrphaned BIT OUTPUT, @ActualOwner BIT OUTPUT, @ActualReader BIT OUTPUT, @ActualWriter BIT OUTPUT, @ActualExec BIT OUTPUT, @ActualViewDef BIT OUTPUT',
                    @UserExists OUTPUT, @IsOrphaned OUTPUT, @ActualOwner OUTPUT, @ActualReader OUTPUT, @ActualWriter OUTPUT, @ActualExec OUTPUT, @ActualViewDef OUTPUT;

                SET @NeedsChange = CASE WHEN
                       (@UserExists = 0)
                    OR (@UserExists = 1 AND @IsOrphaned = 1)
                    OR (@ActualOwner   <> @HasOwner)
                    OR (@ActualReader  <> @HasReader)
                    OR (@ActualWriter  <> @HasWriter)
                    OR (@ActualExec    <> @GrantExec)
                    OR (@ActualViewDef <> @GrantViewDef)
                    THEN 1 ELSE 0 END;

                IF @NeedsChange = 1
                BEGIN
                    SET @CleanupSQL =
                        N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                      + N'IF EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                      + N'           WHERE name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                      + N'             AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                      + N'BEGIN' + NCHAR(10)
                      + CASE WHEN @HasOwner = 0 THEN
                            N'    IF IS_ROLEMEMBER(''db_owner'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                          + N'        ALTER ROLE [db_owner] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                        ELSE N'' END
                      + CASE WHEN @HasReader = 0 THEN
                            N'    IF IS_ROLEMEMBER(''db_datareader'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                          + N'        ALTER ROLE [db_datareader] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                        ELSE N'' END
                      + CASE WHEN @HasWriter = 0 THEN
                            N'    IF IS_ROLEMEMBER(''db_datawriter'', N' + dbo.fnQuoteLiteral(@LoginName) + N') = 1' + NCHAR(10)
                          + N'        ALTER ROLE [db_datawriter] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                        ELSE N'' END
                      + CASE WHEN @GrantExec = 0 THEN
                            N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                          + N'               WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''EXECUTE''' + NCHAR(10)
                          + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N'))' + NCHAR(10)
                          + N'        REVOKE EXECUTE FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                        ELSE N'' END
                      + CASE WHEN @GrantViewDef = 0 THEN
                            N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                          + N'               WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                          + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + dbo.fnQuoteLiteral(@LoginName) + N'))' + NCHAR(10)
                          + N'        REVOKE VIEW DEFINITION FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                        ELSE N'' END
                      + N'END';

                    -- ── Grant SQL ─────────────────────────────────────────
                    SET @RoleSQL = N'';
                    SELECT @RoleSQL += N'ALTER ROLE ' + QUOTENAME(LTRIM(RTRIM(value)))
                                     + N' ADD MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    FROM   STRING_SPLIT(@DbRoles, N'|')
                    WHERE  LTRIM(RTRIM(value)) <> N'';

                    IF @GrantExec    = 1 SET @RoleSQL += N'GRANT EXECUTE TO '        + QUOTENAME(@LoginName) + N';' + NCHAR(10);
                    IF @GrantViewDef = 1 SET @RoleSQL += N'GRANT VIEW DEFINITION TO ' + QUOTENAME(@LoginName) + N';';

                    SET @SQL =
                        N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                      + N'IF NOT EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                      + N'              WHERE name = N' + dbo.fnQuoteLiteral(@LoginName) + N' AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                      + N'BEGIN' + NCHAR(10)
                      + N'    CREATE USER ' + QUOTENAME(@LoginName) + N' FOR LOGIN ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                      + N'END' + NCHAR(10)
                      -- Фиксируем orphaned user (SID рассинхронизирован) — тот же фикс что в spProvisionUsers
                      + N'ELSE IF EXISTS (' + NCHAR(10)
                      + N'    SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                      + N'    WHERE  dp.name = N' + dbo.fnQuoteLiteral(@LoginName) + NCHAR(10)
                      + N'      AND  dp.sid <> SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N')' + NCHAR(10)
                      + N'      AND  SUSER_SID(N' + dbo.fnQuoteLiteral(@LoginName) + N') IS NOT NULL' + NCHAR(10)
                      + N')' + NCHAR(10)
                      + N'BEGIN' + NCHAR(10)
                      + N'    ALTER USER ' + QUOTENAME(@LoginName) + N' WITH LOGIN = ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                      + N'END' + NCHAR(10)
                      + @RoleSQL;

                    IF @DryRun = 0
                    BEGIN
                        EXEC sp_executesql @CleanupSQL;
                        EXEC sp_executesql @SQL;
                    END
                    ELSE
                    BEGIN
                        PRINT N'    [CLEANUP] ' + @CleanupSQL;
                        PRINT N'    [GRANT]   ' + @SQL;
                    END

                    SET @LogStatus = CASE WHEN @DryRun = 1 THEN N'DRY_RUN' ELSE N'OK' END;
                    INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
                    VALUES (@RunId, @LoginName, @DbName, N'OVERRIDE', @LogStatus,
                            N'Type='     + @OverrideType
                            + N' | Roles='   + ISNULL(@DbRoles, N'')
                            + N' | Exec='    + CAST(@GrantExec    AS NCHAR(1))
                            + N' | ViewDef=' + CAST(@GrantViewDef AS NCHAR(1)));

                    PRINT @Prefix + N'  [v] [' + @OverrideType + N'] '
                        + @LoginName + N' → ' + @DbName
                        + N': ' + ISNULL(@DbRoles, N'—')
                        + CASE WHEN @GrantExec    = 1 THEN N' + EXECUTE'         ELSE N'' END
                        + CASE WHEN @GrantViewDef = 1 THEN N' + VIEW DEFINITION' ELSE N'' END;
                END
                ELSE
                    PRINT @Prefix + N'  [=] [' + @OverrideType + N'] '
                        + @LoginName + N' → ' + @DbName + N' -> уже соответствует, пропуск.';

            END TRY
            BEGIN CATCH
                INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, @DbName, N'OVERRIDE', N'ERROR', ERROR_MESSAGE());
                PRINT N'  [!] ОШИБКА (' + @LoginName + N' → ' + @DbName + N'): ' + ERROR_MESSAGE();
            END CATCH;
        END
        ELSE
        BEGIN
            -- CASE WHEN EXISTS(...) не разрешён в контексте PRINT — используем IF/ELSE
            IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = @DbName AND state_desc = N'ONLINE')
                PRINT N'  [-] Пропуск: ' + @LoginName + N' → ' + @DbName + N' (база не найдена / offline)';
            ELSE IF EXISTS (SELECT 1 FROM dbo.tDatabases WHERE DbName = @DbName AND IsActive = 0)
                PRINT N'  [-] Пропуск: ' + @LoginName + N' → ' + @DbName + N' (база деактивирована в tDatabases)';
            ELSE
                PRINT N'  [-] Пропуск: ' + @LoginName + N' → ' + @DbName + N' (логин не существует на сервере)';
        END

        FETCH NEXT FROM cur_Ov INTO @LoginName, @DbName, @DbRoles, @GrantExec, @GrantViewDef,
                                    @OverrideType, @UserCategory, @IsDev;
    END

    CLOSE cur_Ov;
    DEALLOCATE cur_Ov;
    DROP TABLE #Overrides;

    PRINT N'--- Исключения применены ---';

    -- Отдельный отчёт только при standalone-запуске
    IF @OwnRun = 1
    BEGIN
        PRINT N'';
        PRINT N'=============================================================';
        PRINT @Prefix + N'  ГОТОВО. Детали в dbo.tUserProvisioningLog';
        PRINT N'=============================================================';

        SELECT
            L.RunAt,
            L.LoginName,
            L.Scope,
            L.Action,
            L.Status,
            LEFT(ISNULL(L.Details, N''), 300) AS Details
        FROM   dbo.tUserProvisioningLog L
        WHERE  L.RunId = @RunId
        ORDER  BY L.Id;
    END
END
GO

-- ====================================================================
-- [8] ПРОЦЕДУРА: УЧЁТКА ИИ-АГЕНТА
--     Заводит сервисную учётку категории [AI Agent] — по одной
--     на разработчика. Права берутся из матрицы: db_datareader
--     во всех базах из tDatabases.
--
--     Зачем процедура, а не INSERT руками: она следит, чтобы учётка
--     была именно SQL-логином категории [AI Agent], чинит запись,
--     если её завели неправильно, и снимает личные исключения, если
--     они на этом логине откуда-то остались. Последнее важно: молча
--     висящее исключение с пустыми правами закрывает агенту базу,
--     и выглядит это как «доступ есть, а данных не видно».
--
--     Процедура ТОЛЬКО правит таблицы конфигурации. На сервере не
--     меняется ничего, пока не будет запущена spProvisionUsers —
--     она и создаст логин, и разложит права.
-- ====================================================================
CREATE OR ALTER PROCEDURE dbo.spProvisionAiAgent
    @LoginName NVARCHAR(256),          -- svc_ai_<фамилия>
    @Password  NVARCHAR(256) = NULL,   -- обязателен только при заведении новой учётки
    @Notes     NVARCHAR(500) = NULL,
    @DryRun    BIT           = 0       -- 1 → показать, что будет вписано, и ничего не менять
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Prefix    NVARCHAR(10) = CASE WHEN @DryRun = 1 THEN N'[DRY] ' ELSE N'' END,
            @IsNew     BIT,
            @Overrides INT;

    IF ISNULL(LTRIM(RTRIM(@LoginName)), N'') = N''
        THROW 50010, 'Не указан логин учётной записи агента.', 1;

    SET @IsNew = CASE WHEN EXISTS (SELECT 1 FROM dbo.tUserProvisioningList
                                   WHERE LoginName = @LoginName) THEN 0 ELSE 1 END;

    -- Пароль нужен только при заведении: у существующей учётки он уже
    -- затёрт в NULL после первого успешного прогона, и это норма.
    IF @IsNew = 1 AND ISNULL(@Password, N'') = N''
        THROW 50012, 'Новой учётной записи нужен пароль: @Password обязателен при первом заведении.', 1;

    SELECT @Overrides = COUNT(*)
    FROM   dbo.tPermissionOverride
    WHERE  LoginName = @LoginName AND IsActive = 1;

    PRINT N'';
    PRINT N'=============================================================';
    PRINT @Prefix + N'spProvisionAiAgent  |  ' + @LoginName;
    PRINT N'=============================================================';

    IF @DryRun = 1
    BEGIN
        PRINT N'  Учётная запись: ' + CASE WHEN @IsNew = 1 THEN N'будет заведена' ELSE N'уже есть, будет обновлена' END;
        PRINT N'  Права: по матрице категории [AI Agent] — db_datareader во всех базах из tDatabases.';
        IF @Overrides > 0
            PRINT N'  Личные исключения (' + CAST(@Overrides AS NVARCHAR(10)) + N' шт.) будут сняты.';
        SELECT d.DbName, d.IsDev
        FROM   dbo.tDatabases d
        WHERE  d.IsActive = 1
        ORDER  BY d.DbName;
        RETURN;
    END

    BEGIN TRY
        BEGIN TRANSACTION;

        IF @IsNew = 1
        BEGIN
            INSERT INTO dbo.tUserProvisioningList (LoginName, LoginType, SqlPassword, Category, Notes)
            VALUES (@LoginName, N'SQL', @Password, N'AI Agent',
                    ISNULL(@Notes, N'ИИ-агент разработчика, read-only'));
            PRINT N'  [+] Учётная запись заведена.';
        END
        ELSE
        BEGIN
            UPDATE dbo.tUserProvisioningList
            SET    Category    = N'AI Agent',
                   LoginType   = N'SQL',
                   IsActive    = 1,
                   SqlPassword = COALESCE(@Password, SqlPassword),
                   Notes       = COALESCE(@Notes, Notes)
            WHERE  LoginName = @LoginName;
            PRINT N'  [=] Учётная запись уже была, обновлена.';
        END

        -- Личные исключения агенту не нужны: права даёт матрица категории.
        -- Оставшееся от прежних настроек снимаем, иначе исключение с пустыми
        -- правами тихо закроет базу, а выглядеть это будет как баг доступа.
        IF @Overrides > 0
        BEGIN
            DELETE FROM dbo.tPermissionOverride WHERE LoginName = @LoginName;
            PRINT N'  [-] Снято личных исключений: ' + CAST(@Overrides AS NVARCHAR(10)) + N'.';
        END

        COMMIT TRANSACTION;

        PRINT N'';
        PRINT N'  Осталось применить:  EXEC dbo.spProvisionUsers @DryRun = 1;';
        PRINT N'                       EXEC dbo.spProvisionUsers @DryRun = 0;';
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;

    SELECT LoginName, Category, LoginType, IsActive,
           CASE WHEN SqlPassword IS NULL THEN N'затёрт' ELSE N'ждёт прогона' END AS Пароль, Notes
    FROM   dbo.tUserProvisioningList
    WHERE  LoginName = @LoginName;
END
GO

-- ====================================================================
-- [9] ОЧИСТКА СПИСКА БАЗ (разовый прогон)
--     Удаляет из tDatabases записи о базах которых нет на этом сервере.
--
--     Зачем: spProvisionUsers и так молча пропускает несуществующие базы
--     (проверка EXISTS(sys.databases)), ошибок не будет. Эта очистка нужна
--     чтобы tDatabases отражала реальность конкретного сервера и не хранила
--     мусор — например dev-базы на prod-сервере, которых там никогда не будет.
-- ====================================================================
DECLARE @Removed INT;

-- Показываем что удаляем — попадёт в вывод запуска init-скрипта
SELECT d.DbName, d.IsDev, N'Удаляется — нет на этом сервере' AS Reason
FROM   dbo.tDatabases d
WHERE  NOT EXISTS (
    SELECT 1 FROM sys.databases s
    WHERE  s.name = d.DbName AND s.state_desc = N'ONLINE'
)
ORDER  BY d.IsDev, d.DbName;

DELETE FROM dbo.tDatabases
WHERE NOT EXISTS (
    SELECT 1 FROM sys.databases s
    WHERE  s.name = dbo.tDatabases.DbName AND s.state_desc = N'ONLINE'
);

SET @Removed = @@ROWCOUNT;
PRINT 'OK: dbo.tDatabases очищена — удалено записей: ' + CAST(@Removed AS NVARCHAR(10)) + '.';
GO

-- ====================================================================
-- [10] ИТОГ ЗАПУСКА
--     Снимает NOEXEC (иначе сессия останется «немой» и следующие запросы
--     будут молча ничего не делать) и печатает результат.
-- ====================================================================
SET NOEXEC OFF;
GO

DECLARE @Mode NVARCHAR(20)  = (SELECT TOP 1 Mode   FROM #ProvisioningTarget);
DECLARE @Why  NVARCHAR(400) = (SELECT TOP 1 Reason FROM #ProvisioningTarget);

PRINT '';
IF @Mode = N'ABORT'
BEGIN
    PRINT '════════ ИТОГ: скрипт НЕ выполнен, объекты не создавались ════════';
    PRINT 'Причина: ' + ISNULL(@Why, N'нет прав на создание базы [DBAProvisioning].');
    PRINT 'Что делать: попросить DBA создать базу [DBAProvisioning] и выдать db_owner в ней,';
    PRINT '            затем перезапустить этот скрипт — он идемпотентен.';
END
ELSE
BEGIN
    PRINT '════════ ИТОГ: объекты развёрнуты в базе [DBAProvisioning] ════════';
    PRINT 'Запуск:  EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 1;   -- проверка';
    PRINT '         EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;   -- применить';
END

DROP TABLE #ProvisioningTarget;
GO


-- ====================================================================
-- ПРИМЕРЫ И КОМАНДЫ
-- ====================================================================
/*

-- ── Шаг 0: список баз ────────────────────────────────────────────────
-- Посмотреть текущий список:
SELECT * FROM dbo.tDatabases ORDER BY IsDev, DbName;

-- Добавить новую базу (prod):
INSERT INTO dbo.tDatabases (DbName, IsDev, Notes)
VALUES ('NewService', 0, 'Новый микросервис');

-- Добавить новую базу с dev-окружением:
INSERT INTO dbo.tDatabases (DbName, IsDev, Notes)
VALUES ('NewService',     0, 'Новый микросервис'),
       ('NewService_dev', 1, 'Dev-контур нового микросервиса');

-- Временно исключить базу из провижна:
UPDATE dbo.tDatabases SET IsActive = 0 WHERE DbName = 'OldService';

-- Вернуть базу в провижн:
UPDATE dbo.tDatabases SET IsActive = 1 WHERE DbName = 'OldService';

-- Удалить базу из списка насовсем:
DELETE FROM dbo.tDatabases WHERE DbName = 'OldService';


-- ── Шаг 1: заполнить список пользователей ────────────────────────────
-- Посмотреть текущий список:
SELECT * FROM dbo.tUserProvisioningList ORDER BY Category, LoginName;

-- Начальное заполнение (идемпотентно — пропускает уже существующих):
-- !! Не коммить сюда реальные email/логины и тем более пароли — это пример.
--    Реальный seed держи отдельным незакоммиченным скриптом/секретом. !!
INSERT INTO dbo.tUserProvisioningList (LoginName, LoginType, Category, Notes)
SELECT v.LoginName, v.LoginType, v.Category, v.Notes
FROM (VALUES
    -- DB Admin
    ('<login1>@example.com', 'SQL', 'DB Admin',           NULL),
    ('<login2>@example.com', 'SQL', 'DB Admin',           NULL),
    -- Dev Team
    ('<login3>@example.com', 'SQL', 'Dev Team',           NULL),
    ('<login4>@example.com', 'SQL', 'Dev Team',           NULL),
    -- Data Engineer Team
    ('<login5>@example.com', 'SQL', 'Data Engineer Team', NULL),
    -- QA
    ('<login6>@example.com', 'SQL', 'QA',                 NULL),
    -- Support Senior
    ('<login7>@example.com', 'SQL', 'Support Senior',     NULL)
) AS v (LoginName, LoginType, Category, Notes)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.tUserProvisioningList t
    WHERE  t.LoginName = v.LoginName
);

-- !! ОБЯЗАТЕЛЬНО перед запуском spProvisionUsers задай пароли !!
-- SQL-логин без пароля упадёт с ошибкой при создании.
-- Пароль хранится в открытом виде только до первого успешного запуска —
-- после CREATE LOGIN spProvisionUsers сам затирает SqlPassword в NULL.
--
-- Вариант 1: задать каждому индивидуально (сгенерируй реальный пароль отдельно,
-- не бери его из этого файла и не коммить):
-- UPDATE dbo.tUserProvisioningList SET SqlPassword = N'<сгенерированный-пароль>' WHERE LoginName = '<login1>@example.com';
-- ...
--
-- Вариант 2: временно поставить один пароль всем (потом сменить)
-- UPDATE dbo.tUserProvisioningList SET SqlPassword = N'<временный-пароль>' WHERE SqlPassword IS NULL;

-- SQL-логин (сервисный аккаунт):
INSERT INTO dbo.tUserProvisioningList (LoginName, LoginType, SqlPassword, Category, Notes) VALUES
    ('svc_reporting', 'SQL', '<сгенерированный-пароль>', 'Data Engineer Team', 'Аккаунт отчётов');

-- Деактивировать пользователя (не удалять): логин будет отключён (ALTER LOGIN DISABLE),
-- членство в sysadmin и роли/EXECUTE/VIEW DEFINITION во всех базах — отозваны
-- при следующем прогоне spProvisionUsers. Данные из tUserProvisioningList не теряются,
-- поэтому обратное включение (IsActive=1) восстановит доступ по матрице.
UPDATE dbo.tUserProvisioningList SET IsActive = 0 WHERE LoginName = 'CORP\petrov.pp';


-- ── Шаг 2: матрица прав ───────────────────────────────────────────────
SELECT Category, DbType, DbRoles, GrantExecute, GrantViewDefinition, Notes
FROM   dbo.tPermissionMatrix
ORDER  BY Category, DbType;

-- Изменить права для группы:
UPDATE dbo.tPermissionMatrix
SET    GrantExecute = 1, UpdatedAt = SYSDATETIME()
WHERE  Category = 'QA' AND DbType = 'DEV';

-- Отключить VIEW DEFINITION для Support Senior:
UPDATE dbo.tPermissionMatrix
SET    GrantViewDefinition = 0, UpdatedAt = SYSDATETIME()
WHERE  Category = 'Support Senior';


-- ── Шаг 3: исключения из матрицы ────────────────────────────────────
-- Посмотреть текущие исключения:
SELECT * FROM dbo.tPermissionOverride ORDER BY Category, LoginName, DbName;

-- Групповое исключение: Data Engineer Team → crmExtra → db_owner
INSERT INTO dbo.tPermissionOverride (Category, DbName, DbRoles, Notes)
VALUES ('Data Engineer Team', 'crmExtra', 'db_owner', 'DE нужен owner для ETL-пайплайна');

-- Личное исключение: конкретный юзер → Mindbox → writer
INSERT INTO dbo.tPermissionOverride (LoginName, DbName, DbRoles, Notes)
VALUES ('<login6>@example.com', 'Mindbox', 'db_datareader|db_datawriter',
        'Временно выдан write для исследования инцидента');

-- Деактивировать исключение (не удалять):
UPDATE dbo.tPermissionOverride
SET    IsActive = 0, UpdatedAt = SYSDATETIME()
WHERE  LoginName = '<login6>@example.com' AND DbName = 'Mindbox';

-- Удалить исключение насовсем:
DELETE FROM dbo.tPermissionOverride
WHERE  LoginName = '<login6>@example.com' AND DbName = 'Mindbox';


-- ── Шаг 4: запуск ────────────────────────────────────────────────────
-- Окружение уже зашито в tPermissionMatrix при первом прогоне init-скрипта.
-- Процедуры просто читают матрицу — параметры передавать не нужно.

EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 1;   -- проверка
EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;   -- применить

-- Только исключения:
EXEC [DBAProvisioning].dbo.spProvisionOverrides @DryRun = 1;
EXEC [DBAProvisioning].dbo.spProvisionOverrides @DryRun = 0;

-- Сменить окружение на уже настроенном сервере:
--   1. DELETE FROM [DBAProvisioning].dbo.tPermissionMatrix;
--   2. Поменяй @Env вверху файла и перезапусти init-скрипт
--   3. EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;


-- ════════════════════════════════════════════════════════════════════
-- СЕРВИСНЫЕ УЧЁТКИ ДЛЯ ИИ-АГЕНТОВ  (категория [AI Agent])
-- ════════════════════════════════════════════════════════════════════
--
-- Категория даёт чтение: db_datareader + VIEW DEFINITION на PROD и DEV,
-- то есть SELECT на все таблицы и вьюхи каждой базы из tDatabases,
-- включая те, что появятся позже. EXECUTE не выдаётся — процедура умеет
-- писать. В [sysadmin] такая учётка не попадает никогда, логин обязан
-- быть SQL (CHECK chk_upl_AiAgentIsSql).
--
-- МОДЕЛЬ ДОСТУПА: одна учётка на разработчика, svc_ai_<фамилия>.
--
-- Один человек — один логин, один пароль, все контуры. Разбор бага
-- начинается с задачи, а не с контура: клиент выясняется по ходу, и
-- отдельная пара логин-пароль под каждую базу означала бы, что половина
-- разборов упирается в «а этот профиль я не заводил».
--
-- Что при этом остаётся: в аудите MSSQL видно, кто именно читал данные
-- (логин персональный), и отзывается доступ одной строкой при увольнении.
-- Чем платим: утёкшая пара открывает все контуры сразу. Поэтому учётка
-- личная и не передаётся коллеге, а пароль живёт в Keychain, а не в чате.
--
-- ── Где заведены агенты ──────────────────────────────────────────────
-- Готовый список всех разработчиков с логинами и паролями лежит рядом:
--   seed-ai-agents.sql          — рабочий файл, в .gitignore, пароли настоящие
--   seed-ai-agents.example.sql  — шаблон в git, пароли плейсхолдерами
-- Прогонять его после этого файла. Ниже — та же операция поштучно.
--
-- ── Завести агента вручную ───────────────────────────────────────────
-- Пароль сгенерируй отдельно, сюда не вписывай и не коммить: после первого
-- успешного прогона spProvisionUsers затрёт его в NULL.
EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_ippolitov',
     @Password  = '<сгенерированный-пароль>',
     @DryRun    = 1;          -- посмотреть, что будет вписано

EXEC dbo.spProvisionAiAgent
     @LoginName = 'svc_ai_ippolitov',
     @Password  = '<сгенерированный-пароль>';

-- Процедура правит только таблицы конфигурации. Права на сервере разложит
-- обычный прогон:
EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 1;
EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;

-- ── Проверить ────────────────────────────────────────────────────────
-- Все агенты списком:
SELECT LoginName, IsActive, Notes
FROM   dbo.tUserProvisioningList
WHERE  Category = 'AI Agent'
ORDER  BY LoginName;

-- Что учётка реально получила в базах (по факту, а не по таблицам):
SELECT L.Scope AS DbName, L.Action, L.Status, LEFT(ISNULL(L.Details,''),200) AS Details
FROM   dbo.tUserProvisioningLog L
WHERE  L.LoginName = 'svc_ai_ippolitov'
ORDER  BY L.Id DESC;

-- ── Отозвать при увольнении ──────────────────────────────────────────
UPDATE dbo.tUserProvisioningList SET IsActive = 0 WHERE LoginName = 'svc_ai_ippolitov';
EXEC [DBAProvisioning].dbo.spProvisionUsers @DryRun = 0;

-- Смена пароля (утечка, ротация) — мимо этой системы, напрямую:
-- ALTER LOGIN [svc_ai_ippolitov] WITH PASSWORD = N'<новый-пароль>';

-- ── Подстройка прав всей категории ───────────────────────────────────
-- Убрать VIEW DEFINITION (агенты перестанут видеть текст вьюх и процедур):
UPDATE dbo.tPermissionMatrix
SET    GrantViewDefinition = 0, UpdatedAt = SYSDATETIME()
WHERE  Category = 'AI Agent';

-- ── Если конкретного агента всё же надо сузить ───────────────────────
-- Механизм есть, но это не норма, а исключение под причину. Личные
-- исключения: нужной базе db_datareader, остальным пустые права.
-- Учти: spProvisionAiAgent при следующем вызове их снимет — она считает,
-- что агенту исключения не нужны.
-- INSERT INTO dbo.tPermissionOverride (LoginName, DbName, DbRoles, GrantExecute, GrantViewDefinition, Notes)
-- SELECT 'svc_ai_ippolitov', d.DbName,
--        CASE WHEN d.DbName = '<база>' THEN 'db_datareader' ELSE '' END, 0,
--        CASE WHEN d.DbName = '<база>' THEN 1 ELSE 0 END, '<причина сужения>'
-- FROM   dbo.tDatabases d WHERE d.IsActive = 1;


-- ── Очистка списка баз (при необходимости повторить) ─────────────────
-- Выполняется автоматически в конце init-скрипта.
-- Если нужно прогнать отдельно позже:

USE [DBAProvisioning];

-- Посмотреть что будет удалено:
SELECT DbName, IsDev FROM dbo.tDatabases d
WHERE  NOT EXISTS (SELECT 1 FROM sys.databases s
                   WHERE s.name = d.DbName AND s.state_desc = 'ONLINE');

-- Удалить:
DELETE FROM dbo.tDatabases
WHERE NOT EXISTS (SELECT 1 FROM sys.databases s
                  WHERE s.name = dbo.tDatabases.DbName AND s.state_desc = 'ONLINE');


-- ── Журнал и диагностика ─────────────────────────────────────────────
USE [DBAProvisioning];

-- Полный журнал последнего запуска:
SELECT TOP 500 * FROM dbo.tUserProvisioningLog ORDER BY RunAt DESC, Id;

-- Только ошибки:
SELECT * FROM dbo.tUserProvisioningLog WHERE Status = 'ERROR' ORDER BY RunAt DESC;

-- Что получил конкретный пользователь:
SELECT Scope, Action, Status, Details
FROM   dbo.tUserProvisioningLog
WHERE  LoginName = '<login6>@example.com'
ORDER  BY RunAt DESC;

-- Очистить журнал:
-- TRUNCATE TABLE dbo.tUserProvisioningLog;


-- ── Если tProvisioningServerConfig осталась с прошлой версии ─────────
-- Она больше не используется, можно удалить:
-- DROP TABLE IF EXISTS [master].dbo.tProvisioningServerConfig;

*/