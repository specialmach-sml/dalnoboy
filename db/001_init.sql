BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TYPE user_role AS ENUM ('admin', 'carrier', 'dispatcher');
CREATE TYPE invite_status AS ENUM ('pending', 'used', 'expired', 'revoked');
CREATE TYPE response_status AS ENUM ('pending', 'accepted', 'rejected', 'cancelled');
CREATE TYPE deal_status AS ENUM ('agreed', 'loading', 'on_route', 'completed', 'cancelled', 'dispute');
CREATE TYPE route_priority AS ENUM ('primary', 'secondary');

CREATE TABLE body_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE load_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE payment_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE feature_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE permit_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE cargo_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE direction_scopes (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE price_types (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE truck_statuses (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL UNIQUE
);

CREATE TABLE subscription_plans (
    id SMALLSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name_ru TEXT NOT NULL,
    price_minor BIGINT NOT NULL DEFAULT 0,
    truck_limit INT NOT NULL DEFAULT 1,
    cargo_limit INT NOT NULL DEFAULT 1,
    user_limit INT NOT NULL DEFAULT 1,
    priority_rank SMALLINT NOT NULL DEFAULT 0
);

CREATE TABLE cities (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    country_code VARCHAR(2) NOT NULL,
    region TEXT,
    name TEXT NOT NULL,
    geom GEOGRAPHY(Point, 4326) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_cities_name ON cities(name);
CREATE INDEX idx_cities_geom ON cities USING GIST (geom);

INSERT INTO body_types (code, name_ru) VALUES
('tent', 'Тент'),
('reefer', 'Рефрижератор'),
('isotherm', 'Изотерм'),
('board', 'Бортовой'),
('container', 'Контейнеровоз'),
('car_carrier', 'Автовоз'),
('lowbed', 'Трал / негабарит'),
('van', 'Цельнометалл'),
('light', 'До 3.5 т');

INSERT INTO load_types (code, name_ru) VALUES
('rear', 'Задняя'),
('side', 'Боковая'),
('top', 'Верхняя');

INSERT INTO payment_types (code, name_ru) VALUES
('cash', 'Наличные'),
('bank_vat', 'Безнал с НДС'),
('bank_no_vat', 'Безнал без НДС'),
('deferred', 'Отсрочка'),
('any', 'Любой вариант');

INSERT INTO feature_types (code, name_ru) VALUES
('hydrolift', 'Гидроборт'),
('straps', 'Ремни'),
('chains', 'Цепи'),
('stanchions', 'Коники'),
('fasteners', 'Крепёж');

INSERT INTO permit_types (code, name_ru) VALUES
('adr', 'ADR'),
('cmr_tir', 'CMR / TIR'),
('food', 'Пищевые сертификаты'),
('oversized', 'Негабаритный допуск');

INSERT INTO cargo_types (code, name_ru) VALUES
('food', 'Продукты'),
('frozen', 'Заморозка'),
('drinks', 'Напитки'),
('construction', 'Стройматериалы'),
('metal', 'Металл'),
('wood', 'Лес'),
('machinery', 'Техника'),
('container', 'Контейнер'),
('dangerous', 'Опасный груз'),
('groupage', 'Сборный груз'),
('furniture', 'Мебель'),
('other', 'Другое');

INSERT INTO direction_scopes (code, name_ru) VALUES
('local', 'Город / область'),
('intercity', 'Межгород'),
('ru', 'По России'),
('ru_cis', 'Россия + СНГ'),
('any', 'Любые направления');

INSERT INTO price_types (code, name_ru) VALUES
('fixed', 'Фиксированная ставка'),
('per_km', 'За км'),
('negotiable', 'Договорная'),
('auction', 'Предложите цену');

INSERT INTO truck_statuses (code, name_ru) VALUES
('free', 'Свободна'),
('prebook', 'Предварительная договорённость'),
('on_trip', 'В рейсе'),
('busy', 'Занята'),
('inactive', 'Неактивна');

INSERT INTO subscription_plans
(code, name_ru, price_minor, truck_limit, cargo_limit, user_limit, priority_rank)
VALUES
('start', 'Старт', 0, 1, 1, 2, 0),
('business', 'Бизнес', 990000, 10, 50, 10, 1),
('enterprise', 'Enterprise', 4990000, 100000, 100000, 100000, 2);

COMMIT;
