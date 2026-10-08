-- Use the right-half crop for the ADN-512 poster/thumbnail.
UPDATE movies
SET poster_url='/assets/posters/adn-512.png', updated_at=datetime('now')
WHERE slug='adn-512';
