// Toast-уведомления помогай.рф
document.addEventListener('DOMContentLoaded', function() {
    var dataEl = document.getElementById('toast-data');
    if (!dataEl) return;
    
    var messages;
    try {
        messages = JSON.parse(dataEl.textContent);
    } catch(e) {
        return;
    }
    if (!messages || messages.length === 0) return;

    messages.forEach(function(msg) {
        showToast(msg.text, msg.category);
    });
});

// Глобальная функция для вызова тостов из onclick
function showToast(text, category) {
    var container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (category || 'info');
    toast.textContent = text;
    container.appendChild(toast);
    toast.addEventListener('animationend', function(e) {
        if (e.animationName === 'toastOut') {
            toast.remove();
            if (container.children.length === 0) {
                container.remove();
            }
        }
    });
}








// Info-icon: просто и надёжно
function closeAllInfo() {
    document.querySelectorAll('.info-icon.active').forEach(function(el) {
        el.classList.remove('active');
    });
    document.querySelectorAll('.info-popup.active').forEach(function(el) {
        el.classList.remove('active');
    });
}

document.addEventListener('click', function(e) {
    var icon = e.target.closest('.info-icon');
    if (icon) {
        e.preventDefault();
        e.stopPropagation();
        var wasActive = icon.classList.contains('active');
        closeAllInfo();
        if (!wasActive) {
            icon.classList.add('active');
            var popup = icon.nextElementSibling;
            if (popup && popup.classList.contains('info-popup')) {
                popup.classList.add('active');
            }
        }
    } else {
        closeAllInfo();
    }
}, true);
