import { init } from './pages/shop.js';

const response = await fetch('pages/shop.html');
document.getElementById('content').innerHTML = await response.text();
init();
