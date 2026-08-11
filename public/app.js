(() => {
  'use strict';
  const app = document.querySelector('#app');
  const toast = document.querySelector('#toast');
  const searchPanel = document.querySelector('#searchPanel');
  const searchInput = document.querySelector('#searchInput');
  const suggestions = document.querySelector('#searchSuggestions');
  const mobileMenu = document.querySelector('#mobileMenu');
  let movies = [];
  let toastTimer;
  const subtitleCache = new Map();
  const movieDetailCache = new Map();
  const IS_FILE_PREVIEW = location.protocol === 'file:';
  const assetUrl = value => {
    const url = String(value || '');
    return IS_FILE_PREVIEW && url.startsWith('/') ? `.${url}` : url;
  };

  const store = {
    get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
    set(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
  };

  const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const slugify = value => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/đ/g,'d').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
  const allGenres = () => [...new Set(movies.flatMap(m => Array.isArray(m.genres) ? m.genres : String(m.genres || '').split('||')))].filter(Boolean);
  const meta = m => `${m.release_year || '—'} · ${m.type === 'series' ? `${m.episode_count || '?'} tập` : `${m.duration_minutes || '?'} phút`}`;
  const watchHref = m => `#watch/${encodeURIComponent(m.slug)}${m.type === 'series' ? '/1' : ''}`;

  function normalizeMovieData(movie) {
    return {
      ...movie,
      poster_url: assetUrl(movie.poster_url),
      backdrop_url: assetUrl(movie.backdrop_url),
      genres: Array.isArray(movie.genres) ? movie.genres : String(movie.genres || '').split('||').filter(Boolean),
      tags: Array.isArray(movie.tags) ? movie.tags : String(movie.tags || '').split('||').filter(Boolean),
      episodes: Array.isArray(movie.episodes) ? movie.episodes.map(episode => ({
        ...episode,
        video_url: assetUrl(episode.video_url)
      })) : []
    };
  }

  function notify(message) {
    clearTimeout(toastTimer); toast.textContent = message; toast.classList.add('show');
    toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
  }

  async function loadMovies() {
    if (IS_FILE_PREVIEW && Array.isArray(window.CINEZERO_SAMPLE_MOVIES)) {
      movies = structuredClone(window.CINEZERO_SAMPLE_MOVIES);
    } else {
      try {
        const response = await fetch('/api/movies', { headers: { accept:'application/json' } });
        if (!response.ok) throw new Error(`API ${response.status}`);
        const payload = await response.json();
        movies = Array.isArray(payload) ? payload : payload.movies;
      } catch (error) {
        try {
          const fallback = await fetch('./data/movies.json');
          movies = await fallback.json();
        } catch {
          movies = structuredClone(window.CINEZERO_SAMPLE_MOVIES || []);
        }
        console.warn('Đang dùng dữ liệu mẫu:', error.message);
      }
    }
    movies = movies.map(normalizeMovieData);
    if (!movies.length) throw new Error('Không có dữ liệu phim mẫu.');
  }

  function isFavorite(slug) { return store.get('cinezero_favorites', []).includes(slug); }
  function toggleFavorite(slug, button) {
    const current = store.get('cinezero_favorites', []);
    const next = current.includes(slug) ? current.filter(x => x !== slug) : [...current, slug];
    store.set('cinezero_favorites', next);
    button?.classList.toggle('active', next.includes(slug));
    button && (button.textContent = next.includes(slug) ? '♥' : '♡');
    notify(next.includes(slug) ? 'Đã thêm vào danh sách' : 'Đã xóa khỏi danh sách');
  }

  function card(m) {
    return `<article class="movie-card" data-title="${esc(m.title)}">
      <div class="poster-wrap">
        <a href="#movie/${encodeURIComponent(m.slug)}" aria-label="Xem chi tiết ${esc(m.title)}"><img src="${esc(m.poster_url)}" alt="Poster ${esc(m.title)}" loading="lazy"></a>
        <span class="quality">${esc(m.quality || 'HD')}</span>
        <span class="card-score">★ ${(7.6 + (Number(m.id || 1) % 18) / 10).toFixed(1)}</span>
        <button class="favorite-button ${isFavorite(m.slug) ? 'active' : ''}" data-favorite="${esc(m.slug)}" aria-label="Thêm ${esc(m.title)} vào danh sách">${isFavorite(m.slug) ? '♥' : '♡'}</button>
        <div class="card-overlay"><div class="overlay-copy"><small>${esc(m.genres.slice(0,2).join(' · '))}</small><a class="play-circle" href="${watchHref(m)}" aria-label="Phát ${esc(m.title)}">▶</a></div></div>
      </div>
      <h3><a href="#movie/${encodeURIComponent(m.slug)}">${esc(m.title)}</a></h3>
      <div class="card-meta"><span>${esc(m.release_year)}</span><span>•</span><span>${m.type === 'series' ? `${esc(m.episode_count || '?')} tập` : `${esc(m.duration_minutes || '?')} phút`}</span></div>
    </article>`;
  }

  function rankedRow(list) {
    return `<section class="section ranked-section"><div class="section-head"><div><span class="section-kicker">BẢNG XẾP HẠNG</span><h2>Top phim hôm nay</h2><p>Những nội dung được chú ý nhất trong thư viện</p></div><a class="text-link" href="#browse/all">Mở bảng phim →</a></div>
      <div class="ranked-row">${list.slice(0,6).map((m,index)=>`<article class="ranked-card"><span class="rank-number">${index+1}</span><div class="rank-poster"><a href="#movie/${encodeURIComponent(m.slug)}"><img src="${esc(m.poster_url)}" alt="Poster ${esc(m.title)}"></a></div><div class="rank-info"><b>${esc(m.title)}</b><small>${esc(m.genres[0] || 'Điện ảnh')} · ${esc(m.release_year)}</small></div></article>`).join('')}</div></section>`;
  }

  function genreShowcase() {
    const genres = allGenres().slice(0,8);
    return `<section class="genre-showcase"><div><span class="section-kicker">CHỌN NHANH</span><h2>Hôm nay bạn muốn xem gì?</h2><p>Đi thẳng tới thể loại hợp tâm trạng của bạn.</p></div><div class="genre-cloud">${genres.map((g,i)=>`<button class="genre-tile genre-${(i%4)+1}" data-home-genre="${esc(g)}"><span>${['✦','⌁','◈','✺'][i%4]}</span>${esc(g)}</button>`).join('')}</div></section>`;
  }

  function row(title, subtitle, list, link = '') {
    return `<section class="section"><div class="section-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div>${link ? `<a class="text-link" href="${link}">Xem tất cả →</a>` : ''}</div>
      <div class="movie-row">${list.map(card).join('')}</div></section>`;
  }

  function home() {
    const featured = movies.find(m => Number(m.featured) === 1) || movies[0];
    const newest = [...movies].sort((a,b) => (b.release_year || 0) - (a.release_year || 0));
    const series = movies.filter(m => m.type === 'series');
    const movieList = movies.filter(m => m.type === 'movie');
    app.innerHTML = `<section class="hero" style="background-image:url('${esc(featured.backdrop_url)}')">
      <div class="hero-noise" aria-hidden="true"></div><div class="hero-content"><span class="eyebrow"><i></i> CINEZERO PREMIERE</span><h1>${esc(featured.title)}</h1>
      <div class="meta"><span class="match">98% phù hợp</span><span>${esc(featured.release_year)}</span><span class="pill">${esc(featured.age_rating)}</span><span class="pill">${esc(featured.quality)}</span><span>${esc(featured.genres.join(' · '))}</span></div>
      <p>${esc(featured.description)}</p><div class="hero-actions"><a class="button primary" href="${watchHref(featured)}"><span class="button-icon">▶</span> Xem ngay</a><a class="button secondary" href="#movie/${encodeURIComponent(featured.slug)}">ⓘ Chi tiết</a></div>
      <div class="hero-stats"><div><b>06</b><span>phim chọn lọc</span></div><div><b>0đ</b><span>chi phí nền tảng</span></div><div><b>24/7</b><span>sẵn sàng online</span></div></div></div>
      <a class="scroll-cue" href="#featuredRows" aria-label="Cuộn xuống nội dung">Khám phá <span>↓</span></a>
    </section><div class="content-wrap home-content" id="featuredRows">
      ${rankedRow(newest)}
      ${genreShowcase()}
      ${row('Mới cập nhật','Nội dung mới nhất trong thư viện',newest,'#browse/all')}
      ${row('Phim bộ đáng xem','Theo dõi từng tập, lưu tiến độ ngay trên trình duyệt',series,'#browse/series')}
      ${row('Phim lẻ chọn lọc','Xem trọn vẹn trong một buổi tối',movieList,'#browse/movie')}
    </div>`;
    bindCards();
    document.querySelectorAll('[data-home-genre]').forEach(button => button.addEventListener('click', () => browse('all', button.dataset.homeGenre)));
  }

  function browse(type = 'all', selectedGenre = '') {
    let list = type === 'all' ? movies : movies.filter(m => m.type === type);
    if (selectedGenre) list = list.filter(m => m.genres.includes(selectedGenre));
    const label = type === 'movie' ? 'Phim lẻ' : type === 'series' ? 'Phim bộ' : 'Tất cả phim';
    app.innerHTML = `<header class="page-top"><span class="eyebrow">Thư viện CineZero</span><h1>${label}</h1><p>${list.length} nội dung đang hiển thị</p>
      <div class="filters"><button class="filter-chip ${!selectedGenre?'active':''}" data-genre="">Tất cả</button>${allGenres().map(g => `<button class="filter-chip ${g===selectedGenre?'active':''}" data-genre="${esc(g)}">${esc(g)}</button>`).join('')}</div></header>
      <div class="content-wrap"><div class="movie-grid">${list.map(card).join('')}</div></div>`;
    document.querySelectorAll('[data-genre]').forEach(button => button.addEventListener('click', () => browse(type, button.dataset.genre)));
    bindCards();
  }

  function detail(slug) {
    const m = movies.find(item => item.slug === slug);
    if (!m) return notFound();
    app.innerHTML = `<section class="detail-hero" style="background-image:url('${esc(m.backdrop_url)}')"><div class="detail-layout">
      <img class="detail-poster" src="${esc(m.poster_url)}" alt="Poster ${esc(m.title)}"><div class="detail-info"><span class="eyebrow">${m.type === 'series' ? 'Phim bộ' : 'Phim lẻ'}</span><h1>${esc(m.title)}</h1><div class="original">${esc(m.original_title || '')}</div>
      <div class="meta"><span>${esc(m.release_year)}</span><span class="pill">${esc(m.age_rating)}</span><span class="pill">${esc(m.quality)}</span><span>${esc(meta(m))}</span></div>
      <p class="description">${esc(m.description)}</p><div class="hero-actions"><a class="button primary" href="${watchHref(m)}">▶ Xem ngay</a><button class="button secondary" id="detailFavorite">${isFavorite(m.slug)?'♥ Đã lưu':'♡ Thêm vào danh sách'}</button></div></div></div></section>
      <section class="detail-body"><div class="section-head"><div><h2>Thông tin phim</h2><p>Nội dung mẫu có thể chỉnh trong D1 hoặc file JSON.</p></div></div><div class="facts">
      <div class="fact"><span>Thể loại</span>${esc(m.genres.join(', '))}</div>${m.tags?.length ? `<div class="fact"><span>Từ khóa</span>${esc(m.tags.join(', '))}</div>` : ''}<div class="fact"><span>Quốc gia</span>${esc(m.country || 'Đang cập nhật')}</div><div class="fact"><span>Thời lượng</span>${esc(meta(m))}</div><div class="fact"><span>Trạng thái</span>${m.status === 'published' ? 'Đã xuất bản' : 'Bản nháp'}</div></div></section>`;
    document.querySelector('#detailFavorite').addEventListener('click', e => { toggleFavorite(m.slug); e.currentTarget.textContent = isFavorite(m.slug)?'♥ Đã lưu':'♡ Thêm vào danh sách'; });
  }

  function parseVtt(text) {
    const blocks = String(text || '').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split(/\n{2,}/);
    const toSeconds = value => {
      const parts = value.split(':').map(Number);
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      return NaN;
    };
    return blocks.map(block => {
      const lines = block.split('\n');
      const timingIndex = lines.findIndex(line => line.includes('-->'));
      if (timingIndex < 0) return null;
      const timing = lines[timingIndex].match(/(\d{2}:\d{2}(?::\d{2})?\.\d{3})\s+-->\s+(\d{2}:\d{2}(?::\d{2})?\.\d{3})/);
      if (!timing) return null;
      const start = toSeconds(timing[1]);
      const end = toSeconds(timing[2]);
      const cueText = lines.slice(timingIndex + 1).join('\n').trim();
      return Number.isFinite(start) && Number.isFinite(end) && cueText ? { start, end, text: cueText } : null;
    }).filter(Boolean);
  }

  async function attachSubtitleOverlay(video, source, nativeTrack) {
    const overlay = document.querySelector('#subtitleOverlay');
    if (!overlay || !source) return;
    try {
      const response = await fetch(source, { cache: 'no-store' });
      if (!response.ok) throw new Error(`VTT ${response.status}`);
      const cues = parseVtt(await response.text());
      const render = () => {
        const nativeCues = nativeTrack?.cues;
        if (nativeCues && nativeCues.length) {
          overlay.textContent = '';
          overlay.classList.remove('visible');
          return;
        }
        const now = Number(video.currentTime || 0);
        const active = cues.filter(cue => now >= cue.start && now < cue.end).map(cue => cue.text).join('\n');
        overlay.textContent = active;
        overlay.classList.toggle('visible', Boolean(active));
      };
      ['loadedmetadata', 'timeupdate', 'seeking', 'seeked'].forEach(eventName => video.addEventListener(eventName, render));
      render();
    } catch (error) {
      console.warn('Không tải được lớp phụ đề dự phòng:', error.message);
    }
  }

  async function loadMovieDetails(slug) {
    if (movieDetailCache.has(slug)) return movieDetailCache.get(slug);
    const current = movies.find(item => item.slug === slug);
    if (IS_FILE_PREVIEW) {
      const local = normalizeMovieData(current || {});
      movieDetailCache.set(slug, local);
      return local;
    }
    try {
      const response = await fetch(`/api/movies/${encodeURIComponent(slug)}`, { headers: { accept: 'application/json' } });
      if (!response.ok) throw new Error(`API ${response.status}`);
      const movie = normalizeMovieData(await response.json());
      movieDetailCache.set(slug, movie);
      const index = movies.findIndex(item => item.slug === slug);
      if (index >= 0) movies[index] = { ...movies[index], ...movie };
      return movie;
    } catch (error) {
      console.warn('Không tải được danh sách tập:', error.message);
      const fallback = normalizeMovieData(current || {});
      movieDetailCache.set(slug, fallback);
      return fallback;
    }
  }

  async function attachSubtitles(video, slug, episodeNumber = 0) {
    if (IS_FILE_PREVIEW || !video?.isConnected) return;
    try {
      const cacheKey = `${slug}:${episodeNumber || 0}`;
      if (!subtitleCache.has(cacheKey)) {
        const query = episodeNumber > 0 ? `?episode=${encodeURIComponent(episodeNumber)}` : '';
        const response = await fetch(`/api/movies/${encodeURIComponent(slug)}/subtitles${query}`, { headers: { accept: 'application/json' } });
        if (!response.ok) throw new Error(`API ${response.status}`);
        const payload = await response.json();
        subtitleCache.set(cacheKey, Array.isArray(payload.subtitles) ? payload.subtitles : []);
      }
      const tracks = subtitleCache.get(cacheKey) || [];
      const status = document.querySelector('#subtitleStatus');
      if (!tracks.length || !video.isConnected) return;
      const hasDefault = tracks.some(track => Boolean(track.is_default));
      let firstSource = '';
      let firstNativeTrack = null;
      tracks.forEach((track, index) => {
        const element = document.createElement('track');
        element.kind = track.kind || 'subtitles';
        const source = track.url || `/media/${String(track.r2_key || '').split('/').map(encodeURIComponent).join('/')}`;
        const version = encodeURIComponent(`${track.id || 'track'}-${track.language_code || 'und'}-${track.r2_key || ''}`);
        element.src = `${source}${source.includes('?') ? '&' : '?'}v=${version}`;
        element.srclang = track.language_code || 'und';
        element.label = track.label || track.language_code || 'Phụ đề';
        element.default = Boolean(track.is_default) || (!hasDefault && index === 0);
        video.appendChild(element);
        if (element.track) element.track.mode = 'showing';
        if (!firstSource) { firstSource = element.src; firstNativeTrack = element.track; }
      });
      attachSubtitleOverlay(video, firstSource, firstNativeTrack);
      if (status) {
        status.hidden = false;
        status.textContent = `Có ${tracks.length} bản phụ đề rời · bật bằng nút CC của trình phát`;
      }
    } catch (error) {
      console.warn('Không tải được phụ đề:', error.message);
    }
  }

  async function watch(slug, requestedEpisode = 0) {
    const m = await loadMovieDetails(slug);
    if (!m || !m.slug) return notFound();
    const episodes = (Array.isArray(m.episodes) ? m.episodes : [])
      .filter(episode => episode.status === undefined || episode.status === 'published')
      .sort((a, b) => Number(a.episode_number || 0) - Number(b.episode_number || 0));
    const selectedEpisode = episodes.length
      ? episodes.find(episode => Number(episode.episode_number) === Number(requestedEpisode)) || episodes[0]
      : null;
    const episodeNumber = selectedEpisode ? Number(selectedEpisode.episode_number) : 0;
    const episodeLabel = selectedEpisode ? `Tập ${String(episodeNumber).padStart(2, '0')}` : '';
    const sourceKey = selectedEpisode?.video_key || m.video_key;
    const sourceUrl = selectedEpisode?.video_url || m.video_url;
    const source = sourceKey && !IS_FILE_PREVIEW ? `/media/${sourceKey.split('/').map(encodeURIComponent).join('/')}` : sourceUrl;
    const watchKey = `${slug}:${episodeNumber || 0}`;
    const progressMap = store.get('cinezero_progress', {});
    const progress = progressMap[watchKey] ?? progressMap[slug] ?? 0;
    const episodePicker = episodes.length > 1
      ? `<div class='episode-picker'><label for='episodeSelect'>Chọn tập</label><select id='episodeSelect'>${episodes.map(episode => `<option value='${Number(episode.episode_number)}' ${Number(episode.episode_number) === episodeNumber ? 'selected' : ''}>${esc(episode.title || `Tập ${String(episode.episode_number).padStart(2, '0')}`)}</option>`).join('')}</select></div>`
      : episodes.length === 1 ? `<span class='episode-count'>${esc(episodeLabel)}</span>` : '';
    app.innerHTML = `<section class='watch-page'><div class='player-shell'><div class='player-wrap'>${source ? `<video id='videoPlayer' controls playsinline preload='metadata' poster='${esc(m.backdrop_url)}'><source src='${esc(source)}' type='video/mp4'>Trình duyệt không hỗ trợ video HTML5.</video><div class='subtitle-overlay' id='subtitleOverlay' aria-live='polite'></div>` : `<div class='player-empty'><div><h2>Chưa có video cho ${esc(episodeLabel || 'phim này')}</h2><p>Hãy upload bản MP4 lên R2 và cập nhật <strong>video_key</strong> của tập trong D1.</p><code>${esc(sourceKey || `episodes/${m.slug}/episode-${String(episodeNumber || 1).padStart(2, '0')}.mp4`)}</code></div></div>`}</div>
      <div class='watch-info'><div><span class='eyebrow'>Đang xem</span><h1>${esc(m.title)}${episodeLabel ? ` · ${esc(episodeLabel)}` : ''}</h1><div class='meta'><span>${esc(m.release_year)}</span><span>${esc(m.quality)}</span><span>${esc(m.genres.join(' · '))}</span></div></div><div class='watch-actions'><a class='button secondary' href='#movie/${encodeURIComponent(m.slug)}'>ⓘ Chi tiết</a><button class='button secondary' id='watchFavorite'>${isFavorite(m.slug) ? '♥ Đã lưu' : '♡ Lưu phim'}</button></div></div>
      ${episodePicker ? `<div class='episode-toolbar'>${episodePicker}</div>` : ''}
      ${source ? `<p class='subtitle-status' id='subtitleStatus' hidden></p>` : ''}
      ${source ? `<div class='progress-card'><div class='section-head'><div><strong>Tiến độ xem ${episodeLabel ? `· ${esc(episodeLabel)}` : ''}</strong><p id='progressText'>Đang đồng bộ trên thiết bị này</p></div></div><div class='progress-track'><div class='progress-value' id='progressValue'></div></div></div>` : ''}</div></section>`;
    document.querySelector('#watchFavorite').addEventListener('click', e => { toggleFavorite(m.slug); e.currentTarget.textContent = isFavorite(m.slug) ? '♥ Đã lưu' : '♡ Lưu phim'; });
    document.querySelector('#episodeSelect')?.addEventListener('change', event => { location.hash = `watch/${encodeURIComponent(slug)}/${event.currentTarget.value}`; });
    const video = document.querySelector('#videoPlayer');
    if (video) {
      const progressValue = document.querySelector('#progressValue');
      const progressText = document.querySelector('#progressText');
      video.addEventListener('loadedmetadata', () => { if (progress > 10 && progress < video.duration - 20) video.currentTime = progress; });
      video.addEventListener('timeupdate', () => {
        if (!video.duration) return;
        progressValue.style.width = `${Math.min(100, video.currentTime / video.duration * 100)}%`;
        progressText.textContent = `${Math.floor(video.currentTime / 60)}:${String(Math.floor(video.currentTime % 60)).padStart(2, '0')} / ${Math.floor(video.duration / 60)}:${String(Math.floor(video.duration % 60)).padStart(2, '0')}`;
      });
      let lastSaved = 0;
      video.addEventListener('timeupdate', () => { if (video.currentTime - lastSaved > 5) { const p = store.get('cinezero_progress', {}); p[watchKey] = Math.floor(video.currentTime); store.set('cinezero_progress', p); lastSaved = video.currentTime; } });
      video.addEventListener('play', () => { if (!IS_FILE_PREVIEW) fetch('/api/view', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ slug }) }).catch(() => {}); });
      attachSubtitles(video, slug, episodeNumber);
    }
  }

  function favorites() {
    const saved = store.get('cinezero_favorites', []);
    const list = movies.filter(m => saved.includes(m.slug));
    app.innerHTML = `<header class="page-top"><span class="eyebrow">Thư viện cá nhân</span><h1>Danh sách của tôi</h1><p>Lưu trực tiếp trong trình duyệt, không cần tài khoản.</p></header><div class="content-wrap">${list.length ? `<div class="movie-grid">${list.map(card).join('')}</div>` : `<div class="empty-state"><h2>Danh sách đang trống</h2><p>Bấm biểu tượng trái tim trên một phim để lưu tại đây.</p><a class="button primary" href="#home">Khám phá phim</a></div>`}</div>`;
    bindCards();
  }

  function searchResults(query) {
    const q = query.trim().toLocaleLowerCase('vi');
    const list = movies.filter(m => [m.title,m.original_title,m.country,...m.genres].join(' ').toLocaleLowerCase('vi').includes(q));
    app.innerHTML = `<header class="page-top"><span class="eyebrow">Kết quả tìm kiếm</span><h1>“${esc(query)}”</h1><p>Tìm thấy ${list.length} nội dung</p></header><div class="content-wrap">${list.length ? `<div class="movie-grid">${list.map(card).join('')}</div>` : `<div class="empty-state"><h2>Không tìm thấy phim</h2><p>Thử tên khác hoặc chọn một thể loại rộng hơn.</p></div>`}</div>`;
    bindCards();
  }

  function notFound() { app.innerHTML = `<header class="page-top"><span class="eyebrow">404</span><h1>Không tìm thấy trang</h1><p>Liên kết có thể đã thay đổi.</p><a class="button primary" href="#home">Về trang chủ</a></header>`; }

  function bindCards() {
    document.querySelectorAll('[data-favorite]').forEach(button => button.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); toggleFavorite(button.dataset.favorite, button); }));
  }

  function route() {
    window.scrollTo(0,0); closePanels();
    const hash = location.hash.slice(1) || 'home'; const [name, raw, rawEpisode] = hash.split('/'); const value = decodeURIComponent(raw || '');
    const episode = Number(decodeURIComponent(rawEpisode || '')) || 0;
    document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === (name === 'browse' ? value : name)));
    if (name === 'home') home(); else if (name === 'browse') browse(value || 'all'); else if (name === 'movie') detail(value); else if (name === 'watch') watch(value, episode); else if (name === 'favorites') favorites(); else if (name === 'search') searchResults(value); else notFound();
  }

  function closePanels() { searchPanel.hidden = true; mobileMenu.hidden = true; document.querySelector('#menuToggle').setAttribute('aria-expanded','false'); }
  function updateSuggestions() {
    const q = searchInput.value.trim().toLocaleLowerCase('vi');
    if (!q) { suggestions.innerHTML = ''; return; }
    const list = movies.filter(m => [m.title,...m.genres].join(' ').toLocaleLowerCase('vi').includes(q)).slice(0,5);
    suggestions.innerHTML = list.map(m => `<a class="suggestion" href="#movie/${encodeURIComponent(m.slug)}"><img src="${esc(m.poster_url)}" alt=""><span><b>${esc(m.title)}</b><small>${esc(meta(m))}</small></span></a>`).join('') || '<div class="suggestion">Không có gợi ý phù hợp</div>';
  }

  document.querySelector('#searchToggle').addEventListener('click', () => { mobileMenu.hidden=true; searchPanel.hidden=!searchPanel.hidden; if(!searchPanel.hidden) searchInput.focus(); });
  document.querySelector('#searchClose').addEventListener('click', closePanels);
  document.querySelector('#searchForm').addEventListener('submit', e => { e.preventDefault(); const q=searchInput.value.trim(); if(q) location.hash=`search/${encodeURIComponent(q)}`; });
  searchInput.addEventListener('input', updateSuggestions);
  document.querySelector('#menuToggle').addEventListener('click', e => { searchPanel.hidden=true; mobileMenu.hidden=!mobileMenu.hidden; e.currentTarget.setAttribute('aria-expanded', String(!mobileMenu.hidden)); });
  window.addEventListener('scroll', () => document.querySelector('#siteHeader').classList.toggle('scrolled', scrollY > 30), {passive:true});
  window.addEventListener('hashchange', route);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePanels(); });

  loadMovies().then(route).catch(error => { console.error(error); app.innerHTML='<div class="page-top"><h1>Không thể tải dữ liệu</h1><p>Kiểm tra file public/data/movies.json.</p></div>'; });
})();
