-- People bring things up in conversation without it being an interest, a
-- preference, or a fact about them (remarking on a brand someone is wearing).
-- Extraction kept choosing mentions for these and was downgraded to
-- related_to, and the two-stage edge prompt's catalog feedback asked for
-- person -> organization mentions directly. Widen the subject to people.

UPDATE relation_type
SET allowed_from_types = '{document,person}',
    description = 'The document or person refers to the object in passing, '
        'without it being the main subject or a lasting interest; use about '
        'for a document''s main subject and interested_in for ongoing interest.'
WHERE name = 'mentions';
