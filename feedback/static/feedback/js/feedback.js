(function () {
    var liked = document.getElementById('id_liked_most');
    var count = document.getElementById('likedCount');
    if (liked && count) {
        var updateCount = function () { count.textContent = liked.value.length; };
        liked.addEventListener('input', updateCount);
        updateCount();
    }

    var form = document.getElementById('feedbackForm');
    if (form) {
        form.addEventListener('submit', function (event) {
            var rating = form.querySelector('input[name="rating"]:checked');
            if (!rating) {
                event.preventDefault();
                var group = form.querySelector('.fb-rating');
                group.scrollIntoView({ behavior: 'smooth', block: 'center' });
                group.setAttribute('aria-invalid', 'true');
                group.animate([
                    { transform: 'translateX(0)' }, { transform: 'translateX(-5px)' },
                    { transform: 'translateX(5px)' }, { transform: 'translateX(0)' }
                ], { duration: 260 });
            } else {
                var button = form.querySelector('.fb-submit');
                button.disabled = true;
                button.querySelector('span').textContent = 'Sending…';
            }
        });
    }
}());
