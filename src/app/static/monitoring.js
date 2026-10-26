(() => {
  const search = document.getElementById('vehicle-search');
  const filter = document.getElementById('vehicle-filter');
  if (!search || !filter) return;
  for (const time of document.querySelectorAll('[data-monitor-time]')) {
    const date = new Date(time.dataset.monitorTime);
    if (!Number.isNaN(date.getTime())) time.textContent = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'medium', timeStyle: 'short'}).format(date) + ' · время вашего устройства';
  }
  const tiles = [...document.querySelectorAll('.vehicle-tile')];
  const apply = () => {
    const term = search.value.trim().toLocaleLowerCase();
    let shown = 0;
    for (const tile of tiles) {
      const kind = filter.value;
      const matches = kind === 'all' ||
        (kind === 'inactive' ? tile.dataset.inactive === 'yes' :
          kind === 'incomplete' ? tile.dataset.incomplete === 'yes' : tile.dataset.status === kind);
      tile.hidden = !matches || !tile.dataset.search.includes(term);
      if (!tile.hidden) shown += 1;
    }
    document.getElementById('vehicle-count').textContent = `${shown} из ${tiles.length} записей`;
    document.getElementById('vehicle-empty').hidden = shown !== 0;
  };
  search.addEventListener('input', apply);
  filter.addEventListener('change', apply);
  for (const img of document.querySelectorAll('.vehicle-photo img')) {
    img.addEventListener('error', () => {
      img.hidden = true;
      const label = img.parentElement.querySelector('span');
      if (label) label.textContent = 'Изображение недоступно';
    });
  }
})();
