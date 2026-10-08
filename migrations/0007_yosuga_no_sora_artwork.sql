-- Use the supplied Yosuga no Sora artwork for the poster and backdrop.
UPDATE movies
SET poster_url='/assets/posters/yosuga-no-sora.png',
    backdrop_url='/assets/backdrops/yosuga-no-sora.png',
    updated_at=datetime('now')
WHERE slug='yosuga-no-sora';
