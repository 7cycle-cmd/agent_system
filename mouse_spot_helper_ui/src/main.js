import './style.css';
import { renderStep1 } from './step1.js';
import { renderStep2 } from './step2.js';

const app = document.getElementById('app');

function render() {
  app.innerHTML = '';
  const step = localStorage.getItem('msh_step') || '1';
  if (step === '2') {
    renderStep2(app);
  } else {
    renderStep1(app);
  }
}

window.addEventListener('storage', (e) => {
  if (e.key === 'msh_step') render();
});

render();
