// ============================================
// HTML Escape Utility
// ============================================

function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ============================================
// Authentication & Initialization
// ============================================

let activeProject = null;

window.addEventListener('DOMContentLoaded', async () => {
    if (auth.isAuthenticated()) {
        showProjectScreen();
    } else if (localStorage.getItem('refreshToken')) {
        if (await auth.refreshSession()) showProjectScreen();
        else showLoginScreen();
    } else {
        showLoginScreen();
    }
});

function showLoginScreen() {
    document.getElementById('loginScreen').style.display = 'flex';
    document.getElementById('projectScreen').style.display = 'none';
    document.getElementById('mainApp').style.display = 'none';
}

function showProjectScreen() {
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('projectScreen').style.display = 'block';
    document.getElementById('mainApp').style.display = 'none';
    loadProjects();
}

function showMainApp() {
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('projectScreen').style.display = 'none';
    document.getElementById('mainApp').style.display = 'block';
    document.getElementById('projectTitle').textContent = activeProject?.name || '';

    ['jobsList', 'bedrockResults', 'galleryGrid'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });

    const savedTab = localStorage.getItem('activeTab') || 'upload';
    switchToTab(savedTab);
}

// ============================================
// Login
// ============================================

let pendingChallenge = null;

document.getElementById('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('loginEmail').value;
    const errorDiv = document.getElementById('loginError');
    const btn = document.getElementById('loginBtn');
    const newPasswordInput = document.getElementById('newPassword');

    btn.disabled = true;
    errorDiv.style.display = 'none';

    try {
        if (pendingChallenge) {
            btn.textContent = 'Setting password...';
            const result = await auth.respondToNewPasswordChallenge(email, newPasswordInput.value, pendingChallenge.session);
            if (result.success) {
                pendingChallenge = null;
                newPasswordInput.style.display = 'none';
                newPasswordInput.required = false;
                showProjectScreen();
            }
        } else {
            btn.textContent = 'Signing in...';
            const password = document.getElementById('loginPassword').value;
            const result = await auth.login(email, password);
            if (result.challenge === 'NEW_PASSWORD_REQUIRED') {
                pendingChallenge = result;
                document.getElementById('loginPassword').style.display = 'none';
                newPasswordInput.style.display = '';
                newPasswordInput.required = true;
                newPasswordInput.focus();
                btn.textContent = 'Set New Password';
                btn.disabled = false;
                return;
            }
            showProjectScreen();
        }
    } catch (error) {
        errorDiv.textContent = error.message || 'Sign in failed';
        errorDiv.style.display = 'block';
    }
    btn.disabled = false;
    btn.textContent = pendingChallenge ? 'Set New Password' : 'Sign In';
});

document.getElementById('logoutBtn')?.addEventListener('click', () => { auth.logout(); showLoginScreen(); });
document.getElementById('projectLogoutBtn')?.addEventListener('click', () => { auth.logout(); showLoginScreen(); });
document.getElementById('backToProjects')?.addEventListener('click', () => { stopPolling(); showProjectScreen(); });

// ============================================
// Projects
// ============================================

async function loadProjects() {
    const grid = document.getElementById('projectsList');
    grid.innerHTML = '<p style="color:#888;">Loading projects...</p>';
    const data = await apiCall('/projects');
    if (!data) return;

    grid.innerHTML = (data.projects || []).map(p => `
        <div class="project-card" onclick="selectProject('${escapeHtml(p.project_id)}')">
            <button class="delete-project-btn" onclick="event.stopPropagation(); deleteProject('${escapeHtml(p.project_id)}', '${escapeHtml(p.name.replace(/'/g, "\\'"))}')">\u{1F5D1}</button>
            <h3>${escapeHtml(p.name)}</h3>
            <div class="project-badges">
                <span class="project-badge" style="background:#e3f2fd;color:#1565c0;">Nova MME</span>
                <span class="project-badge" style="background:#f3e8ff;color:#7c3aed;">${escapeHtml((getAnalyzerModels(p).find(m => m.id === p.analyzer_model_id) || DEFAULT_ANALYZER).name)}</span>
                <span class="project-badge" style="background:${p.vector_engine === 'opensearch' ? '#fef3c7;color:#92400e' : '#dcfce7;color:#166534'}">${p.vector_engine === 'opensearch' ? 'OpenSearch kNN' : 'S3 Vectors'}</span>
            </div>
            <div class="project-meta">${p.video_count || 0} videos \u00B7 Created ${new Date(p.created_at).toLocaleDateString()}</div>
        </div>
    `).join('') + `
        <div class="project-card project-card-new" onclick="showCreateProjectModal()">
            <span class="plus-icon">+</span>
            <span>New Project</span>
        </div>
    `;
}

async function selectProject(projectId) {
    const data = await apiCall(`/projects/${projectId}`);
    if (!data) return;
    activeProject = data;
    activeProject.project_id = projectId;
    localStorage.setItem('activeProjectId', projectId);
    showMainApp();
}

async function deleteProject(projectId, name) {
    activeProject = { project_id: projectId, name };
    deleteProjectFromSettings();
}

function showCreateProjectModal() {
    const tip = (text) => '<span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:#e5e5e5;color:#666;font-size:0.65rem;font-weight:700;cursor:help;margin-left:6px;vertical-align:middle;" title="' + text + '">?</span>';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:480px;padding:32px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
                <h2 style="font-size:1.2rem;font-weight:700;margin:0;">Create New Project</h2>
                <button onclick="this.closest('.modal').remove()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;color:#999;">&times;</button>
            </div>
            <div style="display:flex;flex-direction:column;gap:16px;">
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Project Name</label>
                    <input type="text" id="newProjectName" placeholder="My Video Project" style="width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
                </div>
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Segment Duration${tip('Target length per video segment. 5-30 sec.')}</label>
                    <input type="number" id="newSegDuration" value="10" min="5" max="30" style="width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
                </div>
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Analyzer Model${tip('LLM for query weight analysis.')}</label>
                    <div style="display:flex;gap:8px;align-items:center;">
                        <select id="newAnalyzerModelSelect" style="flex:1;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
                            <option value="global.anthropic.claude-haiku-4-5-20251001-v1:0">Haiku 4.5</option>
                        </select>
                        <button type="button" onclick="showAddModelDialog('new')" style="width:36px;height:36px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-size:1.2rem;display:flex;align-items:center;justify-content:center;" title="Add model">+</button>
                    </div>
                </div>
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Vector Engine${tip('S3 Vectors offloads storage to S3 (lighter cluster). OpenSearch stores vectors in the index (no S3 Vectors dependency at search time).')}</label>
                    <select id="newVectorEngine" style="width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
                        <option value="s3_vectors">S3 Vectors (default)</option>
                        <option value="opensearch">OpenSearch (nmslib HNSW)</option>
                    </select>
                </div>
                <button onclick="createProject()" style="width:100%;padding:14px;border:none;border-radius:8px;background:#1a1a1a;color:#fff;font-size:0.95rem;font-weight:600;cursor:pointer;margin-top:8px;">Create Project</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    document.getElementById('newProjectName').focus();
}

async function createProject() {
    const name = document.getElementById('newProjectName').value.trim();
    if (!name) { alert('Enter a project name'); return; }

    const body = {
        name,
        embedding_model: 'nova-mme',
        analyzer_model_id: document.getElementById('newAnalyzerModelSelect').value,
        analyzer_models: getAnalyzerModelsFromSelect('newAnalyzerModelSelect'),
        metadata_model: 'nova-lite',
        segment_duration: parseInt(document.getElementById('newSegDuration')?.value || '10'),
        vector_engine: document.getElementById('newVectorEngine').value,
    };

    const data = await apiCall('/projects', { method: 'POST', body: JSON.stringify(body) });
    if (data?.success) {
        document.querySelector('.modal')?.remove();
        selectProject(data.project.project_id);
    }
}

const DEFAULT_ANALYZER = { name: 'Haiku 4.5', id: 'global.anthropic.claude-haiku-4-5-20251001-v1:0' };

function getAnalyzerModels(project) {
    return project?.analyzer_models?.length ? project.analyzer_models : [DEFAULT_ANALYZER];
}

function buildModelOptions(project) {
    const models = getAnalyzerModels(project);
    const active = project?.analyzer_model_id || DEFAULT_ANALYZER.id;
    return models.map(m => `<option value="${escapeHtml(m.id)}" ${m.id === active ? 'selected' : ''}>${escapeHtml(m.name)}</option>`).join('');
}

function getAnalyzerModelsFromSelect(selectId) {
    const sel = document.getElementById(selectId);
    return Array.from(sel.options).map(o => ({ name: o.text, id: o.value }));
}

function showAddModelDialog(prefix) {
    const selectId = prefix + 'AnalyzerModelSelect';
    const existing = Array.from(document.getElementById(selectId).options).map(o => o.text.toLowerCase());
    const d = document.createElement('div');
    d.className = 'modal';
    d.style.zIndex = '10001';
    d.innerHTML = `
        <div class="modal-content" style="max-width:400px;padding:24px;">
            <h3 style="margin:0 0 16px;">Add Analyzer Model</h3>
            <label style="font-size:0.85rem;font-weight:600;display:block;margin-bottom:4px;">Display Name</label>
            <input type="text" id="addModelName" placeholder="e.g. Nova Pro" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.9rem;box-sizing:border-box;margin-bottom:12px;">
            <label style="font-size:0.85rem;font-weight:600;display:block;margin-bottom:4px;">Bedrock Model ID / ARN</label>
            <input type="text" id="addModelArn" placeholder="e.g. amazon.nova-pro-v1:0" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.85rem;box-sizing:border-box;font-family:monospace;margin-bottom:16px;">
            <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button onclick="this.closest('.modal').remove()" style="padding:8px 16px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer;">Cancel</button>
                <button id="addModelBtn" style="padding:8px 16px;border:none;border-radius:6px;background:#1a1a1a;color:#fff;cursor:pointer;">Add</button>
            </div>
        </div>`;
    document.body.appendChild(d);
    document.getElementById('addModelName').focus();
    document.getElementById('addModelBtn').onclick = () => {
        const name = document.getElementById('addModelName').value.trim();
        const arn = document.getElementById('addModelArn').value.trim();
        if (!name || !arn) { alert('Both fields are required'); return; }
        if (existing.includes(name.toLowerCase())) { alert('A model with that name already exists'); return; }
        const sel = document.getElementById(selectId);
        const opt = new Option(name, arn);
        sel.add(opt);
        sel.value = arn;
        d.remove();
    };
}

function showProjectSettings() {
    if (!activeProject) return;
    const p = activeProject;
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="modal-close" onclick="this.parentElement.parentElement.remove()">&times;</span>
            <h2>Project Settings</h2>
            <div class="settings-form">
                <label>Project Name</label>
                <input type="text" id="editProjectName" value="${escapeHtml(p.name || '')}">

                <label>Analyzer Model</label>
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:16px;">
                    <select id="editAnalyzerModelSelect" style="flex:1;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.9rem;">
                        ${buildModelOptions(p)}
                    </select>
                    <button type="button" onclick="showAddModelDialog('edit')" style="width:32px;height:32px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer;font-size:1.1rem;display:flex;align-items:center;justify-content:center;" title="Add model">+</button>
                </div>

                <label>Segment Duration (seconds)</label>
                <input type="number" id="editSegDuration" value="${p.segment_duration || 10}" min="5" max="30" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:6px;font-size:0.9rem;margin-bottom:16px;">

                <button class="btn-create-entity" style="margin-top:16px;padding:12px" onclick="saveProjectSettings()">Save Changes</button>
                <button class="btn-delete-project" onclick="deleteProjectFromSettings()">Delete Project</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function saveProjectSettings() {
    const body = {
        name: document.getElementById('editProjectName').value.trim(),
        analyzer_model_id: document.getElementById('editAnalyzerModelSelect').value,
        analyzer_models: getAnalyzerModelsFromSelect('editAnalyzerModelSelect'),
        segment_duration: parseInt(document.getElementById('editSegDuration').value),
    };

    await apiCall(`/projects/${activeProject.project_id}`, { method: 'PUT', body: JSON.stringify(body) });
    activeProject.name = body.name;
    activeProject.analyzer_model_id = body.analyzer_model_id;
    activeProject.analyzer_models = body.analyzer_models;
    document.getElementById('projectTitle').textContent = body.name;
    document.querySelector('.modal')?.remove();
}

async function deleteProjectFromSettings() {
    const name = activeProject.name;
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:400px;text-align:center;padding:40px 32px;">
            <h2 style="font-size:1.2rem;margin-bottom:6px;font-weight:700;">Delete "${escapeHtml(name)}"?</h2>
            <p style="color:#888;font-size:0.85rem;margin-bottom:20px;">This action is permanent and cannot be undone.</p>
            <div style="display:flex;gap:10px;">
                <button onclick="this.closest('.modal').remove()" style="flex:1;padding:11px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;font-size:0.9rem;font-weight:600;">Cancel</button>
                <button id="confirmDeleteBtn" onclick="executeProjectDeletion()" style="flex:1;padding:11px;border:none;border-radius:8px;background:#1a1a1a;color:#fff;cursor:pointer;font-size:0.9rem;font-weight:600;">Delete</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

async function executeProjectDeletion() {
    const modal = document.getElementById('confirmDeleteBtn').closest('.modal');
    modal.querySelector('.modal-content').innerHTML = '<div style="text-align:center;padding:40px;"><p>Deleting...</p></div>';
    try {
        await apiCall(`/projects/${activeProject.project_id}`, { method: 'DELETE' });
        setTimeout(() => { modal.remove(); showProjectScreen(); loadProjects(); }, 500);
    } catch (e) {
        modal.querySelector('.modal-content').innerHTML = `<div style="text-align:center;padding:40px;"><p>Error: ${escapeHtml(e.message)}</p><button onclick="this.closest('.modal').remove()" style="margin-top:16px;padding:8px 24px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;">Close</button></div>`;
    }
}

// ============================================
// API Helper
// ============================================

async function apiCall(endpoint, options = {}) {
    try {
        let token = auth.getIdToken();
        if (!token || !auth.isAuthenticated()) {
            if (await auth.refreshSession()) token = auth.getIdToken();
            else { showLoginScreen(); return null; }
        }

        const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json', ...options.headers };
        let url = `${CONFIG.API_ENDPOINT}${endpoint}`;
        if (activeProject?.project_id && !endpoint.startsWith('/projects')) {
            url += (endpoint.includes('?') ? '&' : '?') + `project_id=${activeProject.project_id}`;
        }

        const response = await fetch(url, { ...options, headers });
        if (response.status === 401 || response.status === 403) {
            if (await auth.refreshSession()) {
                const retry = await fetch(url, { ...options, headers: { ...headers, 'Authorization': `Bearer ${auth.getIdToken()}` } });
                if (retry.ok) return await retry.json();
            }
            showLoginScreen();
            return null;
        }
        if (!response.ok) throw new Error(`API error: ${response.statusText}`);
        return await response.json();
    } catch (error) {
        console.error('API call failed:', error.message);
        throw error;
    }
}

// ============================================
// Tab Switching
// ============================================

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchToTab(btn.dataset.tab));
});

function switchToTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.tabs [data-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(tabName)?.classList.add('active');
    localStorage.setItem('activeTab', tabName);

    if (tabName === 'gallery') loadGallery();
    if (tabName === 'upload') { loadJobs(); updateFAB('upload'); }
    if (tabName === 'search' || tabName === 'gallery') updateFAB(null);
    if (tabName === 'entityCatalog') { loadEntities(); updateFAB('entityCatalog'); }

    // Sync mobile nav
    document.querySelectorAll('.mobile-nav-item').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tabName);
    });
}

// ============================================
// FAB + Upload
// ============================================

const floatingActionBtn = document.getElementById('floatingActionBtn');
const fabIcon = document.getElementById('fabIcon');
const videoInput = document.getElementById('videoInput');
const uploadToast = document.getElementById('uploadToast');
const uploadStatus = document.getElementById('uploadStatus');

let fabClickHandler = null;

function updateFAB(tab) {
    if (fabClickHandler) {
        floatingActionBtn.removeEventListener('click', fabClickHandler);
        fabClickHandler = null;
    }

    if (tab === 'upload') {
        floatingActionBtn.style.display = 'flex';
        floatingActionBtn.title = 'Upload Video';
        floatingActionBtn.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
        fabClickHandler = () => videoInput.click();
        floatingActionBtn.addEventListener('click', fabClickHandler);
    } else if (tab === 'entityCatalog') {
        floatingActionBtn.style.display = 'flex';
        floatingActionBtn.title = 'Add Entity';
        floatingActionBtn.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
        fabClickHandler = () => showCreateEntityModal();
        floatingActionBtn.addEventListener('click', fabClickHandler);
    } else {
        floatingActionBtn.style.display = 'none';
    }
}
updateFAB('upload');

videoInput.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;
    uploadToast.style.display = 'block';
    uploadStatus.textContent = `Uploading ${files.length} video(s)...`;

    for (const file of files) {
        try {
            const data = await apiCall('/upload', {
                method: 'POST',
                body: JSON.stringify({ filename: file.name, contentType: file.type || 'video/mp4', project_id: activeProject?.project_id || '' })
            });
            if (data?.upload_url) {
                const resp = await fetch(data.upload_url, { method: 'PUT', body: file, headers: { 'Content-Type': file.type || 'video/mp4' } });
                uploadStatus.textContent = resp.ok ? `\u2713 ${file.name} uploaded` : `\u2717 ${file.name}: Upload failed`;
            }
        } catch (error) {
            uploadStatus.textContent = `\u2717 ${file.name}: ${error.message}`;
        }
    }
    setTimeout(() => { uploadToast.style.display = 'none'; loadJobs(); }, 2000);
    videoInput.value = '';
});

// ============================================
// Jobs
// ============================================

let pollInterval = null;

async function loadJobs() {
    const jobsStatus = document.getElementById('jobsStatus');
    const jobsList = document.getElementById('jobsList');

    try {
        const data = await apiCall('/videos');
        if (!data?.videos?.length) {
            jobsStatus.textContent = 'No videos yet. Upload videos to get started.';
            jobsList.innerHTML = '';
            stopPolling();
            return;
        }

        jobsStatus.textContent = `${data.videos.length} video(s)`;
        const hasActive = data.videos.some(v => v.status === 'pending' || v.status === 'processing');
        if (hasActive && !pollInterval) startPolling();
        else if (!hasActive && pollInterval) stopPolling();

        jobsList.innerHTML = data.videos.map((video, i) => `
            <div class="job-card fade-in-item" style="animation-delay:${i * 0.04}s" onclick="loadJobDetails('${escapeHtml(video.video_id)}')">
                <div class="job-header">
                    <h4>${escapeHtml(video.filename || video.video_id)}</h4>
                    <span class="job-status status-${escapeHtml(video.status)}">${escapeHtml(video.status)}</span>
                </div>
                <div class="job-info">
                    <div>Video ID: ${escapeHtml(video.video_id.substring(0, 8))}...</div>
                    <div>Updated: ${new Date(video.updated_at || video.created_at).toLocaleString()}</div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        jobsStatus.textContent = `Error: ${error.message}`;
        stopPolling();
    }
}

function startPolling() { if (!pollInterval) pollInterval = setInterval(loadJobs, 3000); }
function stopPolling() { if (pollInterval) { clearInterval(pollInterval); pollInterval = null; } }

async function loadJobDetails(videoId) {
    try {
        const data = await apiCall(`/videos/${videoId}`);
        if (!data) return;

        let durationHtml = '';
        if (data.created_at && data.completed_at) {
            const secs = Math.round((new Date(data.completed_at) - new Date(data.created_at)) / 1000);
            durationHtml = `<p><strong>Duration:</strong> ${Math.floor(secs/60)}m ${secs%60}s</p>`;
        }

        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content modal-large">
                <span class="modal-close" onclick="this.parentElement.parentElement.remove()">&times;</span>
                <h2>${escapeHtml(data.filename || data.video_id)}</h2>
                <div class="job-details">
                    <p><strong>Video ID:</strong> ${escapeHtml(data.video_id)}</p>
                    <p><strong>Status:</strong> ${escapeHtml(data.status)}</p>
                    <p><strong>Created:</strong> ${new Date(data.created_at).toLocaleString()}</p>
                    ${data.segment_count ? `<p><strong>Segments:</strong> ${data.segment_count}</p>` : ''}
                    ${data.genre ? `<p><strong>Genre:</strong> ${escapeHtml(data.genre)}</p>` : ''}
                    ${durationHtml}
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    } catch (error) {
        alert(`Error: ${error.message}`);
    }
}

// ============================================
// Search
// ============================================

const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const weightReasoning = document.getElementById('weightReasoning');
let allResults = [];
let displayedCount = 0;
const PAGE_SIZE = 12;

searchBtn.addEventListener('click', performSearch);
searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') performSearch(); });

async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    const bedrockStatus = document.getElementById('bedrockStatus');
    const bedrockResults = document.getElementById('bedrockResults');
    const bedrockLatency = document.getElementById('bedrockLatency');

    searchBtn.disabled = true;
    searchBtn.innerHTML = '<span class="search-spinner"></span>Searching';
    bedrockStatus.textContent = '';
    bedrockResults.innerHTML = '';
    weightReasoning.style.display = 'none';
    allResults = [];
    displayedCount = 0;

    const start = Date.now();
    try {
        const data = await apiCall('/search', { method: 'POST', body: JSON.stringify({ query }) });
        const ms = Date.now() - start;
        bedrockLatency.textContent = `${ms}ms`;
        if (data?.results) {
            allResults = data.results;
            displayWeights(data.weights, data.reasoning, data.timings, data.analyzer_model_id);
            displayResultsPage(bedrockResults);
            bedrockStatus.textContent = `Found ${data.total} results`;
        } else {
            bedrockStatus.textContent = data?.error || 'No results';
        }
    } catch (e) {
        bedrockStatus.textContent = `Error: ${e.message}`;
    }

    searchBtn.disabled = false;
    searchBtn.textContent = 'Search';
}

function displayWeights(weights, reasoning, timings, analyzerModelId) {
    weightReasoning.style.display = 'block';
    const models = getAnalyzerModels(activeProject);
    const modelLabel = (models.find(m => m.id === analyzerModelId) || { name: analyzerModelId || 'unknown' }).name;

    let timingInfo = '';
    if (timings && Object.keys(timings).length) {
        const total = (timings.preprocessing_ms || 0) + (timings.search_ms || 0) + (timings.reranking_ms || 0);
        timingInfo = `<div class="timing-info"><strong>Latency:</strong> Preprocessing: ${timings.preprocessing_ms || 0}ms | Search: ${timings.search_ms || 0}ms | Reranking: ${timings.reranking_ms || 0}ms | <strong>Total: ${total}ms</strong></div>`;
    }

    weightReasoning.innerHTML = `
        <details>
        <summary style="cursor:pointer;font-weight:600;font-size:0.95rem;padding:8px 0;">Search Strategy \u25B8</summary>
        <div style="margin-top:8px;">
        <p style="margin:0 0 6px;font-size:0.85rem;color:#888;">Analyzer: <strong>${escapeHtml(modelLabel)}</strong></p>
        <p>${escapeHtml(reasoning)}</p>
        ${timingInfo}
        <div class="weight-bars">
            <div class="weight-bar"><div class="weight-bar-label"><span>Metadata (BM25)</span><span>${((weights.metadata||0)*100).toFixed(0)}%</span></div><div class="weight-bar-fill"><div class="weight-bar-value" style="width:${(weights.metadata||0)*100}%;background:#f59e0b;"></div></div></div>
            <div class="weight-bar"><div class="weight-bar-label"><span>Visual</span><span>${(weights.visual*100).toFixed(0)}%</span></div><div class="weight-bar-fill"><div class="weight-bar-value" style="width:${weights.visual*100}%"></div></div></div>
            <div class="weight-bar"><div class="weight-bar-label"><span>Audio</span><span>${(weights.audio*100).toFixed(0)}%</span></div><div class="weight-bar-fill"><div class="weight-bar-value" style="width:${weights.audio*100}%"></div></div></div>
            <div class="weight-bar"><div class="weight-bar-label"><span>Transcription</span><span>${(weights.transcription*100).toFixed(0)}%</span></div><div class="weight-bar-fill"><div class="weight-bar-value" style="width:${weights.transcription*100}%"></div></div></div>
        </div>
        </div>
        </details>
    `;
}

function displayResultsPage(container) {
    const nextBatch = allResults.slice(displayedCount, displayedCount + PAGE_SIZE);

    nextBatch.forEach((result, i) => {
        const videoFile = `${result.video_id}_${result.filename}`;
        const card = document.createElement('div');
        card.className = 'result-card fade-in-item';
        card.style.animationDelay = `${i * 0.05}s`;
        const people = (result.people || []).filter(p => p);
        const peopleLine = people.length ? `<div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;">${people.map(p => `<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:12px;font-size:0.72rem;font-weight:500;">\u{1F464} ${escapeHtml(p)}</span>`).join('')}</div>` : '';
        card.innerHTML = `
            <video controls data-end="${parseFloat(result.end_sec) || 0}">
                <source src="${CONFIG.VIDEO_CDN}/uploads/${encodeURIComponent(videoFile)}#t=${parseFloat(result.start_sec) || 0},${parseFloat(result.end_sec) || 0}" type="video/mp4">
            </video>
            <div class="result-info" style="padding:10px 12px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                    <span style="font-weight:600;font-size:0.85rem;color:#111;">${formatTime(result.start_sec)} \u2013 ${formatTime(result.end_sec)}</span>
                    <span style="font-size:0.75rem;color:#888;font-weight:500;">Score ${(result.combined_score||0).toFixed(3)}</span>
                </div>
                ${result.caption ? `<div style="font-size:0.82rem;color:#444;line-height:1.45;margin-bottom:8px;">${escapeHtml(result.caption)}</div>` : ''}
                ${result.genre ? `<div><span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:12px;font-size:0.72rem;font-weight:500;">\u{1F3F7}\uFE0F ${escapeHtml(result.genre)}</span></div>` : ''}
                ${peopleLine}
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding-top:6px;border-top:1px solid #f0f0f0;">
                    <span style="font-size:0.75rem;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:70%;" title="${escapeHtml(result.filename)}">${escapeHtml(result.filename)}</span>
                    ${result.upload_date ? `<span style="font-size:0.72rem;color:#aaa;">${escapeHtml(result.upload_date)}</span>` : ''}
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    displayedCount += nextBatch.length;
    const existingBtn = document.getElementById('loadMoreBtn');
    if (existingBtn) existingBtn.remove();
    if (displayedCount < allResults.length) {
        const btn = document.createElement('button');
        btn.id = 'loadMoreBtn';
        btn.className = 'load-more-btn';
        btn.textContent = `Load More (${allResults.length - displayedCount} remaining)`;
        btn.onclick = () => displayResultsPage(container);
        container.appendChild(btn);
    }
}

// Stop video at segment end
document.addEventListener('timeupdate', (e) => {
    if (e.target.tagName === 'VIDEO' && e.target.dataset.end) {
        if (e.target.currentTime >= parseFloat(e.target.dataset.end)) e.target.pause();
    }
}, true);

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// ============================================
// Gallery
// ============================================

async function loadGallery() {
    const galleryStatus = document.getElementById('galleryStatus');
    const galleryGrid = document.getElementById('galleryGrid');
    galleryStatus.textContent = 'Loading videos...';

    try {
        const data = await apiCall('/videos');
        if (!data) { galleryStatus.textContent = 'Not authenticated'; return; }
        if (!data.videos.length) { galleryStatus.textContent = 'No videos uploaded yet'; galleryGrid.innerHTML = ''; return; }

        galleryStatus.textContent = `${data.videos.length} video(s)`;
        galleryGrid.innerHTML = data.videos.map(video => `
            <div class="result-card">
                <video controls>
                    <source src="${CONFIG.VIDEO_CDN}/uploads/${encodeURIComponent(video.video_id + '_' + video.filename)}" type="video/mp4">
                </video>
                <div class="result-info">
                    <div class="result-filename">${escapeHtml(video.filename)}</div>
                    <div class="result-time">${video.segment_count || 0} segments</div>
                    <div class="result-confidence">Status: ${escapeHtml(video.status)}</div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        galleryStatus.textContent = `Error: ${error.message}`;
    }
}

// ============================================
// Mobile Bottom Nav
// ============================================

document.querySelectorAll('.mobile-nav-item').forEach(btn => {
    btn.addEventListener('click', () => switchToTab(btn.dataset.tab));
});

// ============================================
// Entity Catalog
// ============================================

async function loadEntities() {
    const grid = document.getElementById('entityList');
    const status = document.getElementById('entityStatus');
    grid.innerHTML = '<p style="color:#888;">Loading entities...</p>';
    const data = await apiCall(`/entities?project_id=${activeProject?.project_id || ''}`);
    if (!data) return;
    const entities = data.entities || [];
    status.textContent = `${entities.length} entities`;
    if (!entities.length) { grid.innerHTML = ''; return; }
    grid.innerHTML = entities.map(e => {
        const imgSrc = e.image_key ? CONFIG.VIDEO_CDN + '/' + e.image_key : '';
        return `<div class="entity-card" style="border:1px solid #e5e5e5;border-radius:10px;position:relative;cursor:pointer;" data-entity-id="${escapeHtml(e.entity_id)}">
            <div style="padding:20px 16px 8px;display:flex;justify-content:center;">
                <div style="width:100px;height:100px;border-radius:50%;overflow:hidden;background:#f5f5f5;flex-shrink:0;">
                    ${imgSrc ? `<img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(e.name)}" style="width:100%;height:100%;object-fit:cover;">` :
                    `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#ccc" stroke-width="1.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>`}
                </div>
            </div>
            <div style="padding:8px 12px 12px;text-align:center;">
                <h4 style="font-size:0.9rem;margin-bottom:2px;font-weight:600;">${escapeHtml(e.name)}</h4>
                <p style="font-size:0.8rem;color:#888;margin-bottom:4px;">@${escapeHtml(e.name.toLowerCase().replace(/ /g, '_'))}</p>
                <span style="font-size:0.7rem;padding:2px 8px;border-radius:4px;background:${e.has_embedding ? '#dcfce7;color:#166534' : '#fef3c7;color:#92400e'};">${e.has_embedding ? '✓ Embedded' : 'No image'}</span>
                <div style="margin-top:10px;display:flex;gap:6px;">
                    <button onclick="editEntity('${escapeHtml(e.entity_id)}','${escapeHtml(e.name.replace(/'/g, "\\'"))}')" style="flex:1;padding:6px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer;font-size:0.75rem;">Rename</button>
                    <button onclick="deleteEntity('${escapeHtml(e.entity_id)}','${escapeHtml(e.name.replace(/'/g, "\\'"))}')" style="flex:1;padding:6px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer;font-size:0.75rem;color:#c00;">Delete</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

function showCreateEntityModal() {
    const d = document.createElement('div');
    d.className = 'modal';
    d.innerHTML = `
        <div class="modal-content" style="max-width:480px;padding:32px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
                <h2 style="font-size:1.2rem;font-weight:700;margin:0;">Create Entity</h2>
                <button onclick="this.closest('.modal').remove()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;color:#999;">&times;</button>
            </div>
            <div style="display:flex;flex-direction:column;gap:16px;">
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Entity Name <span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:#e5e5e5;color:#666;font-size:0.65rem;font-weight:700;cursor:help;margin-left:4px;" title="Used as @name in search queries">?</span></label>
                    <input type="text" id="entityNameInput" placeholder="e.g. main_speaker, red_car" style="width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:0.9rem;box-sizing:border-box;">
                </div>
                <div>
                    <label style="font-size:0.85rem;font-weight:600;color:#333;display:block;margin-bottom:6px;">Reference Image <span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:#e5e5e5;color:#666;font-size:0.65rem;font-weight:700;cursor:help;margin-left:4px;" title="Upload an image to generate a visual embedding for @search">?</span></label>
                    <div id="entityImageDrop" style="border:2px dashed #ddd;border-radius:8px;padding:32px;text-align:center;cursor:pointer;">
                        <input type="file" id="entityImageInput" accept="image/*" style="display:none;">
                        <p style="color:#888;font-size:0.85rem;margin:0;">Click or drag an image here</p>
                        <img id="entityImagePreview" style="display:none;max-width:200px;max-height:200px;border-radius:8px;margin-top:12px;">
                    </div>
                </div>
                <button id="createEntitySubmit" style="width:100%;padding:14px;border:none;border-radius:8px;background:#1a1a1a;color:#fff;font-size:0.95rem;font-weight:600;cursor:pointer;">Create Entity</button>
            </div>
        </div>`;
    document.body.appendChild(d);
    document.getElementById('entityNameInput').focus();

    const dropZone = document.getElementById('entityImageDrop');
    const fileInput = document.getElementById('entityImageInput');
    const preview = document.getElementById('entityImagePreview');
    dropZone.onclick = () => fileInput.click();
    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.style.borderColor = '#1a1a1a'; };
    dropZone.ondragleave = () => { dropZone.style.borderColor = '#ddd'; };
    dropZone.ondrop = (e) => { e.preventDefault(); dropZone.style.borderColor = '#ddd'; if (e.dataTransfer.files[0]) { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event('change')); } };
    fileInput.onchange = () => {
        if (fileInput.files[0]) {
            const reader = new FileReader();
            reader.onload = (ev) => { preview.src = ev.target.result; preview.style.display = 'block'; };
            reader.readAsDataURL(fileInput.files[0]);
        }
    };

    document.getElementById('createEntitySubmit').onclick = async () => {
        const name = document.getElementById('entityNameInput').value.trim();
        const file = fileInput.files[0];
        if (!name) { alert('Name is required'); return; }
        if (!file) { alert('Please upload a reference image'); return; }

        const btn = document.getElementById('createEntitySubmit');
        btn.disabled = true;
        btn.textContent = 'Creating...';

        try {
            // Step 1: Create entity and get presigned upload URL
            const data = await apiCall('/entities', { method: 'POST', body: JSON.stringify({ name, project_id: activeProject?.project_id || '' }) });
            if (!data?.entity_id) { btn.disabled = false; btn.textContent = 'Create Entity'; return; }

            // Step 2: Upload image to S3
            btn.textContent = 'Uploading image...';
            const uploadResp = await fetch(data.upload_url, { method: 'PUT', body: file, headers: { 'Content-Type': 'image/jpeg' } });
            if (!uploadResp.ok) throw new Error('Image upload failed');

            // Step 3: Generate embedding
            btn.textContent = 'Generating embedding...';
            await apiCall(`/entities/${data.entity_id}`, { method: 'PUT', body: JSON.stringify({ generate_embedding: true }) });

            d.remove();
            loadEntities();
        } catch (e) {
            console.error('Entity creation failed:', e);
            alert(`Entity creation failed: ${e.message}`);
            btn.disabled = false;
            btn.textContent = 'Create Entity';
        }
    };
    d.addEventListener('click', (e) => { if (e.target === d) d.remove(); });
}

async function editEntity(id, currentName) {
    const name = prompt('Rename entity:', currentName);
    if (name && name.trim()) {
        await apiCall(`/entities/${id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) });
        loadEntities();
    }
}

async function deleteEntity(id, name) {
    if (confirm(`Delete entity "${name}"?`)) {
        await apiCall(`/entities/${id}`, { method: 'DELETE' });
        loadEntities();
    }
}
