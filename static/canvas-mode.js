/* Canvas owns its layout; existing chat controls keep their listeners and state. */
(() => {
  if (!document.body.classList.contains('canvas-mode')) return;
  const card = document.querySelector('.chat-card');
  const topics = document.querySelector('.topics-card');
  if (!card || !topics) return;
  document.getElementById('chat-heading').textContent = 'iSchool advising chat';
  card.querySelector('.chat-header').after(topics);

  const toggle = document.getElementById('canvas-topics-toggle');
  const list = document.getElementById('topic-list');
  function closeTopics() {
    list.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }
  toggle.addEventListener('click', () => {
    list.hidden = !list.hidden;
    toggle.setAttribute('aria-expanded', String(!list.hidden));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      closeTopics();
      toggle.focus();
    }
  });
  list.addEventListener('click', event => {
    if (event.target.closest('.topic-btn')) {
      closeTopics();
      toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!topics.contains(event.target)) closeTopics();
  });
  // Tab and Shift+Tab should dismiss the overlay just like an outside click.
  // Keep focus on its new control so keyboard navigation can continue.
  document.addEventListener('focusin', event => {
    if (!topics.contains(event.target)) closeTopics();
  });

  // Reuse the existing reporting flow, resolving the active answer at click time.
  document.getElementById('canvas-report-issue-button').addEventListener('click', event => {
    const conversation = getActiveConversation();
    const messages = conversation?.messages || [];
    let report = null;
    for (let index = messages.length - 1; index >= 0 && !report; index -= 1) {
      report = reportContext(messages, index, conversation.id);
    }
    openReportDialog(event.currentTarget, report || {
      question: 'Canvas interface issue (no question and answer selected)',
      answer: 'No chatbot answer selected.',
      turns: []
    });
    if (!report) {
      const privacy = document.getElementById('report-issue-privacy');
      const original = privacy.textContent;
      privacy.textContent = 'Describe the interface issue in the comment. No chatbot answer is selected. ' + REPORT_RETENTION_NOTICE;
      document.getElementById('report-issue-dialog').addEventListener('close', () => {
        privacy.textContent = original;
      }, { once: true });
    }
  });

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
