(() => {
  const root = document.getElementById('supplier-screening');
  if (!root) return;
  const cards = [...root.querySelectorAll('.supplier-card')];
  const grid = root.querySelector('.supplier-grid');
  const controls = Object.fromEntries([...root.querySelectorAll('[data-filter]')].map(el => [el.dataset.filter, el]));
  const key = `amz:supplier-shortlist:${root.dataset.asin}`;
  let selected = new Set();
  let storageAvailable = true;
  const saveState = root.querySelector('.supplier-save-state');
  try {
    const data = JSON.parse(localStorage.getItem(key) || '[]');
    if (Array.isArray(data)) selected = new Set(data.filter(id => typeof id === 'string' && /^\d+$/.test(id)));
  } catch (_) { storageAvailable = false; }
  const numeric = (card, field) => card.dataset[field] === '' ? null : Number(card.dataset[field]);
  function render() {
    const search = controls.search.value.trim().toLocaleLowerCase();
    let visible = 0;
    const sort = controls.sort.value;
    const ordered = [...cards].sort((a, b) => {
      const av = numeric(a, sort), bv = numeric(b, sort);
      if (av === null && bv !== null) return 1;
      if (bv === null && av !== null) return -1;
      return ((av === null ? 0 : av - bv) * (['rank', 'price'].includes(sort) ? 1 : -1)) || Number(a.dataset.rank) - Number(b.dataset.rank);
    });
    for (const card of ordered) {
      const d = card.dataset;
      const match = controls.match.value;
      let show = d.search.toLocaleLowerCase().includes(search)
        && (!match || (match === 'eligible' ? d.match !== 'excluded' : d.match === match))
        && (!controls.pack.value || d.pack === controls.pack.value)
        && (!controls.factory.value || d.factory === controls.factory.value)
        && (!controls.selected.checked || selected.has(d.id));
      for (const field of ['price', 'repurchase', 'rating']) {
        const control = controls[field];
        if (control.value !== '' && control.validity.valid) {
          const value = numeric(card, field);
          show = show && value !== null && (field === 'price' ? value <= Number(control.value) : value >= Number(control.value));
        }
      }
      card.hidden = !show;
      if (show) visible += 1;
      grid.appendChild(card);
      card.querySelector('[data-supplier-pick]').checked = selected.has(d.id);
    }
    const currentSelected = cards.filter(c => selected.has(c.dataset.id)).length;
    root.querySelector('[data-supplier-count]').textContent = `显示 ${visible} / ${cards.length} 条 · 已选 ${currentSelected} 条`;
    root.querySelector('[data-supplier-empty]').hidden = visible > 0 || cards.length === 0;
    saveState.textContent = storageAvailable ? '候选清单保存在当前浏览器，未向供应商发送任何信息。' : '浏览器存储不可用，当前选择仅在本页保留。';
  }
  for (const control of Object.values(controls)) {
    control.addEventListener('input', render);
    control.addEventListener('change', render);
  }
  root.querySelectorAll('[data-supplier-pick]').forEach(input => input.addEventListener('change', () => {
    if (input.checked) selected.add(input.dataset.supplierPick);
    else selected.delete(input.dataset.supplierPick);
    try { localStorage.setItem(key, JSON.stringify([...selected])); storageAvailable = true; }
    catch (_) { storageAvailable = false; }
    render();
  }));
  root.querySelector('[data-supplier-reset]').addEventListener('click', () => {
    Object.entries(controls).forEach(([field, control]) => {
      if (control.type === 'checkbox') control.checked = false;
      else control.value = field === 'match' ? 'eligible' : field === 'sort' ? 'rank' : '';
    });
    render();
  });
  root.querySelectorAll('.supplier-photo img').forEach(img => {
    const missing = () => {
      const label = document.createElement('span');
      label.className = 'supplier-no-image';
      label.textContent = '图片暂不可用';
      img.replaceWith(label);
    };
    img.addEventListener('error', missing, {once: true});
    if (img.complete && !img.naturalWidth) missing();
  });
  if (window.lucide) window.lucide.createIcons({root});
  render();
})();
