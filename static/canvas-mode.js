/* Canvas owns its layout; existing chat controls keep their listeners and state. */
(() => {
  if (!document.body.classList.contains('canvas-mode')) return;
  const card = document.querySelector('.chat-card');
  const topics = document.querySelector('.topics-card');
  if (!card || !topics) return;
  document.getElementById('chat-heading').textContent = 'iSchool advising chat';
  card.querySelector('.chat-header').after(topics);

  const options = document.createElement('details');
  options.className = 'canvas-options';
  const summary = document.createElement('summary');
  summary.textContent = 'Resources & chat settings';
  options.append(summary);
  ['.links-card', '.status-card', '#voice-tools', '.scope-notice', '.chat-disclaimer'].forEach(selector => {
    const node = document.querySelector(selector);
    if (node) options.append(node);
  });
  card.querySelector('.composer-footer').append(options);

  function viewport() {
    const height = window.visualViewport?.height || window.innerHeight;
    document.body.style.setProperty('--canvas-height', `${Math.round(height)}px`);
  }
  window.visualViewport?.addEventListener('resize', viewport);
  window.addEventListener('resize', viewport);
  document.getElementById('user-input')?.addEventListener('focus', () => { options.open = false; });
  document.getElementById('clear-button')?.addEventListener('click', () => { options.open = false; });
  viewport();
})();
