const API_BASE = window.location.origin;
let autoRefreshInterval = null;
let durationUpdateInterval = null;
let selectedStatus = '';
let lastUpdatedAt = null;
let selectedRunId = null;
let cachedRuns = [];

document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    showKpiSkeletons();
    fetchHealth();
    loadProjects();
    loadRuns();
    startAutoRefresh();
    startDurationUpdater();
});

function setupEventListeners() {
    document.querySelectorAll('.nav a[data-tab]')?.forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            const tab = a.getAttribute('data-tab');
            setActiveTab(tab || 'runs');
        });
    });

    document.getElementById('run-form').addEventListener('submit', handleRunSubmit);
    document.getElementById('refresh-btn').addEventListener('click', loadRuns);

    document.getElementById('auto-refresh').addEventListener('change', (e) => {
        if (e.target.checked) { startAutoRefresh(); setRefreshChip(true); }
        else { stopAutoRefresh(); setRefreshChip(false); }
    });

    const statusSelect = document.getElementById('status-filter');
    if (statusSelect) {
        statusSelect.addEventListener('change', (e) => {
            selectedStatus = e.target.value || '';
            syncChipFilters();
            loadRuns();
        });
    }
    document.querySelectorAll('#status-chips .chip-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            selectedStatus = btn.getAttribute('data-status') || '';
            syncChipFilters();
            loadRuns();
        });
    });

    document.getElementById('all_projects').addEventListener('change', updateProjectSelectionVisibility);

    const selectAllBtn = document.getElementById('select-all-projects');
    const deselectAllBtn = document.getElementById('deselect-all-projects');
    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            document.querySelectorAll('#project-list input[type="checkbox"]').forEach(cb => cb.checked = true);
            updateSelectedCount();
        });
    }
    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            document.querySelectorAll('#project-list input[type="checkbox"]').forEach(cb => cb.checked = false);
            updateSelectedCount();
        });
    }

    document.getElementById('start-run-btn').addEventListener('click', () => {
        document.getElementById('trigger-section').scrollIntoView({ behavior: 'smooth' });
    });

    document.querySelectorAll('#run-details .tab-btn').forEach(btn => {
        btn.addEventListener('click', () => { switchPane(btn.getAttribute('data-pane')); });
    });

    // Setup exemptions UI event listeners
    setupExemptionsEventListeners();
}

function setActiveTab(tab) {
    document.querySelectorAll('.nav a[data-tab]').forEach(a => {
        a.classList.toggle('active', a.getAttribute('data-tab') === tab);
        if (a.getAttribute('data-tab') === tab) a.setAttribute('aria-current', 'page');
        else a.removeAttribute('aria-current');
    });
    const runs = document.getElementById('runs-section');
    const settings = document.getElementById('settings-section');

    if (tab === 'settings') {
        runs.style.display = 'none';
        settings.style.display = '';
        loadSettings();
    } else {
        runs.style.display = '';
        settings.style.display = 'none';
    }
}

function setRefreshChip(on) {
    const chip = document.getElementById('chip-refresh');
    chip.textContent = on ? 'Auto-refresh ON' : 'Auto-refresh OFF';
}

function syncChipFilters() {
    document.querySelectorAll('#status-chips .chip-filter').forEach(btn => {
        btn.classList.toggle('active', (btn.getAttribute('data-status') || '') === selectedStatus);
    });
    const sel = document.getElementById('status-filter');
    if (sel) sel.value = selectedStatus;
}

// Load projects from API and render multi-select checkboxes
async function loadProjects() {
    const list = document.getElementById('project-list');
    if (!list) return;
    list.innerHTML = '<div class="loading">Loading projects...</div>';
    try {
        const res = await fetch(`${API_BASE}/v1/projects`);
        if (!res.ok) throw new Error('Failed to fetch projects');
        const data = await res.json();
        const projects = Array.isArray(data.items) ? data.items : [];
        renderProjectsCheckboxes(projects);
        const search = document.getElementById('search-projects');
        if (search) {
            search.addEventListener('input', (e) => filterProjects(e.target.value || ''));
        }
    } catch (e) {
        list.innerHTML = '<div class="loading" style="color: var(--danger);">Failed to load projects</div>';
    }
}

function renderProjectsCheckboxes(projects) {
    const list = document.getElementById('project-list');
    if (!list) return;
    if (!projects || projects.length === 0) {
        list.innerHTML = '<div class="loading-projects">No projects found</div>';
        return;
    }
    const html = projects.map(p => {
        const safe = String(p);
        return `<label><input type="checkbox" value="${safe}"> ${safe}</label>`;
    }).join('');
    list.innerHTML = html;

    // Add change event listeners to update count
    list.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateSelectedCount);
    });

    updateSelectedCount();
}

function filterProjects(term) {
    const list = document.getElementById('project-list');
    if (!list) return;
    const t = (term || '').toLowerCase();
    list.querySelectorAll('label').forEach(lbl => {
        const txt = (lbl.textContent || '').toLowerCase();
        lbl.style.display = txt.includes(t) ? '' : 'none';
    });
}

function updateProjectSelectionVisibility() {
    const allProjectsChecked = document.getElementById('all_projects').checked;
    const container = document.getElementById('project-selection-container');
    if (container) {
        container.style.display = allProjectsChecked ? 'none' : 'block';
    }
}

function updateSelectedCount() {
    const selected = document.querySelectorAll('#project-list input[type="checkbox"]:checked').length;
    const countElement = document.getElementById('selected-count');
    if (countElement) {
        countElement.textContent = `${selected} selected`;
    }
}

async function fetchHealth() {
    try {
        const res = await fetch(`${API_BASE}/healthz`);
        const data = await res.json();
        const chip = document.getElementById('chip-db');
        if (res.ok && data && data.database === 'connected') {
            chip.textContent = 'DB: Connected';
            chip.classList.remove('warning','danger');
            chip.classList.add('success');
        } else if (data && data.database === 'not_configured') {
            chip.textContent = 'DB: Not Configured';
            chip.classList.remove('success','danger');
            chip.classList.add('warning');
        } else {
            chip.textContent = 'DB: Unhealthy';
            chip.classList.remove('success','warning');
            chip.classList.add('danger');
        }
    } catch (_) {
        const chip = document.getElementById('chip-db');
        chip.textContent = 'DB: Unknown';
        chip.classList.remove('success');
        chip.classList.add('warning');
    }
}

function startAutoRefresh() {
    if (autoRefreshInterval) return;
    autoRefreshInterval = setInterval(() => {
        loadRuns(false);
        updateLastUpdated();
    }, 10000);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) { clearInterval(autoRefreshInterval); autoRefreshInterval = null; }
}

function startDurationUpdater() {
    if (durationUpdateInterval) return;
    durationUpdateInterval = setInterval(() => {
        updateRunningDurations();
    }, 1000); // Update every second
}

function stopDurationUpdater() {
    if (durationUpdateInterval) { clearInterval(durationUpdateInterval); durationUpdateInterval = null; }
}

function updateRunningDurations() {
    // Update table rows
    const tableRows = document.querySelectorAll('#runs-table tbody tr[data-running="true"]');
    tableRows.forEach(row => {
        const runId = row.getAttribute('data-run-id');
        const run = cachedRuns.find(r => r.id === runId);
        if (run) {
            const durationCell = row.querySelector('.duration-cell');
            if (durationCell) {
                durationCell.textContent = calculateRunDuration(run);
            }
        }
    });

    // Update cards
    const cards = document.querySelectorAll('#runs-cards .run-card[data-running="true"]');
    cards.forEach(card => {
        const runId = card.getAttribute('data-run-id');
        const run = cachedRuns.find(r => r.id === runId);
        if (run) {
            const durationCell = card.querySelector('.duration-cell');
            if (durationCell) {
                durationCell.textContent = 'Duration: ' + calculateRunDuration(run);
            }
        }
    });
}

async function loadLeaderboard() {
    const cont = document.getElementById('leaderboard');
    cont.innerHTML = '<div class="loading">Loading leaderboard...</div>';
    try {
        let res = await fetch(`${API_BASE}/v1/leaderboard`);
        if (res.ok) {
            const data = await res.json();
            renderLeaderboard(data.items, 'db');
        } else {
            res = await fetch(`${API_BASE}/v1/leaderboard/files`);
            if (res.ok) {
                const data = await res.json();
                renderLeaderboard(data.items, 'files');
            } else {
                throw new Error('No leaderboard available');
            }
        }
        const projCont = document.getElementById('leaderboard-projects');
        projCont.innerHTML = '<div class="loading">Loading projects...</div>';
        const pres = await fetch(`${API_BASE}/v1/leaderboard/files/projects`);
        if (pres.ok) {
            const pdata = await pres.json();
            renderProjectsLeaderboard(pdata.items);
        } else {
            projCont.innerHTML = '<div class="loading">No projects leaderboard available</div>';
        }
    } catch (e) {
        cont.innerHTML = '<div class="loading" style="color: var(--danger);">Connect DB to enable leaderboard or run a collection to produce artifacts.</div>';
        const projCont = document.getElementById('leaderboard-projects');
        projCont.innerHTML = '<div class="loading">No projects leaderboard available</div>';
    }
}

function renderLeaderboard(items) {
    const cont = document.getElementById('leaderboard');
    if (!items || items.length === 0) { cont.innerHTML = '<div class="loading">No leaderboard data</div>'; return; }
    cont.innerHTML = items.slice(0, 12).map((it, idx) => {
        const rank = idx + 1;
        const name = escapeHtml(it.name || `Item ${rank}`);
        const score = typeof it.score === 'number' ? `${(it.score * 100).toFixed(1)}%` : '--';
        return `
            <div class="leaderboard-card">
                <div class="leaderboard-rank">#${rank}</div>
                <div class="leaderboard-name">${name}</div>
                <div class="leaderboard-score">Maturity: <span class="leaderboard-score-value">${score}</span></div>
            </div>`;
    }).join('');
}

function renderProjectsLeaderboard(items) {
    const cont = document.getElementById('leaderboard-projects');
    if (!items || items.length === 0) { cont.innerHTML = '<div class="loading">No projects</div>'; return; }
    cont.innerHTML = items.slice(0, 12).map((it, idx) => {
        const rank = idx + 1;
        const name = escapeHtml(it.name || `Project ${rank}`);
        const score = typeof it.score === 'number' ? `${(it.score * 100).toFixed(1)}%` : '--';
        return `
            <div class="leaderboard-card">
                <div class="leaderboard-rank">#${rank}</div>
                <div class="leaderboard-name">${name}</div>
                <div class="leaderboard-score">Maturity: <span class="leaderboard-score-value">${score}</span></div>
            </div>`;
    }).join('');
}

function updateLastUpdated() {
    if (!lastUpdatedAt) return;
    const diff = Math.floor((Date.now() - lastUpdatedAt) / 1000);
    const el = document.getElementById('last-updated');
    el.textContent = `Last updated: ${diff}s ago`;
}

function showConnecting(on) { document.getElementById('connect-bar').style.display = on ? 'block' : 'none'; }

function showRunsSkeleton() {
    const tbody = document.querySelector('#runs-table tbody');
    const rows = Array.from({ length: 4 }).map(() => (
        `<tr>
            <td><span class="skeleton" style="display:inline-block;width:90px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:160px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:120px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:120px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:90px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:72px;height:28px;"></span></td>
        </tr>`
    )).join('');
    tbody.innerHTML = rows;
    document.getElementById('runs-cards').innerHTML = '';
}

async function loadRuns(showSkeleton = true) {
    try {
        if (showSkeleton) showRunsSkeleton();
        showConnecting(true);
        const params = new URLSearchParams({ limit: 50 });
        if (selectedStatus) params.append('status', selectedStatus);
        const response = await fetch(`${API_BASE}/v1/runs?${params}`);
        if (!response.ok) throw new Error('Failed to load runs');
        let runs = await response.json();

        // Apply client-side filters
        runs = applyFilters(runs);

        cachedRuns = runs; // Cache runs for duration updater
        lastUpdatedAt = Date.now();
        updateLastUpdated();
        renderKpis(runs);
        renderRunsTable(runs);
        renderRunsCards(runs);
    } catch (error) {
        const tbody = document.querySelector('#runs-table tbody');
        tbody.innerHTML = `<tr><td colspan="7" class="loading" style="color: var(--danger);">Error: ${error.message}</td></tr>`;
        document.getElementById('runs-cards').innerHTML = '';
    } finally {
        showConnecting(false);
    }
}

function applyFilters(runs) {
    const searchTerm = document.getElementById('search-runs')?.value.toLowerCase() || '';
    const dateFrom = document.getElementById('filter-date-from')?.value || '';
    const dateTo = document.getElementById('filter-date-to')?.value || '';

    return runs.filter(run => {
        // Search filter
        if (searchTerm) {
            const label = (run.run_label || '').toLowerCase();
            if (!label.includes(searchTerm)) return false;
        }

        // Date filter
        if (dateFrom || dateTo) {
            const runDate = run.started_at ? new Date(run.started_at).toISOString().split('T')[0] : null;
            if (!runDate) return false;

            if (dateFrom && runDate < dateFrom) return false;
            if (dateTo && runDate > dateTo) return false;
        }

        return true;
    });
}

function showKpiSkeletons() {
    const kpiIds = ['kpi-total-runs', 'kpi-running', 'kpi-succeeded', 'kpi-avg-duration'];
    kpiIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.innerHTML = '<div class="skeleton skeleton-kpi"></div>';
        }
    });
}

function renderKpis(runs) {
    const total = runs.length;
    const running = runs.filter(r => (r.status || '').toLowerCase() === 'running').length;
    const now = Date.now();
    const succeeded24h = runs.filter(r => (r.status || '').toLowerCase() === 'succeeded' && r.finished_at)
        .filter(r => { try { return (now - new Date(r.finished_at).getTime()) <= 24*3600*1000; } catch { return false; } }).length;
    const durations = runs.map(r => { if (!r.started_at || !r.finished_at) return null; try { return (new Date(r.finished_at) - new Date(r.started_at)) / 1000; } catch { return null; } }).filter(x => typeof x === 'number');
    const avgSec = durations.length ? Math.round(durations.reduce((a,b)=>a+b,0)/durations.length) : null;
    document.getElementById('kpi-total-runs').textContent = String(total);
    document.getElementById('kpi-running').textContent = String(running);
    document.getElementById('kpi-succeeded').textContent = String(succeeded24h);
    document.getElementById('kpi-avg-duration').textContent = avgSec ? formatDuration(avgSec) : 'N/A';
}

function formatDuration(sec) { const m = Math.floor(sec/60); const s = Math.floor(sec%60); return `${m}m ${s}s`; }

function calculateRunDuration(run) {
    // If we have a pre-calculated duration, use it
    if (run.duration_seconds) {
        return formatDuration(run.duration_seconds);
    }

    // For running or queued tasks, calculate elapsed time from started_at
    const status = (run.status || '').toLowerCase();
    if ((status === 'running' || status === 'queued') && run.started_at) {
        try {
            const startTime = new Date(run.started_at);
            const now = new Date();
            const elapsedSeconds = Math.floor((now - startTime) / 1000);
            return formatDuration(elapsedSeconds);
        } catch (e) {
            return status === 'running' ? 'Running...' : 'Queued';
        }
    }

    // Default fallback
    if (status === 'running') return 'Running...';
    if (status === 'queued') return 'Queued';
    return '-';
}

function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text ?? ''; return div.innerHTML; }

function renderRunsTable(runs) {
    const tbody = document.querySelector('#runs-table tbody');
    if (!runs || runs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; padding: 3rem 1rem;">
                    <div style="font-size: 3rem; margin-bottom: 1rem;">📊</div>
                    <div style="font-size: 1.125rem; font-weight: 500; color: #333; margin-bottom: 0.5rem;">No runs yet</div>
                    <div style="color: #666; margin-bottom: 1rem;">Start your first metrics collection run above</div>
                    <button onclick="document.querySelector('#start-run-btn').scrollIntoView({behavior: 'smooth'}); document.querySelector('#start-run-btn').focus();"
                            style="background: var(--primary); color: white; padding: 0.5rem 1rem; border: none; border-radius: 4px; cursor: pointer; font-size: 0.875rem;">
                        Go to Trigger Form
                    </button>
                </td>
            </tr>
        `;
        return;
    }
    tbody.innerHTML = runs.map(run => {
        const duration = calculateRunDuration(run);
        const startedAt = run.started_at ? new Date(run.started_at).toLocaleString() : '-';
        const finishedAt = run.finished_at ? new Date(run.finished_at).toLocaleString() : '-';
        const escapedStatus = escapeHtml(run.status || 'unknown');
        const escapedLabel = run.run_label ? escapeHtml(run.run_label) : '<em>No label</em>';
        const escapedId = escapeHtml(run.id);
        const isRunning = (run.status || '').toLowerCase() === 'running' || (run.status || '').toLowerCase() === 'queued';
        return `
            <tr data-run-id="${escapedId}" ${isRunning ? 'data-running="true"' : ''}>
                <td><input type="checkbox" class="run-checkbox" value="${escapedId}" /></td>
                <td><span class="status-badge status-${escapeHtml(run.status || 'unknown')}">${escapedStatus}</span></td>
                <td>${escapedLabel}</td>
                <td>${startedAt}</td>
                <td>${finishedAt}</td>
                <td class="duration-cell">${duration}</td>
                <td>
                    ${isRunning ? `<button class="btn-cancel" data-run-id="${escapedId}" title="Cancel running task">Cancel</button>` : ''}
                    <button class="btn-download" data-run-id="${escapedId}" ${run.status !== 'succeeded' ? 'disabled' : ''} title="Download artifacts">Download</button>
                    <button class="btn-delete" data-run-id="${escapedId}" title="Delete from database" style="background: #d32f2f;">Delete</button>
                </td>
            </tr>`;
    }).join('');

    // Attach event listeners
    tbody.querySelectorAll('.btn-download').forEach(btn => {
        btn.addEventListener('click', (e) => { e.stopPropagation(); const runId = btn.getAttribute('data-run-id'); if (runId) downloadArtifacts(runId); });
    });
    tbody.querySelectorAll('.btn-cancel').forEach(btn => {
        btn.addEventListener('click', (e) => { e.stopPropagation(); const runId = btn.getAttribute('data-run-id'); if (runId) cancelRun(runId); });
    });
    tbody.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', (e) => { e.stopPropagation(); const runId = btn.getAttribute('data-run-id'); if (runId) deleteRun(runId); });
    });

    // Attach checkbox listeners
    tbody.querySelectorAll('.run-checkbox').forEach(cb => {
        cb.addEventListener('change', updateBulkActionsVisibility);
    });
    tbody.querySelectorAll('tr[data-run-id]').forEach(tr => { tr.addEventListener('click', () => { const rid = tr.getAttribute('data-run-id'); if (rid) openRunDetails(rid); }); });
}

function renderRunsCards(runs) {
    const cont = document.getElementById('runs-cards');
    cont.innerHTML = (runs || []).map(run => {
        const duration = calculateRunDuration(run);
        const startedAt = run.started_at ? new Date(run.started_at).toLocaleString() : '-';
        const finishedAt = run.finished_at ? new Date(run.finished_at).toLocaleString() : '-';
        const escapedStatus = escapeHtml(run.status || 'unknown');
        const escapedLabel = run.run_label ? escapeHtml(run.run_label) : 'No label';
        const escapedId = escapeHtml(run.id);
        const disabled = run.status !== 'succeeded' ? 'disabled' : '';
        const isRunning = (run.status || '').toLowerCase() === 'running' || (run.status || '').toLowerCase() === 'queued';
        return `
            <div class="run-card" data-run-id="${escapedId}" ${isRunning ? 'data-running="true"' : ''}>
                <div><span class="status-badge status-${escapeHtml(run.status || 'unknown')}">${escapedStatus}</span></div>
                <div class="meta">${escapedLabel}</div>
                <div class="meta">Started: ${startedAt}</div>
                <div class="meta">Finished: ${finishedAt}</div>
                <div class="meta duration-cell">Duration: ${duration}</div>
                <div><button class="btn-download" data-run-id="${escapedId}" ${disabled}>Download</button></div>
            </div>`;
    }).join('');
    cont.querySelectorAll('.btn-download').forEach(btn => { btn.addEventListener('click', (e) => { e.stopPropagation(); const runId = btn.getAttribute('data-run-id'); if (runId) downloadArtifacts(runId); }); });
    cont.querySelectorAll('.run-card').forEach(card => { card.addEventListener('click', () => { const rid = card.getAttribute('data-run-id'); if (rid) openRunDetails(rid); }); });
}

async function openRunDetails(runId) {
    selectedRunId = runId;
    const details = document.getElementById('run-details');
    details.style.display = '';
    document.querySelectorAll('#run-details .tab-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pane')==='summary'));
    document.getElementById('pane-summary').style.display='';
    document.getElementById('pane-artifacts').style.display='none';
    document.getElementById('pane-validation').style.display='none';
    document.getElementById('pane-summary').innerHTML = `<div><strong>Run ID:</strong> ${escapeHtml(runId)}</div><div style="color: var(--secondary);">Use tabs to view artifacts or validation.</div>`;
    try { await Promise.all([loadArtifacts(runId), loadValidation(runId)]); } catch {}
}

function switchPane(pane) {
    document.querySelectorAll('#run-details .tab-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pane')===pane));
    ['summary','artifacts','validation'].forEach(id => { document.getElementById(`pane-${id}`).style.display = (id===pane) ? '' : 'none'; });
}

async function loadArtifacts(runId) {
    const pane = document.getElementById('pane-artifacts');
    pane.innerHTML = '<div class="loading">Loading artifacts...</div>';
    try {
        const res = await fetch(`${API_BASE}/v1/runs/${runId}/artifacts`);
        if (!res.ok) throw new Error('Failed to load artifacts');
        const data = await res.json();
        const files = (data.files || []).map(f => {
            const name = escapeHtml(f.name || 'file');
            const url = `${API_BASE}/files/${name}`;
            return `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> <small>(${f.size_bytes || 0} bytes)</small></li>`;
        }).join('');
        pane.innerHTML = files ? `<ul>${files}</ul>` : '<div class="loading">No files</div>';
    } catch (e) {
        pane.innerHTML = `<div class="loading" style="color: var(--danger);">Error: ${e.message}</div>`;
    }
}

async function loadValidation(runId) {
    const pane = document.getElementById('pane-validation');
    pane.innerHTML = '<div class="loading">Loading validation...</div>';
    try {
        const res = await fetch(`${API_BASE}/v1/runs/${runId}/validate`);
        if (!res.ok) throw new Error('Failed to validate');
        const data = await res.json();
        const issues = (data.issues || []).map(i => {
            const key = escapeHtml(i.key || '');
            const diff = (i.diff != null) ? `${i.diff.toFixed ? i.diff.toFixed(2) : i.diff}%` : '-';
            return `<tr><td>${key}</td><td>${i.excel ?? '-'}</td><td>${i.db ?? '-'}</td><td>${diff}</td></tr>`;
        }).join('');
        pane.innerHTML = issues ? `<table><thead><tr><th>Key</th><th>Excel</th><th>DB</th><th>Diff%</th></tr></thead><tbody>${issues}</tbody></table>` : '<div class="loading">No issues</div>';
    } catch (e) {
        pane.innerHTML = `<div class="loading" style="color: var(--danger);">Error: ${e.message}</div>`;
    }
}

async function handleRunSubmit(e) {
    e.preventDefault();
    const profileElement = document.querySelector('input[name="profile"]:checked');
    if (!profileElement) { throw new Error('Please select a profile'); }
    const profile = profileElement.value;
    const baseConfig = {
        org: document.getElementById('org').value || null,
        minimal: profile === 'minimal',
        fast: profile === 'fast',
        full: profile === 'full',
        write_files: document.getElementById('write_files').checked,
        write_db: document.getElementById('write_db').checked,
        include_maturity: document.getElementById('include_maturity').checked,
        include_segments: document.getElementById('include_segments').checked,
        run_label: document.getElementById('run_label').value || null,
    };

    const allProjectsFlag = document.getElementById('all_projects').checked;
    const selected = Array.from(document.querySelectorAll('#project-list input[type="checkbox"]:checked')).map(el => el.value);
    const statusDiv = document.getElementById('trigger-status');
    const submitBtn = e.target.querySelector('button[type="submit"]');

    try {
        submitBtn.disabled = true;
        statusDiv.className = '';
        statusDiv.style.display = 'none';

        let messages = [];
        if (allProjectsFlag) {
            const config = { ...baseConfig, project: null, all_projects: true };
            const response = await fetch(`${API_BASE}/v1/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                let errorMsg = 'Failed to start run';
                if (typeof error.detail === 'string') {
                    errorMsg = error.detail;
                } else if (Array.isArray(error.detail)) {
                    errorMsg = error.detail.map(e => e.msg || JSON.stringify(e)).join(', ');
                }
                throw new Error(errorMsg);
            }
            const result = await response.json();
            messages.push(`All projects run started (ID: ${result.id})`);
        } else {
            if (selected.length === 0) {
                throw new Error('Select at least one project or choose "All Projects"');
            }
            for (const project of selected) {
                const config = { ...baseConfig, project: project, all_projects: false };
                const response = await fetch(`${API_BASE}/v1/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
                if (!response.ok) {
                    const error = await response.json().catch(() => ({}));
                    let errorMsg = `Failed to start run for ${project}`;
                    if (typeof error.detail === 'string') {
                        errorMsg = error.detail;
                    } else if (Array.isArray(error.detail)) {
                        errorMsg = error.detail.map(e => e.msg || JSON.stringify(e)).join(', ');
                    }
                    throw new Error(errorMsg);
                }
                const result = await response.json();
                messages.push(`${project}: ${result.id}`);
            }
        }

        statusDiv.className = 'success';
        statusDiv.textContent = `Started ${messages.length} run(s). ${messages.join(' | ')}`;
        statusDiv.style.display = 'block';
        setTimeout(loadRuns, 1000);

        // Reset form and re-check all_projects
        document.getElementById('run-form').reset();
        document.getElementById('all_projects').checked = true;
        updateProjectSelectionVisibility();

    } catch (error) {
        statusDiv.className = 'error';
        statusDiv.textContent = `Error: ${error.message}`;
        statusDiv.style.display = 'block';
    } finally {
        submitBtn.disabled = false;
    }
}

function downloadArtifacts(runId) { window.location.href = `${API_BASE}/v1/runs/${runId}/artifacts.zip`; }

// ========== Exemption Management (New Modal-Based UI) ==========

let EXEMPTIONS_DATA = {
    meta: { metrics: [], dimensions: [] },
    projects: [],
    exemptions: {},
    currentMode: 'add',  // 'add' or 'edit'
    editingProject: null
};

// Load exemptions table
async function loadExemptionsTable() {
    showExemptionsLoading(true);
    hideExemptionsError();

    try {
        const [metaRes, projRes, exRes] = await Promise.all([
            fetch(`${API_BASE}/v1/exemptions/meta`),
            fetch(`${API_BASE}/v1/projects`),
            fetch(`${API_BASE}/v1/exemptions`)
        ]);

        if (!metaRes.ok || !projRes.ok || !exRes.ok) {
            throw new Error('Failed to load exemption data');
        }

        const meta = await metaRes.json();
        const projs = await projRes.json();
        const ex = await exRes.json();

        EXEMPTIONS_DATA.meta = meta || { metrics: [], dimensions: [] };
        EXEMPTIONS_DATA.projects = Array.isArray(projs.items) ? projs.items : [];
        EXEMPTIONS_DATA.exemptions = (ex && ex.exemptions) || {};

        renderExemptionsTable();
    } catch (e) {
        showExemptionsError(`Error loading exemptions: ${e.message}`);
    } finally {
        showExemptionsLoading(false);
    }
}

// Render exemptions table
function renderExemptionsTable() {
    const tbody = document.getElementById('exemptions-tbody');
    const tableContainer = document.getElementById('exemptions-table-container');
    const emptyState = document.getElementById('exemptions-empty');

    const entries = Object.entries(EXEMPTIONS_DATA.exemptions || {});

    if (entries.length === 0) {
        tableContainer.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    tableContainer.style.display = 'block';
    emptyState.style.display = 'none';

    tbody.innerHTML = entries.map(([proj, rule]) => {
        const metrics = (rule.exempt_metrics || []);
        const dimensions = (rule.exempt_dimensions || []);
        const metricsHtml = metrics.length > 0
            ? `<div class="exemption-list">${metrics.map(m => `<span class="exemption-badge">${escapeHtml(m)}</span>`).join('')}</div>`
            : '<span style="color: var(--text-tertiary);">-</span>';
        const dimensionsHtml = dimensions.length > 0
            ? `<div class="exemption-list">${dimensions.map(d => `<span class="exemption-badge">${escapeHtml(d)}</span>`).join('')}</div>`
            : '<span style="color: var(--text-tertiary);">-</span>';

        const effectiveDate = rule.effective_date ? new Date(rule.effective_date).toLocaleDateString() : '-';
        const expirationDate = rule.expiration_date ? new Date(rule.expiration_date).toLocaleDateString() : '-';

        return `
            <tr>
                <td><strong>${escapeHtml(proj)}</strong></td>
                <td>${metricsHtml}</td>
                <td>${dimensionsHtml}</td>
                <td>${escapeHtml(rule.owner || '-')}</td>
                <td>${escapeHtml(rule.reason || '-')}</td>
                <td>${effectiveDate}</td>
                <td>${expirationDate}</td>
                <td>
                    <div class="action-buttons">
                        <button class="btn-edit" data-project="${escapeHtml(proj)}">Edit</button>
                        <button class="btn-delete" data-project="${escapeHtml(proj)}">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    // Attach non-inline handlers for actions
    tbody.querySelectorAll('.btn-edit').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const p = btn.getAttribute('data-project');
            if (p) editExemption(p);
        });
    });
    tbody.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const p = btn.getAttribute('data-project');
            if (p) confirmDeleteExemption(p);
        });
    });
}

// Show/hide loading state
function showExemptionsLoading(show) {
    document.getElementById('exemptions-loading').style.display = show ? 'flex' : 'none';
}

// Show/hide error state
function showExemptionsError(message) {
    const errorEl = document.getElementById('exemptions-error');
    errorEl.textContent = message;
    errorEl.style.display = 'block';
}

function hideExemptionsError() {
    document.getElementById('exemptions-error').style.display = 'none';
}

// Open modal
function openExemptionModal(mode = 'add', projectName = null) {
    EXEMPTIONS_DATA.currentMode = mode;
    EXEMPTIONS_DATA.editingProject = projectName;

    const modal = document.getElementById('exemption-modal');
    const title = document.getElementById('modal-title');

    title.textContent = mode === 'edit' ? `Edit Exemption: ${projectName}` : 'Add New Exemption';

    // Reset form
    resetExemptionForm();

    // Load modal data
    loadModalData();

    // If editing, pre-populate form
    if (mode === 'edit' && projectName) {
        populateEditForm(projectName);
    }

    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

// Close modal
function closeExemptionModal() {
    const modal = document.getElementById('exemption-modal');
    modal.style.display = 'none';
    document.body.style.overflow = '';
    resetExemptionForm();
}

// Reset form
function resetExemptionForm() {
    document.getElementById('exemption-form').reset();
    clearFieldErrors();
    document.getElementById('modal-status').className = 'modal-status';
    document.getElementById('modal-status').style.display = 'none';

    // Set default effective date to today
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('modal-effective').value = today;
}

// Load modal data (projects, metrics, dimensions)
function loadModalData() {
    // Projects
    const projectList = document.getElementById('modal-project-list');
    projectList.innerHTML = EXEMPTIONS_DATA.projects.map(p => {
        const id = `modal_proj_${btoa(p).replace(/=/g, '')}`;
        const disabled = EXEMPTIONS_DATA.currentMode === 'edit' ? 'disabled' : '';
        const checked = (EXEMPTIONS_DATA.currentMode === 'edit' && p === EXEMPTIONS_DATA.editingProject) ? 'checked' : '';
        return `<label><input type="checkbox" value="${escapeHtml(p)}" id="${id}" ${disabled} ${checked}> ${escapeHtml(p)}</label>`;
    }).join('');

    // Metrics
    const metricsList = document.getElementById('modal-metrics');
    const metrics = Array.isArray(EXEMPTIONS_DATA.meta?.metrics) ? EXEMPTIONS_DATA.meta.metrics : [];
    metricsList.innerHTML = metrics.map(m => {
        const id = `modal_metric_${btoa(m).replace(/=/g, '')}`;
        return `<label><input type="checkbox" value="${escapeHtml(m)}" id="${id}"> ${escapeHtml(m)}</label>`;
    }).join('');

    // Dimensions
    const dimensionsList = document.getElementById('modal-dimensions');
    const dimensions = Array.isArray(EXEMPTIONS_DATA.meta?.dimensions) ? EXEMPTIONS_DATA.meta.dimensions : [];
    dimensionsList.innerHTML = dimensions.map(d => {
        const id = `modal_dim_${btoa(d).replace(/=/g, '')}`;
        return `<label><input type="checkbox" value="${escapeHtml(d)}" id="${id}"> ${escapeHtml(d)}</label>`;
    }).join('');

    // Setup search filters
    setupSearchFilters();
}

// Setup search filters
function setupSearchFilters() {
    const searchProjects = document.getElementById('search-projects');
    searchProjects.addEventListener('input', (e) => filterItems('modal-project-list', e.target.value));

    const searchMetrics = document.getElementById('search-metrics');
    searchMetrics.addEventListener('input', (e) => filterItems('modal-metrics', e.target.value));

    const searchDimensions = document.getElementById('search-dimensions');
    searchDimensions.addEventListener('input', (e) => filterItems('modal-dimensions', e.target.value));
}

// Filter items based on search
function filterItems(containerId, searchTerm) {
    const container = document.getElementById(containerId);
    const labels = container.querySelectorAll('label');
    const term = searchTerm.toLowerCase();

    labels.forEach(label => {
        const text = label.textContent.toLowerCase();
        label.style.display = text.includes(term) ? '' : 'none';
    });
}

// Populate form for editing
function populateEditForm(projectName) {
    const rule = EXEMPTIONS_DATA.exemptions[projectName];
    if (!rule) return;

    // Check metrics
    (rule.exempt_metrics || []).forEach(m => {
        const el = document.querySelector(`#modal-metrics input[value="${CSS.escape(m)}"]`);
        if (el) el.checked = true;
    });

    // Check dimensions
    (rule.exempt_dimensions || []).forEach(d => {
        const el = document.querySelector(`#modal-dimensions input[value="${CSS.escape(d)}"]`);
        if (el) el.checked = true;
    });

    // Fill text fields
    document.getElementById('modal-owner').value = rule.owner || '';
    document.getElementById('modal-reason').value = rule.reason || '';
    document.getElementById('modal-effective').value = rule.effective_date ? String(rule.effective_date).slice(0, 10) : '';
    document.getElementById('modal-expiration').value = rule.expiration_date ? String(rule.expiration_date).slice(0, 10) : '';
}

// Validate form
function validateExemptionForm() {
    clearFieldErrors();
    let isValid = true;

    // Check projects
    const selectedProjects = Array.from(document.querySelectorAll('#modal-project-list input[type="checkbox"]:checked'));
    if (selectedProjects.length === 0) {
        showFieldError('error-projects', 'Please select at least one project');
        isValid = false;
    }

    // Check owner
    const owner = document.getElementById('modal-owner').value.trim();
    if (!owner) {
        showFieldError('error-owner', 'Owner is required');
        isValid = false;
    }

    // Check reason
    const reason = document.getElementById('modal-reason').value.trim();
    if (!reason) {
        showFieldError('error-reason', 'Reason is required');
        isValid = false;
    }

    // Check effective date
    const effective = document.getElementById('modal-effective').value;
    if (!effective) {
        showFieldError('error-effective', 'Effective date is required');
        isValid = false;
    }

    // Check date logic
    const expiration = document.getElementById('modal-expiration').value;
    if (effective && expiration && new Date(expiration) <= new Date(effective)) {
        showFieldError('error-expiration', 'Expiration date must be after effective date');
        isValid = false;
    }

    return isValid;
}

// Show field error
function showFieldError(fieldId, message) {
    const errorEl = document.getElementById(fieldId);
    errorEl.textContent = message;
    errorEl.classList.add('show');
}

// Clear field errors
function clearFieldErrors() {
    document.querySelectorAll('.form-error').forEach(el => {
        el.classList.remove('show');
        el.textContent = '';
    });
}

// Save exemption
async function saveExemption() {
    if (!validateExemptionForm()) return;

    const btn = document.getElementById('btn-save-exemption');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    const selectedProjects = Array.from(document.querySelectorAll('#modal-project-list input[type="checkbox"]:checked')).map(i => i.value);
    const selectedMetrics = Array.from(document.querySelectorAll('#modal-metrics input[type="checkbox"]:checked')).map(i => i.value);
    const selectedDimensions = Array.from(document.querySelectorAll('#modal-dimensions input[type="checkbox"]:checked')).map(i => i.value);

    const body = {
        exempt_metrics: selectedMetrics,
        exempt_dimensions: selectedDimensions,
        reason: document.getElementById('modal-reason').value.trim(),
        owner: document.getElementById('modal-owner').value.trim(),
        effective_date: document.getElementById('modal-effective').value || null,
        expiration_date: document.getElementById('modal-expiration').value || null,
    };

    try {
        const promises = selectedProjects.map(p =>
            fetch(`${API_BASE}/v1/exemptions/${encodeURIComponent(p)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            })
        );

        await Promise.all(promises);

        showToast(`Successfully saved exemption(s) for ${selectedProjects.length} project(s)`, 'success');
        closeExemptionModal();
        loadExemptionsTable();
    } catch (e) {
        const status = document.getElementById('modal-status');
        status.className = 'modal-status error';
        status.textContent = `Save failed: ${e.message}`;
        status.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save Exemption';
    }
}

// Edit exemption
function editExemption(projectName) {
    openExemptionModal('edit', projectName);
}

// Confirm delete
function confirmDeleteExemption(projectName) {
    const message = `Are you sure you want to delete the exemption for "${projectName}"?`;
    showConfirmDialog(message, () => deleteExemption(projectName));
}

// Delete exemption
async function deleteExemption(projectName) {
    try {
        const res = await fetch(`${API_BASE}/v1/exemptions/${encodeURIComponent(projectName)}`, {
            method: 'DELETE'
        });

        if (!res.ok) {
            throw new Error('Failed to delete exemption');
        }

        showToast(`Successfully deleted exemption for ${projectName}`, 'success');
        loadExemptionsTable();
    } catch (e) {
        showToast(`Delete failed: ${e.message}`, 'error');
    }
}

// Show toast notification
function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 4000);
}

// Show confirm dialog
function showConfirmDialog(message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.display = 'block';
    overlay.style.zIndex = '2000';

    const dialog = document.createElement('div');
    dialog.className = 'confirm-dialog';

    const title = document.createElement('h4');
    title.textContent = 'Confirm Action';

    const para = document.createElement('p');
    para.innerHTML = escapeHtml(message);

    const actions = document.createElement('div');
    actions.className = 'confirm-dialog-actions';

    const btnCancel = document.createElement('button');
    btnCancel.className = 'btn-secondary';
    btnCancel.textContent = 'Cancel';

    const btnYes = document.createElement('button');
    btnYes.className = 'btn-delete';
    btnYes.textContent = 'Confirm';
    btnYes.id = 'confirm-yes';

    actions.appendChild(btnCancel);
    actions.appendChild(btnYes);

    dialog.appendChild(title);
    dialog.appendChild(para);
    dialog.appendChild(actions);

    document.body.appendChild(overlay);
    document.body.appendChild(dialog);

    const close = () => { try { dialog.remove(); overlay.remove(); } catch (_) {} };
    btnCancel.addEventListener('click', close);
    btnYes.addEventListener('click', () => { close(); try { onConfirm(); } catch (_) {} });
}

// Settings tab logic (replaces old loadSettings)
async function loadSettings() {
    await loadExemptionsTable();
}

// Setup event listeners for exemptions UI
function setupExemptionsEventListeners() {
    document.getElementById('btn-add-exemption').addEventListener('click', () => openExemptionModal('add'));
    document.getElementById('btn-create-first').addEventListener('click', () => openExemptionModal('add'));
    document.getElementById('btn-reload-exemptions').addEventListener('click', loadExemptionsTable);

    document.getElementById('btn-close-modal').addEventListener('click', closeExemptionModal);
    document.getElementById('btn-cancel').addEventListener('click', closeExemptionModal);
    document.getElementById('btn-save-exemption').addEventListener('click', saveExemption);

    // Close modal on overlay click
    document.querySelector('#exemption-modal .modal-overlay').addEventListener('click', closeExemptionModal);

    // Prevent modal content click from closing
    document.querySelector('#exemption-modal .modal-content').addEventListener('click', (e) => e.stopPropagation());

    // Database management
    document.getElementById('btn-clear-database').addEventListener('click', clearDatabase);
}

// Database management functions
async function clearDatabase() {
    const confirmed = confirm(
        '⚠️ WARNING: This will permanently delete ALL runs, metrics, and raw data from the database.\n\n' +
        'This action cannot be undone!\n\n' +
        'Are you sure you want to continue?'
    );

    if (!confirmed) return;

    const doubleConfirm = confirm(
        'Are you ABSOLUTELY sure? This will delete everything!\n\n' +
        'Type OK in your mind and click OK to proceed, or Cancel to abort.'
    );

    if (!doubleConfirm) return;

    const statusDiv = document.getElementById('database-status');
    statusDiv.innerHTML = '<p style="color: #ff9800;">⏳ Clearing database...</p>';

    try {
        const response = await fetch(`${API_BASE}/v1/database/clear`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to clear database');
        }

        const result = await response.json();
        statusDiv.innerHTML = `
            <p style="color: #4caf50;">✓ Database cleared successfully!</p>
            <p style="color: #666; font-size: 0.875rem;">
                Deleted: ${result.deleted.runs} runs, ${result.deleted.metrics} metrics, ${result.deleted.raw_metrics} raw metrics
            </p>
        `;

        // Refresh the runs table
        setTimeout(() => {
            loadRuns();
            statusDiv.innerHTML = '';
        }, 3000);
    } catch (error) {
        statusDiv.innerHTML = `<p style="color: #f44336;">❌ Error: ${error.message}</p>`;
    }
}

async function deleteRun(runId) {
    const confirmed = confirm('Are you sure you want to delete this run and all its data from the database?');
    if (!confirmed) return;

    try {
        const response = await fetch(`${API_BASE}/v1/runs/${runId}/data`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to delete run');
        }

        const result = await response.json();
        console.log('Run deleted:', result);

        // Refresh the runs table
        await loadRuns();
    } catch (error) {
        alert(`Error deleting run: ${error.message}`);
    }
}

async function cancelRun(runId) {
    const confirmed = confirm('Are you sure you want to cancel this running task?');
    if (!confirmed) return;

    try {
        const response = await fetch(`${API_BASE}/v1/runs/${runId}/cancel`, { method: 'POST' });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to cancel run');
        }

        const result = await response.json();
        console.log('Run cancelled:', result);

        // Refresh the runs table
        await loadRuns();
    } catch (error) {
        alert(`Error cancelling run: ${error.message}`);
    }
}

// Status select (fallback) and chips
// setupStatusFilters removed (redundant with setupEventListeners)

function setActiveTab(tab) {
    document.querySelectorAll('.nav a[data-tab]').forEach(a => {
        a.classList.toggle('active', a.getAttribute('data-tab') === tab);
        if (a.getAttribute('data-tab') === tab) a.setAttribute('aria-current', 'page');
        else a.removeAttribute('aria-current');
    });
    const runs = document.getElementById('runs-section');
    const settings = document.getElementById('settings-section');

    if (tab === 'settings') {
        runs.style.display = 'none';
        settings.style.display = '';
        loadSettings();
    } else {
        runs.style.display = '';
        settings.style.display = 'none';
    }
}

function setRefreshChip(on) {
    const chip = document.getElementById('chip-refresh');
    chip.textContent = on ? 'Auto-refresh ON' : 'Auto-refresh OFF';
}

function syncChipFilters() {
    // Update chips
    document.querySelectorAll('#status-chips .chip-filter').forEach(btn => {
        btn.classList.toggle('active', (btn.getAttribute('data-status') || '') === selectedStatus);
    });
    // Update select (fallback)
    const sel = document.getElementById('status-filter');
    if (sel) sel.value = selectedStatus;
}

async function fetchHealth() {
    try {
        const res = await fetch(`${API_BASE}/healthz`);
        const data = await res.json();
        const chip = document.getElementById('chip-db');
        if (res.ok && data && data.database === 'connected') {
            chip.textContent = 'DB: Connected';
            chip.classList.remove('warning','danger');
            chip.classList.add('success');
        } else if (data && data.database === 'not_configured') {
            chip.textContent = 'DB: Not Configured';
            chip.classList.remove('success','danger');
            chip.classList.add('warning');
        } else {
            chip.textContent = 'DB: Unhealthy';
            chip.classList.remove('success','warning');
            chip.classList.add('danger');
        }
    } catch (_) {
        const chip = document.getElementById('chip-db');
        chip.textContent = 'DB: Unknown';
        chip.classList.remove('success');
        chip.classList.add('warning');
    }
}

function startAutoRefresh() {
    if (autoRefreshInterval) return;
    autoRefreshInterval = setInterval(() => {
        loadRuns(false);
        updateLastUpdated();
    }, 10000);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

async function loadLeaderboard() {
    const cont = document.getElementById('leaderboard');
    cont.innerHTML = '<div class="loading">Loading leaderboard...</div>';
    try {
        // Try DB-backed leaderboard first
        let res = await fetch(`${API_BASE}/v1/leaderboard`);
        if (res.ok) {
            const data = await res.json();
            renderLeaderboard(data.items, 'db');
        } else {
            // Fallback to files for segments
            res = await fetch(`${API_BASE}/v1/leaderboard/files`);
            if (res.ok) {
                const data = await res.json();
                renderLeaderboard(data.items, 'files');
            } else {
                throw new Error('No leaderboard available');
            }
        }
        // Projects leaderboard (files-based)
        const projCont = document.getElementById('leaderboard-projects');
        projCont.innerHTML = '<div class="loading">Loading projects...</div>';
        const pres = await fetch(`${API_BASE}/v1/leaderboard/files/projects`);
        if (pres.ok) {
            const pdata = await pres.json();
            renderProjectsLeaderboard(pdata.items);
        } else {
            projCont.innerHTML = '<div class="loading">No projects leaderboard available</div>';
        }
    } catch (e) {
        cont.innerHTML = '<div class="loading" style="color: var(--danger);">Connect DB to enable leaderboard or run a collection to produce artifacts.</div>';
        const projCont = document.getElementById('leaderboard-projects');
        projCont.innerHTML = '<div class="loading">No projects leaderboard available</div>';
    }
}

function renderLeaderboard(items, source) {
    const cont = document.getElementById('leaderboard');
    if (!items || items.length === 0) {
        cont.innerHTML = '<div class="loading">No leaderboard data</div>';
        return;
    }
    cont.innerHTML = items.slice(0, 12).map((it, idx) => {
        const rank = idx + 1;
        const name = escapeHtml(it.name || `Item ${rank}`);
        const score = typeof it.score === 'number' ? `${(it.score * 100).toFixed(1)}%` : '--';
        return `
            <div class="leaderboard-card">
                <div class="leaderboard-rank">#${rank}</div>
                <div class="leaderboard-name">${name}</div>
                <div class="leaderboard-score">Maturity: <span class="leaderboard-score-value">${score}</span></div>
            </div>`;
    }).join('');
}

function renderProjectsLeaderboard(items) {
    const cont = document.getElementById('leaderboard-projects');
    if (!items || items.length === 0) {
        cont.innerHTML = '<div class="loading">No projects</div>';
        return;
    }
    cont.innerHTML = items.slice(0, 12).map((it, idx) => {
        const rank = idx + 1;
        const name = escapeHtml(it.name || `Project ${rank}`);
        const score = typeof it.score === 'number' ? `${(it.score * 100).toFixed(1)}%` : '--';
        return `
            <div class="leaderboard-card">
                <div class="leaderboard-rank">#${rank}</div>
                <div class="leaderboard-name">${name}</div>
                <div class="leaderboard-score">Maturity: <span class="leaderboard-score-value">${score}</span></div>
            </div>`;
    }).join('');
}

function updateLastUpdated() {
    if (!lastUpdatedAt) return;
    const diff = Math.floor((Date.now() - lastUpdatedAt) / 1000);
    const el = document.getElementById('last-updated');
    el.textContent = `Last updated: ${diff}s ago`;
}

function showConnecting(on) {
    const bar = document.getElementById('connect-bar');
    bar.style.display = on ? 'block' : 'none';
}

function showRunsSkeleton() {
    const tbody = document.querySelector('#runs-table tbody');
    const rows = Array.from({ length: 4 }).map(() => (
        `<tr>
            <td><span class="skeleton" style="display:inline-block;width:90px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:160px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:120px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:120px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:90px;height:16px;"></span></td>
            <td><span class="skeleton" style="display:inline-block;width:72px;height:28px;"></span></td>
        </tr>`
    )).join('');
    tbody.innerHTML = rows;
    document.getElementById('runs-cards').innerHTML = '';
}

async function loadRuns(showSkeleton = true) {
    try {
        if (showSkeleton) showRunsSkeleton();
        showConnecting(true);
        const params = new URLSearchParams({ limit: 50 });
        if (selectedStatus) params.append('status', selectedStatus);
        const response = await fetch(`${API_BASE}/v1/runs?${params}`);
        if (!response.ok) throw new Error('Failed to load runs');
        let runs = await response.json();

        // Apply client-side filters
        runs = applyFilters(runs);

        cachedRuns = runs; // Cache runs for duration updater
        lastUpdatedAt = Date.now();
        updateLastUpdated();
        renderKpis(runs);
        renderRunsTable(runs);
        renderRunsCards(runs);
    } catch (error) {
        const tbody = document.querySelector('#runs-table tbody');
        tbody.innerHTML = `<tr><td colspan="7" class="loading" style="color: var(--danger);">Error: ${error.message}</td></tr>`;
        document.getElementById('runs-cards').innerHTML = '';
    } finally {
        showConnecting(false);
    }
}

function applyFilters(runs) {
    const searchTerm = document.getElementById('search-runs')?.value.toLowerCase() || '';
    const dateFrom = document.getElementById('filter-date-from')?.value || '';
    const dateTo = document.getElementById('filter-date-to')?.value || '';

    return runs.filter(run => {
        // Search filter
        if (searchTerm) {
            const label = (run.run_label || '').toLowerCase();
            if (!label.includes(searchTerm)) return false;
        }

        // Date filter
        if (dateFrom || dateTo) {
            const runDate = run.started_at ? new Date(run.started_at).toISOString().split('T')[0] : null;
            if (!runDate) return false;

            if (dateFrom && runDate < dateFrom) return false;
            if (dateTo && runDate > dateTo) return false;
        }

        return true;
    });
}

function renderKpis(runs) {
    const total = runs.length;
    const running = runs.filter(r => (r.status || '').toLowerCase() === 'running').length;
    const now = Date.now();
    const succeeded24h = runs.filter(r => (r.status || '').toLowerCase() === 'succeeded' && r.finished_at)
        .filter(r => {
            try { return (now - new Date(r.finished_at).getTime()) <= 24*3600*1000; } catch { return false; }
        }).length;
    const durations = runs.map(r => {
        if (!r.started_at || !r.finished_at) return null;
        try { return (new Date(r.finished_at) - new Date(r.started_at)) / 1000; } catch { return null; }
    }).filter(x => typeof x === 'number');
    const avgSec = durations.length ? Math.round(durations.reduce((a,b)=>a+b,0)/durations.length) : null;

    document.getElementById('kpi-total-runs').textContent = String(total);
    document.getElementById('kpi-running').textContent = String(running);
    document.getElementById('kpi-succeeded').textContent = String(succeeded24h);
    document.getElementById('kpi-avg-duration').textContent = avgSec ? formatDuration(avgSec) : 'N/A';
}

function formatDuration(sec) {
    const m = Math.floor(sec/60); const s = Math.floor(sec%60);
    return `${m}m ${s}s`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
}

function renderRunsTable(runs) {
    const tbody = document.querySelector('#runs-table tbody');
    if (!runs || runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No runs found</td></tr>';
        return;
    }
    tbody.innerHTML = runs.map(run => {
        const duration = run.duration_seconds
            ? `${Math.floor(run.duration_seconds / 60)}m ${Math.floor(run.duration_seconds % 60)}s`
            : ((run.status || '').toLowerCase() === 'running' ? 'Running...' : '-');
        const startedAt = run.started_at ? new Date(run.started_at).toLocaleString() : '-';
        const finishedAt = run.finished_at ? new Date(run.finished_at).toLocaleString() : '-';
        const escapedStatus = escapeHtml(run.status || 'unknown');
        const escapedLabel = run.run_label ? escapeHtml(run.run_label) : '<em>No label</em>';
        const escapedId = escapeHtml(run.id);
        return `
            <tr data-run-id="${escapedId}">
                <td><span class="status-badge status-${escapeHtml(run.status || 'unknown')}">${escapedStatus}</span></td>
                <td>${escapedLabel}</td>
                <td>${startedAt}</td>
                <td>${finishedAt}</td>
                <td>${duration}</td>
                <td>
                    <button class="btn-download" data-run-id="${escapedId}"
                        ${run.status !== 'succeeded' ? 'disabled' : ''}>
                        Download
                    </button>
                </td>
            </tr>`;
    }).join('');

    // Attach handlers
    tbody.querySelectorAll('.btn-download').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const runId = btn.getAttribute('data-run-id');
            if (runId) downloadArtifacts(runId);
        });
    });
    tbody.querySelectorAll('tr[data-run-id]').forEach(tr => {
        tr.addEventListener('click', () => {
            const rid = tr.getAttribute('data-run-id');
            if (rid) openRunDetails(rid);
        });
    });
}

function renderRunsCards(runs) {
    const cont = document.getElementById('runs-cards');
    cont.innerHTML = (runs || []).map(run => {
        const duration = run.duration_seconds
            ? `${Math.floor(run.duration_seconds / 60)}m ${Math.floor(run.duration_seconds % 60)}s`
            : ((run.status || '').toLowerCase() === 'running' ? 'Running...' : '-');
        const startedAt = run.started_at ? new Date(run.started_at).toLocaleString() : '-';
        const finishedAt = run.finished_at ? new Date(run.finished_at).toLocaleString() : '-';
        const escapedStatus = escapeHtml(run.status || 'unknown');
        const escapedLabel = run.run_label ? escapeHtml(run.run_label) : 'No label';
        const escapedId = escapeHtml(run.id);
        const disabled = run.status !== 'succeeded' ? 'disabled' : '';
        return `
            <div class="run-card" data-run-id="${escapedId}">
                <div><span class="status-badge status-${escapeHtml(run.status || 'unknown')}">${escapedStatus}</span></div>
                <div class="meta">${escapedLabel}</div>
                <div class="meta">Started: ${startedAt}</div>
                <div class="meta">Finished: ${finishedAt}</div>
                <div class="meta">Duration: ${duration}</div>
                <div>
                    <button class="btn-download" data-run-id="${escapedId}" ${disabled}>Download</button>
                </div>
            </div>`;
    }).join('');
    cont.querySelectorAll('.btn-download').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const runId = btn.getAttribute('data-run-id');
            if (runId) downloadArtifacts(runId);
        });
    });
    cont.querySelectorAll('.run-card').forEach(card => {
        card.addEventListener('click', () => {
            const rid = card.getAttribute('data-run-id');
            if (rid) openRunDetails(rid);
        });
    });
}

async function openRunDetails(runId) {
    selectedRunId = runId;
    const details = document.getElementById('run-details');
    details.style.display = '';
    // Activate summary tab
    document.querySelectorAll('#run-details .tab-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pane')==='summary'));
    document.getElementById('pane-summary').style.display='';
    document.getElementById('pane-artifacts').style.display='none';
    document.getElementById('pane-validation').style.display='none';

    // Render summary
    document.getElementById('pane-summary').innerHTML = `
        <div><strong>Run ID:</strong> ${escapeHtml(runId)}</div>
        <div style="color: var(--secondary);">Use tabs to view artifacts or validation.</div>
    `;

    // Preload artifacts and validation (best-effort)
    try { await Promise.all([loadArtifacts(runId), loadValidation(runId)]); } catch {}
}

function switchPane(pane) {
    document.querySelectorAll('#run-details .tab-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pane')===pane));
    ['summary','artifacts','validation'].forEach(id => {
        document.getElementById(`pane-${id}`).style.display = (id===pane) ? '' : 'none';
    });
}

async function loadArtifacts(runId) {
    const pane = document.getElementById('pane-artifacts');
    pane.innerHTML = '<div class="loading">Loading artifacts...</div>';
    try {
        const res = await fetch(`${API_BASE}/v1/runs/${runId}/artifacts`);
        if (!res.ok) throw new Error('Failed to load artifacts');
        const data = await res.json();
        const files = (data.files || []).map(f => {
            const name = escapeHtml(f.name || 'file');
            const url = `${API_BASE}/files/${name}`;
            return `<li><a href="${url}" target="_blank" rel="noopener">${name}</a> <small>(${f.size_bytes || 0} bytes)</small></li>`;
        }).join('');
        pane.innerHTML = files ? `<ul>${files}</ul>` : '<div class="loading">No files</div>';
    } catch (e) {
        pane.innerHTML = `<div class="loading" style="color: var(--danger);">Error: ${e.message}</div>`;
    }
}

async function loadValidation(runId) {
    const pane = document.getElementById('pane-validation');
    pane.innerHTML = '<div class="loading">Loading validation...</div>';
    try {
        const res = await fetch(`${API_BASE}/v1/runs/${runId}/validate`);
        if (!res.ok) throw new Error('Failed to validate');
        const data = await res.json();
        const issues = (data.issues || []).map(i => {
            const key = escapeHtml(i.key || '');
            const diff = (i.diff != null) ? `${i.diff.toFixed ? i.diff.toFixed(2) : i.diff}%` : '-';
            return `<tr><td>${key}</td><td>${i.excel ?? '-'}</td><td>${i.db ?? '-'}</td><td>${diff}</td></tr>`;
        }).join('');
        pane.innerHTML = issues
            ? `<table><thead><tr><th>Key</th><th>Excel</th><th>DB</th><th>Diff%</th></tr></thead><tbody>${issues}</tbody></table>`
            : '<div class="loading">No issues</div>';
    } catch (e) {
        pane.innerHTML = `<div class="loading" style="color: var(--danger);">Error: ${e.message}</div>`;
    }
}

// Advanced options toggle
document.getElementById('toggle-advanced')?.addEventListener('click', function() {
    const advancedOptions = document.getElementById('advanced-options');
    const toggleIcon = this.querySelector('.toggle-icon');

    if (advancedOptions.style.display === 'none') {
        advancedOptions.style.display = 'block';
        toggleIcon.classList.add('expanded');
    } else {
        advancedOptions.style.display = 'none';
        toggleIcon.classList.remove('expanded');
    }
});

// Bulk actions
function updateBulkActionsVisibility() {
    const checkboxes = document.querySelectorAll('.run-checkbox:checked');
    const bulkActions = document.getElementById('bulk-actions');
    const count = document.getElementById('selected-runs-count');

    if (checkboxes.length > 0) {
        bulkActions.style.display = 'flex';
        count.textContent = `${checkboxes.length} selected`;
    } else {
        bulkActions.style.display = 'none';
    }
}

document.getElementById('select-all-runs')?.addEventListener('change', function() {
    const checkboxes = document.querySelectorAll('.run-checkbox');
    checkboxes.forEach(cb => cb.checked = this.checked);
    updateBulkActionsVisibility();
});

document.getElementById('bulk-delete')?.addEventListener('click', async function() {
    const checkboxes = document.querySelectorAll('.run-checkbox:checked');
    const runIds = Array.from(checkboxes).map(cb => cb.value);

    if (!confirm(`Are you sure you want to delete ${runIds.length} run(s)? This action cannot be undone.`)) {
        return;
    }

    for (const runId of runIds) {
        try {
            await fetch(`${API_BASE}/v1/runs/${runId}/data`, { method: 'DELETE' });
        } catch (error) {
            console.error(`Error deleting run ${runId}:`, error);
        }
    }

    await loadRuns();
    updateBulkActionsVisibility();
});

document.getElementById('bulk-cancel')?.addEventListener('click', async function() {
    const checkboxes = document.querySelectorAll('.run-checkbox:checked');
    const runIds = Array.from(checkboxes).map(cb => cb.value);

    if (!confirm(`Are you sure you want to cancel ${runIds.length} running task(s)?`)) {
        return;
    }

    for (const runId of runIds) {
        try {
            await fetch(`${API_BASE}/v1/runs/${runId}/cancel`, { method: 'POST' });
        } catch (error) {
            console.error(`Error cancelling run ${runId}:`, error);
        }
    }

    await loadRuns();
    updateBulkActionsVisibility();
});

// Filter event listeners
document.getElementById('search-runs')?.addEventListener('input', () => loadRuns(false));
document.getElementById('filter-date-from')?.addEventListener('change', () => loadRuns(false));
document.getElementById('filter-date-to')?.addEventListener('change', () => loadRuns(false));

document.getElementById('clear-filters')?.addEventListener('click', function() {
    document.getElementById('search-runs').value = '';
    document.getElementById('filter-date-from').value = '';
    document.getElementById('filter-date-to').value = '';
    loadRuns(false);
});

// Grafana URL management
function loadGrafanaUrl() {
    const savedUrl = localStorage.getItem('grafana-url') || 'http://localhost:3000';
    const input = document.getElementById('grafana-url-input');
    const link = document.getElementById('grafana-link');

    if (input) input.value = savedUrl;
    if (link) link.href = savedUrl;
}

document.getElementById('btn-save-grafana-url')?.addEventListener('click', function() {
    const input = document.getElementById('grafana-url-input');
    const statusDiv = document.getElementById('grafana-status');
    const url = input.value.trim();

    if (!url) {
        statusDiv.textContent = 'Please enter a valid URL';
        statusDiv.className = 'settings-status error';
        return;
    }

    // Validate URL format
    try {
        new URL(url);
    } catch {
        statusDiv.textContent = 'Invalid URL format. Please use format: http://localhost:3000';
        statusDiv.className = 'settings-status error';
        return;
    }

    localStorage.setItem('grafana-url', url);
    document.getElementById('grafana-link').href = url;

    statusDiv.textContent = 'Grafana URL saved successfully!';
    statusDiv.className = 'settings-status success';

    setTimeout(() => {
        statusDiv.textContent = '';
        statusDiv.className = '';
    }, 3000);
});

document.getElementById('btn-test-grafana-url')?.addEventListener('click', async function() {
    const input = document.getElementById('grafana-url-input');
    const statusDiv = document.getElementById('grafana-status');
    const url = input.value.trim();

    if (!url) {
        statusDiv.textContent = 'Please enter a URL to test';
        statusDiv.className = 'settings-status error';
        return;
    }

    statusDiv.textContent = 'Testing connection...';
    statusDiv.className = 'settings-status';

    try {
        // Try to open in new window to test
        const testWindow = window.open(url, '_blank', 'noopener,noreferrer');
        if (testWindow) {
            statusDiv.textContent = 'Opening Grafana in new tab. Please check if it loads correctly.';
            statusDiv.className = 'settings-status success';
        } else {
            statusDiv.textContent = 'Unable to open Grafana. Please check your browser popup settings.';
            statusDiv.className = 'settings-status error';
        }
    } catch (error) {
        statusDiv.textContent = `Error testing connection: ${error.message}`;
        statusDiv.className = 'settings-status error';
    }

    setTimeout(() => {
        if (statusDiv.className === 'settings-status success') {
            statusDiv.textContent = '';
            statusDiv.className = '';
        }
    }, 5000);
});

// Load Grafana URL on page load
loadGrafanaUrl();
