document.addEventListener('DOMContentLoaded', function () {
    // Navbar active state
    const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
    const currentPath = window.location.pathname;
    navLinks.forEach(link => {
        const href = link.getAttribute('href');
        if (href && currentPath.startsWith(href) && href !== '/') {
            link.classList.add('active');
        } else if (href === '/' && currentPath === '/') {
            link.classList.add('active');
        }
    });

    // Vehicle gallery thumbnail switcher
    const mainImage = document.getElementById('main-gallery-image');
    const thumbnails = document.querySelectorAll('.thumbnail-list img');
    if (mainImage && thumbnails.length) {
        thumbnails.forEach(thumb => {
            thumb.addEventListener('click', function () {
                mainImage.src = this.dataset.full || this.src;
                thumbnails.forEach(t => t.classList.remove('active'));
                this.classList.add('active');
            });
        });
    }

    // AJAX contact forms
    const ajaxForms = document.querySelectorAll('form[data-ajax]');
    ajaxForms.forEach(form => {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            const submitBtn = form.querySelector('button[type="submit"]');
            const originalText = submitBtn ? submitBtn.innerHTML : '';
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Sending...';
            }

            const formData = new FormData(form);
            fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showAlert(data.message, 'success');
                    form.reset();
                } else {
                    showAlert('Please correct the errors and try again.', 'danger');
                }
            })
            .catch(() => {
                showAlert('Something went wrong. Please call us or try again.', 'danger');
            })
            .finally(() => {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = originalText;
                }
            });
        });
    });

    // Finance calculator
    const calcForm = document.getElementById('finance-calculator');
    if (calcForm) {
        calcForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const price = parseFloat(document.getElementById('calc-price').value) || 0;
            const down = parseFloat(document.getElementById('calc-down').value) || 0;
            const rate = parseFloat(document.getElementById('calc-rate').value) || 0;
            const term = parseInt(document.getElementById('calc-term').value) || 60;

            const principal = price - down;
            const monthlyRate = rate / 100 / 12;
            const payment = monthlyRate === 0
                ? principal / term
                : (principal * monthlyRate) / (1 - Math.pow(1 + monthlyRate, -term));

            const totalCost = payment * term;
            const totalInterest = totalCost - principal;

            document.getElementById('calc-monthly').textContent = '$' + payment.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
            document.getElementById('calc-total').textContent = '$' + totalCost.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
            document.getElementById('calc-interest').textContent = '$' + totalInterest.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
            document.getElementById('calc-results').classList.remove('d-none');
        });
    }
});

function showAlert(message, type) {
    const wrapper = document.createElement('div');
    wrapper.className = `alert alert-${type} alert-dismissible fade show mt-3`;
    wrapper.innerHTML = `${message}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    const container = document.querySelector('main, .container') || document.body;
    container.prepend(wrapper);
    setTimeout(() => {
        const alert = bootstrap.Alert.getOrCreateInstance(wrapper);
        if (alert) alert.close();
    }, 6000);
}
