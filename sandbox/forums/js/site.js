import { init as initForums } from './pages/forums.js';
import { init as initThread } from './pages/forum_thread.js';

const content = document.getElementById('content');

async function loadPage(path) {
    const threadId = new URL(location.href).searchParams.get('thread');
    const fragment = threadId ? 'pages/forum_thread.html' : 'pages/forums.html';
    const response = await fetch(fragment);
    content.innerHTML = await response.text();
    if (threadId) {
        document.body.dataset.threadId = threadId;
        initThread();
    } else {
        initForums();
    }
}

document.addEventListener('click', event => {
    const link = event.target.closest('a[data-thread-id], a[data-route]');
    if (!link) return;
    event.preventDefault();
    const route = link.dataset.threadId ? `/?thread=${link.dataset.threadId}` : link.dataset.route;
    history.pushState({}, '', route);
    loadPage(route);
});

window.addEventListener('popstate', () => loadPage(location.pathname));
loadPage(location.pathname);
