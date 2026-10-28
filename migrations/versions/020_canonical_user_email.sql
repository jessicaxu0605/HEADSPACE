-- Email is the account credential. Earlier auth work introduced a separate
-- required `login` column even though the product collects and authenticates an
-- email address. Consolidate both generations of rows before removing `login`.

UPDATE users
SET email = COALESCE(email, login)
WHERE email IS NULL;

ALTER TABLE users ALTER COLUMN email SET NOT NULL;

DROP INDEX IF EXISTS users_login_unique_idx;
ALTER TABLE users DROP COLUMN IF EXISTS login;

CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx
  ON users (lower(email));
