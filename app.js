(() => {
  'use strict';

  const STORAGE_KEY = 'shixu.todos.v1';
  const $ = (selector) => document.querySelector(selector);
  const input = $('#todo-input');
  const list = $('#todo-list');
  const error = $('#storage-error');
  let filter = 'all';
  let storageBlocked = false;
  let todos = loadTodos();

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  function loadTodos() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw === null) {
        storageBlocked = false;
        error.hidden = true;
        return [];
      }
      const data = JSON.parse(raw);
      const ids = new Set();
      if (!Array.isArray(data) || !data.every((todo) => {
        if (!todo || typeof todo.id !== 'string' || !todo.id || ids.has(todo.id)
          || typeof todo.text !== 'string' || !todo.text.trim() || todo.text.length > 200
          || typeof todo.completed !== 'boolean') return false;
        ids.add(todo.id);
        return true;
      })) throw new Error('Invalid saved data');
      storageBlocked = false;
      error.hidden = true;
      return data;
    } catch {
      storageBlocked = true;
      showError('无法读取本地数据。为保护已有内容，暂未启用修改；请检查浏览器存储权限或备份本地数据后重试。');
      return [];
    }
  }

  // Save first: if storage is unavailable or full, preserve the previous list.
  function commit(nextTodos, message) {
    if (storageBlocked) return false;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(nextTodos));
    } catch {
      showError('保存失败，本次操作未生效。请检查浏览器存储权限和可用空间后重试。');
      return false;
    }
    todos = nextTodos;
    error.hidden = true;
    render();
    $('#announcement').textContent = message;
    return true;
  }

  function render() {
    const completed = todos.filter((todo) => todo.completed).length;
    const active = todos.length - completed;
    const percent = todos.length ? Math.round(completed / todos.length * 100) : 0;
    const visible = todos.filter((todo) => filter === 'all' || (filter === 'completed' ? todo.completed : !todo.completed));
    for (const [name, count] of [['all', todos.length], ['active', active], ['completed', completed]]) {
      $(`#${name}-count`).textContent = count;
    }
    $('#stat-total').textContent = todos.length;
    $('#stat-active').textContent = active;
    $('#stat-completed').textContent = completed;
    $('#progress-text').textContent = `${percent}%`;
    $('#progress').value = percent;
    $('#visible-count').textContent = visible.length;
    $('#remaining-text').textContent = active ? `还有 ${active} 项任务待完成` : (todos.length ? '全部完成，给自己一个小小的肯定！' : '还没有待完成的任务');

    const fragment = document.createDocumentFragment();
    for (const todo of visible) {
      const row = document.createElement('li');
      row.className = `todo-item${todo.completed ? ' completed' : ''}`;
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'todo-check';
      checkbox.id = `todo-${todo.id}`;
      checkbox.checked = todo.completed;
      checkbox.disabled = storageBlocked;
      checkbox.addEventListener('change', () => {
        if (commit(todos.map((item) => item.id === todo.id ? { ...item, completed: !item.completed } : item), todo.completed ? '已标为待完成' : '已完成一项任务')) {
          const replacement = document.getElementById(checkbox.id);
          (replacement || list.querySelector('.todo-check') || input).focus();
        } else checkbox.checked = todo.completed;
      });
      const label = document.createElement('label');
      label.className = 'todo-label';
      label.htmlFor = checkbox.id;
      // Task text is always plain text, never interpreted as HTML.
      label.textContent = todo.text;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'delete-button';
      remove.setAttribute('aria-label', `删除任务：${todo.text}`);
      remove.title = '删除任务';
      remove.disabled = storageBlocked;
      remove.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7m4-7v7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      remove.addEventListener('click', () => {
        const index = visible.findIndex((item) => item.id === todo.id);
        if (commit(todos.filter((item) => item.id !== todo.id), '任务已删除')) {
          const buttons = list.querySelectorAll('.delete-button');
          (buttons[Math.min(index, buttons.length - 1)] || input).focus();
        }
      });
      row.append(checkbox, label, remove);
      fragment.append(row);
    }
    list.replaceChildren(fragment);
    $('#empty-state').hidden = visible.length > 0;
    const emptyCopy = filter === 'completed'
      ? ['还没有已完成的任务', '完成一件小事后，点击任务旁的方框，把进步记录在这里。']
      : filter === 'active' && todos.length
        ? ['待办清零，做得不错', '享受一下轻松的此刻，或添加下一个小目标。']
        : ['新的一页，从一件小事开始', '在上方添加你的第一个任务，让计划慢慢发生。'];
    $('#empty-title').textContent = emptyCopy[0];
    $('#empty-description').textContent = emptyCopy[1];
    input.disabled = storageBlocked;
    $('.add-button').disabled = storageBlocked;
  }

  function setFilter(value) {
    filter = value;
    const title = { all: '全部任务', active: '待完成', completed: '已完成' }[filter];
    $('#page-title').replaceChildren(document.createTextNode(title));
    const dot = document.createElement('span');
    dot.className = 'title-dot';
    dot.textContent = '.';
    $('#page-title').append(dot);
    $('#breadcrumb').textContent = title;
    document.querySelectorAll('[data-filter]').forEach((button) => {
      const selected = button.dataset.filter === filter;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    render();
  }

  $('#todo-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) {
      input.setCustomValidity('请输入任务内容，不能只输入空格。');
      input.reportValidity();
      return;
    }
    const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    if (commit([{ id, text, completed: false }, ...todos], '任务已添加')) {
      input.value = '';
      if (filter === 'completed') setFilter('all');
      input.focus();
    }
  });
  input.addEventListener('input', () => input.setCustomValidity(''));
  document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => setFilter(button.dataset.filter)));
  window.addEventListener('storage', (event) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      todos = loadTodos();
      render();
    }
  });
  const today = new Date();
  $('#today').dateTime = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  $('#today').textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(today);
  render();
})();
