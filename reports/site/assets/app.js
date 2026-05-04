(function () {
  let DATA = [];
  const tbody = document.querySelector('#grid tbody');
  const q = document.getElementById('q');
  const cat = document.getElementById('category');
  const rank = document.getElementById('rank');
  const panel = document.getElementById('panel');
  const hasshot = document.getElementById('hasshot');
  const count = document.getElementById('count');
  const ths = document.querySelectorAll('thead th');
  let sortKey = 'tranco_rank';
  let sortAsc = true;

  function rankBucket(r) {
    if (r == null) return 'unranked';
    if (r <= 1000) return 'top-1k';
    if (r <= 10000) return 'top-10k';
    if (r <= 100000) return 'top-100k';
    return 'top-1m';
  }

  function passes(d) {
    const term = (q.value || '').trim().toLowerCase();
    if (term) {
      const blob = (
        (d.domain || '') + ' ' + (d.category || '') + ' ' +
        (d.theme || '') + ' ' + (d.sector_tags || []).join(' ')
      ).toLowerCase();
      if (!blob.includes(term)) return false;
    }
    if (cat.value && d.category !== cat.value) return false;
    if (rank.value) {
      if (rank.value === 'unranked' && d.tranco_rank != null) return false;
      else if (rank.value === 'ranked' && d.tranco_rank == null) return false;
      else if (rank.value !== 'ranked' && rank.value !== 'unranked') {
        if (rankBucket(d.tranco_rank) !== rank.value && !(rank.value === 'top-10k' && d.tranco_rank <= 10000)
            && !(rank.value === 'top-100k' && d.tranco_rank <= 100000)
            && !(rank.value === 'top-1m' && d.tranco_rank <= 1000000)) {
          // strict bucket OR cumulative — keep cumulative behavior
          if (d.tranco_rank == null) return false;
          if (rank.value === 'top-1k' && d.tranco_rank > 1000) return false;
          if (rank.value === 'top-10k' && d.tranco_rank > 10000) return false;
          if (rank.value === 'top-100k' && d.tranco_rank > 100000) return false;
          if (rank.value === 'top-1m' && d.tranco_rank > 1000000) return false;
        }
      }
    }
    if (panel && panel.value) {
      if (panel.value === '__none__') {
        if (d.host_panel) return false;
      } else if (d.host_panel !== panel.value) {
        return false;
      }
    }
    if (hasshot.checked && !d.screenshot) return false;
    return true;
  }

  function cmp(a, b, key, asc) {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;  // nulls last
    if (bv == null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return asc ? av - bv : bv - av;
    return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  }

  function escape(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function render() {
    const filtered = DATA.filter(passes).sort((a, b) => cmp(a, b, sortKey, sortAsc));
    count.textContent = filtered.length + ' of ' + DATA.length;
    let html = '';
    for (const d of filtered) {
      const rankCell = d.tranco_rank == null ? '—' : d.tranco_rank.toLocaleString();
      const detail = 'domain/' + encodeURIComponent(d.domain) + '.html';
      const panelCell = d.host_panel
        ? '<span class="tag tag-' + escape(d.host_panel) + '">' + escape(d.host_panel) + '</span>'
        : '—';
      html += '<tr data-href="' + escape(detail) + '">'
        + '<td><a href="' + escape(detail) + '">' + escape(d.domain) + '</a></td>'
        + '<td class="num">' + rankCell + '</td>'
        + '<td class="num">' + (d.score == null ? '—' : d.score) + '</td>'
        + '<td>' + escape(d.category || '—') + '</td>'
        + '<td>' + escape(d.theme || '—') + '</td>'
        + '<td class="num">' + (d.plugin_count == null ? 0 : d.plugin_count) + '</td>'
        + '<td>' + panelCell + '</td>'
        + '</tr>';
    }
    tbody.innerHTML = html;
    ths.forEach(th => {
      const k = th.getAttribute('data-key');
      th.setAttribute('aria-sort', k === sortKey ? (sortAsc ? 'asc' : 'desc') : 'none');
    });
  }

  ths.forEach(th => {
    th.addEventListener('click', () => {
      const k = th.getAttribute('data-key');
      if (sortKey === k) sortAsc = !sortAsc;
      else { sortKey = k; sortAsc = th.hasAttribute('data-num') ? true : true; }
      render();
    });
  });
  [q, cat, rank, panel, hasshot].forEach(el => el && el.addEventListener('input', render));
  tbody.addEventListener('click', (e) => {
    const tr = e.target.closest('tr');
    if (tr && !e.target.closest('a')) {
      const href = tr.getAttribute('data-href');
      if (href) window.location.href = href;
    }
  });

  fetch('data.json').then(r => r.json()).then(d => { DATA = d; render(); });
})();
