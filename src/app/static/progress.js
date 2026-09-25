// Read-only progress view. Starting a live scan remains an explicit local action.
(() => {
  const label = {idle:'Не запущена',starting:'Запуск',preparing:'Обновление реестра',reconciling:'Сверка каталога',link_sync:'Актуализация ссылок',running:'Выполняется',waiting_captcha:'Требуется действие',completed:'Завершена',partial:'Частично',failed:'Сбой',unavailable:'Нет связи'};
  const source = s => ({auto_ru:'Auto.ru',avito:'Avito'})[s] || '';
  const eventLine = e => {
    const stamp = e.at ? new Date(e.at).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow'}) : '';
    let message = '';
    switch(e.event) {
      case 'dealer_preflight_started': message = 'Проверяем каталоги продавца до поискового мониторинга'; break;
      case 'dealer_catalogue_started': message = `${source(e.source)} · каталог продавца · пауза ${e.wait_seconds} с`; break;
      case 'dealer_catalogue_finished': message = `${source(e.source)} · кандидатов ${e.candidates} · ${e.complete ? 'каталог пройден' : 'каталог просмотрен не полностью'}`; break;
      case 'dealer_link_check': message = `${source(e.source)} · проверка старой ссылки · пауза ${e.wait_seconds} с`; break;
      case 'dealer_preflight_finished': message = 'Сверка ссылок завершена. Неоднозначные связи переданы на подтверждение.'; break;
      case 'link_preflight_started': message = 'Сопоставляем unique_id и актуализируем однозначные ссылки до поиска'; break;
      case 'link_sync_finished': message = `Ссылок обновлено: ${e.updated || 0}; требуют разбора: ${e.blocked || 0}`; break;
      case 'source_refresh_started': message = 'Обновление реестра из таблицы'; break;
      case 'source_refresh_finished': message = `Реестр обновлён: ${e.rows_valid} из ${e.rows_total} строк`; break;
      case 'source_refresh_failed': case 'cycle_failed': message = `Сбой: ${e.error || ''}`; break;
      case 'filter_started': message = `${source(e.source)} · ${e.filter_name}`; break;
      case 'filter_wait': case 'page_wait': message = `Пауза ${e.wait_seconds} с`; break;
      case 'filter_retry': message = `Вкладка закрыта. Повтор через ${e.retry_seconds} с`; break;
      case 'page_started': message = `${source(e.source)} · загрузка страницы ${e.page}/${e.pages_total}`; break;
      case 'page_finished': message = `Страница ${e.page}: ${e.cards || 0} карточек, ${e.target_cards || 0} снимков своих объявлений`; break;
      case 'page_failed': message = `Страница ${e.page}: ${e.error || 'сбой'}`; break;
      case 'captcha_refresh': message = `${source(e.source)} · CAPTCHA: одно обычное обновление`; break;
      case 'captcha_operator_required': message = `${source(e.source)} · пройдите CAPTCHA в открытом Chrome (${e.wait_seconds} с)`; break;
      case 'captcha_operator_resolved': message = `${source(e.source)} · CAPTCHA пройдена, продолжаем`; break;
      case 'captcha_operator_unresolved': message = `${source(e.source)} · CAPTCHA не снята, проверка остаётся незавершённой`; break;
      case 'filter_finished': message = e.status === 'technical_error' ? `${e.filter_name}: проверка не удалась · ${e.error || 'технический сбой'}` : `${e.filter_name}: найдено ${e.found || 0} из ${e.expected || 0}${e.links_rejected ? ' · ссылок пропущено: ' + e.links_rejected : ''}`; break;
      case 'filter_skipped': message = `${e.filter_name}: пропущен`; break;
      case 'cycle_finished': message = 'Проверка завершена'; break;
    }
    return message ? `${stamp} · ${message}` : '';
  };
  async function refresh() {
    try {
      const response = await fetch('/api/v1/status/scans/progress',{cache:'no-store'});
      if(!response.ok) throw new Error('HTTP ' + response.status);
      const d = await response.json(), c = d.current || {};
      const staleAfter = d.status === 'waiting_captcha' ? (Number(c.wait_seconds || 180) + 30) * 1000 : 180000;
      const stale = ['running','preparing','reconciling','link_sync','waiting_captcha'].includes(d.status) && Date.now() - Date.parse(d.updated_at) > staleAfter;
      const total = Number(d.total_filters || 0), done = Number(d.completed_filters || 0);
      const percent = total ? Math.min(100,Math.round(done / total * 100)) : 0;
      document.querySelector('#scan-progress-status').textContent = stale ? 'Нет свежего сигнала' : (label[d.status] || d.status);
      const warning = stale || ['partial','failed','unavailable','waiting_captcha'].includes(d.status);
      document.querySelector('#scan-progress-status').className = 'badge ' + (warning ? 'technical_error' : d.status === 'completed' ? 'found' : '');
      document.querySelector('#scan-progress-bar').style.width = percent + '%';
      document.querySelector('#scan-progress-bar').style.backgroundColor = warning ? '#b58b38' : '#168a7c';
      document.querySelector('[role=progressbar]').setAttribute('aria-valuenow', String(percent));
      document.querySelector('#scan-progress-counts').textContent = `${done} из ${total} фильтров обработано · ${percent}%`;
      if(['reconciling','link_sync'].includes(d.status)) document.querySelector('#scan-progress-counts').textContent = 'Предварительный этап: поисковые фильтры ещё не проверяются';
      const summary = d.summary;
      document.querySelector('#scan-progress-result').textContent = summary ? (summary.search_skipped ? 'Поиск не запущен: актуализация ссылок не завершена' : `Обнаружений: ${summary.found || 0} · непоказов: ${(summary.missed_confirmed || 0) + (summary.missed_uncertain || 0)} · сбоев фильтров: ${summary.technical_errors || 0} · ссылок на подтверждение: ${summary.links_need_review || 0}`) : '';
      let current = d.status === 'preparing' ? 'Получаем актуальные ссылки и состав реестра' : d.status === 'running' ? `${source(c.source)} · ${c.filter_name || 'Подготовка'}${c.page ? ' · страница ' + c.page : ''}` : 'Запустите проверку с локального компьютера';
      if(['completed','partial'].includes(d.status)) current = `Последняя проверка: ${new Date(d.updated_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})} МСК`;
      if(d.status === 'reconciling') current = `${source(c.source)} · сверяем каталог продавца${c.page ? ' · страница ' + c.page : ''}`;
      if(d.status === 'link_sync') current = 'Сверяем ID в карточках и фиде перед поиском';
      if(d.status === 'waiting_captcha') current = 'Пройдите CAPTCHA в открытом приватном окне Chrome; цикл ждёт и не пропускает проверку';
      if(d.error) current = d.error;
      if(stale) current = 'Сигнал не обновлялся более 3 минут. Проверьте терминал и окно Chrome.';
      document.querySelector('#scan-progress-current').textContent = current;
      document.querySelector('#scan-progress-log').textContent = (d.events || []).map(eventLine).filter(Boolean).slice(-18).join('\n') || 'Событий пока нет.';
    } catch(e) { document.querySelector('#scan-progress-status').textContent = 'Нет связи'; document.querySelector('#scan-progress-current').textContent = 'Не удалось получить состояние. Проверьте локальное приложение.'; }
    setTimeout(refresh, document.hidden ? 10000 : 2000);
  }
  refresh();
})();
