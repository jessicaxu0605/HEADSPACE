-- Gender is not collected as account profile data. It may still exist as a
-- person-node detail when learned from user-provided memories.

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_gender_not_blank;
ALTER TABLE users DROP COLUMN IF EXISTS gender;
