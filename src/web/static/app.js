/**
 * JobAI — Minimalist Conversational Client Controller
 * Obsidian Palette, Starburst Animations, AI Resume Dropzone & Dynamic Skill Tag Cloud
 */

let allLeads = [];
let filteredDrawerLeads = [];
let drawerSourceFilter = 'all';
let conversationHistory = [];
let currentProfile = null;
let currentSkills = [];

document.addEventListener('DOMContentLoaded', () => {
  initLucide();
  loadProfile();
  loadLeads();
  setupTextarea();
});

function initLucide() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

// Textarea auto-height
function setupTextarea() {
  const textarea = document.getElementById('chat-user-input');
  if (!textarea) return;

  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + 'px';
  });
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

// Profile management
async function loadProfile() {
  try {
    const res = await fetch('/api/profile');
    const data = await res.json();
    currentProfile = data.profile || {};

    const nameBtn = document.getElementById('user-display-name');
    if (nameBtn && currentProfile.name) {
      nameBtn.textContent = currentProfile.name;
    }

    if (currentProfile.name) document.getElementById('onboard-name').value = currentProfile.name;
    if (currentProfile.hourly_rate_usd) document.getElementById('onboard-rate').value = currentProfile.hourly_rate_usd;
    if (currentProfile.github) document.getElementById('onboard-github').value = currentProfile.github;
    if (currentProfile.linkedin) document.getElementById('onboard-linkedin').value = currentProfile.linkedin;

    // Load skills
    const primarySkills = currentProfile.skills?.primary || [];
    currentSkills = primarySkills.length > 0 ? [...primarySkills] : ["Python", "FastAPI", "Next.js", "PostgreSQL"];
    renderSkillsTags();

    if (currentProfile.summary) {
      document.getElementById('onboard-resume').value = currentProfile.summary;
    }
  } catch (err) {
    console.error('Error loading profile:', err);
  }
}

// Interactive Skill Tag Cloud Management
function renderSkillsTags() {
  const container = document.getElementById('skills-tag-list');
  const hiddenInput = document.getElementById('onboard-skills');
  if (!container) return;

  if (hiddenInput) {
    hiddenInput.value = currentSkills.join(', ');
  }

  container.innerHTML = currentSkills.map((skill, idx) => `
    <span class="skill-chip">
      <span>${escapeHtml(skill)}</span>
      <span class="skill-chip-remove" onclick="removeSkill(${idx})" title="Remove skill">✕</span>
    </span>
  `).join('');
}

function addSkill(skillName) {
  const clean = skillName.trim();
  if (clean && !currentSkills.includes(clean)) {
    currentSkills.push(clean);
    renderSkillsTags();
  }
}

function removeSkill(idx) {
  currentSkills.splice(idx, 1);
  renderSkillsTags();
}

function handleSkillAdd(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    addCurrentSkillInput();
  }
}

function addCurrentSkillInput() {
  const input = document.getElementById('skill-add-input');
  if (input && input.value.trim()) {
    addSkill(input.value.trim());
    input.value = '';
  }
}

// AI Resume Upload & Instant Parsing
async function handleResumeUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;

  const defaultBox = document.getElementById('dropzone-default');
  const loadingBox = document.getElementById('dropzone-loading');

  defaultBox.style.display = 'none';
  loadingBox.style.display = 'flex';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/profile/upload-resume', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();

    if (data.error) {
      alert('Upload notice: ' + data.error);
    } else {
      // Auto-fill fields from AI parsed resume!
      if (data.name) document.getElementById('onboard-name').value = data.name;
      if (data.github) document.getElementById('onboard-github').value = data.github;
      if (data.linkedin) document.getElementById('onboard-linkedin').value = data.linkedin;
      if (data.suggested_rate) document.getElementById('onboard-rate').value = data.suggested_rate;
      if (data.summary) document.getElementById('onboard-resume').value = data.summary;

      if (data.primary_skills && Array.isArray(data.primary_skills)) {
        data.primary_skills.forEach(s => {
          if (!currentSkills.includes(s)) currentSkills.push(s);
        });
        renderSkillsTags();
      }

      alert('Resume parsed! Extracted skills, contact links, and profile highlights.');
    }
  } catch (err) {
    alert('Failed to parse resume: ' + err.message);
  } finally {
    defaultBox.style.display = 'flex';
    loadingBox.style.display = 'none';
    event.target.value = '';
  }
}

// Leads loading
async function loadLeads() {
  try {
    const res = await fetch('/api/leads?limit=50&min_score=7');
    const data = await res.json();
    allLeads = data.leads || [];

    const pill = document.getElementById('leads-count-pill');
    const drawerBadge = document.getElementById('drawer-total-count');
    if (pill) pill.textContent = allLeads.length;
    if (drawerBadge) drawerBadge.textContent = allLeads.length;

    filterDrawerLeads();
  } catch (err) {
    console.error('Error loading leads:', err);
  }
}

// Send user message
function sendPrompt(promptText) {
  document.getElementById('chat-user-input').value = promptText;
  sendMessage();
}

async function sendMessage() {
  const input = document.getElementById('chat-user-input');
  const message = input.value.trim();
  if (!message) return;

  // Collapse the center hero
  const hero = document.getElementById('center-hero');
  if (hero) hero.style.display = 'none';

  appendMessage('user', message);
  input.value = '';
  input.style.height = 'auto';

  const typingId = appendThinkingIndicator();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: message,
        history: conversationHistory,
      }),
    });
    const data = await res.json();
    removeElement(typingId);
    appendMessage('ai', data.reply);
  } catch (err) {
    removeElement(typingId);
    appendMessage('ai', 'Error connecting to intelligence engine: ' + err.message);
  }
}

function getLaceVortexSvg(extraClass = '') {
  return `
    <svg class="vortex-svg-icon ${extraClass}" viewBox="0 0 32 32" fill="none">
      <path d="M16 3.5C9.1 3.5 3.5 9.1 3.5 16C3.5 19.4 4.8 22.5 7 24.8" stroke="url(#vtx-p)" stroke-width="2.6" stroke-linecap="round" />
      <path d="M16 28.5C22.9 28.5 28.5 22.9 28.5 16C28.5 12.6 27.2 9.5 25 7.2" stroke="url(#vtx-m)" stroke-width="2.6" stroke-linecap="round" />
      <path d="M10.2 16C10.2 12.8 12.8 10.2 16 10.2C18.1 10.2 20 11.3 21 13" stroke="url(#vtx-g)" stroke-width="2.3" stroke-linecap="round" />
      <path d="M21.8 16C21.8 19.2 19.2 21.8 16 21.8C13.9 21.8 12 20.7 11 19" stroke="url(#vtx-p)" stroke-width="2.3" stroke-linecap="round" />
      <circle cx="16" cy="16" r="2.2" fill="#8b5cf6" />
      <defs>
        <linearGradient id="vtx-p" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#a855f7" />
          <stop offset="100%" stop-color="#6366f1" />
        </linearGradient>
        <linearGradient id="vtx-m" x1="100%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stop-color="#ec4899" />
          <stop offset="100%" stop-color="#8b5cf6" />
        </linearGradient>
        <linearGradient id="vtx-g" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#f59e0b" />
          <stop offset="100%" stop-color="#ec4899" />
        </linearGradient>
      </defs>
    </svg>
  `;
}

function appendMessage(role, content) {
  conversationHistory.push({ role, content });
  const container = document.getElementById('messages-flow');

  const row = document.createElement('div');
  row.className = `msg-row ${role}`;

  // AI Avatar (LACE-style Cyber Vortex Logo)
  if (role === 'ai') {
    const avatar = document.createElement('div');
    avatar.className = 'ai-avatar';
    avatar.innerHTML = getLaceVortexSvg();
    row.appendChild(avatar);
  }

  // Clean Message Content (NO 'YOU' BADGE for user message!)
  const contentBox = document.createElement('div');
  contentBox.className = 'msg-content';
  contentBox.innerHTML = renderFormattedContent(content);
  row.appendChild(contentBox);

  container.appendChild(row);
  scrollToBottom();
  initLucide();
}

function appendThinkingIndicator() {
  const container = document.getElementById('messages-flow');
  const id = 'thinking-' + Date.now();

  const row = document.createElement('div');
  row.id = id;
  row.className = 'msg-row ai thinking-row';
  row.innerHTML = `
    <div class="ai-avatar thinking-logo-spin">
      ${getLaceVortexSvg('vortex-pulse-spin')}
    </div>
    <div class="msg-content">
      <div class="thinking-label">Thinking...</div>
    </div>
  `;

  container.appendChild(row);
  scrollToBottom();
  return id;
}

function removeElement(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function scrollToBottom() {
  const viewport = document.getElementById('chat-viewport');
  if (viewport) {
    viewport.scrollTop = viewport.scrollHeight;
  }
}

// Markdown formatting
function renderFormattedContent(text) {
  if (!text) return '';
  let html = escapeHtml(text);

  // Bold & Italic
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Headers
  html = html.replace(/^### (.*$)/gim, '<h4 style="font-size:0.95rem; font-weight:600; margin:12px 0 6px;">$1</h4>');
  html = html.replace(/^## (.*$)/gim, '<h3 style="font-size:1.05rem; font-weight:600; margin:16px 0 8px;">$1</h3>');

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code style="background:rgba(255,255,255,0.08); padding:2px 5px; border-radius:4px; font-family:var(--font-mono); font-size:0.82rem;">$1</code>');

  // Code blocks with copy
  html = html.replace(/```(?:[a-z]*)\n([\s\S]*?)```/g, (match, code) => {
    const encoded = encodeURIComponent(code.trim());
    return `<div style="background:var(--bg-surface); border:1px solid var(--border-default); border-radius:8px; padding:12px; margin:10px 0; position:relative;">
      <pre style="margin:0; overflow-x:auto; font-family:var(--font-mono); font-size:0.82rem;"><code>${code}</code></pre>
      <button class="btn-inline-copy" style="position:absolute; top:8px; right:8px;" onclick="copyRawText('${encoded}', this)">
        <i data-lucide="copy" class="icon-xs"></i> <span>Copy</span>
      </button>
    </div>`;
  });

  // Links
  html = html.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color:var(--accent); text-decoration:underline;">$1</a>');

  // Paragraph breaks
  html = html.replace(/\n\n/g, '<br><br>');
  html = html.replace(/\n/g, '<br>');

  return html;
}

function escapeHtml(str) {
  return (str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function copyRawText(encoded, btn) {
  const text = decodeURIComponent(encoded);
  navigator.clipboard.writeText(text).then(() => {
    btn.classList.add('copied');
    btn.innerHTML = `<i data-lucide="check" class="icon-xs"></i> <span>Copied</span>`;
    initLucide();
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = `<i data-lucide="copy" class="icon-xs"></i> <span>Copy</span>`;
      initLucide();
    }, 2000);
  });
}

// Live Discovery Scan Action
async function triggerDiscoveryScan() {
  const btn = document.getElementById('btn-quick-scan');
  const icon = document.getElementById('quick-scan-icon');
  btn.disabled = true;
  icon.classList.add('spin-icon');

  try {
    const res = await fetch('/api/pipeline/run', { method: 'POST' });
    const data = await res.json();
    await loadLeads();

    const hero = document.getElementById('center-hero');
    if (hero) hero.style.display = 'none';

    appendMessage('ai', `⚡ **Scan Completed:** Found **${data.new_leads_count || 0} fresh opportunities**!\n\nOpen the **Opportunities Drawer** at top-right to inspect, or ask me: *"Show top 3 leads"*.`);
  } catch (err) {
    alert('Scan error: ' + err.message);
  } finally {
    btn.disabled = false;
    icon.classList.remove('spin-icon');
  }
}

// Drawer Handling
function toggleDrawer() {
  const drawer = document.getElementById('leads-drawer');
  const backdrop = document.getElementById('drawer-backdrop');
  drawer.classList.toggle('open');
  backdrop.classList.toggle('open');
  if (drawer.classList.contains('open')) {
    filterDrawerLeads();
  }
}

function setDrawerSource(src) {
  drawerSourceFilter = src;
  document.querySelectorAll('.pill-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-src') === src);
  });
  filterDrawerLeads();
}

function filterDrawerLeads() {
  const query = (document.getElementById('drawer-search')?.value || '').toLowerCase().trim();
  const listContainer = document.getElementById('drawer-leads-list');
  if (!listContainer) return;

  filteredDrawerLeads = allLeads.filter(l => {
    if (drawerSourceFilter !== 'all' && l.source.toLowerCase() !== drawerSourceFilter) return false;
    if (query) {
      const match = (l.title || '').toLowerCase().includes(query) ||
                    (l.budget || '').toLowerCase().includes(query) ||
                    (l.tech || []).some(t => t.toLowerCase().includes(query));
      if (!match) return false;
    }
    return true;
  });

  if (filteredDrawerLeads.length === 0) {
    listContainer.innerHTML = `
      <div style="text-align:center; padding:40px 10px; color:var(--text-muted); font-size:0.85rem;">
        No opportunities match current filter.
      </div>
    `;
    return;
  }

  listContainer.innerHTML = filteredDrawerLeads.map(l => {
    const encodedDraft = encodeURIComponent(l.draft_reply || '');
    return `
      <div class="lead-result-card">
        <div class="lead-card-header">
          <span class="source-tag">${l.source.toUpperCase()}</span>
          <span class="match-tag">${l.score}/10 Match</span>
        </div>
        <div class="lead-card-title">${escapeHtml(l.title)}</div>
        <div class="lead-card-meta">
          <span class="budget-val">${escapeHtml(l.budget || 'Budget Undisclosed')}</span>
          <span>@${escapeHtml(l.author || 'anon')}</span>
        </div>
        <div class="lead-proposal-box">${escapeHtml(l.draft_reply || 'Draft will be generated upon scan.')}</div>
        <div class="lead-card-footer">
          <a href="${l.url}" target="_blank" rel="noopener noreferrer" style="color:var(--text-secondary); font-size:0.78rem; text-decoration:none; display:flex; align-items:center; gap:4px;">
            <span>Open Post</span> <i data-lucide="external-link" class="icon-xs"></i>
          </a>
          <button class="btn-inline-copy" onclick="copyRawText('${encodedDraft}', this)">
            <i data-lucide="copy" class="icon-xs"></i> <span>Copy Draft</span>
          </button>
        </div>
      </div>
    `;
  }).join('');

  initLucide();
}

// Modal Handling
function openOnboarding() {
  document.getElementById('onboarding-modal').classList.add('open');
  initLucide();
}

function closeOnboarding() {
  document.getElementById('onboarding-modal').classList.remove('open');
}

async function submitOnboarding(e) {
  e.preventDefault();
  const btn = document.getElementById('btn-save-profile');
  btn.disabled = true;

  const payload = {
    name: document.getElementById('onboard-name').value.trim(),
    hourly_rate: parseInt(document.getElementById('onboard-rate').value, 10) || 45,
    github_url: document.getElementById('onboard-github').value.trim(),
    linkedin_url: document.getElementById('onboard-linkedin').value.trim(),
    skills: currentSkills.join(', '),
    resume_or_bio: document.getElementById('onboard-resume').value.trim(),
  };

  try {
    await fetch('/api/onboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeOnboarding();
    loadProfile();
  } catch (err) {
    alert('Failed to save profile: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}
