/*
========================================================================
  USER PROVISIONING SYSTEM FOR MSSQL  — v3
  Безопасна для многократного запуска (Idempotent)

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
  └──────────────────────┴─────────┴───────────────────────────┴──────┴──────┘

  EXEC = GRANT EXECUTE        — запуск всех процедур и функций в базе
  VIEW = GRANT VIEW DEFINITION — просмотр кода процедур, вьюх, функций
  DbType: PROD = базы без суффикса _dev  |  DEV = базы с суффиксом _dev
  Примечание: db_owner уже включает оба права, поэтому DB Admin и Dev Team DEV
              не нуждаются в явных EXEC/VIEW — они получают их через роль.

  ОБЪЕКТЫ (все создаются в базе [DBAProvisioning]):
    [1] dbo.tUserProvisioningList  — список пользователей
    [2] dbo.tUserProvisioningLog   — аудит-журнал
    [3] dbo.tPermissionMatrix      — матрица прав (редактируемая)
    [4] dbo.tDatabases             — список баз для провижна (редактируемый)
    [5] dbo.tPermissionOverride    — исключения из матрицы (группа или юзер + база)
    [6] dbo.spProvisionUsers       — основная процедура (матрица)
    [7] dbo.spProvisionOverrides   — процедура исключений (вызывается автоматически)

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
-- ====================================================================
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'DBAProvisioning')
BEGIN
    CREATE DATABASE [DBAProvisioning];
    PRINT 'OK: База данных [DBAProvisioning] создана.';
END
ELSE
    PRINT 'SKIP: [DBAProvisioning] уже существует.';
GO

USE [DBAProvisioning];
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
                            'Support Senior'
                        )),
        IsActive    BIT           NOT NULL DEFAULT 1,
        Notes       NVARCHAR(500) NULL,
        CreatedAt   DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        -- Один логин = одна строка. Дубликаты приведут к двойному провижну.
        CONSTRAINT uq_upl_LoginName UNIQUE (LoginName)
    );
    PRINT 'OK: Таблица dbo.tUserProvisioningList создана.';
END
ELSE
    PRINT 'SKIP: dbo.tUserProvisioningList уже существует.';
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
                             'Support Senior'
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
    ('PROD', 'Support Senior',     'DEV',  'db_datareader',                0,   1,  'Только чтение + VIEW')
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
                                    'QA', 'Support Senior'
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
        @LogStatus    NVARCHAR(20),
        @Prefix       NVARCHAR(10),
        @RunId        UNIQUEIDENTIFIER;

    SET @RunId  = NEWID();
    SET @Prefix = CASE WHEN @DryRun = 1 THEN N'[DRY] ' ELSE N'' END;

    PRINT N'';
    PRINT N'=============================================================';
    PRINT @Prefix + N'spProvisionUsers  |  RunId: ' + CAST(@RunId AS NVARCHAR(40));
    PRINT N'=============================================================';

    -- ── Перебираем активных пользователей ────────────────────────────
    DECLARE cur_Users CURSOR LOCAL FAST_FORWARD FOR
        SELECT LoginName, LoginType, SqlPassword, Category
        FROM   dbo.tUserProvisioningList
        WHERE  IsActive = 1
        ORDER  BY LoginName;

    OPEN cur_Users;
    FETCH NEXT FROM cur_Users INTO @LoginName, @LoginType, @SqlPassword, @Category;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        PRINT N'';
        PRINT N'-------------------------------------------------------------';
        PRINT @Prefix + N'Пользователь: ' + @LoginName + N'  [' + @Category + N']';
        PRINT N'-------------------------------------------------------------';

        -- =============================================================
        -- A. СЕРВЕРНЫЙ ЛОГИН: создаём или включаем
        -- =============================================================
        BEGIN TRY
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
                             + N' WITH PASSWORD     = N' + QUOTENAME(@SqlPassword, N'''')
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
        END TRY
        BEGIN CATCH
            INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
            VALUES (@RunId, @LoginName, N'SERVER', N'CREATE/ENABLE LOGIN', N'ERROR', ERROR_MESSAGE());
            PRINT N'  [!] ОШИБКА (логин): ' + ERROR_MESSAGE();
        END CATCH;

        -- =============================================================
        -- B. СЕРВЕРНАЯ РОЛЬ — только для DB Admin
        -- =============================================================
        IF @Category = N'DB Admin'
        BEGIN
            BEGIN TRY
                SET @SQL = N'ALTER SERVER ROLE [sysadmin] ADD MEMBER '
                         + QUOTENAME(@LoginName) + N';';
                IF @DryRun = 0 EXEC sp_executesql @SQL;
                SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status)
                VALUES (@RunId, @LoginName, N'SERVER', N'ADD TO [sysadmin]', @LogStatus);
                PRINT @Prefix + N'  [+] Добавлен в серверную роль [sysadmin].';
            END TRY
            BEGIN CATCH
                INSERT INTO dbo.tUserProvisioningLog (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, N'SERVER', N'ADD TO [sysadmin]', N'ERROR', ERROR_MESSAGE());
                PRINT N'  [!] ОШИБКА (sysadmin): ' + ERROR_MESSAGE();
            END CATCH;
        END

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

            BEGIN TRY
                -- ── Определяем тип базы для матрицы прав ────────────
                SET @DbType = CASE WHEN @IsDev = 1 THEN N'DEV' ELSE N'PROD' END;

                -- ── Читаем права из tPermissionMatrix ─────────────────
                -- ВАЖНО: сброс перед SELECT обязателен.
                -- Если строки нет — SQL Server НЕ обнуляет переменные сам,
                -- они остаются со значениями предыдущей итерации (stale data).
                SELECT @DbRoles = NULL, @GrantExec = 0, @GrantViewDef = 0;

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

                -- Строим @CleanupSQL: по каждому managed-объекту —
                -- если НЕ должен быть, но вдруг есть → отзываем.
                -- IS_ROLEMEMBER и EXISTS-check гарантируют идемпотентность:
                -- DROP/REVOKE не вызывается если права и так нет.
                SET @CleanupSQL =
                    N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                  + N'IF EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                  + N'           WHERE name = N' + QUOTENAME(@LoginName, N'''') + NCHAR(10)
                  + N'             AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)

                  -- db_owner
                  + CASE WHEN @HasOwner = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_owner'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_owner] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- db_datareader
                  + CASE WHEN @HasReader = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datareader'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datareader] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- db_datawriter
                  + CASE WHEN @HasWriter = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datawriter'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datawriter] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- EXECUTE
                  + CASE WHEN @GrantExec = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'')' + NCHAR(10)
                      + N'                 AND permission_name = ''EXECUTE''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + QUOTENAME(@LoginName, N'''') + N'))' + NCHAR(10)
                      + N'        REVOKE EXECUTE FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  -- VIEW DEFINITION
                  + CASE WHEN @GrantViewDef = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'')' + NCHAR(10)
                      + N'                 AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + QUOTENAME(@LoginName, N'''') + N'))' + NCHAR(10)
                      + N'        REVOKE VIEW DEFINITION FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END

                  + N'END';

                -- ── Строим SQL назначения ролей из DbRoles (через |) ─
                --    STRING_SPLIT требует SQL Server 2016+
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
                  + N'    WHERE  name = N' + QUOTENAME(@LoginName, N'''') + NCHAR(10)
                  + N'      AND  type IN (''U'', ''S'', ''G'')' + NCHAR(10)
                  + N')' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)
                  + N'    CREATE USER ' + QUOTENAME(@LoginName)
                  + N' FOR LOGIN ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                  + N'END' + NCHAR(10)

                  -- Фиксируем orphaned user (SID рассинхронизирован)
                  + N'ELSE IF EXISTS (' + NCHAR(10)
                  + N'    SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                  + N'    WHERE  dp.name = N' + QUOTENAME(@LoginName, N'''') + NCHAR(10)
                  + N'      AND  dp.sid <> SUSER_SID(N' + QUOTENAME(@LoginName, N'''') + N')' + NCHAR(10)
                  + N'      AND  SUSER_SID(N' + QUOTENAME(@LoginName, N'''') + N') IS NOT NULL' + NCHAR(10)
                  + N')' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)
                  + N'    ALTER USER ' + QUOTENAME(@LoginName)
                  + N' WITH LOGIN = ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                  + N'END' + NCHAR(10)

                  -- Назначение ролей (из @RoleSQL)
                  + @RoleSQL;

                -- Шаг 1: отзываем лишние права (desired-state cleanup)
                IF @DryRun = 0
                    EXEC sp_executesql @CleanupSQL;
                ELSE
                    PRINT N'    [CLEANUP] ' + @CleanupSQL;

                -- Шаг 2: создаём юзера и выдаём актуальные права из матрицы
                IF @DryRun = 0
                    EXEC sp_executesql @SQL;
                ELSE
                    PRINT N'    [GRANT]   ' + @SQL;

                SET @LogStatus = CASE WHEN @DryRun=1 THEN N'DRY_RUN' ELSE N'OK' END;
                INSERT INTO dbo.tUserProvisioningLog
                    (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, @DbName, N'PROVISION', @LogStatus,
                        N'DbType=' + @DbType
                        + N' | Roles='   + ISNULL(@DbRoles, N'')
                        + N' | Exec='    + CAST(@GrantExec    AS NCHAR(1))
                        + N' | ViewDef=' + CAST(@GrantViewDef AS NCHAR(1)));

                PRINT @Prefix + N'  [v] ' + @DbName
                    + N' (' + @DbType + N') -> ' + ISNULL(@DbRoles, N'—')
                    + CASE WHEN @GrantExec    = 1 THEN N' + EXECUTE'         ELSE N'' END
                    + CASE WHEN @GrantViewDef = 1 THEN N' + VIEW DEFINITION' ELSE N'' END;

            END TRY
            BEGIN CATCH
                INSERT INTO dbo.tUserProvisioningLog
                    (RunId, LoginName, Scope, Action, Status, Details)
                VALUES (@RunId, @LoginName, @DbName, N'PROVISION', N'ERROR', ERROR_MESSAGE());
                PRINT N'  [!] ОШИБКА в [' + @DbName + N']: ' + ERROR_MESSAGE();
            END CATCH;

            FETCH NEXT FROM cur_DB INTO @DbName, @IsDev;
        END -- while cur_DB

        CLOSE cur_DB;
        DEALLOCATE cur_DB;

        FETCH NEXT FROM cur_Users INTO @LoginName, @LoginType, @SqlPassword, @Category;
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

                SET @CleanupSQL =
                    N'USE ' + QUOTENAME(@DbName) + N';' + NCHAR(10)
                  + N'IF EXISTS (SELECT 1 FROM sys.database_principals' + NCHAR(10)
                  + N'           WHERE name = N' + QUOTENAME(@LoginName, N'''') + NCHAR(10)
                  + N'             AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)
                  + CASE WHEN @HasOwner = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_owner'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_owner] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END
                  + CASE WHEN @HasReader = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datareader'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datareader] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END
                  + CASE WHEN @HasWriter = 0 THEN
                        N'    IF IS_ROLEMEMBER(''db_datawriter'', N' + QUOTENAME(@LoginName, N'''') + N') = 1' + NCHAR(10)
                      + N'        ALTER ROLE [db_datawriter] DROP MEMBER ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END
                  + CASE WHEN @GrantExec = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''EXECUTE''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + QUOTENAME(@LoginName, N'''') + N'))' + NCHAR(10)
                      + N'        REVOKE EXECUTE FROM ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                    ELSE N'' END
                  + CASE WHEN @GrantViewDef = 0 THEN
                        N'    IF EXISTS (SELECT 1 FROM sys.database_permissions' + NCHAR(10)
                      + N'               WHERE class = 0 AND state IN (''G'',''W'') AND permission_name = ''VIEW DEFINITION''' + NCHAR(10)
                      + N'                 AND grantee_principal_id = DATABASE_PRINCIPAL_ID(N' + QUOTENAME(@LoginName, N'''') + N'))' + NCHAR(10)
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
                  + N'              WHERE name = N' + QUOTENAME(@LoginName, N'''') + N' AND type IN (''U'',''S'',''G''))' + NCHAR(10)
                  + N'BEGIN' + NCHAR(10)
                  + N'    CREATE USER ' + QUOTENAME(@LoginName) + N' FOR LOGIN ' + QUOTENAME(@LoginName) + N';' + NCHAR(10)
                  + N'END' + NCHAR(10)
                  -- Фиксируем orphaned user (SID рассинхронизирован) — тот же фикс что в spProvisionUsers
                  + N'ELSE IF EXISTS (' + NCHAR(10)
                  + N'    SELECT 1 FROM sys.database_principals dp' + NCHAR(10)
                  + N'    WHERE  dp.name = N' + QUOTENAME(@LoginName, N'''') + NCHAR(10)
                  + N'      AND  dp.sid <> SUSER_SID(N' + QUOTENAME(@LoginName, N'''') + N')' + NCHAR(10)
                  + N'      AND  SUSER_SID(N' + QUOTENAME(@LoginName, N'''') + N') IS NOT NULL' + NCHAR(10)
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
-- [8] ОЧИСТКА СПИСКА БАЗ (разовый прогон)
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
INSERT INTO dbo.tUserProvisioningList (LoginName, LoginType, Category, Notes)
SELECT v.LoginName, v.LoginType, v.Category, v.Notes
FROM (VALUES
    -- DB Admin
    ('iippolitov@powbee.ru',  'SQL', 'DB Admin',           NULL),
    ('sippolitova@powbee.ru', 'SQL', 'DB Admin',           NULL),
    ('dulich@powbee.ru',      'SQL', 'DB Admin',           NULL),
    -- Dev Team
    ('aseleznev@powbee.ru',   'SQL', 'Dev Team',           NULL),
    ('agavrilov@powbee.ru',   'SQL', 'Dev Team',           NULL),
    ('amarenkov@powbee.ru',   'SQL', 'Dev Team',           NULL),
    ('dklochkov@powbee.ru',   'SQL', 'Dev Team',           NULL),
    ('rvorobyev@powbee.ru',   'SQL', 'Dev Team',           NULL),
    -- Data Engineer Team
    ('omarkgraf@powbee.ru',   'SQL', 'Data Engineer Team', NULL),
    ('ytirtsov@powbee.ru',    'SQL', 'Data Engineer Team', NULL),
    ('dkorneev@powbee.ru',    'SQL', 'Data Engineer Team', NULL),
    ('iyakovlev@powbee.ru',   'SQL', 'Data Engineer Team', NULL),
    -- QA
    ('vshuvaev@powbee.ru',    'SQL', 'QA',                 NULL),
    ('mbardyuzhina@powbee.ru','SQL', 'QA',                 NULL),
    -- Support Senior
    ('akizilbashev@powbee.ru','SQL', 'Support Senior',     NULL)
) AS v (LoginName, LoginType, Category, Notes)
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.tUserProvisioningList t
    WHERE  t.LoginName = v.LoginName
);

-- !! ОБЯЗАТЕЛЬНО перед запуском spProvisionUsers задай пароли !!
-- SQL-логин без пароля упадёт с ошибкой при создании.
--
-- Вариант 1: задать каждому индивидуально
-- UPDATE dbo.tUserProvisioningList SET SqlPassword = N'P@ssw0rd_2024!' WHERE LoginName = 'iippolitov@powbee.ru';
-- UPDATE dbo.tUserProvisioningList SET SqlPassword = N'...' WHERE LoginName = 'sippolitova@powbee.ru';
-- ...
--
-- Вариант 2: временно поставить один пароль всем (потом сменить)
-- UPDATE dbo.tUserProvisioningList SET SqlPassword = N'Temp_P@ss_2024!' WHERE SqlPassword IS NULL;

-- SQL-логин (сервисный аккаунт):
INSERT INTO dbo.tUserProvisioningList (LoginName, LoginType, SqlPassword, Category, Notes) VALUES
    ('svc_reporting', 'SQL', 'P@ssw0rd_Str0ng!1', 'Data Engineer Team', 'Аккаунт отчётов');

-- Деактивировать пользователя (не удалять, просто не трогать):
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
VALUES ('vshuvaev@powbee.ru', 'Mindbox', 'db_datareader|db_datawriter',
        'Временно выдан write для исследования инцидента');

-- Деактивировать исключение (не удалять):
UPDATE dbo.tPermissionOverride
SET    IsActive = 0, UpdatedAt = SYSDATETIME()
WHERE  LoginName = 'vshuvaev@powbee.ru' AND DbName = 'Mindbox';

-- Удалить исключение насовсем:
DELETE FROM dbo.tPermissionOverride
WHERE  LoginName = 'vshuvaev@powbee.ru' AND DbName = 'Mindbox';


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
WHERE  LoginName = 'vshuvaev@powbee.ru'
ORDER  BY RunAt DESC;

-- Очистить журнал:
-- TRUNCATE TABLE dbo.tUserProvisioningLog;


-- ── Если tProvisioningServerConfig осталась с прошлой версии ─────────
-- Она больше не используется, можно удалить:
-- DROP TABLE IF EXISTS [master].dbo.tProvisioningServerConfig;

*/