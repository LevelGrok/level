/* level: page logic. Reads the desk's public files, renders the wall, runs a read. No framework, no build. */
window.LEVEL = (function () {
  const SAMPLE = window.LEVEL_SAMPLE || location.protocol === 'file:' || /^(localhost|127\.0\.0\.1|0\.0\.0\.0)$/.test(location.hostname) || /\.local$/.test(location.hostname);
  const HOSTS = window.LEVEL_API ? [window.LEVEL_API] : ['https://api.level-bot.tech', 'https://level.162-35-122-197.sslip.io'];
  let API = SAMPLE ? 'sample' : HOSTS[0].replace(/\/$/, '');
  let DEMO = SAMPLE;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = n => { n = +n || 0; return n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e4 ? (n/1e3).toFixed(1)+'K' : String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ','); };
  const usd = n => n == null ? '–' : '$' + (n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : (+n).toFixed(0));
  const ago = ts => { const d = Math.max(0, Math.floor(Date.now()/1000 - ts)); return d < 60 ? d+'s' : d < 3600 ? Math.floor(d/60)+'m' : d < 86400 ? Math.floor(d/3600)+'h' : Math.floor(d/86400)+'d'; };
  const hhmm = ts => new Date(ts * 1000).toISOString().slice(11, 19);
  const short = a => a ? a.slice(0, 4) + '…' + a.slice(-4) : '';
  const MOTION = !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  let LINKS = {};

  function demoBanner(text) {
    if (document.getElementById('demo-banner')) return;
    const d = document.createElement('div'); d.id = 'demo-banner'; d.textContent = text; document.body.prepend(d);
  }
  async function useSampleIfAgentDown() {
    if (API === 'sample') return;
    for (const h of HOSTS) {
      try {
        const c = new AbortController(); const t = setTimeout(() => c.abort(), 4000);
        const r = await fetch(`${h.replace(/\/$/, '')}/state.json`, {cache: 'no-store', signal: c.signal}); clearTimeout(t);
        if (r.ok) { API = h.replace(/\/$/, ''); return; }
      } catch (e) { /* next */ }
    }
    API = 'sample'; DEMO = true;
    demoBanner('Demo. The desk is not reachable from here, so the page shows sample reads and the read box answers from them only.');
  }
  let probed = null;
  async function get(name) {
    if (!probed) probed = useSampleIfAgentDown();
    await probed;
    const r = await fetch(API === 'sample' ? `sample/${name}` : `${API}/${name}`, {cache: 'no-store'});
    if (!r.ok) throw new Error(`${name}: http ${r.status}`);
    return r.json();
  }
  async function post(path, body) {
    if (!probed) probed = useSampleIfAgentDown();
    await probed;
    if (API === 'sample') {
      const reads = await get('reads.json');
      await new Promise(r => setTimeout(r, 1800));
      const hit = reads.find(c => c.mint === body.mint);
      if (hit) return hit;
      throw new Error('demo: the desk is not reachable, so only the sample mints can be shown. try the one in the placeholder.');
    }
    const r = await fetch(`${API}${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let doc = null; try { doc = await r.json(); } catch (e) { doc = {error: `http ${r.status}`}; }
    if (!r.ok) throw new Error(doc.error || `http ${r.status}`);
    return doc;
  }

  // ---------------------------------------------------------------- small ui
  function toast(text) {
    let t = $('#toast'); if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
    t.textContent = text; t.classList.add('show'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('show'), 2200);
  }
  function copyText(t, btn) {
    const done = () => { if (btn) { const o = btn.textContent; btn.textContent = 'copied'; setTimeout(() => { btn.textContent = o; }, 1400); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(done, done);
    else { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch (e) {} ta.remove(); done(); }
  }
  function clock() {
    const el = $('.clock'); if (!el) return;
    const tick = () => { el.textContent = new Date().toISOString().slice(11, 19) + ' UTC'; };
    tick(); setInterval(tick, 1000);
  }
  function agentChip(state) {
    const el = $('#agent'); if (!el) return;
    const age = state && state.updated ? Date.now()/1000 - state.updated : Infinity;
    el.className = 'chip ' + (DEMO ? 'warn' : age < 120 ? 'on' : age < 900 ? 'warn' : 'off');
    el.title = DEMO ? 'sample data' : `desk wrote ${ago(state.updated)} ago`;
    el.lastChild.textContent = DEMO ? 'demo' : age < 120 ? 'desk live' : 'desk stale';
  }
  function navSpy() {
    const links = $$('.rail nav a[href^="#"]'); if (!links.length) return;
    const secs = links.map(a => $(a.getAttribute('href'))).filter(Boolean);
    const on = () => {
      let cur = secs[0];
      for (const s of secs) if (s.getBoundingClientRect().top <= 120) cur = s;
      links.forEach(a => a.classList.toggle('on', cur && a.getAttribute('href') === '#' + cur.id));
    };
    document.addEventListener('scroll', on, {passive: true}); on();
  }
  function reveal() {
    const els = $$('.rv'); if (!els.length) return;
    if (!MOTION || !('IntersectionObserver' in window)) { els.forEach(e => e.classList.add('in')); return; }
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), {rootMargin: '0px 0px -8% 0px', threshold: .08});
    els.forEach(e => io.observe(e));
  }
  function tilt() {
    const hero = $('.hero'), term = $('.term'); if (!hero || !term || !MOTION) return;
    if (!window.matchMedia || !window.matchMedia('(pointer:fine)').matches) return;
    hero.addEventListener('mousemove', e => {
      const r = hero.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
      term.style.setProperty('--ry', (-4 + x * 10).toFixed(2) + 'deg'); term.style.setProperty('--rx', (2 - y * 8).toFixed(2) + 'deg');
    });
    hero.addEventListener('mouseleave', () => { term.style.setProperty('--ry', '-4deg'); term.style.setProperty('--rx', '2deg'); });
  }
  function spotlight() {
    const wall = $('#wall'); if (!wall || !MOTION) return;
    wall.addEventListener('mousemove', e => {
      const c = e.target.closest('.catch'); if (!c) return;
      const r = c.getBoundingClientRect();
      c.style.setProperty('--mx', (e.clientX - r.left) + 'px'); c.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  }
  const NUMS = {};
  function setNum(id, value, suffix, raw) {
    const el = $(id); if (!el) return;
    const to = +value || 0, from = NUMS[id] == null ? Math.max(0, to - Math.max(3, Math.round(to * .06))) : NUMS[id];
    NUMS[id] = to; suffix = suffix || '';
    const show = n => (raw ? raw(n) : fmt(n)) + suffix;
    if (!MOTION || from === to) { el.innerHTML = show(to); return; }
    const t0 = performance.now(), dur = 900;
    const step = now => { const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.innerHTML = show(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- the card
  function hasAlert(c) { return (c.lines || []).some(l => l.kind === 'alert'); }
  function lineHtml(l) { return `<li class="ln-${esc(l.kind)}"><i></i>${esc(l.text)}</li>`; }
  function holdersBar(c) {
    const rows = ((c.chain || {}).holders || []).filter(r => r.pct != null).slice(0, 12);
    if (!rows.length) return '';
    return `<div class="holders" title="largest token accounts, by owner">${rows.map(r => `<span class="h ${esc(r.kind)}" style="width:${Math.max(1, Math.min(100, r.pct))}%" title="${esc(r.kind)} ${esc(short(r.owner))} ${r.pct}%"></span>`).join('')}</div>
<div class="hlegend"><span><i class="wallet"></i>wallets</span><span><i class="curve"></i>bonding curve</span><span><i class="pool"></i>pool</span></div>`;
  }
  function card(c, full) {
    const x = c.x || {};
    const mk = c.market || {};
    const notable = (x.notable || []).slice(0, full ? 5 : 3);
    const lines = (c.lines || []).slice(0, full ? 20 : 7);
    const srcTag = c.source === 'desk' ? '<span class="v tripped">desk picked</span>' : c.source === 'request' ? '<span class="v clean">asked for</span>' : '<span class="v cleared">cli</span>';
    const alertN = (c.lines || []).filter(l => l.kind === 'alert').length;
    return `<article class="catch card ${alertN ? 'confirmed' : ''}" data-mint="${esc(c.mint)}">
<div class="top">${srcTag}${alertN ? `<span class="v confirmed">${alertN} alert${alertN > 1 ? 's' : ''}</span>` : ''}<span class="nm">$${esc(c.symbol || '?')} <small>${esc(c.name || '')}</small></span>
<span class="meta">${c.seconds ? `read in ${c.seconds}s` : ''}<br>${ago(c.ts)} ago${c.reads > 1 ? ` · read ${c.reads}×` : ''}</span></div>
<div class="mintline"><code>${esc(c.mint)}</code><button type="button" class="btn ghost sm copy" data-copy="${esc(c.mint)}">copy</button></div>
<ul class="lines">${lines.map(lineHtml).join('')}</ul>
${holdersBar(c)}
${x.summary ? `<p class="xsum"><b>Grok on X:</b> ${esc(x.summary)}</p>` : c.x_error ? `<p class="xsum d">${esc(c.x_error)}</p>` : ''}
${notable.length ? `<ul class="quotes">${notable.map(n => `<li><a href="${esc(n.url || '#')}" target="_blank" rel="noopener">@${esc((n.handle || '').replace(/^@/, ''))}</a> ${esc(n.text || '')}</li>`).join('')}</ul>` : ''}
<div class="links"><a href="https://pump.fun/coin/${esc(c.mint)}" target="_blank" rel="noopener">pump.fun ↗</a><a href="https://dexscreener.com/solana/${esc(c.mint)}" target="_blank" rel="noopener">dexscreener ↗</a><a href="https://solscan.io/token/${esc(c.mint)}" target="_blank" rel="noopener">solscan ↗</a>${mk.indexed ? `<span class="d">mc ${usd(mk.mc)} · liq ${usd(mk.liq)}</span>` : ''}<span class="d" style="margin-left:auto">${(x.usage || {}).posts != null ? `${x.usage.posts} posts fetched · ` : ''}$${(c.cost_usd || 0).toFixed(3)}</span></div>
</article>`;
  }
  function bindCopies(root) { $$('button[data-copy]', root).forEach(b => { b.onclick = e => copyText(b.dataset.copy, e.currentTarget); }); }


  // ---------------------------------------------------------------- the 3d field: every launch a dot, every read a light
  const field = {ps: [], seen: new Set(), reads: new Set(), running: false, last: 0, pend: []};
  function fieldFeed(tape, reads) {
    (reads || []).forEach(c => field.reads.add(c.mint));
    const ls = (tape.launches || []).slice().reverse();      // oldest first
    const fresh = ls.filter(l => !field.seen.has(l.mint));
    fresh.forEach((l, i) => { field.seen.add(l.mint); field.pend.push({l, at: performance.now() + (field.ps.length ? i * (14000 / Math.max(1, fresh.length)) : i * 120)}); });
    if (field.seen.size > 4000) { field.seen = new Set(ls.map(l => l.mint)); }
    field.ps.forEach(p => { if (field.reads.has(p.mint) && !p.read) { p.read = true; p.lit = performance.now(); } });
    (reads || []).slice(0, 6).forEach(c => { if (!field.ps.some(p => p.mint === c.mint) && !field.seen.has('r:' + c.mint)) { field.seen.add('r:' + c.mint); spawn({mint: c.mint, symbol: c.symbol || '?', sol_in: 1}, true); } });
  }
  function spawn(l, read) {
    const a = Math.random() * Math.PI * 2, r = .25 + Math.random() * .7;
    field.ps.push({mint: l.mint, sym: l.symbol || '?', x: Math.cos(a) * r, y: Math.sin(a) * r * .55, z: read ? .55 : 1.25, v: .00011 + Math.random() * .00007, w: Math.min(1, .35 + (+l.sol_in || 0) / 3), read: !!read, lit: read ? performance.now() : 0, born: performance.now()});
    if (field.ps.length > 160) field.ps.splice(0, field.ps.length - 160);
  }
  function fieldStart() {
    const cv = $('#field'); if (!cv || field.running) return;
    field.running = true;
    const ctx = cv.getContext('2d');
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => { const r = cv.getBoundingClientRect(); cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr); };
    resize(); window.addEventListener('resize', resize);
    let mx = 0, my = 0;
    cv.addEventListener('mousemove', e => { const r = cv.getBoundingClientRect(); mx = (e.clientX - r.left) / r.width - .5; my = (e.clientY - r.top) / r.height - .5; });
    cv.addEventListener('mouseleave', () => { mx = 0; my = 0; });
    cv.addEventListener('click', e => { const r = cv.getBoundingClientRect(); const px = (e.clientX - r.left) * dpr, py = (e.clientY - r.top) * dpr; let best = null, bd = 400; for (const p of field.ps) { if (!p.sx) continue; const d = (p.sx - px) ** 2 + (p.sy - py) ** 2; if (d < bd) { bd = d; best = p; } } if (best) { const inp = $('#mint-in'); if (inp) { inp.value = best.mint; inp.focus(); toast(`$${best.sym} on the desk, press Read it`); } } });
    let camx = 0, camy = 0;
    const draw = now => {
      if (!field.running) return;
      if (document.hidden) { requestAnimationFrame(draw); return; }
      const dt = Math.min(50, now - (field.last || now)); field.last = now;
      while (field.pend.length && field.pend[0].at <= now) spawn(field.pend.shift().l, false);
      const W = cv.width, H = cv.height, f = H * .95, cx = W / 2, cy = H * .52;
      camx += (mx * .18 - camx) * .05; camy += (my * .12 - camy) * .05;
      ctx.clearRect(0, 0, W, H);
      // the floor grid, in perspective
      ctx.strokeStyle = 'rgba(79,209,255,.11)'; ctx.lineWidth = 1;
      for (let i = 1; i <= 9; i++) { const z = i / 9 * 1.3 + .08; const yy = cy + (.5 - camy) * f / z; if (yy > H) continue; ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W, yy); ctx.stroke(); }
      for (let i = -6; i <= 6; i++) { const x0 = cx + ((i * .22) - camx) * f / .08, x1 = cx + ((i * .22) - camx) * f / 1.4; ctx.beginPath(); ctx.moveTo(x1, cy + (.5 - camy) * f / 1.4); ctx.lineTo(x0, H); ctx.stroke(); }
      // particles, far to near
      field.ps.sort((a, b) => b.z - a.z);
      ctx.font = `${Math.round(11 * dpr)}px JetBrains Mono, monospace`; ctx.textAlign = 'center';
      for (const p of field.ps) {
        if (MOTION) p.z -= p.v * dt * (p.read ? .55 : 1);
        if (p.z < .07) { p.z = .07; p.gone = (p.gone || 0) + dt; }
        const s = f / p.z, sx = cx + (p.x - camx) * s, sy = cy + (p.y - camy) * s; p.sx = sx; p.sy = sy;
        const a = Math.max(0, Math.min(1, (1.3 - p.z) * 1.6)) * (p.gone ? Math.max(0, 1 - p.gone / 1500) : 1);
        if (a <= 0) continue;
        const rad = (p.read ? 4.5 : 2.2 + p.w * 1.6) * dpr * Math.min(2.2, .35 / p.z + .3);
        if (p.read) {
          const t = (now - p.lit) / 1000, ring = rad * (2.5 + (t % 2) * 2.2), ra = Math.max(0, .5 - (t % 2) / 4);
          ctx.beginPath(); ctx.arc(sx, sy, ring, 0, Math.PI * 2); ctx.strokeStyle = `rgba(79,209,255,${ra * a})`; ctx.lineWidth = 1.2 * dpr; ctx.stroke();
          ctx.shadowColor = 'rgba(79,209,255,.9)'; ctx.shadowBlur = 18 * dpr; ctx.fillStyle = `rgba(159,232,255,${a})`;
        } else { ctx.shadowColor = 'rgba(238,242,246,.5)'; ctx.shadowBlur = 6 * dpr; ctx.fillStyle = `rgba(238,242,246,${a * .85})`; }
        ctx.beginPath(); ctx.arc(sx, sy, rad, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
        if (p.read || p.z < .45) { ctx.fillStyle = p.read ? `rgba(159,232,255,${a})` : `rgba(151,160,171,${a * .9})`; ctx.fillText('$' + p.sym, sx, sy - rad - 5 * dpr); }
      }
      field.ps = field.ps.filter(p => !(p.gone > 1500));
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
  }

  // ---------------------------------------------------------------- hero pieces
  function termLine(ev) {
    const k = ev.kind || 'info';
    const cls = k === 'read' ? 'clean' : k === 'pick' ? 'watch' : k === 'error' ? 'tripped' : k === 'boot' ? 'boot' : '';
    return `<div class="ln ${cls}"><span class="t">${hhmm(ev.ts)}</span><span class="eco">${esc(k)}</span><span class="nm">${esc(ev.text)}</span></div>`;
  }
  function renderTerm(state, events) {
    const body = $('#term-body'); if (!body) return;
    const evs = (events.events || []).slice(-40).reverse().slice(0, $('.term.field') ? 5 : 40);
    const head = `<div class="ln boot"><span class="t">${hhmm(state.updated || Date.now()/1000)}</span><span class="d">level desk · tape ${state.tape && state.tape.ok ? 'on' : 'off'} · grok ${state.grok && state.grok.ready ? 'ready' : 'no key'} · ${(state.tape || {}).per_min || 0} launches/min</span></div>`;
    body.innerHTML = head + evs.map(termLine).join('') + '<div class="ln" style="opacity:1"><span class="cur"></span></div>';
  }
  function renderTicker(tape) {
    const t = $('#ticker-track'); if (!t) return;
    const ls = (tape.launches || []).slice(0, 40);
    if (!ls.length) { t.innerHTML = '<span>the tape is quiet</span>'; return; }
    const one = ls.map(l => `<span><span class="v watch">new</span><b>$${esc(l.symbol || '?')}</b> ${esc(l.name || '')} · ${l.sol_in} SOL in · ${ago(l.ts)} ago</span>`).join('');
    t.innerHTML = one;
    // constant, slow speed whatever the number of launches: about 38 px a second
    setTimeout(() => { t.style.animationDuration = Math.max(90, Math.round(t.scrollWidth / 38)) + 's'; }, 50);
  }
  function renderStats(st) {
    const tape = st.tape || {}, desk = st.desk || {}, b = desk.budget || {};
    setNum('#st-tape', tape.per_hour || 0);
    setNum('#st-reads', desk.reads_24h || 0);
    const secs = $('#st-secs'); if (secs) secs.innerHTML = desk.avg_seconds ? `${desk.avg_seconds}<small>avg</small>` : '–';
    const bud = $('#st-budget'); if (bud) bud.innerHTML = st.grok && st.grok.ready ? `$${(b.spent_usd || 0).toFixed(2)}<small>of $${b.daily_usd}</small>` : '–<small>no key yet</small>';
    setNum('#st-total', desk.reads_total || 0);
    const rate = $('#tape-rate'); if (rate) rate.textContent = fmt(tape.per_hour || 0);
    const a = desk.auto || {};
    if (a.mc_usd) { const m = $('#line-mc'); if (m) m.textContent = usd(a.mc_usd); const th = $('#th-mc'); if (th) th.innerHTML = `${usd(a.mc_usd)}<small>market cap, ${a.age_min[0]} to ${a.age_min[1]} min old</small>`; const h = $('#th-hour'); if (h) h.innerHTML = `${a.per_hour}<small>then it waits</small>`; }
    if (b.daily_usd) { const t = $('#th-budget'); if (t) t.innerHTML = `$${b.daily_usd}<small>when it is spent the card says so</small>`; }
  }

  // ---------------------------------------------------------------- the wall
  const wall = {data: [], filter: 'all'};
  function renderWall(reads) {
    wall.data = reads;
    const el = $('#wall'); if (!el) return;
    const list = reads.filter(c => wall.filter === 'all' || (wall.filter === 'alert' ? hasAlert(c) : c.source === wall.filter)).slice(0, 40);
    const cnt = $('#wall-count'); if (cnt) cnt.textContent = `${list.length} of ${reads.length}`;
    el.innerHTML = list.length ? list.map(c => card(c, false)).join('') : '<div class="empty">nothing here yet. the desk reads a launch when it crosses the line, or when someone asks.</div>';
    bindCopies(el);
  }
  function filters() {
    $$('.filters button[data-f]').forEach(b => b.onclick = () => { wall.filter = b.dataset.f; $$('.filters button[data-f]').forEach(x => x.classList.toggle('on', x === b)); renderWall(wall.data); });
  }

  // ---------------------------------------------------------------- the read box
  function readForm() {
    const f = $('#readbox'), inp = $('#mint-in'), btn = $('#read-btn'), out = $('#read-out'); if (!f) return;
    const q = new URLSearchParams(location.search).get('m'); if (q) inp.value = q;
    f.onsubmit = async e => {
      e.preventDefault();
      const mint = (inp.value || '').trim();
      if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint)) { toast('paste a Solana mint address'); inp.focus(); return; }
      btn.disabled = true;
      const t0 = Date.now();
      const steps = ['opening the bonding curve', 'naming the top holders', 'reading the creator wallet', 'asking dexscreener', 'grok is reading x'];
      let i = 0;
      out.innerHTML = `<div class="reading"><span class="cur"></span> <span id="read-step">${steps[0]}</span> <span class="d" id="read-clock">0.0s</span></div>`;
      const tick = setInterval(() => { const s = (Date.now() - t0) / 1000; const c = $('#read-clock'); if (c) c.textContent = s.toFixed(1) + 's'; if (s > (i + 1) * 1.6 && i < steps.length - 1) { i++; const st = $('#read-step'); if (st) st.textContent = steps[i]; } }, 100);
      try {
        const c = await post('/read', {mint});
        clearInterval(tick);
        out.innerHTML = card(c, true);
        bindCopies(out);
        try { renderWall([c, ...wall.data.filter(x => x.mint !== c.mint)]); } catch (e) {}
      } catch (err) {
        clearInterval(tick);
        out.innerHTML = `<div class="reading err">${esc(err.message || err)}</div>`;
      }
      btn.disabled = false;
    };
    if (q) f.requestSubmit();
  }

  function renderLinks(links, st) {
    LINKS = links || {};
    const x = LINKS.x; $$('a.xlink, a.xlink2').forEach(a => { if (x) { a.href = x; a.hidden = false; } else a.hidden = true; });
    const gh = LINKS.github; if (gh) $$('a.ghlink').forEach(a => { a.href = a.textContent.includes('desk.py') ? gh.replace(/\/$/, '') + '/blob/main/agent/desk.py' : gh; });
    const bl = $('#bill-line');
    if (bl) {
      const b = (st && st.desk && st.desk.budget) || {};
      bl.textContent = st && st.grok && st.grok.ready && b.daily_usd
        ? `$${(b.spent_usd || 0).toFixed(2)} of the $${b.daily_usd} daily Grok budget is spent. About $0.10 a read.`
        : 'the Grok key is not set on this desk yet, so cards show chain facts only.';
    }
    const an = $('.api-note'); if (an) an.textContent = API === 'sample' ? 'sample data' : `agent: ${API}`;
  }

  // ---------------------------------------------------------------- ca
  // the contract address lives in ca.json next to the page, so it can be set without touching the code
  async function caInit() {
    let ca = '';
    try { const r = await fetch('ca.json?' + Date.now(), {cache: 'no-store'}); if (r.ok) ca = ((await r.json()).ca || '').trim(); } catch (e) {}
    if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(ca)) return;
    const bar = $('#ca-bar'); if (!bar) return;
    $('#ca-code').textContent = ca;
    $('#ca-copy').onclick = e => copyText(ca, e.currentTarget);
    $('#ca-pump').href = 'https://pump.fun/coin/' + ca;
    $('#ca-dex').href = 'https://dexscreener.com/solana/' + ca;
    $('#ca-read').href = '?m=' + ca + '#desk';
    bar.hidden = false; const n = $('#nav-ca'); if (n) n.hidden = false;
  }

  // ---------------------------------------------------------------- page
  async function init() {
    if (window.LEVEL_SAMPLE) demoBanner('Preview with sample reads. The live desk fills the wall once it is deployed.');
    caInit(); clock(); navSpy(); filters(); readForm(); reveal(); tilt(); spotlight();
    const load = async () => {
      try {
        const [st, reads, tape, events] = await Promise.all([get('state.json'), get('reads.json'), get('tape.json'), get('events.json')]);
        agentChip(st); renderStats(st); renderLinks(st.links, st); renderTicker(tape); renderTerm(st, events); renderWall(reads); fieldFeed(tape, reads); fieldStart();
      } catch (e) { console.warn(e); agentChip(null); }
    };
    await load();
    setInterval(load, 15000);
  }
  return {get API() { return API; }, get DEMO() { return DEMO; }, get, post, esc, fmt, init, card, toast, copyText, get LINKS() { return LINKS; }};
})();
