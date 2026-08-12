# Проект: pbeadmin — админка CRM

| | |
|---|---|
| **Что это** | Система администрирования CRM: мастер-данные, справочники, кампании, интеграции |
| **Потребитель** | Сотрудники фармкомпании-заказчика (админы, маркетинг, аналитика) |
| **Репозиторий** | `/Users/amlung/Documents/Powbee/pbeadmin` |
| **Источник истины по коду** | [pbeadmin/CLAUDE.md](../../../pbeadmin/CLAUDE.md) |
| **Владелец от бизнеса** | TODO |
| **Ведущий от ИТ** | Селезнев Алексей — отдел разработки «Админки», 4 чел. |
| **Режим поддержки** | TODO |

## Назначение

Серверная часть и админ-интерфейс продукта. Ведёт мастер-данные HCP/HCO,
согласия, промо-циклы, рассылки и интеграции с внешними системами. Является
источником данных для мобильного приложения [pbeapp](pbeapp.md).

Важно: это **не** CRM «визитов/врачей/аптек» в бытовом смысле. Основные
сущности — `Person` (HCP), `Org` (HCO), `Consent`, `Campaign`/`PromoCycle`,
`Staff`/`User`, `Message`/`Template`, `DrugBrand`/`DrugSKU`, `Survey`.

## Стек

| Слой | Технологии |
|---|---|
| Backend | PHP 8.2, Laravel 8 |
| БД | MSSQL (драйвер `sqlsrv`), database-first, именованные схемы, паттерн «Fields» (данные в JSON-колонке) |
| Auth | JWT (`php-open-source-saver/jwt-auth`), SSO через Socialite: Keycloak / Azure / SAML2 |
| Frontend | AdminLTE 3, Bootstrap 4, jQuery, DataTables, select2, точечно Vue 3 (без сборки) |
| Сборка фронта | Laravel Mix 6 |
| API | OpenAPI / Swagger (`l5-swagger`) |
| Тесты | PHPUnit — контрактные unit + Feature-тесты модулей на dev-БД |

## Модульная архитектура

23 модуля на nwidart (`modules/<Module>`), **каждый — отдельный git-сабмодуль**
(`github.com:powbee/pbeadmin_*`):

`Bi`, `Bots`, `Consent`, `ContentFactory`, `CustomEvents`, `CustomNotifications`,
`Events`, `Export`, `Id360`, `IntegrationExternalSite`, `Mdlp`, `Mindbox`, `Omni`,
`OnPoint`, `OneKey`, `PersonalAccount`, `PharmacyChainContract`,
`PharmacyChainManagement`, `PharmacyStock`, `Planning`, `Presentations`, `Survey`, `Task`.

Управленческое следствие: релиз ядра и релизы модулей развязаны, указатели
сабмодулей двигаются вручную. Это точка риска — рассинхрон указателя ломает
сборку (известный случай: `modules/Export`).

## Внешние интеграции

IQVIA OneKey, IQVIA ID360, МДЛП, Mindbox, SAP, внешний сайт заказчика.
Каждая интеграция — отдельный модуль, отдельный контур ответственности и
отдельный риск при недоступности контрагента.

## Риски и открытые вопросы

- Laravel 8 — версия вне активной поддержки; апгрейд не запланирован. TODO: решение.
- Две MSSQL-базы (основная + адресный справочник `address`).
- Полный прогон `phpunit` ходит в общую dev-БД — безопасен только `--testsuite Unit`.
