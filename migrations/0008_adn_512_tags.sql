-- Add the requested discovery tags to ADN-512.
UPDATE movies
SET tags='Drama||NTR', updated_at=datetime('now')
WHERE slug='adn-512';
