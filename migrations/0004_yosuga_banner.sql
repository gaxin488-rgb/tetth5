-- Promote the supplied Yosuga no Sora artwork to the homepage banner.
UPDATE movies SET featured=0 WHERE slug='hanh-trinh-sao-bang';
UPDATE movies SET backdrop_url='/assets/banners/yosuga-no-sora.jpg',featured=1,updated_at=datetime('now') WHERE slug='yosuga-no-sora';
