-- Use the supplied ADN-512 artwork as the movie backdrop.
UPDATE movies
SET backdrop_url='/assets/backdrops/adn-512.png', updated_at=datetime('now')
WHERE slug='adn-512';
