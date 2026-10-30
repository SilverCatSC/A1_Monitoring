(() => {
  const search = document.getElementById('vehicle-search');
  const filter = document.getElementById('vehicle-filter');
  if (!search || !filter) return;
  for (const time of document.querySelectorAll('[data-monitor-time]')) {
    const date = new Date(time.dataset.monitorTime);
    if (!Number.isNaN(date.getTime())) {
      time.textContent = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'medium', timeStyle: 'short'}).format(date) + (time.hasAttribute('data-monitor-short') ? '' : ' · время вашего устройства');
      time.title = 'Время вашего устройства';
    }
  }
  const tiles = [...document.querySelectorAll('.vehicle-tile')];
  const tabs = [...document.querySelectorAll('.monitor-tabs [role="tab"]')];
  const panels = [document.getElementById('vehicle-panel'), document.getElementById('marketing-panel')];
  let activateView = () => {};
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const phoneLayout = window.matchMedia('(max-width: 1199px)');
  const breakdown = document.querySelector('.overview-breakdown');
  const compactOverview = document.querySelector('.phone-overview');
  if (breakdown && compactOverview) {
    compactOverview.hidden = false;
    const adaptOverview = () => { breakdown.open = !phoneLayout.matches; };
    adaptOverview();
    phoneLayout.addEventListener('change', adaptOverview);
  }
  if (tabs.length === 2 && panels.every(Boolean)) {
    panels.forEach((panel, index) => {
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tabs[index].id);
    });
    const activate = (index, writeHash = false) => {
      const changed = panels[index].hidden;
      tabs.forEach((tab, i) => {
        tab.setAttribute('aria-selected', String(i === index));
        tab.tabIndex = i === index ? 0 : -1;
        panels[i].hidden = i !== index;
      });
      if (changed) {
        panels[index].classList.add('report-view-enter');
        panels[index].addEventListener('animationend', () => panels[index].classList.remove('report-view-enter'), {once:true});
      }
      if (writeHash) history.replaceState(null, '', index === 1 ? '#marketing-summary' : '#vehicle-panel');
    };
    activateView = activate;
    const followHash = () => {
      activate(['#marketing-summary', '#marketing-panel'].includes(location.hash) ? 1 : 0);
      if (location.hash === '#monitoring-evidence') document.getElementById('monitoring-evidence').open = true;
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(index, true));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') next = 1 - index;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = 1;
        else return;
        event.preventDefault();
        activate(next, true);
        tabs[next].focus();
      });
    });
    document.querySelector('.monitor-tabs').hidden = false;
    followHash();
    window.addEventListener('hashchange', followHash);
  }
  for (const link of document.querySelectorAll('a[href="#monitoring-evidence"]')) {
    link.addEventListener('click', () => { document.getElementById('monitoring-evidence').open = true; });
  }
  const batchSize = 12;
  let visibleLimit = batchSize;
  const pagination = document.querySelector('.vehicle-pagination');
  const more = document.getElementById('vehicle-more');
  const showAll = document.getElementById('vehicle-show-all');
  const apply = () => {
    const term = search.value.trim().toLocaleLowerCase();
    let matched = 0;
    for (const tile of tiles) {
      const kind = filter.value;
      const matches = kind === 'all' ||
        (kind === 'inactive' ? tile.dataset.inactive === 'yes' :
          kind === 'incomplete' ? tile.dataset.incomplete === 'yes' : tile.dataset.status === kind);
      const included = matches && tile.dataset.search.includes(term);
      tile.hidden = !included || matched >= visibleLimit;
      if (included) matched += 1;
    }
    document.getElementById('vehicle-count').textContent = `${matched} из ${tiles.length} записей`;
    document.getElementById('vehicle-empty').hidden = matched !== 0;
    if (pagination) {
      pagination.hidden = matched === 0;
      document.getElementById('vehicle-page-status').textContent = `Показано ${Math.min(visibleLimit, matched)} из ${matched}`;
      more.hidden = showAll.hidden = matched <= visibleLimit;
      more.textContent = `Показать ещё ${Math.min(batchSize, Math.max(0, matched - visibleLimit))}`;
      showAll.textContent = `Показать все (${matched})`;
    }
  };
  const reset = () => { visibleLimit = batchSize; apply(); };
  search.addEventListener('input', reset);
  filter.addEventListener('change', reset);
  const reveal = all => {
    const previouslyVisible = new Set(tiles.filter(tile => !tile.hidden));
    visibleLimit = all ? tiles.length : visibleLimit + batchSize;
    apply();
    const firstNew = tiles.find(tile => !tile.hidden && !previouslyVisible.has(tile));
    if (firstNew) {
      firstNew.tabIndex = -1;
      firstNew.focus({preventScroll:true});
      firstNew.scrollIntoView({behavior:reducedMotion() ? 'instant' : 'smooth', block:'start'});
    }
  };
  more?.addEventListener('click', () => reveal(false));
  showAll?.addEventListener('click', () => reveal(true));
  for (const button of document.querySelectorAll('[data-monitor-action="attention"], [data-monitor-action="all"], [data-monitor-action="inactive"]')) button.addEventListener('click', () => {
    search.value = '';
    filter.value = button.dataset.monitorAction;
    reset();
    activateView(0, true);
    search.focus({preventScroll:true});
    document.querySelector('.monitor-toolbar').scrollIntoView({behavior:reducedMotion() ? 'instant' : 'smooth', block:'start'});
  });
  for (const button of document.querySelectorAll('[data-monitor-action="identity"]')) button.addEventListener('click', () => {
    activateView(1, true);
    const group = document.querySelector('[data-brief-group="identity"]');
    if (group) {
      group.open = true;
      group.querySelector('summary').focus({preventScroll:true});
      group.scrollIntoView({behavior:reducedMotion() ? 'instant' : 'smooth', block:'start'});
    }
  });
  apply();
  for (const img of document.querySelectorAll('.vehicle-photo img')) {
    const unavailable = () => {
      img.hidden = true;
      img.parentElement.classList.add('image-unavailable');
      const label = img.parentElement.querySelector('span');
      if (label) label.textContent = 'Изображение недоступно';
    };
    img.addEventListener('error', unavailable);
    if (img.complete && img.naturalWidth === 0) unavailable();
  }
})();
