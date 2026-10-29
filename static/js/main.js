/**
 * PLACEMENT PREPARATION & TRACKING SYSTEM
 * Vanilla JavaScript UI Controller, Session/CSRF Sync & Interactive Charts Engine
 */

document.addEventListener('DOMContentLoaded', () => {
  initAuthAndCsrfSync();
  initSidebarToggle();
  initFlashDismiss();
  initModals();
  initConfirmModals();
  initTaskCheckboxes();
  initDynamicCharts();
  initNotifications();
});

function getAuthToken() {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('logged_out') === '1') {
    try { sessionStorage.removeItem('pt_auth_token'); } catch (e) {}
    return '';
  }
  const meta = document.querySelector('meta[name="auth-token"]');
  const metaToken = meta ? (meta.getAttribute('content') || '').trim() : '';
  if (metaToken) {
    try { sessionStorage.setItem('pt_auth_token', metaToken); } catch (e) {}
    return metaToken;
  }
  const urlToken = urlParams.get('_st');
  if (urlToken) {
    try { sessionStorage.setItem('pt_auth_token', urlToken); } catch (e) {}
    return urlToken;
  }
  try {
    return sessionStorage.getItem('pt_auth_token') || '';
  } catch (e) {
    return '';
  }
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? (meta.getAttribute('content') || '').trim() : '';
}

function initAuthAndCsrfSync() {
  const authToken = getAuthToken();
  const csrfToken = getCsrfToken();

  // Inject hidden csrf_token and _st inputs into all forms
  document.querySelectorAll('form').forEach((form) => {
    const method = (form.getAttribute('method') || 'GET').toUpperCase();
    if (method === 'POST' && csrfToken && !form.querySelector('input[name="csrf_token"]')) {
      const csrfInput = document.createElement('input');
      csrfInput.type = 'hidden';
      csrfInput.name = 'csrf_token';
      csrfInput.value = csrfToken;
      form.appendChild(csrfInput);
    }
    if (authToken && !form.querySelector('input[name="_st"]')) {
      const action = form.getAttribute('action') || '';
      if (!action.includes('/logout') && !action.includes('/login') && !action.includes('/register')) {
        const stInput = document.createElement('input');
        stInput.type = 'hidden';
        stInput.name = '_st';
        stInput.value = authToken;
        form.appendChild(stInput);
      }
    }
  });

  // Ensure internal links carry _st if needed in cookie-blocked iframe
  if (authToken) {
    document.querySelectorAll('a[href]').forEach((link) => {
      const href = link.getAttribute('href') || '';
      if (
        href.startsWith('/') &&
        !href.startsWith('//') &&
        !href.startsWith('/logout') &&
        !href.startsWith('/login') &&
        !href.startsWith('/register') &&
        !href.includes('_st=')
      ) {
        const sep = href.includes('?') ? '&' : '?';
        link.setAttribute('href', `${href}${sep}_st=${encodeURIComponent(authToken)}`);
      }
    });
  }

  // Delegated click handler to guarantee _st is attached on all internal links
  document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]');
    if (!link) return;
    const href = link.getAttribute('href') || '';
    const curToken = getAuthToken();
    if (
      curToken &&
      href.startsWith('/') &&
      !href.startsWith('//') &&
      !href.startsWith('/logout') &&
      !href.startsWith('/login') &&
      !href.startsWith('/register') &&
      !href.includes('_st=')
    ) {
      const sep = href.includes('?') ? '&' : '?';
      link.setAttribute('href', `${href}${sep}_st=${encodeURIComponent(curToken)}`);
    }
  });
}

// 1. Sidebar Toggle for Mobile Devices
function initSidebarToggle() {
  const toggleBtn = document.getElementById('menuToggle');
  const sidebar = document.getElementById('appSidebar');
  
  if (!toggleBtn || !sidebar) return;

  let backdrop = document.querySelector('.sidebar-backdrop');
  if (!backdrop) {
    backdrop = document.createElement('div');
    backdrop.className = 'sidebar-backdrop';
    backdrop.style.cssText = `
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.4);
      z-index: 35;
      backdrop-filter: blur(2px);
    `;
    document.body.appendChild(backdrop);
  }

  function toggleOpen() {
    const isOpen = sidebar.classList.toggle('open');
    backdrop.style.display = isOpen ? 'block' : 'none';
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    backdrop.style.display = 'none';
  }

  toggleBtn.addEventListener('click', toggleOpen);
  backdrop.addEventListener('click', closeSidebar);
}

// 2. Flash Messages Auto-dismiss
function initFlashDismiss() {
  const flashMessages = document.querySelectorAll('.flash-message');
  flashMessages.forEach((msg) => {
    const closeBtn = msg.querySelector('.flash-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        msg.style.opacity = '0';
        msg.style.transform = 'translateY(-6px)';
        msg.style.transition = 'all 0.2s ease-out';
        setTimeout(() => msg.remove(), 200);
      });
    }

    if (msg.classList.contains('flash-success')) {
      setTimeout(() => {
        if (msg.parentElement) {
          msg.style.opacity = '0';
          msg.style.transition = 'opacity 0.4s ease-out';
          setTimeout(() => msg.remove(), 400);
        }
      }, 6000);
    }
  });
}

// 3. Accessible Modal Dialog System
function initModals() {
  const triggers = document.querySelectorAll('[data-modal-target]');
  const closeBtns = document.querySelectorAll('[data-modal-close]');

  triggers.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const targetId = btn.getAttribute('data-modal-target');
      const modal = document.getElementById(targetId);
      if (modal) {
        openModal(modal);
      }
    });
  });

  closeBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const modal = btn.closest('.modal-overlay');
      if (modal) {
        closeModal(modal);
      }
    });
  });

  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        closeModal(overlay);
      }
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const openModals = document.querySelectorAll('.modal-overlay.open');
      if (openModals.length > 0) {
        closeModal(openModals[openModals.length - 1]);
      }
    }
  });
}

// 3b. Custom Confirmation Modal (iframe-safe replacement for window.confirm)
let pendingConfirmFormId = null;

function initConfirmModals() {
  const confirmModal = document.getElementById('confirmActionModal');
  const titleEl = document.getElementById('confirmModalTitle');
  const messageEl = document.getElementById('confirmModalMessage');
  const submitBtn = document.getElementById('confirmModalSubmitBtn');
  const confirmBtns = document.querySelectorAll('[data-confirm-form]');

  confirmBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const formId = btn.getAttribute('data-confirm-form');
      const title = btn.getAttribute('data-confirm-title') || 'Confirm Action';
      const msg = btn.getAttribute('data-confirm-message') || 'Are you sure you want to proceed?';

      if (!confirmModal) {
        const targetForm = document.getElementById(formId);
        if (targetForm) targetForm.submit();
        return;
      }

      pendingConfirmFormId = formId;
      if (titleEl) titleEl.textContent = title;
      if (messageEl) messageEl.textContent = msg;
      openModal(confirmModal);
    });
  });

  if (submitBtn) {
    submitBtn.addEventListener('click', () => {
      if (pendingConfirmFormId) {
        const targetForm = document.getElementById(pendingConfirmFormId);
        if (targetForm) {
          submitBtn.disabled = true;
          submitBtn.textContent = 'Processing...';
          targetForm.submit();
        }
      }
    });
  }
}

function openModal(modal) {
  modal.classList.add('open');
  document.body.style.overflow = 'hidden';
  const firstInput = modal.querySelector('input:not([type="hidden"]):not([disabled]), select, textarea, #confirmModalSubmitBtn');
  if (firstInput) {
    setTimeout(() => firstInput.focus(), 50);
  }
}

function closeModal(modal) {
  modal.classList.remove('open');
  if (document.querySelectorAll('.modal-overlay.open').length === 0) {
    document.body.style.overflow = '';
  }
}

// 4. Task Instant Status Toggle via API
function initTaskCheckboxes() {
  const checkboxes = document.querySelectorAll('.task-checkbox-btn');
  checkboxes.forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const taskId = btn.getAttribute('data-task-id');
      const taskItem = btn.closest('.task-item');
      if (!taskId) return;

      btn.disabled = true;
      const authToken = getAuthToken();
      const csrfToken = getCsrfToken();

      try {
        const response = await fetch(`/api/tasks/${taskId}/toggle`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Auth-Token': authToken,
            'X-CSRF-Token': csrfToken
          }
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (data.success) {
          if (data.new_status === 'completed') {
            btn.classList.add('checked');
            if (taskItem) taskItem.classList.add('completed');
          } else {
            btn.classList.remove('checked');
            if (taskItem) taskItem.classList.remove('completed');
          }
          // Update status label text inside task row
          if (taskItem) {
            const statusText = taskItem.querySelector('.task-status-text');
            if (statusText) {
              statusText.textContent = data.new_status === 'completed' ? 'Completed' : 'Pending';
            }
          }
          // Update status dropdown inside edit modal
          const editModal = document.getElementById(`editTaskModal-${taskId}`);
          if (editModal) {
            const statusSelect = editModal.querySelector('select[name="status"]');
            if (statusSelect) {
              statusSelect.value = data.new_status;
            }
          }
          // Update category tab badge counter
          if (data.category) {
            const catCounter = document.querySelector(`[data-cat-counter="${data.category}"]`);
            if (catCounter) {
              catCounter.textContent = `(${data.category_completed}/${data.category_total})`;
            }
          }
          updateLiveProgress(data.completed_count, data.total_count, data.overall_pct);
        }
      } catch (err) {
        console.error('Error toggling task:', err);
        // Fallback: submit the standard form if present
        const fallbackForm = document.getElementById(`toggleTaskForm-${taskId}`);
        if (fallbackForm) fallbackForm.submit();
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function updateLiveProgress(completedCount, totalCount, overallPct) {
  const progressMeter = document.getElementById('liveTaskPercentage');
  const progressText = document.getElementById('liveTaskCountText');
  const progressBar = document.getElementById('liveTaskProgressBar');

  if (typeof totalCount === 'number' && progressMeter) {
    progressMeter.textContent = overallPct + '%';
    if (progressText) {
      progressText.textContent = `${completedCount} of ${totalCount} tasks completed`;
    }
    if (progressBar) {
      progressBar.style.width = overallPct + '%';
      if (overallPct >= 75) {
        progressBar.classList.add('success');
      } else {
        progressBar.classList.remove('success');
      }
    }
  }
}

// 5. Dynamic Interactive Charts (Zero-Dependency Pure SVG/Canvas)
let cachedChartData = null;

function initDynamicCharts() {
  const scoreTrendCanvas = document.getElementById('scoreTrendCanvas');
  const topicBarContainer = document.getElementById('topicPerformanceChart');

  if (!scoreTrendCanvas && !topicBarContainer) return;

  const authToken = getAuthToken();
  const url = authToken ? `/api/analytics/charts?_st=${encodeURIComponent(authToken)}` : '/api/analytics/charts';

  fetch(url, {
    headers: { 'X-Auth-Token': authToken }
  })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((data) => {
      cachedChartData = data;
      drawCharts(data);
      window.addEventListener('resize', () => {
        if (cachedChartData) drawCharts(cachedChartData);
      });
    })
    .catch((err) => {
      console.warn('Analytics charts data fetch notice:', err);
      if (scoreTrendCanvas) {
        renderEmptyChart(scoreTrendCanvas, "Record mock tests to see your score trend line.");
      }
    });
}

function drawCharts(data) {
  const scoreTrendCanvas = document.getElementById('scoreTrendCanvas');
  const topicBarContainer = document.getElementById('topicPerformanceChart');

  if (scoreTrendCanvas) {
    if (data.tests_timeline && data.tests_timeline.length > 0) {
      renderScoreTrendChart(scoreTrendCanvas, data.tests_timeline);
    } else {
      renderEmptyChart(scoreTrendCanvas, "Log your first practice test to visualize score trends over time.");
    }
  }

  if (topicBarContainer && data.topic_performance && data.topic_performance.length > 0) {
    renderTopicBars(topicBarContainer, data.topic_performance);
  }
}

function renderScoreTrendChart(canvas, tests) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return;

  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const width = rect.width;
  const height = rect.height;
  const padding = { top: 25, right: 30, bottom: 40, left: 45 };

  ctx.clearRect(0, 0, width, height);

  const yTicks = [0, 25, 50, 75, 100];
  ctx.strokeStyle = '#f1f5f9';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#94a3b8';
  ctx.font = '11px JetBrains Mono, monospace';

  yTicks.forEach((tick) => {
    const y = height - padding.bottom - (tick / 100) * (height - padding.top - padding.bottom);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();

    ctx.textAlign = 'right';
    ctx.fillText(`${tick}%`, padding.left - 10, y + 4);
  });

  const graphWidth = width - padding.left - padding.right;
  const graphHeight = height - padding.top - padding.bottom;
  const stepX = tests.length > 1 ? graphWidth / (tests.length - 1) : graphWidth / 2;

  const points = tests.map((t, idx) => {
    const x = tests.length === 1 ? padding.left + graphWidth / 2 : padding.left + idx * stepX;
    const pct = Math.max(0, Math.min(100, Number(t.percentage) || 0));
    const y = height - padding.bottom - (pct / 100) * graphHeight;
    return { x, y, pct, test: t };
  });

  if (points.length > 0) {
    const gradient = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
    gradient.addColorStop(0, 'rgba(79, 70, 229, 0.18)');
    gradient.addColorStop(1, 'rgba(79, 70, 229, 0.01)');

    ctx.beginPath();
    ctx.moveTo(points[0].x, height - padding.bottom);
    points.forEach((p) => ctx.lineTo(p.x, p.y));
    ctx.lineTo(points[points.length - 1].x, height - padding.bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach((p) => ctx.lineTo(p.x, p.y));
    ctx.strokeStyle = '#4f46e5';
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.stroke();

    points.forEach((p) => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
      ctx.strokeStyle = '#4f46e5';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Percentage label above point
      ctx.fillStyle = '#0f172a';
      ctx.font = '600 10px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`${p.pct}%`, p.x, p.y - 10);

      // Date label below axis
      ctx.fillStyle = '#64748b';
      ctx.font = '11px JetBrains Mono, monospace';
      const shortDate = p.test.test_date ? p.test.test_date.slice(5) : '';
      ctx.fillText(shortDate, p.x, height - padding.bottom + 18);
    });
  }
}

function renderEmptyChart(canvas, message) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return;

  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = '#94a3b8';
  ctx.font = '13px Inter, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(message, rect.width / 2, rect.height / 2);
}

function renderTopicBars(container, topics) {
  container.innerHTML = '';
  topics.forEach((t) => {
    const row = document.createElement('div');
    row.style.marginBottom = '14px';

    const header = document.createElement('div');
    header.style.cssText = 'display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 5px;';
    header.innerHTML = `
      <span style="font-weight: 500; color: #0f172a;">${t.category}</span>
      <span class="mono-metric" style="color: ${t.avg_score >= 80 ? '#16a34a' : t.avg_score >= 60 ? '#4f46e5' : '#e11d48'}; font-weight: 600;">
        ${t.avg_score}% <span style="font-size: 11px; font-weight: 400; color: #64748b;">(${t.count} test${t.count === 1 ? '' : 's'})</span>
      </span>
    `;

    const barBg = document.createElement('div');
    barBg.className = 'progress-bar-bg';
    barBg.style.height = '7px';

    const fill = document.createElement('div');
    fill.className = 'progress-bar-fill';
    fill.style.width = `${Math.min(100, t.avg_score)}%`;
    fill.style.backgroundColor = t.avg_score >= 80 ? '#10b981' : t.avg_score >= 60 ? '#4f46e5' : '#f59e0b';
    barBg.appendChild(fill);

    row.appendChild(header);
    row.appendChild(barBg);
    container.appendChild(row);
  });
}

function initNotifications() {
  const bell = document.getElementById('notifBellBtn');
  const dropdown = document.getElementById('notifDropdown');
  const markRead = document.getElementById('markReadBtn');
  const badge = document.getElementById('notifBadgeCount');

  if (!bell || !dropdown) return;

  bell.addEventListener('click', (e) => {
    e.stopPropagation();
    const isShown = dropdown.style.display === 'block';
    dropdown.style.display = isShown ? 'none' : 'block';
  });

  document.addEventListener('click', (e) => {
    if (!dropdown.contains(e.target) && e.target !== bell) {
      dropdown.style.display = 'none';
    }
  });

  if (markRead) {
    markRead.addEventListener('click', () => {
      const csrf = getCsrfToken();
      fetch('/api/notifications/mark-read', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrf
        }
      }).then(() => {
        if (badge) badge.style.display = 'none';
        document.querySelectorAll('#notifDropdown div[style*="background: rgb(240, 253, 250)"]').forEach(el => {
          el.style.background = 'transparent';
          el.style.borderLeftColor = '#e2e8f0';
        });
      }).catch(() => {});
    });
  }
}
