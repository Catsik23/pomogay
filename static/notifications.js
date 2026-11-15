var currentNotifTab = 'new';

function switchNotifTab(tab) {
    currentNotifTab = tab;
    var tabs = document.querySelectorAll('.notif-tab');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].classList.toggle('active', tabs[i].dataset.tab === tab);
    }
    if (tab === 'new') {
        loadState();
    } else {
        loadAllNotifications();
    }
}

function loadAllNotifications() {
    fetch('/api/notifications')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var items = (data && data.items) || [];
            var list = document.getElementById('notifList');
            var dot = document.getElementById('hamburgerDot');
            var count = document.getElementById('notifCount');
            if (!list) return;
            
            if (!items.length) {
                list.innerHTML = '<div class="notif-empty">Пока тихо</div>';
                return;
            }
            
            list.innerHTML = '';
            items.forEach(function(n) {
                var item = document.createElement('a');
                item.className = 'notif-item ' + (n.type || '');
                item.href = n.action_url || '#';
                item.innerHTML = 
                    '<div class="notif-title">' + escapeHtml(n.title) + '</div>' +
                    '<div class="notif-message">' + escapeHtml(n.message || '') + '</div>';
                list.appendChild(item);
            });
        })
        .catch(function() {});
}

// ============================================

function getCsrfToken() {
    // 1. Из meta-тега (если есть)
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    // 2. Из скрытого input (на любой странице)
    var input = document.querySelector('input[name="csrf_token"]');
    if (input) return input.value;
    return '';
}

// Очистка старого localStorage (миграция на sessionStorage)
try { localStorage.removeItem('dismissed_notifs'); } catch (e) {}

// Очистка dialog-записей из dismissed (не должны там быть)
try {
    var _d = JSON.parse(sessionStorage.getItem('dismissed_notifs') || '[]');
    var _clean = _d.filter(function(k) { return k.indexOf('dialog:') !== 0; });
    if (_clean.length !== _d.length) {
        sessionStorage.setItem('dismissed_notifs', JSON.stringify(_clean));
    }
} catch (e) {}

function loadState() {
    // Если открыта вкладка "Все" — не перезаписываем её
    if (currentNotifTab === 'all') return;
    
    fetch('/api/state')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var actions = data.actions || [];
            renderNotifications(actions);
            updateDots(filterDismissed(actions));
        })
        .catch(function() {});
}

function getDismissed() {
    try {
        var arr = JSON.parse(sessionStorage.getItem('dismissed_notifs') || '[]');
        return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
}

function dismissNotification(a) {
    var key = a.type + ':' + (a.action_url || '');
    var dismissed = getDismissed();
    if (dismissed.indexOf(key) === -1) {
        dismissed.push(key);
        // Ограничиваем до последних 50 записей
        if (dismissed.length > 50) {
            dismissed = dismissed.slice(-50);
        }
        sessionStorage.setItem('dismissed_notifs', JSON.stringify(dismissed));
    }
}

function filterDismissed(actions) {
    var dismissed = getDismissed();
    return actions.filter(function(a) {
        // Диалоги НИКОГДА не скрываются через dismissed
        if (a.type === 'dialog') return true;
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
        if (a.type === 'dialog') {
            item.classList.add('dialog-item');
        }
        item.href = a.action_url || '#';
        var titleHtml = '<div class="notif-title">' + escapeHtml(a.title);
        if (a.type === 'dialog' && a.unread_count && a.unread_count > 0) {
            titleHtml += ' <span class="notif-badge">' + a.unread_count + '</span>';
        }
        titleHtml += '</div>';
        item.innerHTML = titleHtml +
            '<div class="notif-message">' + escapeHtml(a.message) + '</div>';
        item.addEventListener('click', function(e) {
            if (a.type === 'dialog') {
                e.preventDefault();
                var dialogId = (a.action_url || '').split('/').pop();
                openDialogChat(dialogId, a.title);
                return;  // dialog НЕ dismiss — управляется через openDialogChat/closeDialogChat
            }
            dismissNotification(a);
        });
        list.appendChild(item);
    });

    // Считаем непрочитанные: dialog — по unread_count, остальные — по 1
    var totalUnread = 0;
    actions.forEach(function(a) {
        if (a.type === 'dialog') {
            totalUnread += (a.unread_count || 0);
        } else {
            totalUnread += 1;
        }
    });

    if (count) {
        count.textContent = totalUnread;
        count.classList.toggle('active', totalUnread > 0);
    }
    if (dot) {
        dot.textContent = totalUnread > 0 ? '1' : '';
        dot.classList.toggle('active', totalUnread > 0);
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
        // Polling каждые 5 секунд
        setInterval(function() {
            if (document.visibilityState === 'visible') {
                loadState();
            }
        }, 5000);
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

// ============================================
// Встроенный чат диалога в sidebar
// ============================================

var currentDialogId = null;
var dialogPollTimer = null;

function openDialogChat(dialogId, subject) {
    currentDialogId = dialogId;
    document.getElementById('dialogChatTitle').textContent = subject || 'Диалог';
    
    // Скрываем уведомления, показываем чат
    var notifPanel = document.getElementById('notifPanel');
    var notifToggle = document.querySelector('.sidebar-notif-toggle');
    if (notifPanel) notifPanel.style.display = 'none';
    if (notifToggle) notifToggle.style.display = 'none';
    
    document.getElementById('dialogChat').style.display = 'flex';
    
    // Автоувеличение textarea
    var _ci = document.getElementById('dialogChatInput');
    if (_ci && !_ci._resizeAttached) {
        _ci._resizeAttached = true;
        _ci.addEventListener('input', function() {
            this.style.height = '40px';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
        });
    }
    
    // Загружаем сообщения с пометкой прочитанным
    loadDialogMessages(true);
    
    // Запускаем polling каждые 5 секунд
    if (dialogPollTimer) clearInterval(dialogPollTimer);
    dialogPollTimer = setInterval(loadDialogMessages, 5000);
}

function closeDialogChat() {
    currentDialogId = null;
    document.getElementById('dialogChat').style.display = 'none';
    
    // Показываем уведомления обратно
    var notifPanel = document.getElementById('notifPanel');
    var notifToggle = document.querySelector('.sidebar-notif-toggle');
    if (notifPanel) notifPanel.style.display = '';
    if (notifToggle) notifToggle.style.display = '';
    
    // Останавливаем polling
    if (dialogPollTimer) {
        clearInterval(dialogPollTimer);
        dialogPollTimer = null;
    }
    
    // Обновляем состояние уведомлений
    loadState();
}

function loadDialogMessages(markRead) {
    if (!currentDialogId) return;
    
    var url = '/api/dialogs/' + currentDialogId + (markRead ? '?mark_read=1' : '');
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) return;
            
            var container = document.getElementById('dialogChatMessages');
            var wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;
            
            container.innerHTML = '';
            data.messages.forEach(function(m) {
                var div = document.createElement('div');
                div.className = 'chat-msg ' + (m.sender_type === 'admin' ? 'admin' : 'user');
                div.innerHTML = escapeHtml(m.message) + 
                    '<div class="chat-msg-time">' + (m.created_at || '').slice(11, 16) + '</div>';
                container.appendChild(div);
            });
            
            if (wasAtBottom) {
                container.scrollTop = container.scrollHeight;
            }
        })
        .catch(function() {});
}

function sendDialogMessage() {
    if (!currentDialogId) return;
    
    var input = document.getElementById('dialogChatInput');
    var text = input.value.trim();
    if (!text) return;
    
    input.value = '';
    
    fetch('/api/dialogs/' + currentDialogId + '/send', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({message: text})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (!data.ok) {
            showToast(data.error || 'Ошибка', 'danger');
            input.value = text;
            return;
        }
        loadDialogMessages();
    })
    .catch(function() {
        showToast('Ошибка сети', 'danger');
        input.value = text;
    });
}

// Автоувеличение textarea чата
document.addEventListener('DOMContentLoaded', function() {
    var chatInput = document.getElementById('dialogChatInput');
    if (chatInput) {
        chatInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
        });
    }
});
