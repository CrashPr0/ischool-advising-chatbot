/* Move existing controls, never clone them: their listeners and state stay intact. */
(() => {
  const card = document.querySelector('.chat-card');
  const sidebar = document.querySelector('.left-panel');
  if (!card || !sidebar) return;
  const media = window.matchMedia('(max-width: 860px)');
  const moved = [];
  const labels = [];
  let topics, options;
  function move(node, destination) {
    if (!node) return;
    const anchor = document.createComment('mobile-control-position');
    node.before(anchor);
    moved.push({ node, anchor });
    destination.append(node);
  }
  function disclosure(label, className) {
    const details = document.createElement('details');
    details.className = className;
    const summary = document.createElement('summary');
    summary.textContent = label;
    details.append(summary);
    const body = document.createElement('div');
    body.className = 'mobile-disclosure-body';
    details.append(body);
    return { details, body, summary };
  }
  function programLabel() {
    if (!options) return;
    const value = document.getElementById('program-select')?.value;
    const names = { isda: 'BS ISDA', mlis: 'MLIS', mara: 'MARA', msis: 'MSIS', both: 'Choose program' };
    options.summary.textContent = `${names[value] || 'Choose program'} · Chat options`;
  }
  function enter() {
    if (topics) return;
    [['#chat-heading', 'Advising chat'], ['.empty-state h3', 'What can we help with?'], ['.empty-lead', 'Explore BS ISDA and MLIS courses, registration, and graduation. Choose a question below or write your own.']].forEach(([selector, text]) => {
      const node = card.querySelector(selector);
      if (node) { labels.push({ node, text: node.textContent }); node.textContent = text; }
    });
    topics = disclosure('Explore topics & resources', 'mobile-topics');
    options = disclosure('Program & chat options', 'mobile-options');
    sidebar.before(topics.details);
    [...sidebar.children].forEach(node => move(node, topics.body));
    card.querySelector('.chat-header').after(options.details);
    ['.program-control', '.composer > .advisor-handoff', '#voice-tools', '#export-chat-button', '.chat-disclaimer', '.scope-notice'].forEach(selector => move(card.querySelector(selector), options.body));
    topics.details.addEventListener('toggle', () => { if (topics?.details.open) options.details.open = false; });
    options.details.addEventListener('toggle', () => { if (options?.details.open) topics.details.open = false; });
    topics.body.addEventListener('click', event => {
      if (event.target.closest('.topic-btn')) {
        topics.details.open = false;
        topics.summary.focus();
      }
    });
    document.documentElement.classList.add('mobile-chat-layout');
    programLabel();
  }
  function leave() {
    if (!topics) return;
    // Restore in reverse order, including nodes originally nested in other nodes.
    moved.reverse().forEach(({node, anchor}) => { anchor.replaceWith(node); });
    moved.length = 0;
    labels.forEach(({node, text}) => { node.textContent = text; });
    labels.length = 0;
    topics.details.remove(); options.details.remove();
    topics = options = null;
    document.documentElement.classList.remove('mobile-chat-layout');
    document.documentElement.style.removeProperty('--mobile-viewport-height');
  }
  function viewport() {
    if (!media.matches) return;
    // visualViewport follows the on-screen keyboard on iOS and Android.
    const height = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty('--mobile-viewport-height', `${Math.round(height)}px`);
  }
  function sync() { media.matches ? enter() : leave(); viewport(); }
  media.addEventListener('change', sync);
  window.visualViewport?.addEventListener('resize', viewport);
  window.addEventListener('resize', viewport);
  document.getElementById('program-select')?.addEventListener('change', programLabel);
  document.getElementById('chat-tabs')?.addEventListener('click', () => queueMicrotask(programLabel));
  document.getElementById('clear-button')?.addEventListener('click', () => { if (options) options.details.open = false; queueMicrotask(programLabel); });
  document.getElementById('user-input')?.addEventListener('focus', () => {
    if (topics) topics.details.open = false;
    if (options) options.details.open = false;
  });
  sync();
})();
