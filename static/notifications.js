// ============================================
// Система уведомлений — общая для всех страниц
// ============================================

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
        item.innerHTML =
            '<div class="notif-title">' + escapeHtml(a.title) + '</div>' +
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
    
    // Загружаем сообщения
    loadDialogMessages();
    
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

function loadDialogMessages() {
    if (!currentDialogId) return;
    
    fetch('/api/dialogs/' + currentDialogId)
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
        headers: {'Content-Type': 'application/json'},
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
