CREATE TABLE IF NOT EXISTS phone_verification_codes (
  id bigserial PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  phone text NOT NULL,
  code_hash text NOT NULL,
  attempts integer DEFAULT 0,
  created_at timestamp DEFAULT now(),
  expires_at timestamp NOT NULL,
  used_at timestamp
);

CREATE INDEX IF NOT EXISTS idx_phone_verification_codes_user
ON phone_verification_codes(user_id);

CREATE INDEX IF NOT EXISTS idx_phone_verification_codes_active
ON phone_verification_codes(user_id, phone, expires_at)
WHERE used_at IS NULL;
