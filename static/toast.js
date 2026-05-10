
document.addEventListener('DOMContentLoaded', function() {
    var flashes = document.querySelectorAll('.flash');
    if (flashes.length === 0) return;

    var container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);

    flashes.forEach(function(flash) {
        var type = 'success';
        if (flash.classList.contains('flash-danger')) type = 'danger';
        else if (flash.classList.contains('flash-info')) type = 'info';
        else if (flash.classList.contains('flash-warning')) type = 'warning';

        var toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.textContent = flash.textContent.trim();
        container.appendChild(toast);

        flash.remove();

        setTimeout(function() { toast.remove(); }, 3000);
    });
});
