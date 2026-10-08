-- Reuse the ADN-512 backdrop for the card poster/thumbnail as requested.
UPDATE movies
SET poster_url='/assets/backdrops/adn-512.png', updated_at=datetime('now')
WHERE slug='adn-512';
