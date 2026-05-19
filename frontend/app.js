// Relative path — in Docker, nginx proxies /api/* to the backend container.
// Works the same when opening the frontend at any host/port.
const API_BASE = "/api";
let currentData = null;
let currentFilename = "";
let allCVs = []; // Cache for dashboard search

let authToken = localStorage.getItem('qcv_token');

// ── DOM References ──────────────────────────────────────────────
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const uploadView = document.getElementById('uploadView');
const loadingView = document.getElementById('loadingView');
const resultsView = document.getElementById('resultsView');
const dashboardView = document.getElementById('dashboardView');
const loginView = document.getElementById('loginView');
const headerActions = document.getElementById('headerActions');
const jsonEditor = document.getElementById('jsonEditor');
const btnSaveDB = document.getElementById('btnSaveDB');
const btnGeneratePPTX = document.getElementById('btnGeneratePPTX');
const navDashboardBtn = document.getElementById('navDashboardBtn');
const navUploadBtn = document.getElementById('navUploadBtn');
const navJobsBtn = document.getElementById('navJobsBtn');
const btnLogout = document.getElementById('btnLogout');
const loginForm = document.getElementById('loginForm');

// ── API Helper ──────────────────────────────────────────────────
async function apiFetch(endpoint, options = {}) {
    const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;
    
    // Add auth header
    options.headers = options.headers || {};
    if (authToken) {
        options.headers['Authorization'] = `Bearer ${authToken}`;
    }

    const response = await fetch(url, options);
    
    if (response.status === 401) {
        logout();
        throw new Error("Session expired. Please login again.");
    }
    
    return response;
}

// ── Navigation & Initialization ─────────────────────────────────
const jobsView = document.getElementById('jobsView');

function showView(viewId) {
    [uploadView, loadingView, resultsView, dashboardView, loginView, jobsView].forEach(v => {
        v.classList.add('hidden');
        v.classList.remove('flex');
    });
    
    const target = document.getElementById(viewId);
    target.classList.remove('hidden');
    if (viewId === 'loadingView' || viewId === 'loginView') target.classList.add('flex');
}

function init() {
    if (!authToken) {
        showView('loginView');
        headerActions.classList.add('hidden');
    } else {
        showView('dashboardView');
        loadDashboard();
        headerActions.classList.remove('flex'); // Switch from flex to hidden logic
        headerActions.classList.remove('hidden');
        headerActions.classList.add('flex');
    }
}

navDashboardBtn.addEventListener('click', () => {
    showView('dashboardView');
    loadDashboard();
});

navUploadBtn.addEventListener('click', () => {
    showView('uploadView');
});

navJobsBtn.addEventListener('click', () => {
    showView('jobsView');
    loadJobs();
});

btnLogout.addEventListener('click', logout);

function logout() {
    authToken = null;
    localStorage.removeItem('qcv_token');
    headerActions.classList.add('hidden');
    headerActions.classList.remove('flex');
    showView('loginView');
}

// ── Login Handler ───────────────────────────────────────────────
loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('loginEmail').value;
    const password = document.getElementById('loginPassword').value;
    const btn = e.target.querySelector('button');
    const originalText = btn.innerText;
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner spinner mr-2"></i>Authenticating...';

    try {
        const formData = new FormData();
        formData.append('username', email);
        formData.append('password', password);

        const response = await fetch(`${API_BASE}/login`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) throw new Error("Invalid credentials");

        const data = await response.json();
        authToken = data.access_token;
        localStorage.setItem('qcv_token', authToken);
        
        init(); // Refresh UI
    } catch (err) {
        alert(err.message);
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
    }
});

// ── Drag & Drop ─────────────────────────────────────────────────
['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.add('drag-active'), false);
});

['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove('drag-active'), false);
});

dropZone.addEventListener('drop', (e) => handleFiles(e.dataTransfer.files), false);
fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

function handleFiles(files) {
    if (files.length > 0) {
        if (files.length > 1) {
            alert("Bitte nur einen Lebenslauf gleichzeitig hochladen.");
        }
        uploadFile(files[0]);
    }
}

// ── Single Upload & Parse ───────────────────────────────────────
async function uploadFile(file) {
    currentFilename = file.name;
    const formData = new FormData();
    formData.append('file', file);

    showView('loadingView');

    try {
        const response = await apiFetch(`/parse-cv`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || "API parsing failed");
        }

        const result = await response.json();
        currentData = result.data;

        showView('resultsView');
        jsonEditor.value = JSON.stringify(currentData, null, 4);
        updatePreview(currentData);

    } catch (error) {
        console.error(error);
        if (authToken) alert("Error parsing document: " + error.message);
        showView('uploadView');
    }
}

// Batch processing has been removed.

// ── URL Import (LinkedIn / Xing / Freelancermap) ────────────────────────────
async function importFromUrl() {
    const url = document.getElementById('importUrlInput').value.trim();
    const text = document.getElementById('importTextInput').value.trim();

    if (!url && !text) {
        alert('Bitte URL eingeben oder Profil-Text einfügen.');
        return;
    }

    const btn = document.getElementById('btnImportUrl');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner spinner mr-1"></i> Importing...';

    showView('loadingView');

    try {
        const response = await apiFetch('/cvs/import-from-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, text }),
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Import failed');

        currentData = result.data;
        currentFilename = result.filename;

        showView('resultsView');
        jsonEditor.value = JSON.stringify(currentData, null, 4);
        updatePreview(currentData);
    } catch (err) {
        alert('Import failed: ' + err.message);
        showView('uploadView');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHTML;
    }
}

// ── Preview Panel ───────────────────────────────────────────────
jsonEditor.addEventListener('input', (e) => {
    try {
        const parsed = JSON.parse(e.target.value);
        currentData = parsed;
        updatePreview(parsed);
    } catch (err) { /* ignore invalid JSON while editing */ }
});

function updatePreview(data) {
    const personal = data.personal_information || {};
    document.getElementById('previewName').textContent = personal.full_name || "Unknown Candidate";
    document.getElementById('previewEmail').textContent = personal.email || "-";
    document.getElementById('previewPhone').textContent = personal.phone || "-";
    document.getElementById('previewLocation').textContent = personal.location || "-";

    document.getElementById('previewSummary').textContent = data.small_summary || "No summary available.";

    const skillsContainer = document.getElementById('previewSkills');
    skillsContainer.innerHTML = '';
    (data.skill_matrix || []).forEach(group => {
        const catName = group.category || "General";
        
        // Add Category Header
        const catHeader = document.createElement('h4');
        catHeader.className = 'text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-4 mb-2';
        catHeader.textContent = catName;
        skillsContainer.appendChild(catHeader);

        (group.skills || []).forEach(s => {
            const name = s.skill || "";
            const rating = s.rating;
            if (name) {
                const r = Math.min(10, Math.max(0, parseInt(rating) || 5));
                const pct = (r / 10) * 100;
                const barColor = r >= 8 ? '#22c55e' : r >= 5 ? '#f59e0b' : '#ef4444';
                const div = document.createElement('div');
                div.className = 'flex items-center gap-2 w-full py-1';
                div.innerHTML = `
                    <span class="text-xs text-slate-700 font-medium w-2/5 shrink-0 break-words leading-tight">${name}</span>
                    <div class="flex-1 bg-slate-200 rounded-full h-1.5 overflow-hidden">
                        <div style="width:${pct}%; background:${barColor};" class="h-full rounded-full transition-all duration-300"></div>
                    </div>
                    <span class="text-xs font-bold w-6 text-right shrink-0" style="color:${barColor}">${r}</span>
                `;
                skillsContainer.appendChild(div);
            }
        });
    });

    const projectsContainer = document.getElementById('previewProjects');
    projectsContainer.innerHTML = '';
    (data.projects || []).forEach(p => {
        const div = document.createElement('div');
        div.className = 'border-l-2 border-brand-blue pl-3';
        div.innerHTML = `
            <div class="text-xs font-bold text-slate-900">${p.name || 'Unnamed Project'}</div>
            <div class="text-[10px] text-slate-500 mb-1">${p.duration || ''}</div>
            <div class="text-[11px] text-slate-600 line-clamp-2">${p.description || ''}</div>
        `;
        projectsContainer.appendChild(div);
    });
}

// ── Save to DB ──────────────────────────────────────────────────
btnSaveDB.addEventListener('click', async () => {
    try {
        const finalData = JSON.parse(jsonEditor.value);
        const payload = { filename: currentFilename, data: finalData };
        const btnOriginalHTML = btnSaveDB.innerHTML;
        btnSaveDB.innerHTML = '<i class="fa-solid fa-spinner spinner"></i> Saving...';

        const response = await apiFetch(`/save-cv`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            btnSaveDB.innerHTML = '<i class="fa-solid fa-check text-green-500"></i> Saved';
            setTimeout(() => { btnSaveDB.innerHTML = btnOriginalHTML; }, 2000);
        } else {
            throw new Error("Failed to save");
        }
    } catch (err) {
        alert("Error saving data: " + err.message);
        btnSaveDB.innerHTML = '<i class="fa-solid fa-database"></i> Save to Database';
    }
});

// ── Generate PPTX ───────────────────────────────────────────────
function triggerBlobDownload(blob, filename) {
    // Ensure filename ends with .pptx
    if (!filename.toLowerCase().endsWith('.pptx')) filename += '.pptx';
    
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.setAttribute('download', filename); // Use setAttribute for better compatibility
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    
    // Slight delay before cleanup
    setTimeout(() => {
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    }, 1000);
}

async function downloadPPTXDirect(cvId) {
    // Fetch the PPTX as a blob, then extract filename from Content-Disposition
    const url = `${API_BASE}/cvs/${cvId}/pptx`;
    const response = await fetch(url, {
        headers: { "Authorization": `Bearer ${authToken}` }
    });
    if (!response.ok) {
        const errData = await response.json().catch(() => ({ detail: "Failed to download PPTX" }));
        throw new Error(errData.detail || "Server error during PPTX generation");
    }

    // Try to get filename from Content-Disposition header
    let filename = "CV_Summary.pptx";
    const disposition = response.headers.get("Content-Disposition");
    if (disposition) {
        const match = disposition.match(/filename[^;=\n]*="?([^";\n]+)"?/i);
        if (match && match[1]) filename = match[1];
    }

    const arrayBuffer = await response.arrayBuffer();
    const blob = new Blob([arrayBuffer], {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    });
    triggerBlobDownload(blob, filename);
}

async function generatePPTXFromData(data) {
    // POST approach for unsaved/edited data (from JSON editor)
    if (!data || Object.keys(data).length === 0) {
        throw new Error("No data available to export.");
    }
    const response = await apiFetch(`/export-pptx`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data })
    });
    if (!response.ok) {
        const errData = await response.json().catch(() => ({ detail: "Failed to generate PPTX" }));
        throw new Error(errData.detail || "Server error during PPTX generation");
    }

    const name = data.personal_information?.full_name || "CV";
    const filename = `${name.replace(/\s+/g, '_')}_Summary.pptx`;

    const arrayBuffer = await response.arrayBuffer();
    const blob = new Blob([arrayBuffer], {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    });
    triggerBlobDownload(blob, filename);
}

btnGeneratePPTX.addEventListener('click', async () => {
    try {
        const finalData = JSON.parse(jsonEditor.value);
        const btnOriginalHTML = btnGeneratePPTX.innerHTML;
        btnGeneratePPTX.innerHTML = '<i class="fa-solid fa-spinner spinner"></i> Generating...';
        await generatePPTXFromData(finalData);
        btnGeneratePPTX.innerHTML = '<i class="fa-solid fa-check"></i> Downloaded';
        setTimeout(() => { btnGeneratePPTX.innerHTML = btnOriginalHTML; }, 2000);
    } catch (err) {
        alert("Error generating PPTX: " + err.message);
        btnGeneratePPTX.innerHTML = '<i class="fa-solid fa-file-powerpoint"></i> Generate PPTX';
    }
});

// ── Dashboard ───────────────────────────────────────────────────
async function loadDashboard() {
    const tbody = document.getElementById('dashboardTableBody');
    const emptyState = document.getElementById('dashboardEmpty');
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-10 text-slate-400"><i class="fa-solid fa-spinner spinner mr-2"></i>Loading...</td></tr>';
    emptyState.classList.add('hidden');

    try {
        const response = await apiFetch(`/cvs`);
        allCVs = await response.json();
        document.getElementById('statTotal').textContent = allCVs.length;
        document.getElementById('statNew').textContent = allCVs.filter(c => (c.status || 'new') === 'new').length;
        document.getElementById('statInvited').textContent = allCVs.filter(c => c.status === 'invited').length;
        renderDashboardTable(allCVs);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center py-10 text-red-400">Failed to load data. ${err.message}</td></tr>`;
    }
}

function renderDashboardTable(cvs) {
    const tbody = document.getElementById('dashboardTableBody');
    const emptyState = document.getElementById('dashboardEmpty');
    if (cvs.length === 0) {
        tbody.innerHTML = '';
        emptyState.classList.remove('hidden');
        return;
    }
    emptyState.classList.add('hidden');
    tbody.innerHTML = cvs.map((cv, i) => {
        const date = cv.created_at ? new Date(cv.created_at).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '-';
        const initials = (cv.name || "??").split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
        
        // JSON-Wissen anwenden: Text in Objekt umwandeln und Skills zählen
        let skillCount = 0;
        try {
            const data = JSON.parse(cv.raw_json || "{}");
            skillCount = (data.skill_matrix || []).reduce((sum, g) => sum + (g.skills || []).length, 0);
        } catch (e) { console.error("JSON Error", e); }

        return `
        <tr class="border-b border-slate-100 hover:bg-slate-50/80 transition-colors">
            <td class="px-3 py-2.5 text-slate-400 font-mono text-xs">${cv.id}</td>
            <td class="px-3 py-2.5">
                <div class="flex items-center gap-2">
                    <div class="w-7 h-7 rounded-full bg-brand-light text-brand-blue flex items-center justify-center text-xs font-bold flex-shrink-0">${initials}</div>
                    <span class="font-semibold text-slate-800 text-sm">${cv.name || 'Unknown'}</span>
                </div>
            </td>
            <td class="px-3 py-2.5 text-slate-500 text-xs max-w-[160px]"><span class="block truncate" title="${cv.email || ''}">${cv.email || '-'}</span></td>
            <td class="px-3 py-2.5 max-w-[120px]"><span class="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs font-mono block truncate" title="${cv.filename || ''}">${cv.filename || '-'}</span></td>
            <td class="px-3 py-2.5">
                <span class="px-2 py-1 bg-blue-50 text-blue-600 rounded-full text-xs font-bold border border-blue-100 whitespace-nowrap">
                    ${skillCount} Skills
                </span>
            </td>
            <td class="px-3 py-2.5">${renderStatusSelect(cv.id, cv.status || 'new')}</td>
            <td class="px-3 py-2.5 text-slate-500 text-xs whitespace-nowrap">${date}</td>
            <td class="px-3 py-2.5 text-right">
                <div class="flex items-center justify-end gap-1">
                    <button onclick="previewCandidate(${cv.id})" class="p-1.5 rounded hover:bg-brand-light text-slate-400 hover:text-brand-blue transition-colors" title="Anzeigen"><i class="fa-solid fa-eye text-sm"></i></button>
                    <button onclick="downloadCVPPTX(${cv.id})" class="p-1.5 rounded hover:bg-green-50 text-slate-400 hover:text-green-600 transition-colors" title="PPTX"><i class="fa-solid fa-file-powerpoint text-sm"></i></button>
                    <button onclick="deleteCV(${cv.id})" class="p-1.5 rounded hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors" title="Löschen"><i class="fa-solid fa-trash-can text-sm"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

async function viewCV(id) {
    try {
        const response = await apiFetch(`/cvs/${id}`);
        const cv = await response.json();
        currentData = cv.data;
        currentFilename = cv.filename;
        showView('resultsView');
        jsonEditor.value = JSON.stringify(currentData, null, 4);
        updatePreview(currentData);
    } catch (err) { alert(err.message); }
}

async function downloadCVPPTX(id) {
    try {
        await downloadPPTXDirect(id);
    } catch (err) { alert("Error downloading PPTX: " + err.message); }
}

async function deleteCV(id) {
    if (!confirm("Are you sure?")) return;
    try {
        await apiFetch(`/cvs/${id}`, { method: 'DELETE' });
        loadDashboard();
    } catch (err) { alert(err.message); }
}

const STATUS_CONFIG = {
    new:       { label: 'New',       classes: 'bg-slate-100 text-slate-600 border-slate-300' },
    in_review: { label: 'In Review', classes: 'bg-blue-50 text-blue-600 border-blue-200' },
    invited:   { label: 'Invited',   classes: 'bg-green-50 text-green-600 border-green-200' },
    rejected:  { label: 'Rejected',  classes: 'bg-red-50 text-red-500 border-red-200' },
};

function renderStatusSelect(id, status) {
    const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.new;
    const options = Object.entries(STATUS_CONFIG)
        .map(([val, c]) => `<option value="${val}" ${val === status ? 'selected' : ''}>${c.label}</option>`)
        .join('');
    return `<select onchange="updateStatus(${id}, this.value)"
        class="text-xs font-semibold border rounded-full px-2 py-1 cursor-pointer outline-none ${cfg.classes}">
        ${options}
    </select>`;
}

async function updateStatus(id, newStatus) {
    try {
        await apiFetch(`/cvs/${id}/status`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
        });
        const cv = allCVs.find(c => c.id === id);
        if (cv) cv.status = newStatus;
        document.getElementById('statNew').textContent = allCVs.filter(c => (c.status || 'new') === 'new').length;
        document.getElementById('statInvited').textContent = allCVs.filter(c => c.status === 'invited').length;
    } catch (err) { alert('Failed to update status: ' + err.message); }
}

// ── Jobs ────────────────────────────────────────────────────────────────────

let currentJobData = null;

async function parseJobEmail() {
    const emailText = document.getElementById('jobEmailInput').value.trim();
    if (!emailText) { alert('Please paste an email first.'); return; }

    const btn = document.getElementById('btnParseEmail');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner spinner mr-2"></i> Parsing...';

    try {
        const response = await apiFetch('/jobs/parse-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email_text: emailText }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Parsing failed');
        currentJobData = result.data;
        renderJobPreview(currentJobData);
        document.getElementById('jobPreviewCard').classList.remove('hidden');
    } catch (err) {
        alert('Error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Parse Email';
    }
}

function renderJobPreview(data) {
    document.getElementById('jobPreviewTitle').textContent = data.title || '—';
    document.getElementById('jobPreviewDesc').textContent = data.description || '';
    document.getElementById('jobPreviewExp').textContent = data.experience_years ? `${data.experience_years}+ years` : '—';
    document.getElementById('jobPreviewLoc').textContent = data.location || '—';
    document.getElementById('jobPreviewRemote').textContent =
        data.remote === true || data.remote === 'true' ? 'Yes' :
        data.remote === false || data.remote === 'false' ? 'No' : '—';

    const req = document.getElementById('jobPreviewRequired');
    req.innerHTML = (data.required_skills || []).map(s =>
        `<span class="bg-brand-light text-brand-blue text-xs font-medium px-2 py-0.5 rounded-full">${s}</span>`
    ).join('');

    const nice = document.getElementById('jobPreviewNice');
    nice.innerHTML = (data.nice_to_have_skills || []).map(s =>
        `<span class="bg-slate-100 text-slate-600 text-xs font-medium px-2 py-0.5 rounded-full">${s}</span>`
    ).join('');
}

async function saveJob() {
    if (!currentJobData) return;
    try {
        const response = await apiFetch('/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                data: currentJobData,
                raw_email: document.getElementById('jobEmailInput').value.trim(),
            }),
        });
        if (!response.ok) throw new Error('Save failed');
        document.getElementById('jobPreviewCard').classList.add('hidden');
        document.getElementById('jobEmailInput').value = '';
        currentJobData = null;
        loadJobs();
    } catch (err) {
        alert('Error saving job: ' + err.message);
    }
}

async function loadJobs() {
    const tbody = document.getElementById('jobsTableBody');
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-10 text-slate-400"><i class="fa-solid fa-spinner spinner mr-2"></i>Loading...</td></tr>';
    try {
        const response = await apiFetch('/jobs');
        const jobs = await response.json();
        renderJobsTable(jobs);
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center py-10 text-red-400">Failed to load jobs.</td></tr>`;
    }
}

const JOB_STATUS_CONFIG = {
    open:      { label: 'Open',      classes: 'bg-blue-50 text-blue-600 border-blue-200' },
    filled:    { label: 'Filled',    classes: 'bg-green-50 text-green-600 border-green-200' },
    cancelled: { label: 'Cancelled', classes: 'bg-red-50 text-red-500 border-red-200' },
};

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobsTableBody');
    const empty = document.getElementById('jobsEmpty');
    if (!jobs.length) { tbody.innerHTML = ''; empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    tbody.innerHTML = jobs.map(j => {
        const date = j.created_at ? new Date(j.created_at).toLocaleDateString('de-DE') : '—';
        const skills = (j.required_skills || []).slice(0, 4).map(s =>
            `<span class="bg-brand-light text-brand-blue text-xs px-1.5 py-0.5 rounded">${s}</span>`
        ).join(' ');
        const cfg = JOB_STATUS_CONFIG[j.status] || JOB_STATUS_CONFIG.open;
        const statusOpts = Object.entries(JOB_STATUS_CONFIG).map(([v, c]) =>
            `<option value="${v}" ${v === j.status ? 'selected' : ''}>${c.label}</option>`
        ).join('');
        return `
        <tr class="border-b border-slate-100 hover:bg-slate-50/80 transition-colors group">
            <td class="px-5 py-3 text-slate-400 font-mono text-xs">${j.id}</td>
            <td class="px-5 py-3 font-semibold text-slate-800">${j.title}</td>
            <td class="px-5 py-3"><div class="flex flex-wrap gap-1">${skills}</div></td>
            <td class="px-5 py-3 text-slate-500">${j.location || '—'}</td>
            <td class="px-5 py-3">
                <select onchange="updateJobStatus(${j.id}, this.value)"
                    class="text-xs font-semibold border rounded-full px-2 py-1 cursor-pointer outline-none ${cfg.classes}">
                    ${statusOpts}
                </select>
            </td>
            <td class="px-5 py-3 text-slate-500">${date}</td>
            <td class="px-5 py-3 text-right">
                <div class="flex items-center justify-end gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                    <button onclick="viewJob(${j.id})" class="p-1.5 rounded hover:bg-brand-light text-slate-500 hover:text-brand-blue transition-colors"><i class="fa-solid fa-eye text-sm"></i></button>
                    <button onclick="deleteJob(${j.id})" class="p-1.5 rounded hover:bg-red-50 text-slate-500 hover:text-red-500 transition-colors"><i class="fa-solid fa-trash-can text-sm"></i></button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

async function updateJobStatus(id, newStatus) {
    try {
        await apiFetch(`/jobs/${id}/status`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus }),
        });
    } catch (err) { alert('Failed to update status: ' + err.message); }
}

async function deleteJob(id) {
    if (!confirm('Delete this job requirement?')) return;
    try {
        await apiFetch(`/jobs/${id}`, { method: 'DELETE' });
        loadJobs();
    } catch (err) { alert(err.message); }
}

// ── Candidate Detail Modal ───────────────────────────────────────────────────

let currentCandidateId = null;

function closeCandidateModal() {
    document.getElementById('candidateModal').classList.add('hidden');
    currentCandidateId = null;
}

async function previewCandidate(id) {
    currentCandidateId = id;
    try {
        const response = await apiFetch(`/cvs/${id}`);
        const cv = await response.json();
        const data = cv.data || {};
        const personal = data.personal_information || {};

        const name = personal.full_name || 'Unknown';
        const initials = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
        document.getElementById('candidateModalAvatar').textContent = initials;
        document.getElementById('candidateModalName').textContent = name;
        document.getElementById('candidateModalEmail').textContent = personal.email || '';
        document.getElementById('candidateModalLocation').textContent = personal.location ? `📍 ${personal.location}` : '';
        document.getElementById('candidateModalSummary').textContent = data.small_summary || '';

        document.getElementById('candidateModalEditBtn').onclick = () => {
            closeCandidateModal();
            currentData = data;
            currentFilename = cv.filename;
            showView('resultsView');
            jsonEditor.value = JSON.stringify(data, null, 4);
            updatePreview(data);
        };

        const skillsContainer = document.getElementById('candidateModalSkills');
        skillsContainer.innerHTML = '';
        (data.skill_matrix || []).forEach(group => {
            const section = document.createElement('div');
            const header = document.createElement('div');
            header.className = 'text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2';
            header.textContent = group.category || 'General';
            section.appendChild(header);
            (group.skills || []).forEach(s => {
                const r = Math.min(10, Math.max(0, parseInt(s.rating) || 5));
                const pct = (r / 10) * 100;
                const color = r >= 8 ? '#22c55e' : r >= 5 ? '#f59e0b' : '#ef4444';
                const row = document.createElement('div');
                row.className = 'flex items-center gap-2 py-0.5';
                row.innerHTML = `
                    <span class="text-xs text-slate-700 w-40 shrink-0 truncate">${s.skill}</span>
                    <div class="flex-1 bg-slate-200 rounded-full h-1.5 overflow-hidden">
                        <div style="width:${pct}%;background:${color}" class="h-full rounded-full"></div>
                    </div>
                    <span class="text-xs font-bold w-5 text-right shrink-0" style="color:${color}">${r}</span>`;
                section.appendChild(row);
            });
            skillsContainer.appendChild(section);
        });

        const projectsContainer = document.getElementById('candidateModalProjects');
        projectsContainer.innerHTML = '';
        (data.projects || []).forEach(p => {
            const div = document.createElement('div');
            div.className = 'border-l-2 border-brand-blue pl-3 py-0.5';
            div.innerHTML = `
                <div class="text-sm font-bold text-slate-900">${p.name || 'Unnamed'}</div>
                <div class="text-xs text-slate-400 mb-1">${p.duration || ''}</div>
                <div class="text-xs text-slate-600 line-clamp-3">${p.description || ''}</div>`;
            projectsContainer.appendChild(div);
        });

        document.getElementById('candidateModal').classList.remove('hidden');
    } catch (err) {
        alert('Error loading candidate: ' + err.message);
    }
}

// ── Job Detail Modal & Matching ─────────────────────────────────────────────

let currentJobId = null;

function closeJobModal() {
    document.getElementById('jobModal').classList.add('hidden');
    currentJobId = null;
}

// Score-color thresholds (single source of truth)
const SCORE_THRESHOLDS = {
    high:   { min: 70, classes: 'bg-green-50 text-green-700 border-green-200' },
    medium: { min: 40, classes: 'bg-amber-50 text-amber-700 border-amber-200' },
    low:    { min: 0,  classes: 'bg-red-50 text-red-500 border-red-200' },
};

function scoreClasses(score) {
    if (score >= SCORE_THRESHOLDS.high.min) return SCORE_THRESHOLDS.high.classes;
    if (score >= SCORE_THRESHOLDS.medium.min) return SCORE_THRESHOLDS.medium.classes;
    return SCORE_THRESHOLDS.low.classes;
}

// Match filter state (per-modal-open)
let lastMatchResults = [];
let matchStatusFilters = new Set();
let matchMinScore = 0;

async function viewJob(id) {
    try {
        const response = await apiFetch(`/jobs/${id}`);
        const job = await response.json();
        currentJobId = job.id;

        document.getElementById('modalJobTitle').textContent = job.title || '—';
        document.getElementById('modalJobDesc').textContent = job.description || '';
        document.getElementById('modalJobExp').textContent = job.experience_years ? `${job.experience_years}+ yrs exp` : '';
        document.getElementById('modalJobLoc').textContent = job.location ? `📍 ${job.location}` : '';
        document.getElementById('modalJobRemote').textContent =
            job.remote === 'true' ? 'Remote: Yes' :
            job.remote === 'false' ? 'Remote: No' : '';

        document.getElementById('modalJobRequired').innerHTML = (job.required_skills || []).map(s =>
            `<span class="bg-brand-light text-brand-blue text-xs font-medium px-2 py-0.5 rounded-full">${s}</span>`
        ).join('');
        document.getElementById('modalJobNice').innerHTML = (job.nice_to_have_skills || []).map(s =>
            `<span class="bg-slate-100 text-slate-600 text-xs font-medium px-2 py-0.5 rounded-full">${s}</span>`
        ).join('');

        // Reset filter state for the new modal
        matchStatusFilters = new Set();
        matchMinScore = 0;
        document.getElementById('matchMinScore').value = 0;
        document.getElementById('matchMinScoreValue').textContent = '0%';
        document.querySelectorAll('.match-status-btn').forEach(b => {
            b.classList.remove('bg-brand-blue', 'text-white', 'border-brand-blue');
            b.classList.add('text-slate-600', 'border-slate-300');
        });
        document.getElementById('matchFilters').classList.add('hidden');
        document.getElementById('matchResults').innerHTML = '';
        document.getElementById('jobModal').classList.remove('hidden');

        // Auto-load matches
        matchCandidates();
    } catch (err) {
        alert('Error loading job: ' + err.message);
    }
}

async function matchCandidates() {
    if (!currentJobId) return;
    const btn = document.getElementById('btnMatchCandidates');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner spinner mr-1"></i> Matching...';

    try {
        const response = await apiFetch(`/jobs/${currentJobId}/matches`);
        lastMatchResults = await response.json();

        if (!lastMatchResults.length) {
            document.getElementById('matchResults').innerHTML =
                '<p class="text-sm text-slate-400 text-center py-4">No candidates in database yet.</p>';
            document.getElementById('matchFilters').classList.add('hidden');
            return;
        }
        document.getElementById('matchFilters').classList.remove('hidden');
        renderMatchResults();
    } catch (err) {
        document.getElementById('matchResults').innerHTML = `<p class="text-sm text-red-400 py-2">Error: ${err.message}</p>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Refresh';
    }
}

function renderMatchResults() {
    const container = document.getElementById('matchResults');
    const filtered = lastMatchResults.filter(c => {
        if (c.score < matchMinScore) return false;
        if (matchStatusFilters.size > 0 && !matchStatusFilters.has(c.status)) return false;
        return true;
    });

    document.getElementById('matchResultsCount').textContent =
        `${filtered.length} / ${lastMatchResults.length} shown`;

    if (!filtered.length) {
        container.innerHTML = '<p class="text-sm text-slate-400 text-center py-4">No candidates match the current filters.</p>';
        return;
    }

    const stripParen = s => s.replace(/\s*\(.*?\)/g, '').trim();

    container.innerHTML = filtered.map(c => {
        const sc = scoreClasses(c.score);
        const matched = c.matched_required.map(s =>
            `<span class="bg-green-50 text-green-700 text-xs px-1.5 py-0.5 rounded border border-green-200">${stripParen(s)}</span>`
        ).join('');
        const missing = c.missing_required.map(s =>
            `<span class="bg-red-50 text-red-500 text-xs px-1.5 py-0.5 rounded border border-red-200 line-through">${stripParen(s)}</span>`
        ).join('');
        const nice = c.matched_nice.map(s =>
            `<span class="bg-blue-50 text-blue-600 text-xs px-1.5 py-0.5 rounded border border-blue-100">${stripParen(s)}</span>`
        ).join('');

        // Component-score breakdown row
        const parts = [`Skills: <strong>${c.skills_score}%</strong>`];
        if (c.experience_score !== null && c.experience_score !== undefined) {
            parts.push(`Experience: <strong>${c.experience_score}%</strong>`);
        }
        if (c.location_score !== null && c.location_score !== undefined) {
            parts.push(`Location: <strong>${c.location_score}%</strong>`);
        }
        const breakdown = parts.join(' · ');

        return `
        <div class="border border-slate-200 rounded-xl p-4 mb-3 flex items-start gap-4 hover:bg-slate-50/60 transition-colors">
            <div class="flex-shrink-0 w-14 h-14 rounded-lg border-2 flex flex-col items-center justify-center font-bold text-xl leading-none ${sc}">
                ${c.score}<span class="text-[10px] font-normal leading-tight">%</span>
            </div>
            <div class="flex-1 min-w-0">
                <div class="flex items-center justify-between mb-1">
                    <div>
                        <span class="font-semibold text-slate-800">${c.name || 'Unknown'}</span>
                        <span class="text-xs text-slate-400 ml-2">${c.email || ''}</span>
                    </div>
                    <button onclick="previewCandidate(${c.id})"
                        class="p-1.5 rounded hover:bg-brand-light text-slate-400 hover:text-brand-blue transition-colors flex-shrink-0" title="Kandidat anzeigen">
                        <i class="fa-solid fa-eye text-sm"></i>
                    </button>
                </div>
                <div class="text-[11px] text-slate-500 mb-2">${breakdown}</div>
                <div class="flex flex-wrap gap-1.5">${matched}${missing}${nice}</div>
            </div>
        </div>`;
    }).join('');
}

function toggleMatchStatusFilter(status) {
    const btn = document.querySelector(`.match-status-btn[data-status="${status}"]`);
    if (matchStatusFilters.has(status)) {
        matchStatusFilters.delete(status);
        btn.classList.remove('bg-brand-blue', 'text-white', 'border-brand-blue');
        btn.classList.add('text-slate-600', 'border-slate-300');
    } else {
        matchStatusFilters.add(status);
        btn.classList.add('bg-brand-blue', 'text-white', 'border-brand-blue');
        btn.classList.remove('text-slate-600', 'border-slate-300');
    }
    renderMatchResults();
}

// Min-score slider listener (attached once at load)
document.addEventListener('DOMContentLoaded', () => {
    const slider = document.getElementById('matchMinScore');
    if (slider) {
        slider.addEventListener('input', (e) => {
            matchMinScore = parseInt(e.target.value, 10);
            document.getElementById('matchMinScoreValue').textContent = `${matchMinScore}%`;
            renderMatchResults();
        });
    }
});

let activeFilter = 'all';

function filterByStatus(filter) {
    activeFilter = filter;
    document.querySelectorAll('.status-filter-btn').forEach(btn => {
        const isActive = btn.dataset.filter === filter;
        btn.className = btn.className
            .replace(/bg-\S+ text-white border-\S+/g, '')
            .trim();
        if (isActive) {
            btn.classList.add('bg-brand-blue', 'text-white', 'border-brand-blue');
            btn.classList.remove('text-slate-600', 'border-slate-300');
        } else {
            btn.classList.remove('bg-brand-blue', 'text-white', 'border-brand-blue');
            btn.classList.add('text-slate-600', 'border-slate-300');
        }
    });
    const filtered = filter === 'all' ? allCVs : allCVs.filter(c => (c.status || 'new') === filter);
    renderDashboardTable(filtered);
}

document.getElementById('dashboardSearch').addEventListener('input', (e) => {
    const query = e.target.value.toLowerCase().trim();
    const filtered = allCVs.filter(cv => 
        (cv.name || '').toLowerCase().includes(query) || 
        (cv.email || '').toLowerCase().includes(query) ||
        (cv.raw_json || '').toLowerCase().includes(query)
    );
    renderDashboardTable(filtered);
});

document.getElementById('dashboardRefreshBtn').addEventListener('click', loadDashboard);

// ── Outlook / Inbox OAuth ───────────────────────────────────────────────────
function toggleInboxPanel() {
    document.getElementById('inboxPanel').classList.toggle('hidden');
}

// Click outside → close panel
document.addEventListener('click', (e) => {
    const panel = document.getElementById('inboxPanel');
    const btn = document.getElementById('inboxStatusBtn');
    if (!panel || !btn) return;
    if (panel.classList.contains('hidden')) return;
    if (!panel.contains(e.target) && !btn.contains(e.target)) {
        panel.classList.add('hidden');
    }
});

async function refreshInboxStatus() {
    if (!authToken) return;
    try {
        const r = await apiFetch('/oauth/status');
        if (!r.ok) return;
        const status = await r.json();
        const ms = status.microsoft || { connected: false };

        const dot = document.getElementById('inboxStatusDot');
        const notConnected = document.getElementById('inboxNotConnected');
        const connected = document.getElementById('inboxConnected');
        const notConfigured = document.getElementById('inboxNotConfigured');

        if (ms.connected) {
            dot.classList.remove('bg-slate-300', 'bg-red-400');
            dot.classList.add('bg-green-500');
            notConnected.classList.add('hidden');
            connected.classList.remove('hidden');
            document.getElementById('inboxConnectedEmail').textContent = ms.email || '';
        } else {
            dot.classList.remove('bg-green-500', 'bg-red-400');
            dot.classList.add('bg-slate-300');
            connected.classList.add('hidden');
            notConnected.classList.remove('hidden');
            // Backend not configured → show hint
            if (ms.configured === false) {
                notConfigured.classList.remove('hidden');
            } else {
                notConfigured.classList.add('hidden');
            }
        }
    } catch (err) {
        console.error('inbox status failed', err);
    }
}

async function connectOutlook() {
    try {
        const r = await apiFetch('/oauth/microsoft/authorize');
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || 'Authorize failed');
        // Redirect the browser to Microsoft's sign-in page
        window.location.href = data.url;
    } catch (err) {
        alert('Verbindung fehlgeschlagen: ' + err.message);
    }
}

async function disconnectOutlook() {
    if (!confirm('Outlook-Verbindung wirklich trennen?')) return;
    try {
        const r = await apiFetch('/oauth/microsoft/disconnect', { method: 'POST' });
        if (!r.ok) {
            const d = await r.json();
            throw new Error(d.detail || 'Disconnect failed');
        }
        refreshInboxStatus();
        document.getElementById('inboxPanel').classList.add('hidden');
    } catch (err) {
        alert('Trennen fehlgeschlagen: ' + err.message);
    }
}

// Handle OAuth callback redirect (?connected=microsoft or ?oauth_error=...)
(function handleOAuthCallbackParams() {
    const params = new URLSearchParams(window.location.search);
    if (params.has('connected')) {
        alert('Outlook erfolgreich verbunden.');
        history.replaceState({}, '', window.location.pathname);
    } else if (params.has('oauth_error')) {
        alert('Outlook-Verbindung fehlgeschlagen: ' + params.get('oauth_error'));
        history.replaceState({}, '', window.location.pathname);
    }
})();

// Refresh status whenever the user logs in or the panel opens
document.getElementById('inboxStatusBtn').addEventListener('click', refreshInboxStatus);

// Run init on load
init();
if (authToken) refreshInboxStatus();
