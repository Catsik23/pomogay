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

    var container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);

    var timings = {
        'success': 3000,
        'info': 3000,
        'warning': 5000,
        'danger': 6000
    };

    messages.forEach(function(msg) {
        var toast = document.createElement('div');
        toast.className = 'toast toast-' + msg.category;
        toast.textContent = msg.text;
        container.appendChild(toast);

        // Удаляем после окончания CSS-анимации
        toast.addEventListener('animationend', function(e) {
            if (e.animationName === 'toastOut') {
                toast.remove();
                // Убираем контейнер, если он пуст
                if (container.children.length === 0) {
                    container.remove();
                }
            }
        });
    });
});