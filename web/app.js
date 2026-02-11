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
            // Reconnect after 5 seconds
            setTimeout(() => this.setupEventSource(), 5000);
        };

        // Handle heartbeat ticks
        this.eventSource.addEventListener('heartbeat.tick', (event) => {
            const data = JSON.parse(event.data);
            this.updateHeartbeatStatus(data);
        });
    }

    handleEvent(event) {
        console.log('Event received:', event.event_type, event.payload);

        // Handle different event types
        if (event.event_type.startsWith('task.')) {
            this.loadTasks(); // Reload tasks on any task event
        } else if (event.event_type.startsWith('session.')) {
            this.handleSessionEvent(event);
        } else if (event.event_type === 'heartbeat.tick') {
            this.updateHeartbeatStatus(event);
        }

        // Update system status
        this.loadSystemStatus();
    }

    updateHeartbeatStatus(event) {
        const heartbeatEl = document.getElementById('heartbeat-status');
        heartbeatEl.textContent = '🟢';
        heartbeatEl.title = 'Heartbeat active';

        // Update rate limit display
        if (event.payload.rate_limit) {
            const rl = event.payload.rate_limit;
            document.getElementById('rate-limit-status').textContent =
                `${rl.messages_used}/${rl.messages_limit} (${rl.percent_used.toFixed(0)}%)`;

            if (rl.is_limited) {
                document.getElementById('rate-limit-status').style.color = '#f44336';
            } else if (rl.percent_used > 80) {
                document.getElementById('rate-limit-status').style.color = '#ffa500';
            } else {
                document.getElementById('rate-limit-status').style.color = '#4caf50';
            }
        }
    }

    handleSessionEvent(event) {
        if (event.event_type === 'session.output') {
            // Stream output to viewer if open
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

        // Close session viewer
        document.getElementById('close-session').addEventListener('click', () => {
            document.getElementById('session-viewer').classList.add('hidden');
        });
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
                // Clear form
                document.getElementById('task-title').value = '';
                document.getElementById('task-description').value = '';
                document.getElementById('task-priority').value = '0';

                // Reload tasks
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

        // Filter tasks
        let filteredTasks = this.tasks;
        if (this.currentFilter !== 'all') {
            filteredTasks = this.tasks.filter(t => t.status === this.currentFilter);
        }

        // Sort by position
        filteredTasks.sort((a, b) => a.position - b.position);

        // Render
        taskList.innerHTML = '';
        filteredTasks.forEach(task => {
            const taskEl = this.createTaskElement(task);
            taskList.appendChild(taskEl);
        });

        // Setup drag and drop
        this.setupDragAndDrop();
    }

    createTaskElement(task) {
        const div = document.createElement('div');
        div.className = `task-item ${task.status}`;
        div.draggable = true;
        div.dataset.taskId = task.id;

        // Status emoji
        const statusEmoji = {
            pending: '⏱',
            assessing: '🔍',
            executing: '🟢',
            completed: '✓',
            failed: '❌',
            cancelled: '⏸',
        };

        // Format timestamp
        const createdAt = new Date(task.created_at).toLocaleString();

        div.innerHTML = `
            <div class="task-header">
                <div class="task-title">${statusEmoji[task.status]} ${this.escapeHtml(task.title)}</div>
                <div class="task-status">${task.status}</div>
            </div>
            <div class="task-description">${this.escapeHtml(task.description).substring(0, 150)}...</div>
            <div class="task-meta">
                <span>Priority: ${task.priority}</span>
                ${task.complexity ? `<span>Complexity: ${task.complexity}</span>` : ''}
                <span>Created: ${createdAt}</span>
            </div>
            ${this.renderTaskActions(task)}
        `;

        return div;
    }

    renderTaskActions(task) {
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
            return '';
        }

        let html = '<div class="task-actions">';

        if (task.status === 'executing' && task.active_session_id) {
            html += `<button class="btn-view" onclick="app.viewSession(${task.active_session_id})">View Output</button>`;
        }

        html += `<button class="btn-cancel" onclick="app.cancelTask(${task.id})">Cancel</button>`;
        html += '</div>';

        return html;
    }

    async cancelTask(taskId) {
        if (!confirm('Are you sure you want to cancel this task?')) {
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
        outputEl.textContent = 'Loading session output...\n';

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
                outputEl.textContent = 'Failed to load session output';
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
        // Find positions
        const draggedTask = this.tasks.find(t => t.id == draggedId);
        const targetTask = this.tasks.find(t => t.id == targetId);

        if (!draggedTask || !targetTask) return;

        // Swap positions
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
