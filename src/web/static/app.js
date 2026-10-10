/**
 * SARTHI client.
 * Queue of leads on the left, a thread with SARTHI on the right.
 * Overview thread opens with a briefing; selecting a lead opens its card and a thread about that lead.
 */

const state = {
  leads: [],
  status: null,
  profile: {},
  skills: [],
  tab: 'new',
  showSamples: localStorage.getItem('sarthi.showSamples') === '1',
  selectedId: null,
  threads: { overview: [] },  // key -> [{ role: 'user'|'ai', text, draft? }]
  scanning: false,
  sending: false,
};

const TAB_OF = { new: 'new', contacted: 'sent', replied: 'sent', won: 'won', lost: 'passed' };
const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', async () => {
  setupInput();
  setupDropzone();
  setupKeys();
  await Promise.all([loadProfile(), loadStatus(), loadLeads()]);
  renderAll();
  const linked = Number((location.hash.match(/lead=(\d+)/) || [])[1]);
  if (leadById(linked)) selectLead(linked);
  else if (location.hash === '#profile') showProfile();
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

async function loadLeads() {
  const data = await api('/api/leads');
  state.leads = (data.leads || []).sort((a, b) => a.is_sample - b.is_sample);
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

function leadById(id) {
  return state.leads.find((l) => l.id === id);
}

function visibleLeads(tab = state.tab) {
  return state.leads.filter((l) => TAB_OF[l.status] === tab && (state.showSamples || !l.is_sample));
}

/* ---------------- Rendering ---------------- */

function renderAll() {
  renderSourceLine();
  renderQueue();
  renderStage();
}

function renderSourceLine() {
  const scan = state.status?.last_scan;
  const parts = [];
  parts.push(`<span class="source">${scan ? `Last scan ${timeAgo(scan.finished_at)}` : 'No scan yet'}</span>`);
  const sources = scan?.sources || {};
  const names = { hn: 'Hacker News', reddit: 'Reddit' };
  for (const key of ['hn', 'reddit']) {
    const info = sources[key];
    if (!info) continue;
    const label = info.ok ? names[key] : `${names[key]} off`;
    parts.push(`<span class="source ${info.ok ? 'ok' : 'bad'}" title="${escapeAttr(info.error || '')}"><span class="dot"></span>${label}</span>`);
  }
  if (state.status && !state.status.gemini) {
    parts.push(`<span class="source bad"><span class="dot"></span>Gemini off</span>`);
  }
  $('source-line').innerHTML = parts.join('');
}

function renderQueue() {
  for (const tab of ['new', 'sent', 'won', 'passed']) {
    $(`count-${tab}`).textContent = visibleLeads(tab).length;
  }
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === state.tab));

  const list = visibleLeads();
  const container = $('queue-list');

  if (!list.length) {
    const empty = {
      new: 'Nothing waiting. Run a scan to check for new posts.',
      sent: 'Replies you send show up here, so you can follow up.',
      won: 'Work you win lands here.',
      passed: 'Posts you pass on go here.',
    };
    container.innerHTML = `<div class="queue-empty">${empty[state.tab]}</div>`;
  } else {
    container.innerHTML = list.map((l) => `
      <button class="lead-row ${l.id === state.selectedId ? 'selected' : ''}" data-id="${l.id}" onclick="selectLead(${l.id})">
        <span class="lead-score">${l.score}<small>/10</small></span>
        <span class="lead-title">${escapeHtml(l.title)}</span>
        <span class="lead-meta">
          ${l.is_sample ? '<span class="tag-sample">SAMPLE</span>' : ''}
          <span>${l.source === 'hn' ? 'HN' : 'Reddit'}</span>
          ${isKnown(l.budget) ? `<span>${escapeHtml(shorten(l.budget, 24))}</span>` : ''}
          <span>${timeAgo(l.created_at)}</span>
        </span>
      </button>`).join('');
  }

  const sampleCount = state.leads.filter((l) => l.is_sample).length;
  const toggle = $('toggle-samples');
  toggle.hidden = sampleCount === 0;
  toggle.textContent = state.showSamples ? `Hide ${sampleCount} sample posts` : `Show ${sampleCount} sample posts`;
  refreshIcons();
}

function renderStage() {
  const thread = $('thread');
  const lead = state.selectedId ? leadById(state.selectedId) : null;

  if (lead) {
    thread.innerHTML = leadCardHtml(lead) + messagesHtml(state.threads[lead.id] || []);
    $('context-chip').hidden = false;
    $('context-label').textContent = lead.title;
    $('input').placeholder = 'Ask about this post, or say "make the draft shorter"';
    wireDraft(lead);
  } else {
    thread.innerHTML = messageHtml({ role: 'ai', ...briefing() }) + peekHtml() + messagesHtml(state.threads.overview);
    $('context-chip').hidden = true;
    $('input').placeholder = 'Ask SARTHI anything, or paste a job post';
  }
  refreshIcons();
}

function briefing() {
  const real = state.leads.filter((l) => !l.is_sample);
  const waiting = real.filter((l) => l.status === 'new');
  const sent = real.filter((l) => TAB_OF[l.status] === 'sent').length;
  const scan = state.status?.last_scan;
  const lines = [];

  const name = state.profile.name ? state.profile.name.split(' ')[0] : null;
  const hello = name ? `Hey ${name}.` : 'Hey.';

  if (!scan && !real.length) {
    lines.push(`${hello} I haven't scanned yet. Hit **Scan** and I'll read this month's Hacker News hiring threads for posts that fit you.`);
  } else if (waiting.length) {
    const top = waiting[0];
    const budget = isKnown(top.budget) ? `, ${top.budget}` : '';
    lines.push(`${hello} ${waiting.length} ${waiting.length === 1 ? 'post fits' : 'posts fit'} you and ${waiting.length === 1 ? 'is' : 'are'} waiting for a reply. The strongest is **${top.title}**${budget}, scored ${top.score}/10.`);
    lines.push('Open one to read the post and your draft.');
  } else {
    lines.push(`${hello} Nothing new is waiting.${sent ? ` You've sent ${sent} ${sent === 1 ? 'reply' : 'replies'} so far.` : ''} Run a scan to check for new posts.`);
  }

  const notes = [];
  if (scan) notes.push(`Last scan ${timeAgo(scan.finished_at)}: checked ${scan.checked} posts, ${scan.saved} new ${scan.saved === 1 ? 'fit' : 'fits'}.`);
  for (const [key, info] of Object.entries(scan?.sources || {})) {
    if (info.error) notes.push(`${key === 'hn' ? 'Hacker News' : 'Reddit'} was skipped: ${info.error}`);
  }
  if (state.status && !state.status.gemini) notes.push('Gemini is not configured, so scoring and drafts use simple rules.');
  const samples = state.leads.filter((l) => l.is_sample).length;
  if (samples && !state.showSamples) notes.push(`${samples} sample posts from the old demo data are hidden. They aren't real.`);

  return { text: lines.join('\n\n'), notes };
}

/** The top waiting leads, shown right under the briefing so the next action is one click away. */
function peekHtml() {
  const waiting = state.leads.filter((l) => l.status === 'new' && (state.showSamples || !l.is_sample)).slice(0, 3);
  if (!waiting.length) return '';
  return `<div class="peek">${waiting.map((l) => `
    <button class="peek-row" onclick="selectLead(${l.id})">
      <span class="lead-score">${l.score}<small>/10</small></span>
      <span class="peek-main">
        <span class="lead-title">${escapeHtml(l.title)}</span>
        <span class="why">${escapeHtml(shorten(l.score_reason || l.need || '', 150))}</span>
      </span>
      <i data-lucide="arrow-right"></i>
    </button>`).join('')}</div>`;
}

function leadCardHtml(l) {
  const meta = [
    l.source === 'hn' ? 'Hacker News' : 'Reddit',
    `found ${timeAgo(l.created_at)}`,
    isKnown(l.budget) ? `<b>${escapeHtml(l.budget)}</b>` : 'budget not stated',
    isKnown(l.timeline) ? escapeHtml(l.timeline) : null,
    `<b>${l.score}/10</b>`,
  ].filter(Boolean);

  const tech = (l.tech || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('');
  const draft = l.draft_reply || '';

  return `
    <article class="lead-card">
      <section>
        <div class="card-meta">${l.is_sample ? '<span class="tag-sample">SAMPLE</span>' : ''}${meta.map((m) => `<span>${m}</span>`).join('')}</div>
        <h2 class="card-title">${escapeHtml(l.title)}</h2>
        ${tech ? `<div class="chips">${tech}</div>` : ''}
      </section>
      ${l.score_reason ? `
      <section>
        <span class="label">Why it fits</span>
        <p class="why">${escapeHtml(l.score_reason)}</p>
      </section>` : ''}
      <section>
        <span class="label">The post</span>
        <div class="post-text" id="post-text">${linkify(escapeHtml(l.raw_content || l.need || ''))}</div>
        <button class="link-btn post-toggle" onclick="togglePost(this)">Show full post</button>
        ${isKnown(l.contact_method) ? `<p class="contact"><span>How to reach them</span>${linkify(escapeHtml(l.contact_method))}</p>` : ''}
      </section>
      <section>
        <div class="draft-head">
          <span class="label">Your reply</span>
          <span class="num" id="draft-count">${wordCount(draft)} words</span>
        </div>
        <textarea class="draft" id="draft" spellcheck="true">${escapeHtml(draft)}</textarea>
      </section>
      <section class="card-actions">${actionsHtml(l)}<span class="spacer"></span><span class="hint" id="card-hint"></span></section>
    </article>`;
}

function actionsHtml(l) {
  const open = l.is_sample
    ? `<button class="btn btn-primary" onclick="copyDraft()"><i data-lucide="copy"></i>Copy reply</button>`
    : `<button class="btn btn-primary" onclick="copyAndOpen()"><i data-lucide="send"></i>Copy reply &amp; open post</button>`;
  const btn = (status, icon, label) =>
    `<button class="btn btn-quiet" onclick="setStatus('${status}')"><i data-lucide="${icon}"></i>${label}</button>`;

  switch (l.status) {
    case 'new': return open + btn('contacted', 'check', 'Mark sent') + btn('lost', 'x', 'Pass');
    case 'contacted': return open + btn('replied', 'message-square', 'They replied') + btn('lost', 'x', 'No luck');
    case 'replied': return btn('won', 'trophy', 'Won it') + btn('lost', 'x', 'Lost it');
    default: return btn('new', 'rotate-ccw', 'Move back to New');
  }
}

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
      <button class="btn btn-quiet" onclick="useDraft(this)" data-draft="${escapeAttr(m.draft)}"><i data-lucide="check"></i>Use this reply</button>
    </div>` : '';
  return `
    <div class="msg ai" ${m.id ? `id="${m.id}"` : ''}>
      <div class="avatar"><svg class="chakra ${m.thinking ? 'spinning' : ''}"><use href="#chakra" /></svg></div>
      <div class="body">${m.thinking ? `<span class="thinking">${escapeHtml(m.text)}<span class="num" data-elapsed></span></span>` : renderText(m.text)}${notesHtml(m.notes)}${suggestion}</div>
    </div>`;
}

/* ---------------- Lead actions ---------------- */

function selectLead(id) {
  closeProfile(false);
  state.selectedId = id;
  history.replaceState(null, '', `#lead=${id}`);
  renderQueue();
  renderStage();
  $('thread').scrollTop = 0;
  document.querySelector(`.lead-row[data-id="${id}"]`)?.scrollIntoView({ block: 'nearest' });
}

function showOverview() {
  state.selectedId = null;
  history.replaceState(null, '', location.pathname);
  renderQueue();
  renderStage();
}

function setTab(tab) {
  state.tab = tab;
  renderQueue();
}

function toggleSamples() {
  state.showSamples = !state.showSamples;
  localStorage.setItem('sarthi.showSamples', state.showSamples ? '1' : '0');
  const selected = leadById(state.selectedId);
  if (selected?.is_sample && !state.showSamples) state.selectedId = null;
  renderQueue();
  renderStage();
}

function togglePost(btn) {
  const el = $('post-text');
  el.classList.toggle('expanded');
  btn.textContent = el.classList.contains('expanded') ? 'Show less' : 'Show full post';
}

function wireDraft(lead) {
  const box = $('draft');
  if (!box) return;
  autosize(box);
  box.addEventListener('input', () => {
    autosize(box);
    const n = wordCount(box.value);
    const count = $('draft-count');
    count.textContent = `${n} words`;
    count.classList.toggle('over', n > 100);
  });
  box.addEventListener('blur', () => saveDraft(lead, box.value));
}

async function saveDraft(lead, text) {
  if (text === (lead.draft_reply || '')) return;
  try {
    await api(`/api/leads/${lead.id}/draft`, { method: 'PUT', body: JSON.stringify({ draft: text }) });
    lead.draft_reply = text;
    hint('Saved');
  } catch (err) {
    hint(`Couldn't save: ${err.message}`);
  }
}

async function copyDraft() {
  const text = $('draft')?.value || '';
  await navigator.clipboard.writeText(text);
  hint('Reply copied');
}

async function copyAndOpen() {
  const lead = leadById(state.selectedId);
  if (!lead) return;
  await copyDraft();
  window.open(lead.url, '_blank', 'noopener');
  hint('Copied. Paste it in the post, then mark it sent.');
}

async function setStatus(status) {
  const lead = leadById(state.selectedId);
  if (!lead) return;
  await saveDraft(lead, $('draft')?.value ?? lead.draft_reply);
  try {
    await api(`/api/leads/${lead.id}/status`, { method: 'POST', body: JSON.stringify({ status }) });
  } catch (err) {
    hint(err.message);
    return;
  }

  // Inbox behaviour: when a lead leaves the current tab, move to the next one in it.
  const before = visibleLeads();
  const idx = before.findIndex((l) => l.id === lead.id);
  lead.status = status;
  const after = visibleLeads();
  if (TAB_OF[status] !== state.tab) {
    const next = after[Math.min(idx, after.length - 1)];
    state.selectedId = next ? next.id : null;
  }
  renderQueue();
  renderStage();
}

function useDraft(btn) {
  const box = $('draft');
  const lead = leadById(state.selectedId);
  if (!box || !lead) return;
  box.value = btn.dataset.draft;
  box.dispatchEvent(new Event('input'));
  saveDraft(lead, box.value);
  box.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function hint(text) {
  const el = $('card-hint');
  if (!el) return;
  el.textContent = text;
  clearTimeout(hint.timer);
  hint.timer = setTimeout(() => (el.textContent = ''), 3500);
}

/* ---------------- Conversation ---------------- */

function threadKey() {
  return state.selectedId ?? 'overview';
}

function pushMessage(key, msg) {
  (state.threads[key] ||= []).push(msg);
  if (key === threadKey()) {
    $('thread').insertAdjacentHTML('beforeend', messageHtml(msg));
    refreshIcons();
    scrollThread();
  }
}

/** Shows a spinning-chakra row with an elapsed timer; returns a function that removes it. */
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
      body: JSON.stringify({ message: text, history, lead_id: key === 'overview' ? null : key }),
    });
    stop();
    if (data.action === 'scan') {
      runScan();
    } else {
      pushMessage(key, { role: 'ai', text: data.reply || '', draft: data.draft || null });
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
  closeProfile(false);
  if (state.selectedId) showOverview();

  const stop = showThinking('overview', 'Reading Hacker News hiring threads and scoring posts. This takes a minute or two.');
  try {
    const data = await api('/api/pipeline/run', { method: 'POST' });
    stop();
    const report = data.report || {};
    const lines = [
      data.new_leads_count
        ? `Done. I checked ${report.checked ?? 0} posts and **${data.new_leads_count} new ${data.new_leads_count === 1 ? 'one fits' : 'ones fit'}** you. ${data.new_leads_count === 1 ? "It's" : "They're"} in New on the left.`
        : `Done. I checked ${report.checked ?? 0} posts, and nothing new cleared your 7/10 bar this time.`,
    ];
    const notes = Object.entries(report.sources || {})
      .filter(([, i]) => i.error)
      .map(([k, i]) => `${k === 'hn' ? 'Hacker News' : 'Reddit'} was skipped: ${i.error}`);
    await Promise.all([loadLeads(), loadStatus()]);
    state.tab = 'new';
    renderSourceLine();
    renderQueue();
    pushMessage('overview', { role: 'ai', text: lines.join('\n\n'), notes });
  } catch (err) {
    stop();
    pushMessage('overview', { role: 'ai', text: `The scan failed: ${err.message}` });
  } finally {
    state.scanning = false;
    $('btn-scan').disabled = false;
    $('brand-chakra').classList.remove('spinning');
  }
}

/* ---------------- Profile ---------------- */

function showProfile() {
  const p = state.profile;
  $('f-name').value = p.name || '';
  $('f-rate').value = p.hourly_rate_usd ?? '';
  $('f-min').value = p.minimum_project_budget_usd ?? '';
  $('f-github').value = p.github || '';
  $('f-linkedin').value = p.linkedin || '';
  renderSkillChips();
  $('thread').hidden = true;
  $('composer').hidden = true;
  $('profile-view').hidden = false;
  refreshIcons();
}

function closeProfile(render = true) {
  $('profile-view').hidden = true;
  $('thread').hidden = false;
  $('composer').hidden = false;
  if (render) renderStage();
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
    if (data.suggested_rate) $('f-rate').value = data.suggested_rate;
    if (data.summary) $('f-bio').value = data.summary;
    (data.primary_skills || []).forEach((s) => { if (!state.skills.includes(s)) state.skills.push(s); });
    renderSkillChips();
    $('dz-main').textContent = `Read ${file.name}`;
    $('dz-sub').textContent = 'Check the fields below, then save.';
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
    await loadProfile();
    $('profile-status').textContent = 'Saved. New scans will use this.';
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
    const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if (e.key === 'Escape') {
      if (typing) document.activeElement.blur();
      else if (!$('profile-view').hidden) closeProfile();
      else showOverview();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === '/') {
      e.preventDefault();
      $('input').focus();
    } else if (e.key === 'j' || e.key === 'k') {
      const list = visibleLeads();
      if (!list.length) return;
      const idx = list.findIndex((l) => l.id === state.selectedId);
      const next = e.key === 'j' ? Math.min(idx + 1, list.length - 1) : Math.max(idx - 1, 0);
      selectLead(list[idx === -1 ? 0 : next].id);
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

function isKnown(value) {
  return value && !/^(none|n\/a|not specified|unspecified|negotiable|unspecified \/ negotiable)/i.test(value.trim());
}

function shorten(s, n) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function wordCount(s) {
  return (s.trim().match(/\S+/g) || []).length;
}

function formatElapsed(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function timeAgo(iso) {
  if (!iso) return '';
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso);
  const then = new Date(hasZone ? iso : `${iso.replace(' ', 'T')}Z`);
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
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

function notesHtml(notes) {
  return notes?.length ? `<p class="note">${notes.map(escapeHtml).join('<br>')}</p>` : '';
}

/** Minimal markdown for SARTHI replies. Everything is escaped first. */
function renderText(text) {
  const blocks = String(text ?? '').split(/\n{2,}/);
  return blocks.map((block) => {
    const fence = block.match(/^```[a-z]*\n?([\s\S]*?)```$/);
    if (fence) return `<pre>${escapeHtml(fence[1].trim())}</pre>`;
    let html = escapeHtml(block)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\n/g, '<br>');
    return `<p>${html}</p>`;
  }).join('');
}
