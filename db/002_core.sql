BEGIN;

CREATE TABLE companies (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,

    rating NUMERIC(3,2) NOT NULL DEFAULT 0,
    reviews_count INT NOT NULL DEFAULT 0,
    completed_deals_count INT NOT NULL DEFAULT 0,

    verified BOOLEAN NOT NULL DEFAULT FALSE,

    balance_minor BIGINT NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_companies_name ON companies(name)
WHERE deleted_at IS NULL;


CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),

    tg_id BIGINT NOT NULL UNIQUE,
    username TEXT,
    first_name TEXT,
    last_name TEXT,

    name TEXT NOT NULL,
    phone TEXT NOT NULL,

    role user_role NOT NULL,

    rating NUMERIC(3,2) NOT NULL DEFAULT 0,
    reviews_count INT NOT NULL DEFAULT 0,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_users_company_id ON users(company_id)
WHERE deleted_at IS NULL;

CREATE INDEX idx_users_tg_id ON users(tg_id)
WHERE deleted_at IS NULL;


CREATE TABLE invites (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),
    created_by_user_id BIGINT NOT NULL REFERENCES users(id),

    code TEXT NOT NULL UNIQUE,

    role_to_assign user_role NOT NULL,
    status invite_status NOT NULL DEFAULT 'pending',

    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_invites_code ON invites(code)
WHERE deleted_at IS NULL;


CREATE TABLE company_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),
    subscription_plan_id SMALLINT NOT NULL REFERENCES subscription_plans(id),

    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ends_at TIMESTAMPTZ NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_company_subscriptions_company
ON company_subscriptions(company_id)
WHERE deleted_at IS NULL;


CREATE TABLE payments (
    id BIGSERIAL PRIMARY KEY,
    public_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    company_id BIGINT NOT NULL REFERENCES companies(id),

    amount_minor BIGINT NOT NULL,
    currency_code VARCHAR(3) NOT NULL DEFAULT 'RUB',

    operation_type TEXT NOT NULL,
    description TEXT,

    external_payment_id TEXT,

    created_by_user_id BIGINT REFERENCES users(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX idx_payments_company_id
ON payments(company_id)
WHERE deleted_at IS NULL;

COMMIT;
