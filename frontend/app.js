const API_BASE = "http://localhost:8000/api";
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
function showView(viewId) {
    [uploadView, loadingView, resultsView, dashboardView, loginView].forEach(v => {
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
        <tr class="border-b border-slate-100 hover:bg-slate-50/80 transition-colors group">
            <td class="px-5 py-3 text-slate-400 font-mono text-xs">${cv.id}</td>
            <td class="px-5 py-3">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 rounded-full bg-brand-light text-brand-blue flex items-center justify-center text-xs font-bold flex-shrink-0">${initials}</div>
                    <span class="font-semibold text-slate-800">${cv.name || 'Unknown'}</span>
                </div>
            </td>
            <td class="px-5 py-3 text-slate-500">${cv.email || '-'}</td>
            <td class="px-5 py-3"><span class="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs font-mono">${cv.filename || '-'}</span></td>
            <td class="px-5 py-3">
                <span class="px-2 py-1 bg-blue-50 text-blue-600 rounded-full text-xs font-bold border border-blue-100">
                    ${skillCount} Skills
                </span>
            </td>
            <td class="px-5 py-3">${renderStatusSelect(cv.id, cv.status || 'new')}</td>
            <td class="px-5 py-3 text-slate-500">${date}</td>
            <td class="px-5 py-3 text-right">
                <div class="flex items-center justify-end gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                    <button onclick="viewCV(${cv.id})" class="p-1.5 rounded hover:bg-brand-light text-slate-500 hover:text-brand-blue transition-colors"><i class="fa-solid fa-eye text-sm"></i></button>
                    <button onclick="downloadCVPPTX(${cv.id})" class="p-1.5 rounded hover:bg-green-50 text-slate-500 hover:text-green-600 transition-colors"><i class="fa-solid fa-file-powerpoint text-sm"></i></button>
                    <button onclick="deleteCV(${cv.id})" class="p-1.5 rounded hover:bg-red-50 text-slate-500 hover:text-red-500 transition-colors"><i class="fa-solid fa-trash-can text-sm"></i></button>
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

// Run init on load
init();
