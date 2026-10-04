(() => {
  const root = document.querySelector('[data-dimension-research]');
  if (!root) return;
  const form = root.querySelector('#dimensionForm');
  const tabs = [...root.querySelectorAll('[data-dimension]')];
  let mode = 'color';
  function render() {
    root.querySelectorAll('[data-dimension-form]').forEach(block => {
      block.hidden = block.dataset.dimensionForm !== form.value;
      block.querySelectorAll('[data-dimension-panel]').forEach(panel => {
        panel.hidden = panel.dataset.dimensionPanel !== mode;
      });
    });
    tabs.forEach(tab => {
      const selected = tab.dataset.dimension === mode;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) root.querySelector('#dimension-panel').setAttribute('aria-labelledby', tab.id);
    });
  }
  form.addEventListener('change', render);
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => { mode = tab.dataset.dimension; render(); });
    tab.addEventListener('keydown', event => {
      const next = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      tabs[next].click();
      tabs[next].focus();
    });
  });
  render();
})();
