// Agent Queue Web UI

class AgentQueue {
    constructor() {
        this.tasks = [];
        this.currentFilter = 'all';
        this.eventSource = null;
        this.heartbeatCount = 0;
        this.init();
    }

    init() {
        this.setupEventSource();
        this.setupFormHandlers();
        this.setupFilterTabs();
        this.setupModalHandlers();
        this.setupFAB();
        this.loadTasks();
        this.loadSystemStatus();
    }

    // SSE Event Stream
    setupEventSource() {
        this.eventSource = new EventSource('/api/events/stream');

        this.eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleEvent(data);
        };

        this.eventSource.onerror = (error) => {
            console.error('SSE connection error:', error);
            setTimeout(() => this.setupEventSource(), 5000);
        };

        this.eventSource.addEventListener('heartbeat.tick', (event) => {
            const data = JSON.parse(event.data);
            this.updateHeartbeatStatus(data);
        });
    }

    handleEvent(event) {
        console.log('Event:', event.event_type);

        if (event.event_type.startsWith('task.')) {
            this.loadTasks();
        } else if (event.event_type.startsWith('session.')) {
            this.handleSessionEvent(event);
        } else if (event.event_type === 'heartbeat.tick') {
            this.updateHeartbeatStatus(event);
        }

        this.loadSystemStatus();
    }

    updateHeartbeatStatus(event) {
        const indicator = document.getElementById('heartbeat-status');
        if (!indicator) return;
        indicator.classList.add('active');
        setTimeout(() => indicator.classList.remove('active'), 2000);

        // Increment and flash counter
        this.heartbeatCount++;
        const countEl = document.getElementById('heartbeat-count');
        if (countEl) {
            countEl.textContent = this.heartbeatCount;
            countEl.classList.add('flash');
            setTimeout(() => countEl.classList.remove('flash'), 600);
        }

        const payload = event && event.payload ? event.payload : (event || {});
        const rl = payload.rate_limit;
        const el = document.getElementById('rate-limit-status');
        if (!el) return;

        if (rl) {
            if (rl.is_limited) {
                const resetAt = rl.reset_at ? new Date(rl.reset_at).toLocaleTimeString() : '?';
                el.textContent = `Limited (resets ${resetAt})`;
                el.style.color = '#ef4444';
            } else {
                el.textContent = 'Available';
                el.style.color = '#22c55e';
            }
        } else if (payload.error) {
            el.textContent = 'Check failed';
            el.style.color = '#f59e0b';
        }
    }

    handleSessionEvent(event) {
        if (event.event_type === 'session.output') {
            const viewer = document.getElementById('session-viewer');
            if (!viewer.classList.contains('hidden')) {
                const outputEl = document.getElementById('session-output');
                outputEl.textContent += event.payload.output;
                outputEl.scrollTop = outputEl.scrollHeight;
            }
        }
    }

    // Form handlers
    setupFormHandlers() {
        const form = document.getElementById('create-task-form');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.createTask();
        });

        document.getElementById('close-session').addEventListener('click', () => {
            document.getElementById('session-viewer').classList.add('hidden');
        });
    }

    setupModalHandlers() {
        const backdrop = document.getElementById('modal-backdrop');
        const taskModal = document.getElementById('task-modal');
        const addTaskModal = document.getElementById('add-task-modal');
        const closeModal = document.getElementById('close-modal');
        const closeFormModal = document.getElementById('close-form-modal');
        const cancelFormBtn = document.getElementById('cancel-task-form');

        // Close task detail modal
        closeModal.addEventListener('click', () => {
            this.closeTaskModal();
        });

        // Close add task modal
        closeFormModal.addEventListener('click', () => {
            this.closeAddTaskModal();
        });

        cancelFormBtn.addEventListener('click', () => {
            this.closeAddTaskModal();
        });

        // Close on backdrop click
        backdrop.addEventListener('click', () => {
            this.closeTaskModal();
            this.closeAddTaskModal();
        });
    }

    setupFAB() {
        const toggle = document.getElementById('fab-toggle');
        const container = document.getElementById('fab-container');

        toggle.addEventListener('click', () => {
            container.classList.toggle('open');
        });

        document.getElementById('fab-add').addEventListener('click', () => {
            container.classList.remove('open');
            this.openAddTaskModal();
        });

        document.getElementById('fab-heartbeat').addEventListener('click', () => {
            container.classList.remove('open');
            this.triggerHeartbeat();
        });

        // Close menu on outside tap
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                container.classList.remove('open');
            }
        });
    }

    async triggerHeartbeat() {
        try {
            const resp = await fetch('/api/heartbeat/trigger', { method: 'POST' });
            if (resp.ok) {
                const diag = await resp.json();
                console.log('Heartbeat diag:', diag);
                this.showToast(this.formatDiag(diag));
            } else {
                this.showToast('Trigger failed: ' + resp.status);
            }
        } catch (error) {
            console.error('Error triggering heartbeat:', error);
            this.showToast('Trigger error: ' + error.message);
        }
    }

    formatDiag(d) {
        const parts = [];
        if (d.phase) parts.push(`#${d.beat_number} ${d.phase}`);
        if (d.dupes_removed) parts.push(`${d.dupes_removed} dupe(s)`);
        if (d.rate_error) return parts.concat('Rate error').join(' · ');
        if (d.rate_limited) return parts.concat('Rate limited').join(' · ');
        if (d.comment_error) return parts.concat('Comment error').join(' · ');
        if (d.assess_error) return parts.concat('Assess error').join(' · ');
        if (d.execute_error) return parts.concat('Execute error').join(' · ');
        if (d.comments_left > 0) parts.push(`${d.comments_left} comment(s)`);
        else if (d.comments_left === 0 && d.phase === 'comment') parts.push('No comments');
        if (d.tasks_assessed > 0) parts.push(`${d.tasks_assessed} assessed`);
        else if (d.tasks_assessed === 0 && d.phase === 'assess') parts.push('Nothing to assess');
        if (d.task_executed === true) parts.push('Task running');
        else if (d.task_executed === false && d.phase === 'execute') parts.push('Nothing to execute');
        return parts.join(' · ') || 'Beat OK';
    }

    showToast(msg) {
        let toast = document.getElementById('toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'toast';
            toast.className = 'toast';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.classList.add('visible');
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
    }

    openTaskModal(task) {
        const isActive = task.metadata && task.metadata.active === true;
        const isDecompose = task.metadata && task.metadata.decompose_on_heartbeat === true;

        document.getElementById('modal-title').textContent = task.title;
        document.getElementById('modal-task-title').textContent = task.title;
        document.getElementById('modal-task-description').textContent = task.description;
        document.getElementById('modal-task-priority').textContent = `P${task.priority}`;
        document.getElementById('modal-task-status').textContent = task.status;
        document.getElementById('modal-task-complexity').textContent = task.complexity || '-';
        document.getElementById('modal-task-created').textContent = new Date(task.created_at).toLocaleDateString();

        // Render flags
        const flagsContainer = document.getElementById('modal-flags');
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' || task.status === 'decomposed') {
            flagsContainer.innerHTML = '';
        } else {
            flagsContainer.innerHTML = `
                <span class="task-flag flag-active ${isActive ? 'is-set' : ''}"
                      onclick="app.toggleActive(${task.id}, ${!isActive})">
                    ${isActive ? 'Active' : 'Inactive'}
                </span>
                <span class="task-flag flag-decompose ${isDecompose ? 'is-set' : ''}"
                      onclick="app.toggleDecompose(${task.id}, ${!isDecompose})">
                    Decompose
                </span>
            `;
        }

        // Render status changer
        const statusContainer = document.getElementById('modal-status-change');
        if (statusContainer) {
            const statuses = ['pending', 'executing', 'completed', 'failed', 'cancelled', 'decomposed'];
            statusContainer.innerHTML = `
                <select id="modal-status-select" class="status-select">
                    ${statuses.map(s => `<option value="${s}" ${s === task.status ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <button class="btn-status-apply" onclick="app.changeStatus(${task.id})">Apply</button>
            `;
        }

        // Render actions
        const actionsContainer = document.getElementById('modal-actions');
        let actionsHTML = '';
        if (task.active_session_id) {
            actionsHTML += `<button class="btn-view" onclick="app.viewSession(${task.active_session_id})">View Output</button>`;
        }
        if (task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled' && task.status !== 'decomposed') {
            actionsHTML += `<button class="btn-cancel" onclick="app.cancelTask(${task.id})">Cancel Task</button>`;
        }
        actionsContainer.innerHTML = actionsHTML;

        // Load event log
        const eventsContainer = document.getElementById('modal-events');
        eventsContainer.innerHTML = '<span class="event-loading">Loading...</span>';
        this.loadTaskEvents(task.id, eventsContainer);

        document.getElementById('task-modal').classList.remove('hidden');
        document.getElementById('modal-backdrop').classList.remove('hidden');
    }

    async loadTaskEvents(taskId, container) {
        try {
            const resp = await fetch(`/api/tasks/${taskId}/events`);
            if (!resp.ok) {
                container.innerHTML = '<span class="event-loading">Failed to load events</span>';
                return;
            }
            const events = await resp.json();
            if (events.length === 0) {
                container.innerHTML = '<span class="event-loading">No events yet</span>';
                return;
            }
            container.innerHTML = events.map(ev => {
                const time = new Date(ev.created_at).toLocaleTimeString();
                const label = ev.event_type.replace('task.', '');
                const detail = this.formatEventDetail(ev);
                return `<div class="event-row">
                    <span class="event-time">${time}</span>
                    <span class="event-label event-${label}">${label}</span>
                    ${detail ? `<span class="event-detail">${this.escapeHtml(detail)}</span>` : ''}
                </div>`;
            }).join('');
        } catch (e) {
            container.innerHTML = '<span class="event-loading">Error loading events</span>';
        }
    }

    formatEventDetail(ev) {
        const p = ev.payload || {};
        if (p.complexity) return `complexity: ${p.complexity}, model: ${p.recommended_model || '?'}`;
        if (p.subtasks && p.subtasks.length) return `${p.subtasks.length} subtask(s) suggested`;
        if (p.error) return p.error;
        if (p.exit_code !== undefined) return `exit code: ${p.exit_code}`;
        return '';
    }

    closeTaskModal() {
        document.getElementById('task-modal').classList.add('hidden');
        document.getElementById('modal-backdrop').classList.add('hidden');
    }

    openAddTaskModal() {
        document.getElementById('add-task-modal').classList.remove('hidden');
        document.getElementById('modal-backdrop').classList.remove('hidden');
        document.getElementById('task-title').focus();
    }

    closeAddTaskModal() {
        document.getElementById('add-task-modal').classList.add('hidden');
        document.getElementById('modal-backdrop').classList.add('hidden');
        document.getElementById('task-title').value = '';
        document.getElementById('task-description').value = '';
        document.getElementById('task-priority').value = '0';
    }

    async createTask() {
        const title = document.getElementById('task-title').value;
        const description = document.getElementById('task-description').value;
        const priority = parseInt(document.getElementById('task-priority').value);

        try {
            const response = await fetch('/api/tasks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description, priority }),
            });

            if (response.ok) {
                this.closeAddTaskModal();
                await this.loadTasks();
            } else {
                alert('Failed to create task');
            }
        } catch (error) {
            console.error('Error creating task:', error);
            alert('Failed to create task');
        }
    }

    // Task management
    async loadTasks() {
        try {
            const response = await fetch('/api/tasks');
            if (response.ok) {
                this.tasks = await response.json();
                this.renderTasks();
            }
        } catch (error) {
            console.error('Error loading tasks:', error);
        }
    }

    renderTasks() {
        const taskList = document.getElementById('task-list');
        const largeTaskList = document.getElementById('large-task-list');
        const largeTaskSection = document.getElementById('large-task-section');

        // Split into large (root-level decomposed parents) vs regular tasks
        // Only root tasks (no parent_task_id) go in the large section
        const isLargeTask = (t) =>
            !t.parent_task_id && (
                t.status === 'decomposed' ||
                (t.metadata && t.metadata.decomposed_into && t.metadata.decomposed_into.length > 0)
            );
        const largeTasks = this.tasks.filter(isLargeTask);
        const regularTasks = this.tasks.filter(t => !isLargeTask(t));

        // Render large task queue
        if (largeTasks.length > 0) {
            largeTaskSection.classList.remove('hidden');
            largeTaskList.innerHTML = '';
            largeTasks.forEach(task => {
                const el = this.createLargeTaskElement(task);
                largeTaskList.appendChild(el);
            });
        } else {
            largeTaskSection.classList.add('hidden');
            largeTaskList.innerHTML = '';
        }

        // Render regular queue with filters
        let filteredTasks = regularTasks;
        if (this.currentFilter !== 'all') {
            filteredTasks = regularTasks.filter(t => t.status === this.currentFilter);
        }

        filteredTasks.sort((a, b) => a.position - b.position);

        taskList.innerHTML = '';

        if (filteredTasks.length === 0) {
            taskList.innerHTML = '<div class="empty-state">No tasks</div>';
            return;
        }

        filteredTasks.forEach(task => {
            const taskEl = this.createTaskElement(task);
            taskList.appendChild(taskEl);
        });

        this.setupDragAndDrop();
    }

    createLargeTaskElement(task) {
        const div = document.createElement('div');
        div.className = `large-task ${task.status}`;
        div.dataset.taskId = task.id;

        const subtaskIds = (task.metadata && task.metadata.decomposed_into) || [];
        const subtasks = this.tasks.filter(t => subtaskIds.includes(t.id));
        const doneCount = subtasks.filter(t =>
            t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled'
        ).length;
        const total = subtasks.length;
        const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

        div.innerHTML = `
            <div class="large-task-header" data-task-id="${task.id}">
                <div class="large-task-info">
                    <div class="task-title">${this.escapeHtml(task.title)}</div>
                    <div class="task-status status-${task.status}">${task.status}</div>
                </div>
                <div class="subtask-progress">
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${pct}%"></div>
                    </div>
                    <span class="progress-label">${doneCount}/${total} subtasks</span>
                </div>
                <button class="accordion-toggle" aria-label="Toggle subtasks">
                    <span class="accordion-arrow">&#9662;</span>
                </button>
            </div>
            <div class="accordion-body">
                ${subtasks.map(st => `
                    <div class="subtask-item ${st.status}" data-task-id="${st.id}">
                        <span class="subtask-title">${this.escapeHtml(st.title)}</span>
                        <span class="task-status status-${st.status}">${st.status}</span>
                    </div>
                `).join('')}
                ${subtasks.length === 0 ? '<div class="empty-state">No subtasks loaded</div>' : ''}
            </div>
        `;

        // Header click opens parent detail modal
        div.querySelector('.large-task-header').addEventListener('click', (e) => {
            if (e.target.closest('.accordion-toggle')) return;
            this.openTaskModal(task);
        });

        // Accordion toggle
        div.querySelector('.accordion-toggle').addEventListener('click', (e) => {
            e.stopPropagation();
            div.classList.toggle('expanded');
        });

        // Subtask clicks open their detail modals
        div.querySelectorAll('.subtask-item').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const stId = parseInt(el.dataset.taskId);
                const st = this.tasks.find(t => t.id === stId);
                if (st) this.openTaskModal(st);
            });
        });

        return div;
    }

    createTaskElement(task) {
        const div = document.createElement('div');
        const isActive = task.metadata && task.metadata.active === true;
        const isDecompose = task.metadata && task.metadata.decompose_on_heartbeat === true;

        div.className = `task-item ${task.status}`;
        if (isActive) {
            div.classList.add('active-task');
        } else {
            div.classList.add('inactive');
        }
        div.draggable = true;
        div.dataset.taskId = task.id;

        // Tap to open detail modal
        div.addEventListener('click', (e) => {
            // Don't open modal if clicking a button or flag
            if (e.target.closest('button') || e.target.closest('.task-flag')) {
                return;
            }
            this.openTaskModal(task);
        });

        const createdAt = new Date(task.created_at).toLocaleDateString();

        div.innerHTML = `
            <div class="task-header">
                <div class="task-title">${this.escapeHtml(task.title)}</div>
                <div class="task-status status-${task.status}">${task.status}</div>
            </div>
            <div class="task-description">${this.escapeHtml(task.description)}</div>
            <div class="task-meta">
                <span>P${task.priority}</span>
                ${task.complexity ? `<span>${task.complexity}</span>` : ''}
                <span>${createdAt}</span>
            </div>
            ${this.renderTaskBadges(task)}
            ${this.renderTaskFlags(task, isActive, isDecompose)}
            ${this.renderTaskActions(task)}
        `;

        return div;
    }

    renderTaskBadges(task) {
        const m = task.metadata || {};
        const badges = [];

        if (m.decomposed_into && m.decomposed_into.length) {
            badges.push(`<span class="task-badge badge-decomposed">Decomposed into ${m.decomposed_into.length} subtasks</span>`);
        } else if (m.assessment) {
            const a = m.assessment;
            if (a.subtasks && a.subtasks.length && task.status === 'pending') {
                badges.push(`<span class="task-badge badge-decompose">Needs decomposition (${a.subtasks.length} subtasks)</span>`);
            } else if (a.reasoning) {
                badges.push(`<span class="task-badge badge-assessed">Assessed</span>`);
            }
        }

        if (task.parent_task_id) {
            badges.push(`<span class="task-badge badge-child">Subtask of #${task.parent_task_id}</span>`);
        }

        if (m.error) {
            badges.push(`<span class="task-badge badge-error">${this.escapeHtml(m.error)}</span>`);
        }

        if (!badges.length) return '';
        return `<div class="task-badges">${badges.join('')}</div>`;
    }

    renderTaskFlags(task, isActive, isDecompose) {
        // Only show flags for actionable tasks
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' || task.status === 'decomposed') {
            return '';
        }

        return `
            <div class="task-flags">
                <span class="task-flag flag-active ${isActive ? 'is-set' : ''}"
                      onclick="event.stopPropagation(); app.toggleActive(${task.id}, ${!isActive})">
                    ${isActive ? 'Active' : 'Inactive'}
                </span>
                <span class="task-flag flag-decompose ${isDecompose ? 'is-set' : ''}"
                      onclick="event.stopPropagation(); app.toggleDecompose(${task.id}, ${!isDecompose})">
                    ${isDecompose ? 'Decompose' : 'Decompose'}
                </span>
            </div>
        `;
    }

    renderTaskActions(task) {
        let html = '<div class="task-actions">';
        let hasActions = false;

        // Show output button for any task that has a session
        if (task.active_session_id) {
            html += `<button class="btn-view" onclick="event.stopPropagation(); app.viewSession(${task.active_session_id})">Output</button>`;
            hasActions = true;
        }

        // Show cancel only for non-terminal tasks
        if (task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled' && task.status !== 'decomposed') {
            html += `<button class="btn-cancel" onclick="event.stopPropagation(); app.cancelTask(${task.id})">Cancel</button>`;
            hasActions = true;
        }

        html += '</div>';
        return hasActions ? html : '';
    }

    // Toggle active state
    async toggleActive(taskId, active) {
        try {
            const task = this.tasks.find(t => t.id === taskId);
            if (!task) return;

            const metadata = { ...(task.metadata || {}), active: active };

            const response = await fetch(`/api/tasks/${taskId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ metadata }),
            });

            if (response.ok) {
                await this.loadTasks();
            }
        } catch (error) {
            console.error('Error toggling active:', error);
        }
    }

    // Toggle decompose flag
    async toggleDecompose(taskId, decompose) {
        try {
            const task = this.tasks.find(t => t.id === taskId);
            if (!task) return;

            const metadata = { ...(task.metadata || {}), decompose_on_heartbeat: decompose };

            const response = await fetch(`/api/tasks/${taskId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ metadata }),
            });

            if (response.ok) {
                await this.loadTasks();
            }
        } catch (error) {
            console.error('Error toggling decompose:', error);
        }
    }

    async changeStatus(taskId) {
        const select = document.getElementById('modal-status-select');
        if (!select) return;
        const newStatus = select.value;

        try {
            const response = await fetch(`/api/tasks/${taskId}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: newStatus }),
            });

            if (response.ok) {
                this.closeTaskModal();
                await this.loadTasks();
                this.showToast(`Task ${taskId} → ${newStatus}`);
            } else {
                const err = await response.json();
                this.showToast(`Failed: ${err.detail || response.status}`);
            }
        } catch (error) {
            console.error('Error changing status:', error);
            this.showToast('Error changing status');
        }
    }

    async cancelTask(taskId) {
        if (!confirm('Cancel this task?')) {
            return;
        }

        try {
            const response = await fetch(`/api/tasks/${taskId}`, {
                method: 'DELETE',
            });

            if (response.ok) {
                await this.loadTasks();
            } else {
                alert('Failed to cancel task');
            }
        } catch (error) {
            console.error('Error cancelling task:', error);
            alert('Failed to cancel task');
        }
    }

    async viewSession(sessionId) {
        const viewer = document.getElementById('session-viewer');
        const outputEl = document.getElementById('session-output');

        viewer.classList.remove('hidden');
        outputEl.innerHTML = '<span class="log-info">Loading...</span>\n';

        try {
            const response = await fetch(`/api/sessions/${sessionId}/output`);
            if (response.ok) {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                outputEl.innerHTML = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // keep incomplete line in buffer

                    for (const line of lines) {
                        const el = this.parseLogLine(line.trim());
                        if (el) {
                            outputEl.appendChild(el);
                        }
                    }
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
                // flush remaining buffer
                if (buffer.trim()) {
                    const el = this.parseLogLine(buffer.trim());
                    if (el) outputEl.appendChild(el);
                }
            } else {
                outputEl.innerHTML = '<span class="log-error">Failed to load output</span>';
            }
        } catch (error) {
            console.error('Error loading session output:', error);
            outputEl.innerHTML = `<span class="log-error">Error: ${this.escapeHtml(error.message)}</span>`;
        }
    }

    parseLogLine(line) {
        if (!line) return null;

        try {
            const obj = JSON.parse(line);
            return this.renderLogEvent(obj);
        } catch {
            // Not JSON — render as plain text
            const div = document.createElement('div');
            div.className = 'log-line log-plain';
            div.textContent = line;
            return div;
        }
    }

    renderLogEvent(event) {
        const type = event.type || '';

        // Skip noisy system init events
        if (type === 'system' && event.subtype === 'init') {
            const div = document.createElement('div');
            div.className = 'log-line log-system';
            div.innerHTML = `<span class="log-label">system</span> Session started · model: ${this.escapeHtml(event.model || '?')}`;
            return div;
        }

        if (type === 'system') {
            const div = document.createElement('div');
            div.className = 'log-line log-system';
            div.innerHTML = `<span class="log-label">system</span> ${this.escapeHtml(event.subtype || JSON.stringify(event))}`;
            return div;
        }

        if (type === 'assistant') {
            const content = (event.message && event.message.content) || [];
            const frag = document.createDocumentFragment();

            for (const block of content) {
                if (block.type === 'text' && block.text) {
                    const div = document.createElement('div');
                    div.className = 'log-line log-assistant';
                    div.innerHTML = `<span class="log-label">claude</span><span class="log-text">${this.escapeHtml(block.text)}</span>`;
                    frag.appendChild(div);
                } else if (block.type === 'tool_use') {
                    const div = document.createElement('div');
                    div.className = 'log-line log-tool-call';
                    const name = block.name || '?';
                    const input = block.input || {};
                    let summary = this.summarizeToolInput(name, input);
                    div.innerHTML = `<span class="log-label">tool</span><strong>${this.escapeHtml(name)}</strong> ${this.escapeHtml(summary)}`;
                    frag.appendChild(div);
                }
            }
            return frag.childNodes.length ? frag : null;
        }

        if (type === 'user') {
            // Tool results — show condensed
            const msg = event.message || {};
            const content = msg.content || [];
            const toolId = event.parent_tool_use_id || '';

            for (const block of content) {
                if (block.type === 'tool_result') {
                    const div = document.createElement('div');
                    div.className = 'log-line log-tool-result';
                    const resultContent = block.content || '';
                    const text = typeof resultContent === 'string' ? resultContent :
                        (Array.isArray(resultContent) ? resultContent.map(c => c.text || '').join('') : JSON.stringify(resultContent));
                    const truncated = text.length > 300 ? text.substring(0, 300) + '...' : text;
                    div.innerHTML = `<span class="log-label">result</span><span class="log-result-text">${this.escapeHtml(truncated)}</span>`;
                    return div;
                }
            }
            return null;
        }

        if (type === 'result') {
            const div = document.createElement('div');
            div.className = 'log-line log-result';
            const text = event.result || '';
            const truncated = text.length > 500 ? text.substring(0, 500) + '...' : text;
            div.innerHTML = `<span class="log-label">done</span><span class="log-text">${this.escapeHtml(truncated)}</span>`;
            return div;
        }

        // Unknown type — skip
        return null;
    }

    summarizeToolInput(name, input) {
        if (name === 'Bash') return input.command || input.description || '';
        if (name === 'Read') return input.file_path || '';
        if (name === 'Write') return input.file_path || '';
        if (name === 'Edit') return input.file_path || '';
        if (name === 'Glob') return input.pattern || '';
        if (name === 'Grep') return `/${input.pattern || ''}/ ${input.path || ''}`;
        if (name === 'WebFetch') return input.url || '';
        if (name === 'WebSearch') return input.query || '';
        if (name === 'Task') return input.description || '';
        // Generic fallback — show first string value
        for (const v of Object.values(input)) {
            if (typeof v === 'string' && v.length < 100) return v;
        }
        return '';
    }

    // Drag and drop
    setupDragAndDrop() {
        const taskItems = document.querySelectorAll('#task-list .task-item');

        taskItems.forEach(item => {
            item.addEventListener('dragstart', this.handleDragStart.bind(this));
            item.addEventListener('dragover', this.handleDragOver.bind(this));
            item.addEventListener('drop', this.handleDrop.bind(this));
            item.addEventListener('dragend', this.handleDragEnd.bind(this));
        });
    }

    handleDragStart(e) {
        e.currentTarget.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', e.currentTarget.innerHTML);
        e.dataTransfer.setData('task-id', e.currentTarget.dataset.taskId);
    }

    handleDragOver(e) {
        if (e.preventDefault) {
            e.preventDefault();
        }
        e.dataTransfer.dropEffect = 'move';
        return false;
    }

    handleDrop(e) {
        if (e.stopPropagation) {
            e.stopPropagation();
        }

        const draggedTaskId = e.dataTransfer.getData('task-id');
        const targetTaskId = e.currentTarget.dataset.taskId;

        if (draggedTaskId !== targetTaskId) {
            this.reorderTasks(draggedTaskId, targetTaskId);
        }

        return false;
    }

    handleDragEnd(e) {
        e.currentTarget.classList.remove('dragging');
    }

    async reorderTasks(draggedId, targetId) {
        const draggedTask = this.tasks.find(t => t.id == draggedId);
        const targetTask = this.tasks.find(t => t.id == targetId);

        if (!draggedTask || !targetTask) return;

        const newPositions = [];
        this.tasks.forEach(task => {
            if (task.id == draggedId) {
                newPositions.push({ id: task.id, position: targetTask.position });
            } else if (task.id == targetId) {
                newPositions.push({ id: task.id, position: draggedTask.position });
            } else {
                newPositions.push({ id: task.id, position: task.position });
            }
        });

        try {
            const response = await fetch('/api/tasks/reorder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newPositions),
            });

            if (response.ok) {
                await this.loadTasks();
            }
        } catch (error) {
            console.error('Error reordering tasks:', error);
        }
    }

    // Filter tabs
    setupFilterTabs() {
        const tabs = document.querySelectorAll('.filter-tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this.currentFilter = tab.dataset.filter;
                this.renderTasks();
            });
        });
    }

    // System status
    async loadSystemStatus() {
        try {
            const response = await fetch('/api/status');
            if (response.ok) {
                const status = await response.json();
                document.getElementById('active-count').textContent = status.active_tasks;
                document.getElementById('pending-count').textContent = status.pending_tasks;
            }
        } catch (error) {
            console.error('Error loading system status:', error);
        }
    }

    // Utility
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app
const app = new AgentQueue();
