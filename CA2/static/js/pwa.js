// PWA glue, loaded on every page: registers the service worker, powers the "Install app"
// button and shows a banner while the device is offline.
(() => {
    'use strict';

    // 1. Service worker (served from /sw.js so that it covers the whole site)
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js').catch((error) => {
                console.warn('Service worker registration failed:', error);
            });
        });
    }

    // 2. Install button: browsers fire beforeinstallprompt when the app is installable.
    const installButton = document.getElementById('install-btn');
    let installPrompt = null;
    window.addEventListener('beforeinstallprompt', (event) => {
        event.preventDefault();               // hold the prompt until the user clicks our button
        installPrompt = event;
        if (installButton) installButton.hidden = false;
    });
    if (installButton) {
        installButton.addEventListener('click', async () => {
            if (!installPrompt) return;
            installPrompt.prompt();
            await installPrompt.userChoice;
            installPrompt = null;
            installButton.hidden = true;
        });
    }
    window.addEventListener('appinstalled', () => { if (installButton) installButton.hidden = true; });

    // 3. Offline banner
    const banner = document.createElement('div');
    banner.className = 'offline-banner';
    banner.setAttribute('role', 'status');
    banner.textContent = "You're offline. Showing saved data where available.";
    banner.hidden = navigator.onLine;
    document.body.prepend(banner);
    window.addEventListener('offline', () => { banner.hidden = false; });
    window.addEventListener('online', () => { banner.hidden = true; });
})();
