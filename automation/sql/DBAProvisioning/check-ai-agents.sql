/*
========================================================================
  ПРОВЕРКА УЧЁТОК ИИ-АГЕНТОВ — действительно ли они только на чтение

  Запускать на сервере, где заведены учётки категории [AI Agent],
  ПОД УЧЁТНОЙ ЗАПИСЬЮ DBA. Ничего не меняет: только смотрит и печатает.

  Через db-query.sh он не пройдёт, и это правильно: у агента нет доступа
  к базе [DBAProvisioning] — проверено, `Cannot open database`. Учётка,
  которую проверяют, не должна уметь читать таблицы провижининга: там до
  первого прогона лежат пароли остальных агентов.

  Зачем. Обёртка db-query.sh больше не проверяет текст запроса — она
  подставляет логин и пароль, и всё. Единственная гарантия «агент не
  напишет в боевую базу» — права самой учётной записи. Значит эту
  гарантию надо не предполагать, а проверять: после смены матрицы,
  после ручной правки прав, после добавления новой базы.

  Пустой результат = всё в порядке. Каждая строка = нарушение.

  Что считается нарушением:
    · членство в любой серверной роли, кроме public;
    · членство в ролях базы, кроме db_datareader и public;
    · выданное право, кроме SELECT, VIEW DEFINITION и CONNECT;
    · логин не SQL-типа или его нет на сервере вовсе.

  Разумно повесить на регулярный прогон — раз в неделю хватит.
========================================================================
*/

SET NOCOUNT ON;

IF OBJECT_ID('tempdb..#Issues') IS NOT NULL DROP TABLE #Issues;
CREATE TABLE #Issues
(
    LoginName NVARCHAR(256),
    Scope     NVARCHAR(128),
    Issue     NVARCHAR(200),
    Detail    NVARCHAR(500)
);

DECLARE @LoginName NVARCHAR(256), @DbName NVARCHAR(128), @SQL NVARCHAR(MAX);

DECLARE cur_Agents CURSOR LOCAL FAST_FORWARD FOR
    SELECT LoginName
    FROM   [DBAProvisioning].dbo.tUserProvisioningList
    WHERE  Category = N'AI Agent' AND IsActive = 1;

OPEN cur_Agents;
FETCH NEXT FROM cur_Agents INTO @LoginName;

WHILE @@FETCH_STATUS = 0
BEGIN
    -- ── Логин вообще есть и он SQL-типа? ─────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM sys.server_principals
                   WHERE name = @LoginName AND type = 'S')
        INSERT #Issues VALUES (@LoginName, N'SERVER', N'Логина нет или он не SQL-типа',
                               N'Ожидался SQL-логин; проверь spProvisionUsers');

    -- ── Серверные роли: допустима только public ──────────────────────
    INSERT #Issues
    SELECT @LoginName, N'SERVER', N'Членство в серверной роли', r.name
    FROM   sys.server_role_members m
    JOIN   sys.server_principals   r ON r.principal_id = m.role_principal_id
    JOIN   sys.server_principals   p ON p.principal_id = m.member_principal_id
    WHERE  p.name = @LoginName AND r.name <> N'public';

    -- ── По каждой базе из списка провижининга ────────────────────────
    DECLARE cur_DB CURSOR LOCAL FAST_FORWARD FOR
        SELECT d.DbName
        FROM   [DBAProvisioning].dbo.tDatabases d
        WHERE  d.IsActive = 1
          AND  EXISTS (SELECT 1 FROM sys.databases s
                       WHERE s.name = d.DbName AND s.state_desc = N'ONLINE');

    OPEN cur_DB;
    FETCH NEXT FROM cur_DB INTO @DbName;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @SQL = N'
        USE ' + QUOTENAME(@DbName) + N';
        IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @L)
        BEGIN
            -- Роли базы: допустимы db_datareader и public
            INSERT #Issues
            SELECT @L, @D, N''Членство в роли базы'', r.name
            FROM   sys.database_role_members m
            JOIN   sys.database_principals   r ON r.principal_id = m.role_principal_id
            JOIN   sys.database_principals   p ON p.principal_id = m.member_principal_id
            WHERE  p.name = @L AND r.name NOT IN (N''db_datareader'', N''public'');

            -- Права: допустимы SELECT, VIEW DEFINITION, CONNECT.
            -- state G — выдано, W — выдано с правом передачи (тем более лишнее).
            INSERT #Issues
            SELECT @L, @D, N''Выдано право'' ,
                   perm.permission_name + N'' ('' + perm.class_desc + N'')''
            FROM   sys.database_permissions perm
            WHERE  perm.grantee_principal_id = DATABASE_PRINCIPAL_ID(@L)
              AND  perm.state IN (''G'', ''W'')
              AND  perm.permission_name NOT IN (N''SELECT'', N''VIEW DEFINITION'', N''CONNECT'');
        END';

        BEGIN TRY
            EXEC sp_executesql @SQL, N'@L NVARCHAR(256), @D NVARCHAR(128)', @LoginName, @DbName;
        END TRY
        BEGIN CATCH
            INSERT #Issues VALUES (@LoginName, @DbName, N'Проверка не выполнена', ERROR_MESSAGE());
        END CATCH;

        FETCH NEXT FROM cur_DB INTO @DbName;
    END

    CLOSE cur_DB; DEALLOCATE cur_DB;
    FETCH NEXT FROM cur_Agents INTO @LoginName;
END

CLOSE cur_Agents; DEALLOCATE cur_Agents;

-- ── Результат ────────────────────────────────────────────────────────
IF EXISTS (SELECT 1 FROM #Issues)
BEGIN
    PRINT '!! Найдены отклонения. Учётки ИИ-агентов не гарантированно read-only.';
    SELECT LoginName, Scope, Issue, Detail FROM #Issues ORDER BY LoginName, Scope, Issue;
END
ELSE
BEGIN
    PRINT 'OK: все активные учётки категории [AI Agent] только на чтение.';
    SELECT LoginName,
           (SELECT COUNT(*) FROM [DBAProvisioning].dbo.tDatabases WHERE IsActive = 1) AS БазВСписке
    FROM   [DBAProvisioning].dbo.tUserProvisioningList
    WHERE  Category = N'AI Agent' AND IsActive = 1
    ORDER  BY LoginName;
END

DROP TABLE #Issues;
