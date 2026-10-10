/**
 * SARTHI client.
 * Queue of items (internships, jobs, freelance leads) on the left; SARTHI's thread on the right.
 * Items are addressed by ref: 'opp:<id>' (feed postings) or 'lead:<id>' (HN/Reddit leads).
 */

const state = {
  items: [],
  status: null,
  profile: {},
  skills: [],
  tab: 'new',
  kind: localStorage.getItem('sarthi.kind') || 'all',
  showSamples: localStorage.getItem('sarthi.showSamples') === '1',
  selected: null,            // ref
  page: null,                // 'profile' | 'settings' | null
  threads: { overview: [] }, // key -> messages
  extra: {},                 // items opened from search results that aren't in the queue
  scanning: false,
  sending: false,
  lastRunsKey: '',
};

const $ = (id) => document.getElementById(id);
const UNDO_SECONDS = 5;

document.addEventListener('DOMContentLoaded', async () => {
  setupInput();
  setupDropzone();
  setupKeys();
  await Promise.all([loadProfile(), loadStatus(), loadItems()]);
  state.lastRunsKey = runsKey();
  renderAll();
  const linked = (location.hash.match(/item=([a-z]+:\d+)/) || [])[1];
  if (linked) selectItem(linked);
  else if (location.hash === '#profile') showPage('profile');
  else if (location.hash === '#settings') showPage('settings');
  setInterval(poll, 30000);
});

/* ---------------- Data ---------------- */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function loadItems() {
  const data = await api('/api/items');
  state.items = data.items || [];
  state.totalPostings = data.total_postings || 0;
}

async function loadStatus() {
  state.status = await api('/api/status').catch(() => null);
}

async function loadProfile() {
  const data = await api('/api/profile').catch(() => ({}));
  state.profile = data.profile || {};
  const skills = state.profile.skills || {};
  state.skills = Array.isArray(skills) ? [...skills] : Object.values(skills).flat().filter(Boolean);
  if (state.profile.name) $('profile-name').textContent = state.profile.name.split(' ')[0];
}

function runsKey() {
  const runs = state.status?.runs || {};
  return Object.values(runs).map((r) => r.finished_at).sort().join('|');
}

/** Background refresh: picks up auto-scan results without disturbing what you're doing. */
async function poll() {
  if (state.scanning) return;
  await loadStatus();
  const key = runsKey();
  if (key !== state.lastRunsKey) {
    state.lastRunsKey = key;
    await loadItems();
    renderQueue();
    if (!state.selected && !state.page) renderStage();
  }
  renderSourceLine();
  updateTitle();
}

function itemByRef(ref) {
  return state.items.find((i) => i.ref === ref) || state.extra[ref];
}

function visibleItems(tab = state.tab) {
  return state.items
    .filter((i) =>
      i.tab === tab &&
      (state.kind === 'all' || i.kind === state.kind) &&
      (state.showSamples || !i.is_sample))
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
}

/* ---------------- Rendering ---------------- */

function renderAll() {
  renderSourceLine();
  renderQueue();
  renderStage();
  updateTitle();
}

function updateTitle() {
  const alerts = (state.status?.alerts || []).reduce((s, a) => s + (a.new_count || 0), 0);
  document.title = alerts ? `(${alerts}) SARTHI` : 'SARTHI';
}

function renderSourceLine() {
  const s = state.status;
  if (!s) return;
  const latest = Object.values(s.runs || {}).map((r) => r.finished_at).sort().pop();
  const failing = Object.entries(s.runs || {}).filter(([, r]) => !r.ok).map(([k]) => labelFor(k));
  const interval = s.settings?.scan_interval_minutes;
  const parts = [
    `<span class="source">${s.scanning ? 'Scanning now' : latest ? `Last scan ${timeAgo(latest)}` : 'No scan yet'}</span>`,
    `<span class="source ${interval ? 'ok' : ''}"><span class="dot"></span>${interval ? `Auto every ${formatInterval(interval)}` : 'Auto-scan off'}</span>`,
  ];
  if (failing.length) parts.push(`<span class="source bad" title="Open Settings for details"><span class="dot"></span>${failing.join(', ')} off</span>`);
  if (!s.gemini) parts.push(`<span class="source bad"><span class="dot"></span>Gemini off</span>`);
  $('source-line').innerHTML = parts.join('');
  $('brand-chakra').classList.toggle('spinning', !!s.scanning || state.scanning);
}

function labelFor(key) {
  return (state.status?.sources || []).find((s) => s.key === key)?.label || { hn: 'Hacker News', reddit: 'Reddit', companies: 'Company careers' }[key] || key;
}

function renderQueue() {
  document.querySelectorAll('#kinds button').forEach((b) => b.classList.toggle('active', b.dataset.kind === state.kind));
  for (const tab of ['new', 'sent', 'won', 'passed']) $(`count-${tab}`).textContent = visibleItems(tab).length;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === state.tab));

  const list = visibleItems();
  const container = $('queue-list');
  if (!list.length) {
    const empty = {
      new: 'Nothing waiting here. Run a scan, or ask SARTHI for something specific.',
      sent: 'Applications and replies you send show up here, so you can follow up.',
      won: 'Offers and work you win land here.',
      passed: "Posts you pass on, or that didn't work out, go here.",
    };
    container.innerHTML = `<div class="queue-empty">${empty[state.tab]}</div>`;
  } else {
    container.innerHTML = list.map(rowHtml).join('');
  }

  const sampleCount = state.items.filter((i) => i.is_sample).length;
  const toggle = $('toggle-samples');
  toggle.hidden = sampleCount === 0;
  toggle.textContent = state.showSamples ? `Hide ${sampleCount} sample posts` : `Show ${sampleCount} sample posts`;
  refreshIcons();
}

function rowHtml(i) {
  return `
    <button class="lead-row ${i.ref === state.selected ? 'selected' : ''}" data-ref="${i.ref}" onclick="selectItem('${i.ref}')">
      <span class="lead-score">${scoreText(i.score, false)}</span>
      <span class="lead-title">${escapeHtml(i.title)}${i.company ? `<span class="row-company"> · ${escapeHtml(i.company)}</span>` : ''}</span>
      <span class="lead-meta">
        ${i.is_sample ? '<span class="tag-sample">SAMPLE</span>' : ''}
        <span>${escapeHtml(i.source_label)}</span>
        ${i.pay_text ? `<span>${escapeHtml(shorten(i.pay_text, 22))}</span>` : ''}
        ${i.contact_email ? '<span class="meta-mail" title="Accepts email applications"><i data-lucide="mail"></i></span>' : ''}
        <span>${timeAgo(i.posted_at)}</span>
      </span>
    </button>`;
}

function scoreText(score, outOf = true) {
  if (score === null || score === undefined) return '<small>–</small>';
  const v = Number(score);
  return `${Number.isInteger(v) ? v : v.toFixed(1)}${outOf ? '<small>/10</small>' : ''}`;
}

function renderStage() {
  if (state.page) return;
  const thread = $('thread');
  const item = state.selected ? itemByRef(state.selected) : null;

  if (item) {
    thread.innerHTML = itemCardHtml(item) + messagesHtml(state.threads[item.ref] || []);
    $('context-chip').hidden = false;
    $('context-label').textContent = item.title;
    $('input').placeholder = 'Ask about this post, or say "make it shorter"';
    wireDraft(item);
  } else {
    thread.innerHTML = messageHtml({ role: 'ai', ...briefing() }) + peekHtml() + messagesHtml(state.threads.overview);
    $('context-chip').hidden = true;
    $('input').placeholder = 'Ask for roles, or paste a LinkedIn post to track that company';
  }
  refreshIcons();
}

function briefing() {
  const real = state.items.filter((i) => !i.is_sample);
  const waiting = real.filter((i) => i.tab === 'new');
  const sent = real.filter((i) => i.tab === 'sent').length;
  const s = state.status;
  const lines = [];
  const name = state.profile.name ? state.profile.name.split(' ')[0] : null;
  const hello = name ? `Hey ${name}.` : 'Hey.';

  if (!Object.keys(s?.runs || {}).length && !real.length) {
    lines.push(`${hello} I haven't scanned yet. Hit **Scan** and I'll pull internships, jobs and freelance posts from every source, then rank them for you.`);
  } else if (waiting.length) {
    const top = [...waiting].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
    lines.push(`${hello} ${waiting.length} ${waiting.length === 1 ? 'post fits' : 'posts fit'} your profile and ${waiting.length === 1 ? 'is' : 'are'} waiting. The strongest is **${top.title}**${top.company ? ` at ${top.company}` : ''}${top.pay_text ? `, ${top.pay_text}` : ''}.`);
    lines.push('Open one to read it and apply, or ask me for something specific.');
  } else {
    lines.push(`${hello} Nothing new is waiting.${sent ? ` You've applied to ${sent} so far.` : ''} Run a scan, or ask me for something specific.`);
  }

  const notes = [];
  if (state.totalPostings) notes.push(`I'm tracking ${state.totalPostings.toLocaleString()} postings${s?.companies?.boards ? `, including ${s.companies.boards} company careers boards I check directly` : ''}.`);
  for (const [key, r] of Object.entries(s?.runs || {})) {
    if (!r.ok && r.error) notes.push(`${labelFor(key)} was skipped: ${r.error}`);
  }
  if (s && !s.gemini) notes.push('Gemini is not configured, so drafts use plain templates.');
  if (s && !s.email) notes.push('Email sending is not set up yet. See Settings.');
  const samples = state.items.filter((i) => i.is_sample).length;
  if (samples && !state.showSamples) notes.push(`${samples} sample posts from the old demo data are hidden. They aren't real.`);

  const alertButtons = (s?.alerts || []).filter((a) => a.new_count);
  return { text: lines.join('\n\n'), notes, alertButtons };
}

function peekHtml() {
  const waiting = state.items
    .filter((i) => i.tab === 'new' && (state.showSamples || !i.is_sample) && (state.kind === 'all' || i.kind === state.kind))
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 3);
  return waiting.length ? `<div class="peek">${waiting.map(peekRowHtml).join('')}</div>` : '';
}

function peekRowHtml(i) {
  const why = i.why || i.description || '';
  return `
    <button class="peek-row" onclick="selectItem('${i.ref}')">
      <span class="lead-score">${scoreText(i.score)}</span>
      <span class="peek-main">
        <span class="lead-title">${escapeHtml(i.title)}${i.company ? `<span class="row-company"> · ${escapeHtml(i.company)}</span>` : ''}</span>
        <span class="why">${escapeHtml(shorten(why, 150))}</span>
      </span>
      <i data-lucide="arrow-right"></i>
    </button>`;
}

/* ---------------- Item card ---------------- */

function itemCardHtml(i) {
  const meta = [
    escapeHtml(i.source_label),
    i.location ? escapeHtml(shorten(i.location, 40)) : null,
    i.pay_text ? `<b>${escapeHtml(i.pay_text)}</b>` : 'pay not stated',
    i.deadline ? `<b>closes ${dueIn(i.deadline)}</b>` : null,
    i.experience_years ? `${i.experience_years} yr exp asked` : null,
    i.posted_at ? `posted ${timeAgo(i.posted_at)}` : null,
    i.score !== null && i.score !== undefined ? `<b>${scoreText(i.score).replace(/<\/?small>/g, '')}</b>` : null,
  ].filter(Boolean);
  const tech = (i.skills || []).slice(0, 12).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('');
  const closed = i.active === false ? '<span class="tag-sample">NO LONGER LISTED</span>' : '';

  return `
    <article class="lead-card">
      <section>
        <div class="card-meta">${i.is_sample ? '<span class="tag-sample">SAMPLE</span>' : ''}${closed}${meta.map((m) => `<span>${m}</span>`).join('')}</div>
        <h2 class="card-title">${escapeHtml(i.title)}</h2>
        ${i.company ? `<p class="card-company">${escapeHtml(i.company)}</p>` : ''}
        ${tech ? `<div class="chips">${tech}</div>` : ''}
      </section>
      ${i.why ? `
      <section>
        <span class="label">Why it fits</span>
        <p class="why">${escapeHtml(i.why)}</p>
      </section>` : ''}
      ${i.description ? `
      <section>
        <span class="label">The post</span>
        <div class="post-text" id="post-text">${linkify(escapeHtml(i.description))}</div>
        <button class="link-btn post-toggle" onclick="togglePost(this)">Show full post</button>
        ${i.contact_hint && !/^(n\/a|none)/i.test(i.contact_hint) ? `<p class="contact"><span>How to reach them</span>${linkify(escapeHtml(i.contact_hint))}</p>` : ''}
      </section>` : ''}
      <section id="draft-section">${draftSectionHtml(i)}</section>
      ${i.contact_email ? `<section id="email-section">${emailSectionHtml(i)}</section>` : ''}
      <section class="card-actions">${actionsHtml(i)}<span class="spacer"></span><span class="hint" id="card-hint"></span></section>
    </article>`;
}

function draftSectionHtml(i) {
  if (!i.draft) {
    return `
      <span class="label">Your application</span>
      <p class="muted">No draft yet. SARTHI writes one from your resume and this post, and you can edit it before sending.</p>
      <div class="inline-actions"><button class="btn btn-quiet" id="btn-write" onclick="writeDraft()"><i data-lucide="pen-line"></i>Write my application</button></div>`;
  }
  return `
    <div class="draft-head">
      <span class="label">${i.type === 'lead' ? 'Your reply' : 'Your application'}</span>
      <span class="num" id="draft-count">${wordCount(i.draft)} words</span>
    </div>
    <textarea class="draft" id="draft" spellcheck="true">${escapeHtml(i.draft)}</textarea>`;
}

function emailSectionHtml(i) {
  const s = state.status || {};
  if (!s.email) {
    return `
      <span class="label">Email</span>
      <p class="muted">This post takes applications at <b>${escapeHtml(i.contact_email)}</b>. Set up email in <button class="link-btn inline" onclick="showPage('settings')">Settings</button> to send from here in one click.</p>`;
  }
  return `
    <span class="label">Email</span>
    <div class="email-grid">
      <label for="mail-to">To</label><input id="mail-to" value="${escapeAttr(i.contact_email)}">
      <label for="mail-subject">Subject</label><input id="mail-subject" value="${escapeAttr(defaultSubject(i))}">
    </div>
    <label class="check-line"><input type="checkbox" id="mail-attach" ${s.resume_file ? 'checked' : 'disabled'}>
      ${s.resume_file ? `Attach my resume (${escapeHtml(s.resume_file)})` : 'No resume file yet. Upload one in Profile to attach it.'}</label>`;
}

function defaultSubject(i) {
  const name = state.profile.name || '';
  return i.type === 'lead'
    ? `Re: ${shorten(i.title, 70)}`
    : `Application: ${i.title}${name ? ` – ${name}` : ''}`;
}

function actionsHtml(i) {
  const s = state.status || {};
  const canEmail = i.contact_email && s.email && i.draft;
  const primary = canEmail
    ? `<button class="btn btn-primary" id="btn-send-mail" onclick="sendEmail()"><i data-lucide="send"></i>Send email</button>`
    : i.is_sample
      ? `<button class="btn btn-primary" onclick="copyDraft()"><i data-lucide="copy"></i>Copy reply</button>`
      : `<button class="btn btn-primary" onclick="copyAndOpen()"><i data-lucide="external-link"></i>${i.draft ? 'Copy & open' : 'Open'} on ${escapeHtml(i.source_label)}</button>`;
  const btn = (status, icon, label) =>
    `<button class="btn btn-quiet" onclick="setStatus('${status}')"><i data-lucide="${icon}"></i>${label}</button>`;

  if (i.type === 'lead') {
    switch (i.status) {
      case 'new': return primary + btn('contacted', 'check', 'Mark sent') + btn('lost', 'x', 'Pass');
      case 'contacted': return primary + btn('replied', 'message-square', 'They replied') + btn('lost', 'x', 'No luck');
      case 'replied': return btn('won', 'trophy', 'Won it') + btn('lost', 'x', 'Lost it');
      default: return btn('new', 'rotate-ccw', 'Move back to New');
    }
  }
  switch (i.status) {
    case 'new': return primary + btn('applied', 'check', 'Mark applied') + btn('passed', 'x', 'Pass');
    case 'applied': return btn('interviewing', 'message-square', 'Interviewing') + btn('rejected', 'x', 'Rejected');
    case 'interviewing': return btn('offer', 'trophy', 'Got an offer') + btn('rejected', 'x', 'Rejected');
    default: return btn('new', 'rotate-ccw', 'Move back to New');
  }
}

/* ---------------- Messages ---------------- */

function messagesHtml(list) {
  return list.map(messageHtml).join('');
}

function messageHtml(m) {
  if (m.role === 'user') {
    return `<div class="msg user"><div class="body">${renderText(m.text)}</div></div>`;
  }
  const suggestion = m.draft ? `
    <div class="suggestion">${escapeHtml(m.draft)}</div>
    <div class="inline-actions">
      <button class="btn btn-quiet" onclick="useDraft(this)" data-draft="${escapeAttr(m.draft)}"><i data-lucide="check"></i>Use this</button>
    </div>` : '';
  const chips = m.chips?.length ? `<div class="chips query-chips">${m.chips.map((c) => `<span class="chip">${escapeHtml(c)}</span>`).join('')}</div>` : '';
  const results = m.results?.length ? `<div class="peek in-thread">${m.results.map(peekRowHtml).join('')}</div>` : '';
  const alertBtn = m.searchText ? `
    <div class="inline-actions">
      <button class="btn btn-quiet" onclick="saveAlert(this)" data-text="${escapeAttr(m.searchText)}"><i data-lucide="bell"></i>Alert me about new matches</button>
    </div>` : '';
  const alertLinks = m.alertButtons?.length ? `
    <div class="inline-actions">${m.alertButtons.map((a) =>
      `<button class="btn btn-quiet" onclick="openAlert('${a.id}')"><i data-lucide="bell"></i>${escapeHtml(shorten(a.text, 40))} · ${a.new_count} new</button>`).join('')}</div>` : '';
  const body = m.thinking
    ? `<span class="thinking">${escapeHtml(m.text)}<span class="num" data-elapsed></span></span>`
    : renderText(m.text) + chips + notesHtml(m.notes) + results + alertBtn + alertLinks + suggestion;
  return `
    <div class="msg ai" ${m.id ? `id="${m.id}"` : ''}>
      <div class="avatar"><svg class="chakra ${m.thinking ? 'spinning' : ''}"><use href="#chakra" /></svg></div>
      <div class="body">${body}</div>
    </div>`;
}

function notesHtml(notes) {
  return notes?.length ? `<p class="note">${notes.map(escapeHtml).join('<br>')}</p>` : '';
}

/* ---------------- Navigation ---------------- */

function selectItem(ref) {
  closePage(false);
  state.selected = ref;
  history.replaceState(null, '', `#item=${ref}`);
  if (!itemByRef(ref)) {
    // Deep link or search result not in the queue yet.
    api(`/api/items/${ref}`).then((it) => { state.extra[ref] = it; renderStage(); }).catch(() => showOverview());
  }
  renderQueue();
  renderStage();
  $('thread').scrollTop = 0;
  document.querySelector(`.lead-row[data-ref="${ref}"]`)?.scrollIntoView({ block: 'nearest' });
}

function showOverview() {
  closePage(false);
  state.selected = null;
  history.replaceState(null, '', location.pathname);
  renderQueue();
  renderStage();
}

function setTab(tab) {
  state.tab = tab;
  renderQueue();
}

function setKind(kind) {
  state.kind = kind;
  localStorage.setItem('sarthi.kind', kind);
  renderQueue();
  if (!state.selected) renderStage();
}

function toggleSamples() {
  state.showSamples = !state.showSamples;
  localStorage.setItem('sarthi.showSamples', state.showSamples ? '1' : '0');
  if (itemByRef(state.selected)?.is_sample && !state.showSamples) state.selected = null;
  renderQueue();
  renderStage();
}

function togglePost(btn) {
  const el = $('post-text');
  el.classList.toggle('expanded');
  btn.textContent = el.classList.contains('expanded') ? 'Show less' : 'Show full post';
}

/* ---------------- Item actions ---------------- */

function wireDraft(item) {
  const box = $('draft');
  if (!box) return;
  autosize(box);
  box.addEventListener('input', () => {
    autosize(box);
    $('draft-count').textContent = `${wordCount(box.value)} words`;
  });
  box.addEventListener('blur', () => saveDraft(item, box.value));
}

async function saveDraft(item, text) {
  if (text === (item.draft || '')) return;
  try {
    await api(`/api/items/${item.ref}/draft`, { method: 'PUT', body: JSON.stringify({ draft: text }) });
    item.draft = text;
    hint('Saved');
  } catch (err) {
    hint(`Couldn't save: ${err.message}`);
  }
}

async function writeDraft() {
  const item = itemByRef(state.selected);
  if (!item) return;
  const btn = $('btn-write');
  btn.disabled = true;
  btn.innerHTML = `<svg class="chakra spinning small"><use href="#chakra" /></svg>Writing from your resume and the post`;
  try {
    const data = await api(`/api/items/${item.ref}/write-draft`, { method: 'POST' });
    item.draft = data.draft;
    renderStage();
    hint('Draft written. Read it before sending.');
  } catch (err) {
    btn.disabled = false;
    btn.textContent = `Couldn't write it: ${err.message}`;
  }
}

async function copyDraft() {
  const text = $('draft')?.value || '';
  if (text) await navigator.clipboard.writeText(text);
  hint(text ? 'Copied' : 'Nothing to copy yet');
}

async function copyAndOpen() {
  const item = itemByRef(state.selected);
  if (!item) return;
  const text = $('draft')?.value;
  if (text) await navigator.clipboard.writeText(text);
  window.open(item.url, '_blank', 'noopener');
  hint(text ? 'Copied. Paste it into their form, then mark it applied.' : 'Opened. Mark it applied once you have.');
}

/** One click, with a few seconds to undo, then the email goes out from your account. */
async function sendEmail() {
  const item = itemByRef(state.selected);
  const btn = $('btn-send-mail');
  if (!item || !btn) return;
  const payload = {
    ref: item.ref,
    to: $('mail-to').value.trim(),
    subject: $('mail-subject').value.trim(),
    body: $('draft').value,
    attach_resume: $('mail-attach')?.checked ?? false,
  };

  let cancelled = false;
  let left = UNDO_SECONDS;
  const original = btn.innerHTML;
  btn.classList.add('btn-undo');
  const tick = () => { btn.innerHTML = `<i data-lucide="undo-2"></i>Sending in ${left}s · Undo`; refreshIcons(); };
  tick();
  btn.onclick = () => { cancelled = true; };
  await new Promise((resolve) => {
    const timer = setInterval(() => {
      left -= 1;
      if (cancelled || left <= 0) { clearInterval(timer); resolve(); } else tick();
    }, 1000);
  });

  btn.classList.remove('btn-undo');
  btn.onclick = sendEmail;
  if (cancelled) {
    btn.innerHTML = original;
    refreshIcons();
    hint('Cancelled. Nothing was sent.');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = `<svg class="chakra spinning small"><use href="#chakra" /></svg>Sending`;
  try {
    const res = await api('/api/outreach/send', { method: 'POST', body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(res.error);
    item.draft = payload.body;
    if (item.status === 'new') {
      item.status = item.type === 'lead' ? 'contacted' : 'applied';
      item.tab = 'sent';
      if (!state.items.includes(item)) state.items.push(item);
    }
    renderQueue();
    renderStage();
    pushMessage(item.ref, { role: 'ai', text: `Sent to ${payload.to}${res.attached ? ` with ${res.attached} attached` : ''}. I moved it to Sent.` });
  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = original;
    refreshIcons();
    hint(`Not sent: ${err.message}`);
  }
}

async function setStatus(status) {
  const item = itemByRef(state.selected);
  if (!item) return;
  if ($('draft')) await saveDraft(item, $('draft').value);
  try {
    await api(`/api/items/${item.ref}/status`, { method: 'POST', body: JSON.stringify({ status }) });
  } catch (err) {
    hint(err.message);
    return;
  }
  const tabOf = { new: 'new', contacted: 'sent', replied: 'sent', applied: 'sent', interviewing: 'sent', won: 'won', offer: 'won', lost: 'passed', rejected: 'passed', passed: 'passed' };
  const before = visibleItems();
  const idx = before.findIndex((i) => i.ref === item.ref);
  item.status = status;
  item.tab = tabOf[status];
  if (!state.items.includes(item)) state.items.push(item);
  if (item.tab !== state.tab && idx !== -1) {
    // Inbox behaviour: open the next item in this tab.
    const after = visibleItems();
    const next = after[Math.min(idx, after.length - 1)];
    state.selected = next ? next.ref : null;
    history.replaceState(null, '', next ? `#item=${next.ref}` : location.pathname);
  }
  renderQueue();
  renderStage();
}

function useDraft(btn) {
  const item = itemByRef(state.selected);
  if (!item) return;
  const text = btn.dataset.draft;
  saveDraft(item, text).then(() => {
    item.draft = text;
    renderStage();
    $('draft')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

function hint(text) {
  const el = $('card-hint');
  if (!el) return;
  el.textContent = text;
  clearTimeout(hint.timer);
  hint.timer = setTimeout(() => (el.textContent = ''), 4000);
}

/* ---------------- Conversation ---------------- */

function threadKey() {
  return state.selected ?? 'overview';
}

function pushMessage(key, msg) {
  (state.threads[key] ||= []).push(msg);
  if (key === threadKey() && !state.page) {
    $('thread').insertAdjacentHTML('beforeend', messageHtml(msg));
    refreshIcons();
    scrollThread();
  }
}

function showThinking(key, text) {
  const id = `thinking-${Date.now()}`;
  pushMessage(key, { role: 'ai', text, thinking: true, id });
  const started = Date.now();
  const timer = setInterval(() => {
    const el = document.querySelector(`#${id} [data-elapsed]`);
    if (el) el.textContent = formatElapsed(Date.now() - started);
  }, 1000);
  return () => {
    clearInterval(timer);
    state.threads[key] = state.threads[key].filter((m) => m.id !== id);
    document.getElementById(id)?.remove();
  };
}

async function send() {
  const input = $('input');
  const text = input.value.trim();
  if (!text || state.sending) return;
  if (state.page) closePage();

  const key = threadKey();
  const history = (state.threads[key] || []).filter((m) => !m.thinking).map((m) => ({
    role: m.role === 'user' ? 'user' : 'assistant',
    content: m.text,
  }));

  input.value = '';
  autosize(input);
  pushMessage(key, { role: 'user', text });
  state.sending = true;
  $('btn-send').disabled = true;
  const stop = showThinking(key, key === 'overview' ? 'Thinking' : 'Reading the post');

  try {
    const data = await api('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message: text, history, item_ref: key === 'overview' ? null : key }),
    });
    stop();
    if (data.action === 'scan') {
      runScan();
    } else {
      (data.results || []).forEach((r) => { if (!itemByRef(r.ref)) state.extra[r.ref] = r; });
      pushMessage(key, {
        role: 'ai',
        text: data.reply || '',
        draft: data.draft || null,
        chips: data.chips,
        notes: data.note ? [data.note] : null,
        results: data.results,
        searchText: data.search_text,
      });
    }
  } catch (err) {
    stop();
    pushMessage(key, { role: 'ai', text: `Something went wrong: ${err.message}` });
  } finally {
    state.sending = false;
    $('btn-send').disabled = false;
  }
}

async function runScan() {
  if (state.scanning) return;
  state.scanning = true;
  $('btn-scan').disabled = true;
  $('brand-chakra').classList.add('spinning');
  if (state.selected || state.page) showOverview();

  const stop = showThinking('overview', 'Checking job feeds and company careers boards, then ranking what fits. This takes two or three minutes.');
  try {
    const report = await api('/api/scan', { method: 'POST' });
    stop();
    await Promise.all([loadItems(), loadStatus()]);
    state.lastRunsKey = runsKey();
    const checked = Object.entries(report.sources || {});
    const ran = checked.filter(([, r]) => r.fetched !== undefined);
    const skipped = checked.filter(([, r]) => r.skipped).map(([k]) => labelFor(k));
    const failed = checked.filter(([, r]) => r.error).map(([k, r]) => `${labelFor(k)}: ${r.error}`);
    const newCount = (report.new || 0) + (report.new_leads || 0);
    const text = newCount
      ? `Done. **${newCount} new postings** came in from ${ran.length} sources. The best fits are in New.`
      : `Done. I checked ${ran.length} sources and found nothing new since the last scan.`;
    const notes = [];
    const crawl = report.sources?.companies?.crawl;
    if (crawl) {
      notes.push(`Company careers: checked ${crawl.checked} companies, ${crawl.matches} open roles fit you.` +
        (crawl.not_found ? ` ${crawl.not_found} had no public board.` : '') +
        (crawl.remaining_due ? ` ${crawl.remaining_due} more will be checked on the next scans.` : ''));
    }
    if (skipped.length) notes.push(`Checked recently, so skipped this time: ${skipped.join(', ')}.`);
    notes.push(...failed);
    state.tab = 'new';
    renderAll();
    pushMessage('overview', { role: 'ai', text, notes });
  } catch (err) {
    stop();
    pushMessage('overview', { role: 'ai', text: `The scan didn't run: ${err.message}` });
  } finally {
    state.scanning = false;
    $('btn-scan').disabled = false;
    renderSourceLine();
  }
}

/* ---------------- Alerts ---------------- */

async function saveAlert(btn) {
  try {
    await api('/api/alerts', { method: 'POST', body: JSON.stringify({ text: btn.dataset.text }) });
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="bell-ring"></i>Alert saved. New matches will show in your briefing.';
    refreshIcons();
    await loadStatus();
  } catch (err) {
    btn.textContent = `Couldn't save: ${err.message}`;
  }
}

async function openAlert(id) {
  const alert = (state.status?.alerts || []).find((a) => a.id === id);
  if (!alert) return;
  await api(`/api/alerts/${id}/seen`, { method: 'POST' });
  await loadStatus();
  $('input').value = alert.text;
  send();
}

async function deleteAlert(id) {
  await api(`/api/alerts/${id}`, { method: 'DELETE' });
  await loadStatus();
  renderSettings();
}

/* ---------------- Pages: profile & settings ---------------- */

function showPage(name) {
  state.page = name;
  $('thread').hidden = true;
  $('composer').hidden = true;
  $('page-profile').hidden = name !== 'profile';
  $('page-settings').hidden = name !== 'settings';
  history.replaceState(null, '', `#${name}`);
  if (name === 'profile') fillProfile();
  if (name === 'settings') renderSettings();
  refreshIcons();
}

function closePage(render = true) {
  if (!state.page) return;
  state.page = null;
  $('page-profile').hidden = true;
  $('page-settings').hidden = true;
  $('thread').hidden = false;
  $('composer').hidden = false;
  if (render) {
    history.replaceState(null, '', state.selected ? `#item=${state.selected}` : location.pathname);
    renderStage();
  }
}

function fillProfile() {
  const p = state.profile;
  $('f-name').value = p.name || '';
  $('f-rate').value = p.hourly_rate_usd ?? '';
  $('f-min').value = p.minimum_project_budget_usd ?? '';
  $('f-github').value = p.github || '';
  $('f-linkedin').value = p.linkedin || '';
  const s = p.search || {};
  document.querySelectorAll('#f-kinds input').forEach((c) => { c.checked = (s.kinds || ['internship']).includes(c.value); });
  $('f-locations').value = (s.locations || []).join(', ');
  $('f-exp').value = s.experience_years ?? '';
  $('f-pay').value = s.expected_monthly ?? '';
  $('f-currency').value = s.currency || 'INR';
  $('f-remote-only').checked = !!s.remote_only;
  renderSkillChips();
}

async function saveSearchPrefs(e) {
  e.preventDefault();
  const kinds = [...document.querySelectorAll('#f-kinds input:checked')].map((c) => c.value);
  const prefs = {
    kinds: kinds.length ? kinds : ['internship'],
    locations: $('f-locations').value.split(',').map((s) => s.trim()).filter(Boolean),
    remote_only: $('f-remote-only').checked,
    experience_years: $('f-exp').value === '' ? null : Number($('f-exp').value),
    expected_monthly: $('f-pay').value === '' ? null : Number($('f-pay').value),
    currency: $('f-currency').value,
  };
  try {
    await api('/api/profile/search', { method: 'PUT', body: JSON.stringify(prefs) });
    await Promise.all([loadProfile(), loadItems()]);
    renderQueue();
    $('search-status').textContent = 'Saved. Your queue is re-ranked.';
  } catch (err) {
    $('search-status').textContent = `Couldn't save: ${err.message}`;
  }
}

function renderSettings() {
  const s = state.status || {};
  $('s-interval').value = String(s.settings?.scan_interval_minutes ?? 0);

  const disabled = new Set(s.settings?.disabled_sources || []);
  const allSources = [...(s.sources || []),
    { key: 'hn', label: 'Hacker News', about: "Monthly 'Who is hiring' and freelancer threads, scored with Gemini." },
    { key: 'reddit', label: 'Reddit', about: 'r/forhire and similar. Needs valid API keys in .env.' },
    { key: 'companies', label: 'Company careers boards', about: 'Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Workday, YC and plain careers pages, read directly.' }];
  $('s-sources').innerHTML = allSources.map((src) => {
    const run = s.runs?.[src.key];
    const statusText = !run ? 'Not run yet'
      : run.ok ? `${run.fetched} fetched, ${run.new} new · ${timeAgo(run.finished_at)}`
      : `Failed: ${run.error}`;
    const toggleable = !['hn', 'reddit'].includes(src.key);
    return `
      <div class="source-item">
        <div>
          <b>${escapeHtml(src.label)}</b>
          <p class="muted">${escapeHtml(src.about)}</p>
          <p class="mono small ${run && !run.ok ? 'warn' : 'muted'}">${escapeHtml(statusText)}</p>
        </div>
        ${toggleable ? `<label class="switch" title="Use this source"><input type="checkbox" ${disabled.has(src.key) ? '' : 'checked'} onchange="toggleSource('${src.key}', this.checked)"><span></span></label>` : ''}
      </div>`;
  }).join('');

  $('s-email').innerHTML = s.email
    ? `<div class="setting-row">
         <div><b>Sending from your account</b><p class="muted">Each email waits ${UNDO_SECONDS} seconds so you can undo. Daily limit:</p></div>
         <input type="number" min="1" max="50" class="mono cap-input" value="${s.settings?.daily_email_cap ?? 20}" onchange="saveSettings({ daily_email_cap: Number(this.value) })">
       </div>
       <div class="inline-actions"><button class="btn btn-quiet" onclick="testEmail(this)"><i data-lucide="mail-check"></i>Send a test email to myself</button></div>`
    : `<p class="muted">To send applications in one click, add these to <code>.env</code> and restart SARTHI:</p>
       <pre class="setup">SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your 16-character app password</pre>
       <p class="muted small">Gmail needs an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">App Password</a>, which requires 2-step verification. Your normal password won't work.</p>`;

  renderCompanies();

  const alerts = s.alerts || [];
  $('s-alerts').innerHTML = alerts.length
    ? alerts.map((a) => `
        <div class="source-item">
          <div><b>${escapeHtml(a.text)}</b><p class="mono small muted">${a.total ?? '–'} matching · ${a.new_count || 0} new</p></div>
          <button class="btn btn-ghost" onclick="deleteAlert('${a.id}')"><i data-lucide="trash-2"></i>Remove</button>
        </div>`).join('')
    : '<p class="muted">No alerts yet. Ask SARTHI for something in chat, then click "Alert me about new matches".</p>';
  refreshIcons();
}

async function renderCompanies() {
  const data = await api('/api/companies').catch(() => null);
  if (!data) return;
  const atsLabel = { greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', workable: 'Workable', smartrecruiters: 'SmartRecruiters',
    recruitee: 'Recruitee', workday: 'Workday', yc: 'YC', jsonld: 'careers page', html: 'careers page' };
  const rows = data.companies.filter((c) => c.ats || c.origin === 'user').slice(0, 60);
  $('s-companies').innerHTML = `
    <p class="mono small muted">${data.total} companies · ${data.boards} boards found · ${data.pending} still to check · ${data.not_found} without a public board</p>
    <div class="company-list">${rows.map((c) => `
      <div class="company-row">
        <span class="company-name">${escapeHtml(c.name)}</span>
        <span class="mono small muted">${c.ats ? atsLabel[c.ats] : c.discovery_state === 'pending' ? 'checking soon' : 'no public board'}</span>
        <span class="mono small ${c.last_matches ? '' : 'muted'}">${c.ats && c.last_open_roles !== null ? `${c.last_matches ?? 0} saved / ${c.last_open_roles} open` : ''}</span>
        <span class="mono small muted">${c.next_check_at && c.ats ? `next ${dueIn(c.next_check_at)}` : ''}</span>
        ${c.origin === 'user' ? `<button class="link-btn" onclick="removeCompany(${c.id})" title="Stop watching"><i data-lucide="x"></i></button>` : '<span></span>'}
      </div>`).join('')}</div>`;
  refreshIcons();
}

async function addCompany(e) {
  e.preventDefault();
  const name = $('c-name').value.trim();
  const website = $('c-site').value.trim();
  $('c-status').textContent = `Looking for ${name}'s careers board...`;
  try {
    const res = await api('/api/companies', { method: 'POST', body: JSON.stringify({ name, website }) });
    $('c-status').textContent = res.reply.replace(/\*\*/g, '');
    $('c-name').value = '';
    $('c-site').value = '';
    await loadItems();
    renderQueue();
    renderCompanies();
  } catch (err) {
    $('c-status').textContent = `Couldn't add it: ${err.message}`;
  }
}

async function removeCompany(id) {
  await api(`/api/companies/${id}`, { method: 'DELETE' });
  renderCompanies();
}

async function saveSettings(changes) {
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify(changes) });
    await loadStatus();
    renderSourceLine();
  } catch (err) {
    console.warn(err.message);
  }
}

async function toggleSource(key, enabled) {
  const disabled = new Set(state.status?.settings?.disabled_sources || []);
  if (enabled) disabled.delete(key);
  else disabled.add(key);
  await saveSettings({ disabled_sources: [...disabled] });
}

async function testEmail(btn) {
  btn.disabled = true;
  const res = await api('/api/outreach/test', { method: 'POST' }).catch((e) => ({ ok: false, error: e.message }));
  btn.disabled = false;
  btn.innerHTML = res.ok ? '<i data-lucide="check"></i>Sent. Check your inbox.' : `<i data-lucide="x"></i>${escapeHtml(res.error)}`;
  refreshIcons();
}

function renderSkillChips() {
  $('skill-chips').innerHTML = state.skills.map((s, i) => `
    <span class="chip">${escapeHtml(s)}<button type="button" onclick="removeSkill(${i})" title="Remove"><i data-lucide="x"></i></button></span>`).join('');
  refreshIcons();
}

function skillKey(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    const v = e.target.value.trim();
    if (v && !state.skills.includes(v)) state.skills.push(v);
    e.target.value = '';
    renderSkillChips();
  } else if (e.key === 'Backspace' && !e.target.value && state.skills.length) {
    state.skills.pop();
    renderSkillChips();
  }
}

function removeSkill(i) {
  state.skills.splice(i, 1);
  renderSkillChips();
}

function setupDropzone() {
  const dz = $('dropzone');
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('over'); }));
  dz.addEventListener('drop', (e) => uploadResume(e.dataTransfer.files[0]));
}

async function uploadResume(file) {
  if (!file) return;
  $('dz-main').textContent = `Reading ${file.name}...`;
  $('dz-sub').textContent = 'Extracting your skills and links.';
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/profile/upload-resume', { method: 'POST', body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.name) $('f-name').value = data.name;
    if (data.github) $('f-github').value = data.github;
    if (data.linkedin) $('f-linkedin').value = data.linkedin;
    if (data.summary) $('f-bio').value = data.summary;
    (data.primary_skills || []).forEach((s) => { if (!state.skills.includes(s)) state.skills.push(s); });
    renderSkillChips();
    await loadStatus();
    $('dz-main').textContent = `Read ${file.name}`;
    $('dz-sub').textContent = 'Saved, and it will be attached to emails. Check the fields below, then save.';
  } catch (err) {
    $('dz-main').textContent = "Couldn't read that file";
    $('dz-sub').textContent = err.message;
  } finally {
    $('resume-file').value = '';
  }
}

async function saveProfile(e) {
  e.preventDefault();
  const btn = $('btn-save-profile');
  btn.disabled = true;
  $('profile-status').textContent = 'Saving and re-reading your profile...';
  try {
    await api('/api/onboard', {
      method: 'POST',
      body: JSON.stringify({
        name: $('f-name').value.trim(),
        hourly_rate: parseInt($('f-rate').value, 10) || 35,
        min_budget: parseInt($('f-min').value, 10) || 250,
        github_url: $('f-github').value.trim(),
        linkedin_url: $('f-linkedin').value.trim(),
        skills: state.skills.join(', '),
        resume_or_bio: $('f-bio').value.trim(),
      }),
    });
    await Promise.all([loadProfile(), loadItems()]);
    renderQueue();
    $('profile-status').textContent = 'Saved. Your queue is re-ranked.';
  } catch (err) {
    $('profile-status').textContent = `Couldn't save: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- Input & keys ---------------- */

function setupInput() {
  const input = $('input');
  input.addEventListener('input', () => autosize(input));
}

function inputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

function setupKeys() {
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
    if (e.key === 'Escape') {
      if (typing) document.activeElement.blur();
      else if (state.page) closePage();
      else showOverview();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') {
      e.preventDefault();
      $('input').focus();
    } else if (e.key === 'j' || e.key === 'k') {
      const list = visibleItems();
      if (!list.length) return;
      const idx = list.findIndex((i) => i.ref === state.selected);
      const next = e.key === 'j' ? Math.min(idx + 1, list.length - 1) : Math.max(idx - 1, 0);
      selectItem(list[idx === -1 ? 0 : next].ref);
    }
  });
}

/* ---------------- Helpers ---------------- */

function refreshIcons() {
  window.lucide?.createIcons();
}

function scrollThread() {
  const t = $('thread');
  t.scrollTop = t.scrollHeight;
}

function autosize(el) {
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight + 2}px`;
}

function shorten(s, n) {
  s = String(s || '');
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function wordCount(s) {
  return (String(s || '').trim().match(/\S+/g) || []).length;
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function formatInterval(min) {
  return min < 60 ? `${min}m` : `${min / 60}h`;
}

function parseDate(iso) {
  if (!iso) return null;
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso);
  const d = new Date(hasZone ? iso : `${String(iso).replace(' ', 'T')}Z`);
  return isNaN(d) ? null : d;
}

function timeAgo(iso) {
  const then = parseDate(iso);
  if (!then) return '';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 60) return `${days}d ago`;
  return days < 730 ? `${Math.round(days / 30)}mo ago` : `${Math.round(days / 365)}y ago`;
}

function dueIn(iso) {
  const d = parseDate(iso);
  if (!d) return '';
  const days = Math.round((d - Date.now()) / 86400000);
  if (days < 0) return 'already';
  if (days === 0) return 'today';
  return days === 1 ? 'tomorrow' : `in ${days} days`;
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(str) {
  return escapeHtml(str).replace(/\n/g, '&#10;');
}

function linkify(html) {
  return html.replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

/** Minimal markdown for SARTHI replies. Everything is escaped first. */
function renderText(text) {
  return String(text ?? '').split(/\n{2,}/).filter(Boolean).map((block) => {
    const fence = block.match(/^```[a-z]*\n?([\s\S]*?)```$/);
    if (fence) return `<pre>${escapeHtml(fence[1].trim())}</pre>`;
    const html = escapeHtml(block)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\n/g, '<br>');
    return `<p>${html}</p>`;
  }).join('');
}
