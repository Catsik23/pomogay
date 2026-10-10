// ============================================
// Система уведомлений — общая для всех страниц
// ============================================

function loadState() {
    fetch('/api/state')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            renderNotifications(data.actions || []);
            updateDots(data.actions || []);
        })
        .catch(function() {});
}

function getDismissed() {
    try {
        return JSON.parse(localStorage.getItem('dismissed_notifs') || '[]');
    } catch (e) { return []; }
}

function dismissNotification(a) {
    var key = a.type + ':' + (a.action_url || '');
    var dismissed = getDismissed();
    if (dismissed.indexOf(key) === -1) {
        dismissed.push(key);
        localStorage.setItem('dismissed_notifs', JSON.stringify(dismissed));
    }
}

function filterDismissed(actions) {
    var dismissed = getDismissed();
    return actions.filter(function(a) {
        var key = a.type + ':' + (a.action_url || '');
        return dismissed.indexOf(key) === -1;
    });
}

function renderNotifications(actions) {
    var list = document.getElementById('notifList');
    var count = document.getElementById('notifCount');
    var dot = document.getElementById('hamburgerDot');

    if (!list) return;

    actions = filterDismissed(actions);

    if (!actions.length) {
        list.innerHTML = '<div class="notif-empty">Пока тихо</div>';
        if (count) count.classList.remove('active');
        if (dot) dot.classList.remove('active');
        return;
    }

    list.innerHTML = '';
    actions.forEach(function(a) {
        var item = document.createElement('a');
        item.className = 'notif-item ' + (a.type || '');
        item.href = a.action_url;
        item.innerHTML =
            '<div class="notif-title">' + escapeHtml(a.title) + '</div>' +
            '<div class="notif-message">' + escapeHtml(a.message) + '</div>';
        item.addEventListener('click', function() {
            dismissNotification(a);
        });
        list.appendChild(item);
    });

    if (count) {
        count.textContent = actions.length;
        count.classList.add('active');
    }
    if (dot) {
        dot.textContent = actions.length > 9 ? '9+' : actions.length;
        dot.classList.add('active');
    }
}

function updateDots(actions) {
    var hasDonations = actions.some(function(a) { return a.type === 'donation'; });
    var dotReceived = document.getElementById('dotReceived');
    if (dotReceived) {
        dotReceived.classList.toggle('active', hasDonations);
    }
}

function escapeHtml(s) {
    if (!s) return '';
    return String(s).replace(/[&<>"']/g, function(c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}

// Автозагрузка при старте
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('hamburgerDot')) {
        loadState();
    }
});

// Обновление при возврате через bfcache
window.addEventListener('pageshow', function(e) {
    if (e.persisted && document.getElementById('hamburgerDot')) {
        loadState();
    }
});

// Обновление при возврате во вкладку
document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'visible' && document.getElementById('hamburgerDot')) {
        loadState();
    }
});
