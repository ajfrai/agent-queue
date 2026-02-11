// Agent Harness Web UI

class AgentHarness {
    constructor() {
        this.tasks = [];
        this.currentFilter = 'all';
        this.eventSource = null;
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
        indicator.classList.add('active');

        if (event.payload && event.payload.rate_limit) {
            const rl = event.payload.rate_limit;
            const el = document.getElementById('rate-limit-status');
            el.textContent = `${rl.messages_used}/${rl.messages_limit} (${rl.percent_used.toFixed(0)}%)`;

            if (rl.is_limited) {
                el.style.color = '#ef4444';
            } else if (rl.percent_used > 80) {
                el.style.color = '#f59e0b';
            } else {
                el.style.color = '#22c55e';
            }
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
        const fab = document.getElementById('fab-add-task');
        fab.addEventListener('click', () => {
            this.openAddTaskModal();
        });
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
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
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

        // Render actions
        const actionsContainer = document.getElementById('modal-actions');
        let actionsHTML = '';
        if (task.status === 'executing' && task.active_session_id) {
            actionsHTML += `<button class="btn-view" onclick="app.viewSession(${task.active_session_id})">View Output</button>`;
        }
        if (task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled') {
            actionsHTML += `<button class="btn-cancel" onclick="app.cancelTask(${task.id})">Cancel Task</button>`;
        }
        actionsContainer.innerHTML = actionsHTML;

        document.getElementById('task-modal').classList.remove('hidden');
        document.getElementById('modal-backdrop').classList.remove('hidden');
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

        let filteredTasks = this.tasks;
        if (this.currentFilter !== 'all') {
            filteredTasks = this.tasks.filter(t => t.status === this.currentFilter);
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
            ${this.renderTaskFlags(task, isActive, isDecompose)}
            ${this.renderTaskActions(task)}
        `;

        return div;
    }

    renderTaskFlags(task, isActive, isDecompose) {
        // Only show flags for actionable tasks
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
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
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
            return '';
        }

        let html = '<div class="task-actions">';

        if (task.status === 'executing' && task.active_session_id) {
            html += `<button class="btn-view" onclick="event.stopPropagation(); app.viewSession(${task.active_session_id})">Output</button>`;
        }

        html += `<button class="btn-cancel" onclick="event.stopPropagation(); app.cancelTask(${task.id})">Cancel</button>`;
        html += '</div>';

        return html;
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
        outputEl.textContent = 'Loading...\n';

        try {
            const response = await fetch(`/api/sessions/${sessionId}/output`);
            if (response.ok) {
                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                outputEl.textContent = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value);
                    outputEl.textContent += chunk;
                    outputEl.scrollTop = outputEl.scrollHeight;
                }
            } else {
                outputEl.textContent = 'Failed to load output';
            }
        } catch (error) {
            console.error('Error loading session output:', error);
            outputEl.textContent = `Error: ${error.message}`;
        }
    }

    // Drag and drop
    setupDragAndDrop() {
        const taskItems = document.querySelectorAll('.task-item');

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
const app = new AgentHarness();
