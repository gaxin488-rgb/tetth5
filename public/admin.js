(() => {
  const $ = selector => document.querySelector(selector);
  const tokenInput = $('#token');
  const status = $('#status');
  const list = $('#movieList');
  const movieSelect = $('#subtitleMovieId');
  tokenInput.value = sessionStorage.getItem('cinezero_admin_token') || '';

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const auth = () => ({ authorization: `Bearer ${tokenInput.value.trim()}`, 'content-type': 'application/json' });

  function toast(message) {
    const element = $('#toast');
    element.textContent = message;
    element.classList.add('show');
    setTimeout(() => element.classList.remove('show'), 2200);
  }

  async function load() {
    sessionStorage.setItem('cinezero_admin_token', tokenInput.value.trim());
    status.textContent = 'Đang kết nối…';
    try {
      const response = await fetch('/api/admin/movies', { headers: auth() });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      status.textContent = `Đã kết nối · ${data.movies.length} phim`;
      render(data.movies);
      populateMovieSelect(data.movies);
    } catch (error) {
      status.textContent = `Lỗi: ${error.message}`;
      list.innerHTML = '<div class="empty-state">Không thể tải dữ liệu. Kiểm tra D1 và ADMIN_TOKEN.</div>';
      movieSelect.innerHTML = '<option value="">Không tải được danh sách phim</option>';
    }
  }

  function populateMovieSelect(movies) {
    movieSelect.innerHTML = movies.map(movie => `<option value="${movie.id}">${esc(movie.title)} · ${esc(movie.slug)}</option>`).join('') || '<option value="">Chưa có phim</option>';
  }

  function render(movies) {
    list.innerHTML = movies.map(movie => `<div class="admin-item"><img src="${esc(movie.poster_url)}" alt=""><div><b>${esc(movie.title)}</b><div class="status">${esc(movie.slug)} · ${esc(movie.status)}</div></div><button class="button secondary" data-delete="${movie.id}">Xóa</button></div>`).join('') || '<div class="empty-state">Chưa có phim.</div>';
    document.querySelectorAll('[data-delete]').forEach(button => {
      button.onclick = async () => {
        if (!confirm('Xóa phim này?')) return;
        const response = await fetch(`/api/admin/movies/${button.dataset.delete}`, { method: 'DELETE', headers: auth() });
        const data = await response.json();
        if (!response.ok) return toast(data.error || 'Xóa thất bại');
        toast('Đã xóa');
        load();
      };
    });
  }

  $('#connect').onclick = load;
  $('#reload').onclick = load;

  $('#movieForm').onsubmit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = Object.fromEntries(form.entries());
    body.release_year = Number(body.release_year);
    body.duration_minutes = Number(body.duration_minutes);
    body.genres = String(body.genres).split(',').map(value => value.trim()).filter(Boolean);
    body.slug = body.slug.trim() || body.title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/đ/g, 'd').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const response = await fetch('/api/admin/movies', { method: 'POST', headers: auth(), body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) return toast(data.error || 'Tạo phim thất bại');
    toast('Đã thêm phim');
    event.currentTarget.reset();
    load();
  };

  $('#subtitleForm').onsubmit = async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget).entries());
    body.movie_id = Number(body.movie_id);
    const response = await fetch('/api/admin/subtitles', { method: 'POST', headers: auth(), body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) return toast(data.error || 'Đăng ký phụ đề thất bại');
    toast(`Đã gắn phụ đề: ${data.url}`);
    event.currentTarget.reset();
  };
})();
