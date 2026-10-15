/* Дизайнер презентаций — интерфейс. Источник истины — сервер; браузер хранит только черновики комментариев. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const POLL_MS = 1500;
  const SLIDE_W = 1920, SLIDE_H = 1080;

  const S = {
    projects: [], guides: [], agent: null, chrome: true,
    mode: 'project', project: null, guide: null, data: null, current: null, projectStates: {},
    drafts: [], tab: 'drafts', thumbs: new Map(), previewKey: null, pendingReload: false,
    exportWatch: false,
  };

  // ---------- API ----------
  async function api(path, body) {
    const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (_) { /* пусто */ }
    if (!r.ok || !data || data.ok === false) throw new Error((data && data.error) || 'Сервер недоступен');
    return data;
  }
  const enc = encodeURIComponent;
  const viewKey = () => (S.mode === 'guide' ? 'g:' + S.guide : S.project);
  const slideUrl = (s) => `${S.mode === 'guide' ? '/g/' + enc(S.guide) : '/p/' + enc(S.project)}/${s.file.split('/').map(enc).join('/')}?v=${s.mtime || 0}`;
  const readonly = () => !S.data || S.mode === 'guide' || !S.data.editable;

  // ---------- черновики (только в браузере, до «Сохранить») ----------
  const draftKey = () => `pd:drafts:${S.project}`;
  function loadDrafts() { try { S.drafts = JSON.parse(localStorage.getItem(draftKey()) || '[]'); } catch (_) { S.drafts = []; } }
  function storeDrafts() { try { localStorage.setItem(draftKey(), JSON.stringify(S.drafts)); } catch (_) { /* приватный режим */ } }
  function addDraft(d) {
    S.drafts.push(Object.assign({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6) }, d));
    storeDrafts(); renderDrafts(); renderThumbBadges();
  }

  // ---------- иконки ----------
  const ICON = {
    eye: '<svg class="i" viewBox="0 0 20 20"><path d="M2.5 10s3-5 7.5-5 7.5 5 7.5 5-3 5-7.5 5-7.5-5-7.5-5Z"/><circle cx="10" cy="10" r="2.3"/></svg>',
    eyeOff: '<svg class="i" viewBox="0 0 20 20"><path d="M3 3l14 14"/><path d="M8.6 5.2A8.6 8.6 0 0 1 10 5c4.5 0 7.5 5 7.5 5a14 14 0 0 1-2.2 2.8M5.4 6.7A13.7 13.7 0 0 0 2.5 10s3 5 7.5 5a7.3 7.3 0 0 0 3.3-.8"/></svg>',
    x: '<svg class="i" viewBox="0 0 20 20"><path d="M5 5l10 10M15 5 5 15"/></svg>',
    doc: '<svg class="i" viewBox="0 0 20 20"><rect x="3" y="4.5" width="14" height="11" rx="2"/><path d="M6.5 8h7M6.5 11h4.5"/></svg>',
    book: '<svg class="i" viewBox="0 0 20 20"><path d="M4 3.5h9.5a2 2 0 0 1 2 2V16.5H6a2 2 0 0 1-2-2v-11Z"/><path d="M4 14.5a2 2 0 0 1 2-2h9.5"/></svg>',
    trash: '<svg class="i" viewBox="0 0 20 20"><path d="M3.5 5h13M8 5V3.5h4V5M5.5 5l.8 10.2A1.5 1.5 0 0 0 7.8 16.5h4.4a1.5 1.5 0 0 0 1.5-1.3L14.5 5M8.5 8v5.5M11.5 8v5.5"/></svg>',
    copy: '<svg class="i" viewBox="0 0 20 20"><rect x="7" y="7" width="9.5" height="9.5" rx="2"/><path d="M13 7V5a1.5 1.5 0 0 0-1.5-1.5h-6A1.5 1.5 0 0 0 4 5v6.5A1.5 1.5 0 0 0 5.5 13H7"/></svg>',
  };
  const ST = {
    queued:      ['Ждёт агента', '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M8 5v3l2 1.5"/></svg>'],
    in_progress: ['В работе', '<svg class="spin" viewBox="0 0 16 16"><path d="M8 2a6 6 0 1 0 6 6"/></svg>'],
    stalled:     ['Агент не отвечает', '<svg viewBox="0 0 16 16"><path d="M8 2.5 14 13H2Z"/><path d="M8 6.5v3M8 11.3v.2"/></svg>'],
    completed:   ['Готово', '<svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7"/></svg>'],
    completed_with_warnings: ['Готово с замечаниями', '<svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7"/></svg>'],
    needs_input: ['Нужен ответ', '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M6.3 6.3a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.6-.7 1.1M8 11.3v.2"/></svg>'],
    failed:      ['Ошибка', '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="m6 6 4 4M10 6l-4 4"/></svg>'],
  };
  const RES = {
    pending: ['Ожидает', '<circle cx="8" cy="8" r="5.5"/>'],
    done: ['Сделано', '<path d="m3.5 8.5 3 3 6-7"/>'],
    partial: ['Частично', '<path d="M3 8h10"/>'],
    needs_input: ['Нужно уточнение', '<circle cx="8" cy="8" r="5.5"/><path d="M8 5.5v3M8 10.6v.2"/>'],
    unsupported: ['Пока нельзя', '<circle cx="8" cy="8" r="5.5"/><path d="m4.2 11.8 7.6-7.6"/>'],
    failed: ['Ошибка', '<path d="m4.5 4.5 7 7M11.5 4.5l-7 7"/>'],
  };

  // ---------- утилиты ----------
  function when(iso) {
    if (!iso) return '';
    const d = new Date(iso), m = Math.round((Date.now() - d) / 60000);
    if (m < 1) return 'только что';
    if (m < 60) return `${m} мин назад`;
    return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function plural(n, a, b, c) { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? b : c; }
  let toastTimer;
  function toast(text) {
    const t = $('#toast');
    t.textContent = text; t.hidden = false; t.classList.remove('out');
    t.style.animation = 'none'; void t.offsetWidth; t.style.animation = '';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.classList.add('out'); setTimeout(() => { t.hidden = true; }, 260); }, 3200);
  }
  const slidesSorted = () => (S.data ? S.data.slides.slice().sort((a, b) => a.order - b.order) : []);
  const slideById = (id) => S.data && S.data.slides.find((s) => s.id === id);
  const numberOf = (id) => slidesSorted().findIndex((s) => s.id === id) + 1;
  const isTyping = () => document.activeElement && /TEXTAREA|INPUT/.test(document.activeElement.tagName) && document.activeElement.value.trim() !== '';

  // ---------- загрузка ----------
  async function refreshState() {
    const st = await api('/api/state');
    S.projects = st.projects; S.guides = st.guides; S.agent = st.agent; S.chrome = st.chrome; S.editCommands = st.edit_commands || ['/presentations правки']; S.projectStates = st.project_states || {};
    if (S.mode === 'guide' && !S.guides.some((g) => g.id === S.guide)) S.mode = 'project';
    if (S.mode === 'project' && (!S.project || !S.projects.includes(S.project))) selectProject(S.projects[0] || null, true);
    renderSidebar();
  }
  async function refreshProject() {
    let data;
    if (S.mode === 'guide') {
      const g = await api(`/api/guides/${enc(S.guide)}`);
      data = { name: g.name, guide: { id: g.id, name: g.name }, slides: g.examples, iterations: [], export: null,
               lifecycle: g.lifecycle, editable: false, guideInfo: g };
    } else {
      if (!S.project) { renderEmptyApp(); return; }
      data = await api(`/api/projects/${enc(S.project)}`);
    }
    const prev = S.data; S.data = data;
    if (!S.current || !slideById(S.current)) {
      let saved = null;
      try { saved = localStorage.getItem(`pd:current:${viewKey()}`); } catch (_) { /* приватный режим */ }
      S.current = slideById(saved) ? saved : (slidesSorted()[0] || {}).id;
    }
    $('#app').classList.toggle('readonly', readonly());
    $('#exportBtn').disabled = readonly() || !S.data.slides.length;
    renderHeader(); renderThumbs(); renderStage(prev); renderTasks(); renderAgent(); renderExport(); renderStatus(); renderSidebar();
  }
  function renderEmptyApp() {
    $('#projectTitle').textContent = 'Нет проектов';
    $('#stageEmpty').hidden = false; $('#canvasWrap').hidden = true;
    $('#stageEmptyTitle').textContent = 'Проектов нет';
    $('#stageEmptyText').textContent = 'Создай проект или гайдлайн слева.';
  }
  async function tick() {
    try { await refreshState(); await refreshProject(); setOffline(false); }
    catch (e) { setOffline(true); }
    setTimeout(tick, S.exportWatch ? 600 : POLL_MS);
  }
  function setOffline(off) {
    if (!off) return;
    const a = $('#agentStatus');
    a.dataset.state = 'stalled';
    $('#agentIcon').innerHTML = ST.stalled[1];
    $('#agentText').textContent = 'Нет связи с приложением';
  }
  function selectGuide(id) {
    if (S.mode === 'guide' && S.guide === id) return;
    S.mode = 'guide'; S.guide = id; S.data = null; S.current = null; S.previewKey = null;
    S.thumbs.forEach((t) => t.remove()); S.thumbs.clear();
    renderSidebar(); refreshProject();
  }
  function selectProject(name, silent) {
    if (S.mode === 'project' && name === S.project) return;
    S.mode = 'project';
    S.project = name; S.data = null; S.current = null; S.previewKey = null;
    S.thumbs.forEach((t) => t.remove()); S.thumbs.clear();
    loadDrafts(); renderDrafts();
    if (!silent) refreshProject();
  }

  // ---------- сайдбар и шапка ----------
  const PILL = {
    waiting_agent: ['ждёт агента', 'wait'], working: ['в работе', 'work'], waiting_user: ['нужен ответ', 'wait'],
    stalled: ['не отвечает', 'bad'], failed: ['ошибка', 'bad'], cancelled: ['отменён', ''], broken: ['повреждён', 'bad'],
  };
  function sideItem(tag, icon, label, on, state) {
    const b = el(tag, 'side-item' + (on ? ' on' : ''));
    if (tag === 'button') b.type = 'button';
    b.innerHTML = icon; b.append(el('span', null, label));
    if (state && PILL[state]) { const p = el('span', 'side-pill ' + PILL[state][1], PILL[state][0]); b.append(p); }
    return b;
  }
  function sideRow(item, onDelete) {
    const row = el('div', 'side-row');
    const del = el('button', 'side-del'); del.type = 'button'; del.innerHTML = ICON.trash;
    del.title = 'Удалить'; del.setAttribute('aria-label', 'Удалить «' + item.textContent + '»');
    del.onclick = (e) => { e.stopPropagation(); onDelete(); };
    row.append(item, del);
    return row;
  }
  function renderSidebar() {
    const pl = $('#projectList');
    const sig = JSON.stringify([S.projects, S.projectStates, S.mode, S.project]);
    if (pl.dataset.sig !== sig) {
      pl.dataset.sig = sig; pl.innerHTML = '';
      S.projects.forEach((p) => {
        const st = S.projectStates[p];
        const b = sideItem('button', ICON.doc, p, S.mode === 'project' && p === S.project, st && st !== 'ready' ? st : null);
        b.title = p; b.onclick = () => selectProject(p);
        pl.append(sideRow(b, () => askDelete('project', p)));
      });
    }
    const gl = $('#guideList');
    const gsig = JSON.stringify([S.guides.map((g) => [g.id, g.name, g.state, g.used_by]), S.mode, S.guide]);
    if (gl.dataset.sig !== gsig) {
      gl.dataset.sig = gsig; gl.innerHTML = '';
      S.guides.forEach((g) => {
        const b = sideItem('button', ICON.book, g.name, S.mode === 'guide' && S.guide === g.id, g.state !== 'ready' ? g.state : null);
        b.title = g.state === 'ready' ? 'Эталонные слайды' : 'Гайдлайн собирается';
        b.onclick = () => selectGuide(g.id);
        gl.append(sideRow(b, () => askDelete('guide', g.id)));
      });
    }
  }
  function renderHeader() {
    $('#projectTitle').textContent = S.mode === 'guide' ? 'Гайдлайн · ' + S.data.name : S.data.name;
    $('#guideChip').hidden = S.mode === 'guide';
    $('#guideName').textContent = (S.data.guide && S.data.guide.name) || 'Без гайдлайна';
    const vis = S.data.slides.filter((s) => !s.hidden).length;
    $('#slideCount').textContent = vis === S.data.slides.length ? `${vis}` : `${vis} из ${S.data.slides.length}`;
  }

  // ---------- навигатор ----------
  function renderThumbs() {
    const list = $('#thumbs');
    const sorted = slidesSorted();
    sorted.forEach((s, i) => {
      let t = S.thumbs.get(s.id);
      if (!t) {
        t = el('li', 'thumb'); t.tabIndex = 0; t.draggable = true; t.dataset.id = s.id;
        t.innerHTML = `<span class="thumb-no"></span><div class="thumb-frame"><iframe sandbox="allow-same-origin" loading="lazy" tabindex="-1" aria-hidden="true"></iframe></div><button class="thumb-eye" type="button"></button>`;
        t.onclick = (e) => { if (!e.target.closest('.thumb-eye')) go(s.id); };
        t.querySelector('.thumb-eye').onclick = () => toggleHidden(s.id);
        bindDrag(t);
        S.thumbs.set(s.id, t);
      }
      const fr = t.querySelector('iframe');
      const src = slideUrl(s);
      if (fr.dataset.src !== src) { fr.dataset.src = src; fr.src = src; }
      t.querySelector('.thumb-no').textContent = i + 1;
      t.classList.toggle('on', s.id === S.current);
      t.classList.toggle('is-hidden', !!s.hidden);
      const eye = t.querySelector('.thumb-eye');
      eye.innerHTML = s.hidden ? ICON.eyeOff : ICON.eye;
      eye.title = s.hidden ? 'Показать слайд' : 'Скрыть слайд';
      eye.setAttribute('aria-label', eye.title);
      t.setAttribute('aria-label', `Слайд ${i + 1}: ${s.title}${s.hidden ? ', скрыт' : ''}`);
      if (list.children[i] !== t) list.insertBefore(t, list.children[i] || null);
    });
    S.thumbs.forEach((t, id) => { if (!slideById(id)) { t.remove(); S.thumbs.delete(id); } });
    renderThumbBadges(); fitThumbs();
  }
  function renderThumbBadges() {
    const count = {};
    S.drafts.forEach((d) => { count[d.slide_id] = (count[d.slide_id] || 0) + 1; });
    S.thumbs.forEach((t, id) => {
      let b = t.querySelector('.thumb-badge');
      if (count[id]) { if (!b) { b = el('span', 'thumb-badge'); t.append(b); } b.textContent = count[id]; b.title = 'Несохранённые комментарии'; }
      else if (b) b.remove();
    });
  }
  function fitThumbs() {
    S.thumbs.forEach((t) => {
      const f = t.querySelector('.thumb-frame');
      const w = f.clientWidth;
      if (w) f.firstElementChild.style.transform = `scale(${w / SLIDE_W})`;
    });
  }
  function go(id) {
    if (!id || id === S.current) return;
    S.current = id; hidePopover();
    try { localStorage.setItem(`pd:current:${viewKey()}`, id); } catch (_) { /* приватный режим */ }
    renderThumbs(); renderStage();
    const t = S.thumbs.get(id); if (t) t.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  async function toggleHidden(id) {
    const s = slideById(id); if (!s) return;
    const hidden = !s.hidden;
    s.hidden = hidden; renderThumbs(); renderStage(); renderHeader();
    try { await api(`/api/projects/${enc(S.project)}/slides/${enc(id)}/hidden`, { hidden }); toast(hidden ? 'Слайд скрыт' : 'Слайд показан'); }
    catch (e) { s.hidden = !hidden; renderThumbs(); renderStage(); toast(e.message); }
  }

  // перетаскивание для порядка
  let dragId = null;
  function bindDrag(t) {
    t.addEventListener('dragstart', (e) => { if (readonly()) { e.preventDefault(); return; } dragId = t.dataset.id; t.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragId); });
    t.addEventListener('dragend', () => { dragId = null; t.classList.remove('dragging'); clearDrop(); });
    t.addEventListener('dragover', (e) => {
      if (!dragId || dragId === t.dataset.id) return;
      e.preventDefault(); clearDrop();
      const r = t.getBoundingClientRect();
      t.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after');
    });
    t.addEventListener('drop', async (e) => {
      e.preventDefault();
      if (!dragId || dragId === t.dataset.id) return;
      const after = t.classList.contains('drop-after'); clearDrop();
      const ids = slidesSorted().map((s) => s.id).filter((x) => x !== dragId);
      ids.splice(ids.indexOf(t.dataset.id) + (after ? 1 : 0), 0, dragId);
      const before = S.data.slides.map((s) => [s.id, s.order]);
      ids.forEach((id, i) => { slideById(id).order = i + 1; });
      renderThumbs(); renderStage();
      try { await api(`/api/projects/${enc(S.project)}/order`, { ids }); }
      catch (err) { before.forEach(([id, o]) => { slideById(id).order = o; }); renderThumbs(); toast(err.message); }
    });
  }
  function clearDrop() { document.querySelectorAll('.drop-before,.drop-after').forEach((x) => x.classList.remove('drop-before', 'drop-after')); }

  // ---------- сцена ----------
  const preview = $('#preview');
  function renderStage() {
    const s = slideById(S.current);
    $('#stageEmpty').hidden = !!s; $('#canvasWrap').hidden = !s;
    $('.stage-bar').style.visibility = s ? '' : 'hidden';
    if (!s) {
      const lc = S.data && S.data.lifecycle;
      $('#stageEmptyTitle').textContent = S.mode === 'guide'
        ? (lc && lc.state !== 'ready' ? 'Гайдлайн собирается' : 'Эталонных слайдов нет')
        : (lc && lc.state !== 'ready' ? 'Проект собирается' : 'Слайдов нет');
      $('#stageEmptyText').textContent = lc && lc.state !== 'ready'
        ? 'Слайды появятся здесь. Статус — справа.'
        : (S.mode === 'guide' ? 'Образцы не заказаны.' : '');
      return;
    }
    $('#slideNo').textContent = numberOf(s.id);
    $('#slideTitle').textContent = s.title;
    const hb = $('#hideBtn');
    hb.innerHTML = (s.hidden ? ICON.eye : ICON.eyeOff) + (s.hidden ? 'Показать слайд' : 'Скрыть слайд');
    hb.onclick = () => toggleHidden(s.id);
    $('#canvas').classList.toggle('is-hidden', !!s.hidden);
    const key = slideUrl(s);
    if (key !== S.previewKey) {
      const sameSlide = S.previewKey && S.previewKey.split('?')[0] === key.split('?')[0];
      if (sameSlide && (popoverOpen() || isTyping())) { S.pendingReload = true; return; }  // не сбиваем ввод
      S.previewKey = key; S.pendingReload = false;
      $('#canvas').classList.add('fade');
      preview.src = key;
    }
  }
  preview.addEventListener('load', () => {
    $('#canvas').classList.remove('fade');
    const doc = preview.contentDocument;
    if (!doc) return;
    doc.addEventListener('mouseup', () => setTimeout(checkSelection, 0));
    doc.addEventListener('keyup', checkSelection);
    doc.addEventListener('mousedown', () => { if (!$('#selForm').hidden) return; hidePopover(); });
  });
  function fitCanvas() {
    const wrap = $('#canvasWrap');
    const w = wrap.clientWidth, h = wrap.clientHeight;
    const scale = Math.min(w / SLIDE_W, h / SLIDE_H);
    const c = $('#canvas');
    c.style.width = SLIDE_W * scale + 'px'; c.style.height = SLIDE_H * scale + 'px';
    preview.style.transform = `scale(${scale})`;
    S.scale = scale;
  }

  // ---------- выделение текста ----------
  let pendingSel = null;
  function popoverOpen() { return !$('#selPopover').hidden && !$('#selForm').hidden; }
  function checkSelection() {
    const doc = preview.contentDocument; if (!doc) return;
    const sel = doc.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) { if ($('#selForm').hidden) hidePopover(); return; }
    const range = sel.getRangeAt(0);
    const root = doc.querySelector('.slide') || doc.body;
    const pre = doc.createRange(); pre.setStart(root, 0); pre.setEnd(range.startContainer, range.startOffset);
    const post = doc.createRange(); post.setStart(range.endContainer, range.endOffset); post.setEnd(root, root.childNodes.length);
    pendingSel = {
      text: sel.toString(),
      prefix: pre.toString().slice(-40),
      suffix: post.toString().slice(0, 40),
    };
    const r = range.getBoundingClientRect(), f = preview.getBoundingClientRect();
    const x = f.left + (r.left + r.width / 2) * S.scale, y = f.top + r.bottom * S.scale + 8;
    showPopover(x, y);
  }
  function showPopover(x, y) {
    const p = $('#selPopover');
    $('#selForm').hidden = true; $('#selTrigger').hidden = false;
    p.hidden = false;
    p.style.animation = 'none'; void p.offsetWidth; p.style.animation = '';
    const w = p.offsetWidth;
    p.style.left = Math.max(12, Math.min(window.innerWidth - w - 12, x - w / 2)) + 'px';
    p.style.top = Math.min(window.innerHeight - 60, y) + 'px';
  }
  function hidePopover() {
    $('#selPopover').hidden = true; $('#selForm').hidden = true; pendingSel = null;
    if (S.pendingReload) renderStage();
  }
  $('#selTrigger').onclick = () => {
    $('#selTrigger').hidden = true; $('#selForm').hidden = false;
    $('#selQuote').textContent = pendingSel ? pendingSel.text.replace(/\s+/g, ' ').trim() : '';
    const p = $('#selPopover'), w = p.offsetWidth;
    p.style.left = Math.max(12, Math.min(window.innerWidth - w - 12, parseFloat(p.style.left) + 0)) + 'px';
    if (p.getBoundingClientRect().bottom > window.innerHeight - 12) p.style.top = (window.innerHeight - p.offsetHeight - 12) + 'px';
    $('#selComment').value = ''; $('#selComment').focus();
  };
  $('#selCancel').onclick = hidePopover;
  $('#selForm').onsubmit = (e) => {
    e.preventDefault();
    const c = $('#selComment').value.trim();
    if (!c || !pendingSel) return;
    addDraft({ slide_id: S.current, comment: c, selection: pendingSel });
    const doc = preview.contentDocument; if (doc) doc.getSelection().removeAllRanges();
    hidePopover(); setTab('drafts'); toast('Комментарий добавлен');
  };
  $('#selComment').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) $('#selForm').requestSubmit(); });

  // ---------- комментарий к слайду ----------
  const ta = $('#slideComment');
  function autosize() { ta.style.height = 'auto'; ta.style.height = Math.min(120, ta.scrollHeight) + 'px'; $('#addSlideComment').disabled = !ta.value.trim(); }
  ta.addEventListener('input', autosize);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#composer').requestSubmit(); } });
  ta.addEventListener('blur', () => { if (S.pendingReload) renderStage(); });
  $('#composer').onsubmit = (e) => {
    e.preventDefault();
    const c = ta.value.trim(); if (!c) return;
    addDraft({ slide_id: S.current, comment: c, selection: null });
    ta.value = ''; autosize(); setTab('drafts');
  };

  // ---------- инспектор ----------
  function setTab(tab) {
    S.tab = tab;
    $('.segmented').dataset.tab = tab;
    document.querySelectorAll('.seg').forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', on); });
    document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + tab));
  }
  document.querySelectorAll('.seg').forEach((b) => { b.onclick = () => setTab(b.dataset.tab); });

  function renderDrafts() {
    const box = $('#drafts'); box.innerHTML = '';
    $('#draftBadge').textContent = S.drafts.length || '';
    $('#draftBadge').classList.toggle('hot', S.drafts.length > 0);
    const save = $('#saveDrafts');
    save.disabled = !S.drafts.length;
    save.textContent = S.drafts.length ? `Отправить агенту (${S.drafts.length})` : 'Отправить агенту';
    if (!S.drafts.length) {
      box.innerHTML = '<div class="empty"><b>Комментариев нет</b><p>Напиши, что изменить,<br>или выдели текст на слайде.</p></div>';
      return;
    }
    const order = (d) => numberOf(d.slide_id) || 999;
    S.drafts.slice().sort((a, b) => order(a) - order(b)).forEach((d) => {
      const c = el('div', 'card');
      const head = el('div', 'card-head');
      const link = el('button', 'slide-link', `Слайд ${numberOf(d.slide_id) || '?'}`); link.type = 'button';
      link.onclick = () => go(d.slide_id);
      head.append(link, document.createTextNode(d.selection ? '· фрагмент' : '· весь слайд'));
      const x = el('button', 'card-x'); x.type = 'button'; x.innerHTML = ICON.x; x.title = 'Удалить'; x.setAttribute('aria-label', 'Удалить комментарий');
      x.onclick = () => { c.classList.add('leaving'); setTimeout(() => { S.drafts = S.drafts.filter((z) => z.id !== d.id); storeDrafts(); renderDrafts(); renderThumbBadges(); }, 220); };
      head.append(x); c.append(head);
      if (d.selection) c.append(el('p', 'quote', d.selection.text.replace(/\s+/g, ' ').trim()));
      c.append(el('div', 'card-text', d.comment));
      box.append(c);
    });
  }
  $('#saveDrafts').onclick = async () => {
    if (!S.drafts.length) return;
    const btn = $('#saveDrafts'); btn.disabled = true;
    try {
      const items = S.drafts.map((d) => ({ slide_id: d.slide_id, scope: 'slide', comment: d.comment, selection: d.selection }));
      const r = await api(`/api/projects/${enc(S.project)}/edits`, { items });
      S.drafts = []; storeDrafts(); renderDrafts(); renderThumbBadges();
      toast(`Задание v${r.version} отправлено`);
      setTab('tasks'); refreshProject();
    } catch (e) { btn.disabled = false; toast(e.message); }
  };

  function renderTasks() {
    const its = S.data.iterations;
    const open = its.filter((t) => ['queued', 'in_progress', 'stalled'].includes(t.status)).length;
    $('#taskBadge').textContent = open || '';
    $('#taskBadge').classList.toggle('hot', open > 0);
    const box = $('#tasks');
    const sig = JSON.stringify(its) + S.data.slides.map((s) => s.id + s.order).join();
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig; box.innerHTML = '';
    if (!its.length) { box.innerHTML = '<div class="empty"><b>Заданий нет</b><p>Здесь будут отправленные комментарии<br>и ответ агента.</p></div>'; return; }
    its.forEach((t) => {
      const c = el('div', 'card');
      const head = el('div', 'task-head');
      head.append(el('span', 'task-title', `Задание v${t.version}`), el('span', 'task-time', when(t.finished_at || t.created_at)));
      const stKey = t.status;
      const st = ST[stKey] || [stKey, ''];
      const badge = el('span', 'status st-' + stKey); badge.innerHTML = st[1]; badge.append(st[0]);
      head.append(badge); c.append(head);
      const ul = el('ul', 'task-items');
      t.items.forEach((it) => {
        const li = el('li', it.reverted ? 'is-reverted' : null);
        const r = RES[it.result] || RES.pending;
        li.innerHTML = `<svg class="ri ${it.result}" viewBox="0 0 16 16">${r[1]}</svg>`;
        const body = el('div');
        const what = el('div', 'what');
        const n = numberOf(it.slide_id);
        const link = el('button', 'btn-link', n ? `Слайд ${n}` : 'Слайд'); link.type = 'button'; link.onclick = () => go(it.slide_id);
        what.append(link, document.createTextNode(' — ' + it.comment));
        body.append(what);
        if (it.selection && it.selection.text) body.append(el('p', 'quote', it.selection.text.replace(/\s+/g, ' ').trim()));
        if (it.result !== 'pending' || it.note) {
          const note = el('div', 'note'); const res = el('span', 'res', r[0]); note.append(res);
          if (it.note) note.append(document.createTextNode(': ' + it.note));
          body.append(note);
        }
        const group = t.items.filter((x) => x.slide_id === it.slide_id && x.revertable);
        if (FINAL.includes(t.status) && group.length && t.items.filter((x) => x.slide_id === it.slide_id).pop() === it) body.append(slideActions(t, it.slide_id, group));
        li.append(body); ul.append(li);
      });
      c.append(ul);
      if (t.status === 'queued' || t.status === 'stalled') {
        c.append(cmdBlock(t.status === 'queued' ? 'Отправь агенту:' : 'Агент прервался. Отправь агенту:', S.editCommands));
      }
      box.append(c);
    });
  }

  const FINAL = ['completed', 'completed_with_warnings', 'needs_input', 'failed'];
  const UNDO = '<svg class="i" viewBox="0 0 20 20"><path d="M7 5 4 8l3 3"/><path d="M4 8h8a4 4 0 0 1 0 8H9"/></svg>';
  const REDO = '<svg class="i" viewBox="0 0 20 20"><path d="m13 5 3 3-3 3"/><path d="M16 8H8a4 4 0 0 0 0 8h3"/></svg>';
  function slideActions(t, slideId, group) {
    const row = el('div', 'item-actions');
    const undone = group.every((x) => x.reverted);
    if (undone) {
      const tag = el('span', 'tag'); tag.innerHTML = UNDO; tag.append('Откатено'); row.append(tag);
    }
    const b = el('button', 'btn btn-plain btn-s'); b.type = 'button';
    b.innerHTML = (undone ? REDO : UNDO) + (undone ? 'Вернуть правки' : 'Откатить слайд');
    b.title = undone ? 'Снова применить правки задания к слайду' : 'Убрать правки задания только с этого слайда';
    b.onclick = async () => {
      b.disabled = true;
      try {
        await api(`/api/projects/${enc(S.project)}/edits/${t.version}/slides/${enc(slideId)}/revert`, { revert: !undone });
        toast(undone ? 'Правки слайда возвращены' : 'Правки слайда откатены');
        refreshProject();
      } catch (e) { b.disabled = false; toast(e.message); }
    };
    row.append(b);
    return row;
  }

  // ---------- статус задачи (гайдлайн / новый проект) ----------
  const STAGE = { copying: 'Копирование файлов', analysis: 'Изучение файлов', interview: 'Интервью', structure: 'Структура',
    generating: 'Генерация слайдов', self_check: 'Проверка слайдов', building: 'Сборка гайдлайна' };
  const TSTATE = {
    waiting_agent: ['Ждёт агента', ST.queued[1], 'waiting_agent'], working: ['В работе', ST.in_progress[1], 'working'],
    waiting_user: ['Нужен ответ в чате', ST.needs_input[1], 'waiting_user'], stalled: ['Агент не отвечает', ST.stalled[1], 'stalled'],
    ready: ['Готово', ST.completed[1], 'ready'], failed: ['Ошибка', ST.failed[1], 'failed'], cancelled: ['Отменено', ST.failed[1], 'cancelled'],
  };
  function cmdBlock(text, cmds) {  // по строке на каждого установленного агента: /presentations … или $slides …
    const box = el('div', 'task-cmd'); box.append(document.createTextNode(text));
    (cmds || []).forEach((cmd) => {
      const code = el('code'); code.append(el('span', null, cmd));
      const cp = el('button', 'btn btn-plain'); cp.type = 'button'; cp.style.height = '24px'; cp.innerHTML = ICON.copy;
      cp.title = 'Копировать команду'; cp.setAttribute('aria-label', cp.title);
      cp.onclick = async () => { try { await navigator.clipboard.writeText(cmd); toast('Команда скопирована'); } catch (_) { toast('Не скопировалось — выдели вручную'); } };
      code.append(cp); box.append(code);
    });
    return box;
  }
  function renderStatus() {
    const pane = $('#statusPane');
    const lc = S.data && S.data.lifecycle;
    const show = readonly() && (S.mode === 'guide' || lc);
    pane.hidden = !show;
    if (!show) { pane.dataset.sig = ''; return; }
    const sig = JSON.stringify([S.mode, S.guide, S.project, lc, S.data.slides.length]);
    if (pane.dataset.sig === sig) return;
    pane.dataset.sig = sig; pane.innerHTML = '';
    const kind = S.mode === 'guide' ? 'guide' : 'project';
    const card = el('div', 'status-card');
    card.append(el('h3', null, kind === 'guide' ? 'Гайдлайн «' + S.data.name + '»' : 'Новый проект'));
    const rq = kind === 'project' && S.data.request;
    if (rq) card.append(el('p', 'req', [(MODES.find((m) => m.value === rq.mode) || {}).label, S.data.guide && S.data.guide.name].filter(Boolean).join(' · ')));
    if (!lc) {  // встроенный гайд без заявки
      card.append(el('p', 'sub', 'Встроенный гайдлайн'));
      pane.append(card); return;
    }
    const t = TSTATE[lc.state] || [lc.state, '', lc.state];
    const badge = el('span', 'status st-' + t[2]); badge.innerHTML = t[1];
    badge.append(lc.state === 'working' && STAGE[lc.stage] ? STAGE[lc.stage] : (lc.stage === 'copying' && lc.state === 'waiting_agent' ? STAGE.copying : t[0]));
    card.append(badge);
    if (lc.message && !['waiting_agent', 'waiting_user'].includes(lc.state)) card.append(el('p', 'msg', lc.message));
    if (lc.question) card.append(el('p', 'question', lc.question));
    if (lc.progress && lc.progress.total) {
      const bar = el('div', 'bar'); const fill = el('span'); fill.style.width = Math.round(lc.progress.done / lc.progress.total * 100) + '%';
      bar.append(fill); card.append(bar);
      card.append(el('p', 'sub', `${lc.progress.done} из ${lc.progress.total}`));
    }
    const order = kind === 'guide' ? ['copying', 'analysis', 'building', 'ready']
      : ['copying', 'analysis', 'interview', 'structure', 'generating', 'self_check', 'ready'];
    const names = Object.assign({}, STAGE, { ready: 'Готово' });
    const cur = lc.state === 'ready' ? 'ready' : (lc.state === 'waiting_agent' ? (lc.sources_copied ? 'analysis' : 'copying') : (lc.stage || 'analysis'));
    const ci = order.indexOf(cur);
    const steps = el('ul', 'steps');
    order.forEach((k, i) => { const li = el('li', i < ci || lc.state === 'ready' ? 'done' : (i === ci ? 'now' : ''), names[k]); steps.append(li); });
    card.append(steps);
    pane.append(card);
    if (lc.state === 'waiting_agent' && lc.sources_copied !== false) pane.append(cmdBlock('Отправь агенту:', lc.commands));
    if (lc.state === 'stalled') pane.append(cmdBlock('Агент прервался. Отправь команду — продолжит с того же места:', lc.commands));
    if (lc.state === 'waiting_user') pane.append(cmdBlock('Ответь агенту в чате. Если чат закрыт, отправь команду — он повторит вопрос:', lc.commands));
    if (!['ready', 'failed', 'cancelled'].includes(lc.state)) {
      const acts = el('div', 'status-actions');
      const cancel = el('button', 'btn btn-plain btn-s', 'Отменить'); cancel.type = 'button';
      cancel.onclick = async () => {
        if (!confirm(kind === 'guide' ? 'Отменить сборку гайдлайна?' : 'Отменить создание проекта?')) return;
        try { await api(`/api/tasks/${kind}/${enc(kind === 'guide' ? S.guide : S.project)}/cancel`, {}); toast('Отменено'); refreshProject(); }
        catch (e) { toast(e.message); }
      };
      acts.append(cancel); pane.append(acts);
    }
  }

  // ---------- форма: новый гайдлайн ----------
  const gScrim = $('#guideScrim');
  let guidePick = null;
  function openSheet(scrim) { scrim.hidden = false; scrim.classList.remove('closing'); }
  function closeSheet(scrim) { if (scrim.hidden) return; scrim.classList.add('closing'); setTimeout(() => { scrim.hidden = true; scrim.classList.remove('closing'); }, 200); }
  async function pick(kind, prompt) {
    try { const r = await api('/api/pick', { kind, prompt }); return r.cancelled ? null : r; }
    catch (e) { toast(e.message); return null; }
  }
  $('#addGuide').onclick = () => {
    guidePick = null; $('#guideNameInput').value = ''; $('#guideExamples').checked = true;
    $('#guideFolder').textContent = 'Не выбрана'; $('#guideFolder').classList.remove('set'); $('#guideError').hidden = true;
    openSheet(gScrim); setTimeout(() => $('#guideNameInput').focus(), 60);
  };
  $('#guidePick').onclick = async () => {
    const b = $('#guidePick'); b.disabled = true; b.textContent = 'Выбор в окне…';
    const r = await pick('folder', 'Папка с файлами гайдлайна');
    b.disabled = false; b.textContent = 'Выбрать папку…';
    if (r) { guidePick = r.token; $('#guideFolder').textContent = r.items[0].label; $('#guideFolder').classList.add('set'); }
  };
  $('#guideCancel').onclick = () => closeSheet(gScrim);
  gScrim.addEventListener('mousedown', (e) => { if (e.target === gScrim) closeSheet(gScrim); });
  $('#guideForm').onsubmit = async (e) => {
    e.preventDefault();
    const err = $('#guideError'); err.hidden = true;
    const name = $('#guideNameInput').value.trim();
    if (!name) { err.textContent = 'Укажи название'; err.hidden = false; return; }
    if (!guidePick) { err.textContent = 'Выбери папку'; err.hidden = false; return; }
    $('#guideSave').disabled = true;
    try {
      const r = await api('/api/guides', { name, token: guidePick, examples: $('#guideExamples').checked });
      closeSheet(gScrim); toast('Гайдлайн создан');
      await refreshState(); selectGuide(r.id);
    } catch (e2) { err.textContent = e2.message; err.hidden = false; }
    $('#guideSave').disabled = false;
  };

  // ---------- выпадающий список в стиле приложения ----------
  const CHEV = '<svg class="i" viewBox="0 0 20 20"><path d="m5.5 8 4.5 4.5L14.5 8"/></svg>';
  function makeSelect(root, placeholder, onChange) {
    const ctl = { value: null, options: [], empty: 'Нет вариантов' };
    const btn = el('button', 'select-btn'); btn.type = 'button'; btn.setAttribute('aria-haspopup', 'listbox');
    const val = el('span', 'val ph', placeholder);
    btn.append(val); btn.insertAdjacentHTML('beforeend', CHEV); root.append(btn);
    let menu = null, act = -1;
    function close() { if (!menu) return; menu.remove(); menu = null; root.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); }
    function choose(o) { ctl.value = o.value; val.textContent = o.label; val.classList.remove('ph'); close(); btn.focus(); if (onChange) onChange(o.value); }
    function hl(i) {
      act = i; if (!menu) return;
      [...menu.children].forEach((li, j) => li.classList.toggle('act', j === i && ctl.options[j] && !ctl.options[j].disabled));
      if (menu.children[i]) menu.children[i].scrollIntoView({ block: 'nearest' });
    }
    function open() {
      root.classList.add('open'); btn.setAttribute('aria-expanded', 'true');
      menu = el('ul', 'select-menu'); menu.setAttribute('role', 'listbox');
      if (!ctl.options.length) menu.append(el('li', 'empty-opt', ctl.empty));
      ctl.options.forEach((o, i) => {
        const li = el('li', o.disabled ? 'off' : null, o.label);
        li.setAttribute('role', 'option'); li.setAttribute('aria-selected', String(o.value === ctl.value));
        if (o.hint) li.append(el('small', null, o.hint));
        li.onmouseenter = () => { if (!o.disabled) hl(i); };
        li.onmousedown = (e) => { e.preventDefault(); if (!o.disabled) choose(o); };  // фокус остаётся на кнопке
        menu.append(li);
      });
      root.append(menu);
      hl(Math.max(0, ctl.options.findIndex((o) => o.value === ctl.value)));
    }
    btn.onclick = () => (menu ? close() : open());
    btn.addEventListener('blur', close);
    btn.addEventListener('keydown', (e) => {
      if (!menu) { if (['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); open(); } return; }
      const n = ctl.options.length;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && n) {
        e.preventDefault();
        let i = act;
        for (let k = 0; k < n; k++) { i = (i + (e.key === 'ArrowDown' ? 1 : -1) + n) % n; if (!ctl.options[i].disabled) break; }
        hl(i);
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault(); const o = ctl.options[act]; if (o && !o.disabled) choose(o);
      }
    });
    ctl.set = (options, value, empty) => {
      ctl.options = options; if (empty) ctl.empty = empty;
      const o = options.find((x) => x.value === value && !x.disabled);
      ctl.value = o ? o.value : null; val.textContent = o ? o.label : placeholder; val.classList.toggle('ph', !o);
    };
    return ctl;
  }

  // ---------- форма: новый проект ----------
  const pScrim = $('#projectScrim');
  const MODES = [
    { value: 'create', label: 'Создать', hint: 'С нуля по теме и материалам' },
    { value: 'extend', label: 'Дополнить', hint: 'Добавить слайды в готовую' },
    { value: 'rebuild', label: 'Пересобрать', hint: 'Старое содержание в новом гайдлайне' },
  ];
  const FOLDER = '<svg class="i" viewBox="0 0 20 20"><path d="M2.5 6A1.5 1.5 0 0 1 4 4.5h3.6l1.6 1.8H16A1.5 1.5 0 0 1 17.5 7.8v6.7A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5Z"/></svg>';
  const P = { source: 'project', file: null, refs: [], presets: [] };
  const pMode = makeSelect($('#pMode'), 'Выбери задачу', () => renderSourceField());
  const pSrcProject = makeSelect($('#pSourceProject'), 'Выбери проект');
  const pGuide = makeSelect($('#pGuide'), 'Выбери гайдлайн');

  function renderSourceField() {
    const m = pMode.value;
    $('#pSourceField').hidden = m !== 'extend' && m !== 'rebuild';
    document.querySelectorAll('#pSourceType button').forEach((b) => b.classList.toggle('on', b.dataset.type === P.source));
    $('#pSourceProject').hidden = P.source !== 'project';
    $('#pSourceFileRow').hidden = P.source !== 'file';
    $('#pSourceHint').textContent = P.source === 'file'
      ? 'pptx, key или pdf. Текст и картинки возьмём, слайды сделаем заново.'
      : (m === 'extend' ? 'Старые слайды останутся, новые добавятся.' : 'Содержание переедет в выбранный гайдлайн.');
  }
  document.querySelectorAll('#pSourceType button').forEach((b) => { b.onclick = () => { P.source = b.dataset.type; renderSourceField(); }; });
  $('#pSourcePick').onclick = async () => {
    const b = $('#pSourcePick'); b.disabled = true; b.textContent = 'Выбор в окне…';
    const r = await pick('file', 'Исходная презентация');
    b.disabled = false; b.textContent = 'Выбрать файл…';
    if (r) { P.file = r.token; $('#pSourceFile').textContent = r.items[0].label; $('#pSourceFile').classList.add('set'); }
  };
  $('#pDuration').addEventListener('input', () => {
    const n = parseInt($('#pDuration').value, 10);
    $('#pDurationHint').textContent = n > 0 ? `≈ ${n} ${plural(n, 'слайд', 'слайда', 'слайдов')}`
      : '≈ 1 слайд на минуту';
  });

  function renderPresets() {
    const box = $('#pPresets'); box.innerHTML = '';
    P.presets.forEach((p) => {
      const chip = el('span', 'preset');
      const use = el('button', 'use', [p.name, p.position].filter(Boolean).join(' · ')); use.type = 'button'; use.title = 'Подставить';
      use.onclick = () => { $('#pName').value = p.name; $('#pPosition').value = p.position; };
      const del = el('button', 'del'); del.type = 'button'; del.innerHTML = ICON.x; del.title = 'Удалить пресет'; del.setAttribute('aria-label', del.title);
      del.onclick = async () => {
        try { P.presets = (await api('/api/presets', { action: 'delete', name: p.name, position: p.position })).presets; renderPresets(); }
        catch (e) { toast(e.message); }
      };
      chip.append(use, del); box.append(chip);
    });
    const save = el('button', 'btn-link', 'Сохранить как пресет'); save.type = 'button';
    save.onclick = async () => {
      try {
        P.presets = (await api('/api/presets', { action: 'save', name: $('#pName').value, position: $('#pPosition').value })).presets;
        renderPresets(); toast('Пресет сохранён');
      } catch (e) { toast(e.message); }
    };
    box.append(save);
  }

  function renderRefs() {
    const ul = $('#pRefs'); ul.innerHTML = '';
    P.refs.forEach((r, k) => {
      const li = el('li'); li.innerHTML = r.is_dir ? FOLDER : ICON.doc;
      const dir = r.label.slice(0, r.label.length - r.name.length).replace(/\/$/, '');
      const nm = el('span', 'nm', r.name), where = el('span', 'dir', dir);
      li.title = r.label; li.append(nm, where);
      const x = el('button'); x.type = 'button'; x.innerHTML = ICON.x; x.title = 'Убрать'; x.setAttribute('aria-label', 'Убрать ' + r.name);
      x.onclick = () => { P.refs.splice(k, 1); renderRefs(); };
      li.append(x); ul.append(li);
    });
  }
  async function addRefs(kind, btn) {
    const text = btn.textContent; btn.disabled = true; btn.textContent = 'Выбор в окне…';
    const r = await pick(kind, kind === 'folder' ? 'Папка с материалами' : 'Файлы с материалами');
    btn.disabled = false; btn.textContent = text;
    if (!r) return;
    r.items.forEach((it, i) => { if (!P.refs.some((x) => x.label === it.label)) P.refs.push(Object.assign({ token: r.token, i }, it)); });
    renderRefs();
  }
  $('#pAddFiles').onclick = () => addRefs('files', $('#pAddFiles'));
  $('#pAddFolder').onclick = () => addRefs('folder', $('#pAddFolder'));

  $('#newProject').onclick = async () => {
    P.source = 'project'; P.file = null; P.refs = [];
    ['#pTopic', '#pDuration', '#pName', '#pPosition'].forEach((s) => { $(s).value = ''; });
    $('#pDuration').dispatchEvent(new Event('input'));
    $('#pSourceFile').textContent = 'Не выбран'; $('#pSourceFile').classList.remove('set');
    $('#pSelfCheck').checked = true; $('#pError').hidden = true;
    pMode.set(MODES, 'create');
    const ready = S.guides.filter((g) => g.state === 'ready');
    const cur = S.mode === 'project' && S.data && S.data.guide && ready.find((g) => g.id === S.data.guide.id);
    pGuide.set(S.guides.map((g) => ({ value: g.id, label: g.name, disabled: g.state !== 'ready', hint: g.state !== 'ready' ? 'Собирается' : null })),
      (cur || ready[0] || {}).id, 'Сначала создай гайдлайн');
    pSrcProject.set(S.projects.map((p) => {
      const busy = S.projectStates[p] && S.projectStates[p] !== 'ready';
      return { value: p, label: p, disabled: busy, hint: busy ? 'Собирается' : null };
    }), null, 'Проектов нет — выбери файл');
    renderSourceField(); renderRefs(); renderPresets();
    openSheet(pScrim); setTimeout(() => $('#pTopic').focus(), 60);
    try { P.presets = (await api('/api/presets')).presets; renderPresets(); } catch (_) { /* пресеты необязательны */ }
  };
  $('#pCancel').onclick = () => closeSheet(pScrim);
  pScrim.addEventListener('mousedown', (e) => { if (e.target === pScrim) closeSheet(pScrim); });
  $('#projectForm').onsubmit = async (e) => {
    e.preventDefault();
    const err = $('#pError'); err.hidden = true;
    const fail = (m) => { err.textContent = m; err.hidden = false; };
    const mode = pMode.value;
    if (!mode) return fail('Выбери задачу');
    let source = null;
    if (mode !== 'create') {
      if (P.source === 'project') { if (!pSrcProject.value) return fail('Выбери проект'); source = { type: 'project', project: pSrcProject.value }; }
      else { if (!P.file) return fail('Выбери файл'); source = { type: 'file', token: P.file }; }
    }
    if (!pGuide.value) return fail('Выбери гайдлайн');
    const topic = $('#pTopic').value.trim();
    if (!topic) { $('#pTopic').focus(); return fail('Укажи тему'); }
    const dur = $('#pDuration').value.trim();
    const byToken = {};
    P.refs.forEach((r) => { (byToken[r.token] = byToken[r.token] || []).push(r.i); });
    const body = {
      mode, source, guide: pGuide.value, topic, duration: dur ? Number(dur) : null,
      author: { name: $('#pName').value.trim(), position: $('#pPosition').value.trim() },
      references: Object.keys(byToken).map((token) => ({ token, items: byToken[token] })),
      self_check: $('#pSelfCheck').checked,
    };
    $('#pSave').disabled = true;
    try {
      const r = await api('/api/projects', body);
      closeSheet(pScrim); toast('Проект создан');
      await refreshState(); selectProject(r.name);
    } catch (e2) { fail(e2.message); }
    $('#pSave').disabled = false;
  };

  // ---------- удаление проекта / гайдлайна ----------
  const dScrim = $('#deleteScrim');
  let pendingDelete = null;
  function askDelete(kind, id) {
    pendingDelete = { kind, id };
    const g = kind === 'guide' && S.guides.find((x) => x.id === id);
    const name = g ? g.name : id;
    $('#deleteTitle').textContent = kind === 'guide' ? `Удалить гайдлайн «${name}»?` : `Удалить проект «${name}»?`;
    let text = kind === 'guide'
      ? 'Папка гайдлайна переедет в Корзину.'
      : 'Папка проекта переедет в Корзину. Экспортированные файлы останутся.';
    if (g && g.used_by) text += ` Его используют ${g.used_by} ${plural(g.used_by, 'проект', 'проекта', 'проектов')}: слайды останутся, новых картинок из гайдлайна не будет.`;
    $('#deleteText').textContent = text + ' Вернуть можно из Корзины.';
    $('#deleteError').hidden = true; $('#deleteGo').disabled = false;
    openSheet(dScrim); setTimeout(() => $('#deleteCancel').focus(), 60);  // по умолчанию — безопасная кнопка
  }
  $('#deleteCancel').onclick = () => closeSheet(dScrim);
  dScrim.addEventListener('mousedown', (e) => { if (e.target === dScrim) closeSheet(dScrim); });
  $('#deleteGo').onclick = async () => {
    if (!pendingDelete) return;
    const { kind, id } = pendingDelete;
    $('#deleteGo').disabled = true;
    try {
      await api(`/api/${kind === 'guide' ? 'guides' : 'projects'}/${enc(id)}/delete`, {});
      closeSheet(dScrim); toast('Перемещено в Корзину');
      if (kind === 'project') { try { localStorage.removeItem(`pd:drafts:${id}`); localStorage.removeItem(`pd:current:${id}`); } catch (_) { /* приватный режим */ } }
      if (kind === 'project' && S.mode === 'project' && S.project === id) S.project = null;
      if (kind === 'guide' && S.mode === 'guide' && S.guide === id) { S.mode = 'project'; S.project = null; }
      await refreshState(); await refreshProject();
    } catch (e) {
      $('#deleteError').textContent = e.message; $('#deleteError').hidden = false; $('#deleteGo').disabled = false;
    }
  };

  // ---------- статус агента ----------
  function renderAgent() {
    const a = $('#agentStatus');
    const its = S.data ? S.data.iterations : [];
    const queued = its.filter((t) => t.status === 'queued').length;
    const ag = S.agent || {};
    let state, text, icon;
    if (ag.status === 'in_progress') { state = 'in_progress'; text = 'Агент работает'; icon = ST.in_progress[1]; }
    else if (ag.status === 'waiting_user') { state = 'waiting'; text = 'Ждёт ответа в чате'; icon = ST.needs_input[1]; }
    else if (ag.status === 'stalled' || its.some((t) => t.status === 'stalled')) { state = 'stalled'; text = 'Агент не отвечает'; icon = ST.stalled[1]; }
    else if (queued) { state = 'waiting'; text = `Ждёт агента · ${queued} ${plural(queued, 'задание', 'задания', 'заданий')}`; icon = ST.queued[1]; }
    else { state = 'idle'; text = 'Агент свободен'; icon = ST.completed[1]; }
    if (a.dataset.state !== state || $('#agentText').textContent !== text) {
      a.dataset.state = state; $('#agentIcon').innerHTML = icon; $('#agentText').textContent = text;
      a.title = ag.message || '';
    }
  }

  // ---------- экспорт ----------
  const scrim = $('#exportScrim');
  const FMT = { pdf: 'PDF', jpeg: 'JPEG', pptx: 'PPTX' };
  const shortPath = (p) => (p.includes('/Экспорт/') ? 'Экспорт/' + p.split('/Экспорт/').pop() : p.replace(/^\/Users\/[^/]+/, '~'));
  function defaultDest() { return `Экспорт/${S.data.name}`; }
  function renderDest() {
    $('#destLabel').textContent = S.dest ? S.dest.label : defaultDest();
    $('#destLabel').title = $('#destLabel').textContent;
    $('#destReset').hidden = !S.dest;
  }
  function openExport() {
    const vis = S.data.slides.filter((s) => !s.hidden).length;
    $('#exportSub').textContent = `${vis} ${plural(vis, 'слайд', 'слайда', 'слайдов')}` + (vis < S.data.slides.length ? ', скрытые не войдут' : '');
    scrim.hidden = false; scrim.classList.remove('closing');
    const ex = S.data.export;
    S.exportWatch = !!(ex && ex.status === 'in_progress');
    $('#exportProgress').hidden = !S.exportWatch;
    if (!S.chrome) showExportMsg('Для экспорта нужен Google Chrome. Установи его и повтори.', 'err');
    $('#exportGo').disabled = !S.chrome || S.exportWatch;
    renderDest(); renderLast(); renderExport();
    setTimeout(() => (scrim.querySelector('input:checked') || $('#exportGo')).focus(), 50);
  }
  function closeExport() {
    if (scrim.hidden) return;
    scrim.classList.add('closing');
    setTimeout(() => { scrim.hidden = true; scrim.classList.remove('closing'); }, 200);
  }
  function showExportMsg(text, cls) {
    $('#exportProgress').hidden = false;
    const m = $('#exportMsg'); m.textContent = text; m.className = cls || '';
  }
  function renderLast() {
    const box = $('#lastExports');
    const last = (S.data.export && S.data.export.last) || {};
    const sig = JSON.stringify(last);
    if (box.dataset.sig === sig) return;
    box.dataset.sig = sig; box.innerHTML = '';
    const keys = Object.keys(FMT).filter((k) => last[k]);
    box.hidden = !keys.length;
    keys.forEach((k) => {
      const row = el('div', 'last-row');
      row.append(el('span', 'fmt', FMT[k]));
      const meta = el('span', 'meta'); meta.append(el('b', null, 'Последний · ' + when(last[k].finished_at)), document.createTextNode(' · ' + shortPath(last[k].path)));
      meta.title = last[k].path; row.append(meta);
      const b = el('button', 'btn btn-plain btn-s', 'Показать в Finder'); b.type = 'button';
      b.onclick = () => api(`/api/projects/${enc(S.project)}/reveal`, { format: k }).catch((e) => toast(e.message));
      row.append(b); box.append(row);
    });
  }
  function renderExport() {
    if (scrim.hidden || !S.data) return;
    renderLast();
    const ex = S.data.export;
    if (!S.exportWatch || !ex) return;
    const pct = ex.total ? Math.round((ex.progress || 0) / ex.total * 100) : 0;
    $('#exportBar').style.width = (ex.status === 'completed' ? 100 : Math.max(4, pct)) + '%';
    if (ex.status === 'in_progress') {
      const PPTX_STAGE = { scan: 'Чтение слайдов…', raster: `Графики и баннеры: ${ex.progress || 0} из ${ex.total}`, build: 'Сборка файла…' };
      showExportMsg(ex.format === 'jpeg' ? `Картинки: ${ex.progress || 0} из ${ex.total}`
        : ex.format === 'pptx' ? (PPTX_STAGE[ex.stage] || 'PPTX…') : 'Сборка PDF…');
      $('#exportGo').disabled = true;
    } else if (ex.status === 'completed') {
      S.exportWatch = false;
      let done = 'Готово: ' + shortPath(ex.path);
      if (ex.format === 'pptx') {
        if (ex.raster && ex.raster.length) done += ` · картинкой: ${plural(ex.raster.length, 'слайд', 'слайды', 'слайды')} ${ex.raster.join(', ')}`;
        done += ' · на другом компьютере установи шрифты из папки «Шрифты»';
      }
      showExportMsg(done, 'ok');
      $('#exportGo').disabled = false;
    } else if (ex.status === 'failed') {
      S.exportWatch = false;
      showExportMsg(ex.error ? 'Ошибка: ' + ex.error : 'Ошибка экспорта', 'err');
      $('#exportGo').disabled = false;
    }
  }
  $('#exportBtn').onclick = () => { if (S.data) openExport(); };
  $('#exportClose').onclick = closeExport;
  scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) closeExport(); });
  $('#destChange').onclick = async () => {
    const b = $('#destChange'); b.disabled = true; b.textContent = 'Выбор в окне…';
    const r = await pick('folder', 'Папка для экспорта');  // ошибки показывает сам pick
    if (r) { S.dest = { token: r.token, label: r.items[0].label }; renderDest(); }
    b.disabled = false; b.textContent = 'Изменить…';
  };
  $('#destReset').onclick = () => { S.dest = null; renderDest(); };
  $('#exportGo').onclick = async () => {
    const fmt = scrim.querySelector('input[name=fmt]:checked').value;
    $('#exportGo').disabled = true; $('#exportBar').style.width = '4%';
    showExportMsg('Запуск…');
    try {
      await api(`/api/projects/${enc(S.project)}/export`, { format: fmt, target: S.dest ? S.dest.token : null });
      S.data.export = Object.assign({}, S.data.export, { status: 'in_progress', format: fmt, progress: 0, total: S.data.slides.filter((s) => !s.hidden).length });
      S.exportWatch = true; renderExport();
      S.dest = null;  // выбор папки — на один экспорт
    } catch (e) { showExportMsg(e.message, 'err'); $('#exportGo').disabled = false; }
  };

  // ---------- сайдбар ----------
  function setSide(collapsed) {
    $('#app').classList.toggle('side-collapsed', collapsed);
    const t = $('#sideToggle');
    t.title = collapsed ? 'Показать боковую панель' : 'Скрыть боковую панель';
    t.setAttribute('aria-label', t.title); t.setAttribute('aria-expanded', String(!collapsed));
    t.querySelector('span').textContent = collapsed ? 'Показать панель' : 'Скрыть панель';
    try { localStorage.setItem('pd:side-collapsed', collapsed ? '1' : ''); } catch (_) { /* приватный режим */ }
  }
  $('#sideToggle').onclick = () => setSide(!$('#app').classList.contains('side-collapsed'));
  try { setSide(localStorage.getItem('pd:side-collapsed') === '1'); } catch (_) { setSide(false); }

  // ---------- клавиатура ----------
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if (!dScrim.hidden) closeSheet(dScrim); else if (!gScrim.hidden) closeSheet(gScrim); else if (!pScrim.hidden) closeSheet(pScrim); else if (!scrim.hidden) closeExport(); else hidePopover(); return; }
    if (/TEXTAREA|INPUT/.test((document.activeElement || {}).tagName) || !scrim.hidden || !gScrim.hidden || !pScrim.hidden || !dScrim.hidden) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const list = slidesSorted(), i = list.findIndex((s) => s.id === S.current);
      const n = list[i + (e.key === 'ArrowDown' ? 1 : -1)];
      if (n) { e.preventDefault(); go(n.id); }
    }
  });

  const ro = new ResizeObserver(() => { fitCanvas(); fitThumbs(); hidePopover(); });
  ro.observe($('#canvasWrap')); ro.observe($('#thumbs'));
  fitCanvas(); renderDrafts(); tick();
})();
