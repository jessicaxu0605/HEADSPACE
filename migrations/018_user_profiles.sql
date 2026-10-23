-- Optional profile data owned by the user account. A workspace may copy this
-- data into an explicitly requested self node when the workspace is created.

ALTER TABLE users ADD COLUMN IF NOT EXISTS birthday date;
ALTER TABLE users ADD COLUMN IF NOT EXISTS gender text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS about text;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_gender_not_blank;
ALTER TABLE users ADD CONSTRAINT users_gender_not_blank
  CHECK (gender IS NULL OR btrim(gender) <> '');
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_about_not_blank;
ALTER TABLE users ADD CONSTRAINT users_about_not_blank
  CHECK (about IS NULL OR btrim(about) <> '');
