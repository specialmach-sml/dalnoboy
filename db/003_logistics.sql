BEGIN;

CREATE TYPE listing_status AS ENUM ('draft','active','paused','closed','archived');

CREATE TABLE trucks (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),
    created_by_user_id BIGINT NOT NULL REFERENCES users(id),

    current_city_id BIGINT NOT NULL REFERENCES cities(id),
    direction_scope_id SMALLINT NOT NULL REFERENCES direction_scopes(id),
    body_type_id SMALLINT NOT NULL REFERENCES body_types(id),
    status_id SMALLINT NOT NULL REFERENCES truck_statuses(id),

    listing_status listing_status NOT NULL DEFAULT 'draft',

    capacity_g BIGINT NOT NULL,
    volume_l BIGINT NOT NULL,

    available_from TIMESTAMPTZ,
    adr BOOLEAN NOT NULL DEFAULT FALSE,

    temperature_min SMALLINT,
    temperature_max SMALLINT,

    comment TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_trucks_city_status
ON trucks(current_city_id, status_id)
WHERE deleted_at IS NULL;


CREATE TABLE truck_routes (
    id BIGSERIAL PRIMARY KEY,
    truck_id BIGINT NOT NULL REFERENCES trucks(id),
    from_city_id BIGINT NOT NULL REFERENCES cities(id),
    to_city_id BIGINT NOT NULL REFERENCES cities(id),
    priority route_priority NOT NULL DEFAULT 'secondary',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE truck_load_types (
    truck_id BIGINT NOT NULL REFERENCES trucks(id) ON DELETE CASCADE,
    load_type_id SMALLINT NOT NULL REFERENCES load_types(id),
    PRIMARY KEY (truck_id, load_type_id)
);

CREATE TABLE truck_features (
    truck_id BIGINT NOT NULL REFERENCES trucks(id) ON DELETE CASCADE,
    feature_type_id SMALLINT NOT NULL REFERENCES feature_types(id),
    PRIMARY KEY (truck_id, feature_type_id)
);

CREATE TABLE truck_permits (
    truck_id BIGINT NOT NULL REFERENCES trucks(id) ON DELETE CASCADE,
    permit_type_id SMALLINT NOT NULL REFERENCES permit_types(id),
    PRIMARY KEY (truck_id, permit_type_id)
);


CREATE TABLE cargo (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),
    created_by_user_id BIGINT NOT NULL REFERENCES users(id),

    from_city_id BIGINT NOT NULL REFERENCES cities(id),
    to_city_id BIGINT NOT NULL REFERENCES cities(id),

    cargo_type_id SMALLINT NOT NULL REFERENCES cargo_types(id),
    price_type_id SMALLINT NOT NULL REFERENCES price_types(id),

    listing_status listing_status NOT NULL DEFAULT 'draft',

    required_trucks_count INT NOT NULL DEFAULT 1,
    filled_trucks_count INT NOT NULL DEFAULT 0,

    weight_g BIGINT NOT NULL,
    volume_l BIGINT NOT NULL,

    price_amount_minor BIGINT,
    currency_code VARCHAR(3) NOT NULL DEFAULT 'RUB',

    loading_date TIMESTAMPTZ,
    requires_adr BOOLEAN NOT NULL DEFAULT FALSE,

    temperature_min SMALLINT,
    temperature_max SMALLINT,

    description TEXT,
    comment TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_cargo_route
ON cargo(from_city_id, to_city_id)
WHERE deleted_at IS NULL;


CREATE TABLE cargo_body_types (
    cargo_id BIGINT NOT NULL REFERENCES cargo(id) ON DELETE CASCADE,
    body_type_id SMALLINT NOT NULL REFERENCES body_types(id),
    PRIMARY KEY (cargo_id, body_type_id)
);

CREATE TABLE cargo_load_types (
    cargo_id BIGINT NOT NULL REFERENCES cargo(id) ON DELETE CASCADE,
    load_type_id SMALLINT NOT NULL REFERENCES load_types(id),
    PRIMARY KEY (cargo_id, load_type_id)
);

CREATE TABLE cargo_payment_types (
    cargo_id BIGINT NOT NULL REFERENCES cargo(id) ON DELETE CASCADE,
    payment_type_id SMALLINT NOT NULL REFERENCES payment_types(id),
    PRIMARY KEY (cargo_id, payment_type_id)
);

CREATE TABLE cargo_required_features (
    cargo_id BIGINT NOT NULL REFERENCES cargo(id) ON DELETE CASCADE,
    feature_type_id SMALLINT NOT NULL REFERENCES feature_types(id),
    PRIMARY KEY (cargo_id, feature_type_id)
);

CREATE TABLE cargo_required_permits (
    cargo_id BIGINT NOT NULL REFERENCES cargo(id) ON DELETE CASCADE,
    permit_type_id SMALLINT NOT NULL REFERENCES permit_types(id),
    PRIMARY KEY (cargo_id, permit_type_id)
);


CREATE TABLE responses (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    cargo_id BIGINT NOT NULL REFERENCES cargo(id),
    truck_id BIGINT NOT NULL REFERENCES trucks(id),
    responder_user_id BIGINT NOT NULL REFERENCES users(id),

    message TEXT,
    status response_status NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX uniq_active_response
ON responses(cargo_id, truck_id)
WHERE deleted_at IS NULL;


CREATE TABLE deals (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    response_id BIGINT NOT NULL UNIQUE REFERENCES responses(id),
    cargo_id BIGINT NOT NULL REFERENCES cargo(id),
    truck_id BIGINT NOT NULL REFERENCES trucks(id),

    carrier_company_id BIGINT NOT NULL REFERENCES companies(id),
    dispatcher_company_id BIGINT NOT NULL REFERENCES companies(id),

    agreed_price_minor BIGINT,
    currency_code VARCHAR(3) NOT NULL DEFAULT 'RUB',

    status deal_status NOT NULL DEFAULT 'agreed',

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);


CREATE TABLE reviews (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    deal_id BIGINT NOT NULL REFERENCES deals(id),

    from_company_id BIGINT NOT NULL REFERENCES companies(id),
    to_company_id BIGINT NOT NULL REFERENCES companies(id),

    from_user_id BIGINT NOT NULL REFERENCES users(id),
    to_user_id BIGINT REFERENCES users(id),

    review_type TEXT NOT NULL,

    overall_score SMALLINT NOT NULL CHECK (overall_score BETWEEN 1 AND 5),
    timeliness_score SMALLINT CHECK (timeliness_score BETWEEN 1 AND 5),
    payment_score SMALLINT CHECK (payment_score BETWEEN 1 AND 5),
    communication_score SMALLINT CHECK (communication_score BETWEEN 1 AND 5),
    conditions_match_score SMALLINT CHECK (conditions_match_score BETWEEN 1 AND 5),

    comment TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

COMMIT;
