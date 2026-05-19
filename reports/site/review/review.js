(function(){
  let DATA = [];
  const tbody = document.querySelector('#grid tbody');
  const q = document.getElementById('q');
  const cat = document.getElementById('category');
  const srv = document.getElementById('http_server');
  const hasemail = document.getElementById('hasemail');
  const hasphone = document.getElementById('hasphone');
  const outdatedonly = document.getElementById('outdatedonly');
  const compronly = document.getElementById('compronly');
  const hidesuppr = document.getElementById('hidesuppr');
  const count = document.getElementById('count');
  const selectedCount = document.getElementById('selectedCount');
  const ths = document.querySelectorAll('thead th[data-key]');
  const selAll2 = document.getElementById('selAll2');
  let sortKey = 'tranco_rank';
  let sortAsc = true;
  const selected = new Set();

  function passes(d) {
    const term = (q.value||'').trim().toLowerCase();
    if (term) {
      const blob = (d.domain+' '+d.category+' '+d.theme+' '+d.email+' '+d.phones+' '+d.all_emails).toLowerCase();
      if (!blob.includes(term)) return false;
    }
    if (cat.value && d.category !== cat.value) return false;
    if (srv.value && d.http_server !== srv.value) return false;
    if (hasemail.checked && !d.email) return false;
    if (hasphone.checked && !d.phones) return false;
    if (outdatedonly.checked && !d.outdated_flags) return false;
    if (compronly.checked && !d.compromised) return false;
    if (hidesuppr.checked && d.suppressed) return false;
    return true;
  }

  function cmp(a, b, key, asc) {
    const av = a[key], bv = b[key];
    const na = (av==null||av==='');
    const nb = (bv==null||bv==='');
    if (na && nb) return 0;
    if (na) return 1;
    if (nb) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return asc ? av-bv : bv-av;
    return asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  }

  function escape(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":'&#39;'}[c]));}

  function render() {
    const filtered = DATA.filter(passes).sort((a,b)=>cmp(a,b,sortKey,sortAsc));
    count.textContent = filtered.length+' of '+DATA.length;
    const liveSiteBase = window.location.protocol==='file:' ? '' : '/';
    const html = filtered.map(d=>{
      const cls = [];
      if (d.suppressed) cls.push('suppressed');
      else if (d.compromised) cls.push('compromised');
      else if (d.outdated_flags) cls.push('outdated');
      if (selected.has(d.domain)) cls.push('selected');
      const checked = selected.has(d.domain) ? 'checked' : '';
      const rank = d.tranco_rank==null ? '—' : d.tranco_rank.toLocaleString();
      const live = `https://zw-wp-research.vercel.app/domain/${encodeURIComponent(d.domain)}.html`;
      return `<tr class="${cls.join(' ')}" data-domain="${escape(d.domain)}">`
        + `<td class="check-col"><input type="checkbox" class="rowcheck" ${checked}></td>`
        + `<td><strong>${escape(d.domain)}</strong></td>`
        + `<td class="num">${rank}</td>`
        + `<td>${escape(d.category||'—')}</td>`
        + `<td>${escape(d.http_server||'—')}${d.http_version?' '+escape(d.http_version):''}</td>`
        + `<td>${escape(d.wp_version||'—')}</td>`
        + `<td class="flag">${escape((d.outdated_flags||'').replace(/;/g,', '))}${d.compromised?'<br><strong>COMPROMISED</strong>':''}</td>`
        + `<td>${escape(d.email||'—')}</td>`
        + `<td>${escape((d.phones||'').split(';')[0]||'—')}</td>`
        + `<td><a href="${live}" target="_blank" rel="noopener">↗</a></td>`
        + '</tr>';
    }).join('');
    tbody.innerHTML = html;
    ths.forEach(th=>{
      const k = th.getAttribute('data-key');
      th.setAttribute('aria-sort', k===sortKey?(sortAsc?'asc':'desc'):'none');
    });
    selectedCount.textContent = selected.size;
  }

  ths.forEach(th=>th.addEventListener('click',()=>{
    const k=th.getAttribute('data-key');
    if (sortKey===k) sortAsc=!sortAsc;
    else { sortKey=k; sortAsc=true; }
    render();
  }));
  [q,cat,srv,hasemail,hasphone,outdatedonly,compronly,hidesuppr].forEach(el=>el.addEventListener('input',render));

  tbody.addEventListener('change', e=>{
    const cb = e.target;
    if (cb.classList.contains('rowcheck')) {
      const d = cb.closest('tr').getAttribute('data-domain');
      if (cb.checked) selected.add(d);
      else selected.delete(d);
      render();
    }
  });

  document.getElementById('selectAll').addEventListener('click',()=>{
    DATA.filter(passes).forEach(d=>selected.add(d.domain));
    render();
  });
  document.getElementById('selectNone').addEventListener('click',()=>{
    selected.clear();
    render();
  });
  selAll2.addEventListener('change',()=>{
    if (selAll2.checked) DATA.filter(passes).forEach(d=>selected.add(d.domain));
    else DATA.filter(passes).forEach(d=>selected.delete(d.domain));
    render();
  });

  function selectedRows(){ return DATA.filter(d=>selected.has(d.domain)); }

  function downloadBlob(content, name, type){
    const blob = new Blob([content], {type});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=name; document.body.appendChild(a); a.click();
    setTimeout(()=>{URL.revokeObjectURL(url); a.remove();}, 100);
  }

  document.getElementById('exportCsv').addEventListener('click',()=>{
    const rows = selectedRows();
    if (!rows.length) return alert('No rows selected.');
    const cols = ['domain','tranco_rank','category','http_server','http_version','wp_version','outdated_flags','email','all_emails','phones','socials','addresses','compromised','indicator_paths'];
    const csv = [cols.join(',')].concat(
      rows.map(r=>cols.map(c=>{
        let v = r[c]==null?'':String(r[c]);
        if (v.includes(',')||v.includes('"')||v.includes('\n')) v='"'+v.replace(/"/g,'""')+'"';
        return v;
      }).join(','))
    ).join('\n');
    downloadBlob(csv, `outreach_selection_${Date.now()}.csv`, 'text/csv');
  });

  document.getElementById('exportJson').addEventListener('click',()=>{
    const rows = selectedRows();
    if (!rows.length) return alert('No rows selected.');
    downloadBlob(JSON.stringify(rows, null, 2), `outreach_selection_${Date.now()}.json`, 'application/json');
  });

  document.getElementById('copyEmails').addEventListener('click',()=>{
    const rows = selectedRows();
    const emails = rows.map(r=>r.email).filter(Boolean);
    if (!emails.length) return alert('No emails in selection.');
    navigator.clipboard.writeText(emails.join(', ')).then(()=>{
      alert(`Copied ${emails.length} emails to clipboard.`);
    });
  });

  fetch('data.json').then(r=>r.json()).then(d=>{ DATA=d; render(); });
})();
