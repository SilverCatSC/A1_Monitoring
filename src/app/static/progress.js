// Read-only progress view. Starting a live scan remains an explicit local action.
(() => {
  const label = {idle:'Не запущена',starting:'Запуск',preparing:'Обновление реестра',reconciling:'Сверка каталога',link_sync:'Актуализация ссылок',running:'Выполняется',waiting_captcha:'Требуется действие',waiting_navigation:'Ожидаем восстановления доступа',completed:'Завершена',partial:'Частично',failed:'Сбой',unavailable:'Нет связи'};
  const source = s => ({auto_ru:'Auto.ru',avito:'Avito'})[s] || '';
  const captchaWaitReason = reason => ({ip_refresh_wait: 'Сообщение об ограничении IP; ждём перед обновлением этой страницы', ip_refresh_exhausted: 'Повторы исчерпаны; ждём в той же вкладке, объявление не пропускается', profile_load_failed: 'Avito не загрузил профиль; нажмите «Обновить» в этой вкладке — проверка ждёт', redirect_pending: 'Проверка пройдена; ждём перехода Avito, вкладку не обновляйте', redirect_stalled: 'Avito не завершил переход после проверки; обновите ЭТУ ЖЕ вкладку один раз (Cmd+R)', challenge_present: 'Страница проверки ещё открыта', stabilizing: 'Проверяем восстановленную страницу', wrong_destination: 'Открыт другой адрес; вернитесь к исходной странице', manual_refresh_required: 'Проверка снята, но документ ещё отдаёт отказ — обновите ЭТУ ЖЕ вкладку (Cmd+R)', awaiting_successful_document: 'Ждём HTTP 200; если каталог уже открыт, обновите его вручную'})[reason] || 'Ждём прохождения проверки';
  const eventLine = e => {
    const stamp = e.at ? new Date(e.at).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow'}) : '';
    let message = '';
    switch(e.event) {
      case 'search_collection_started': message = 'Сначала собираем позиции в поисковой выдаче'; break;
      case 'search_collection_finished': message = 'Выдача сохранена; переходим к активным объявлениям'; break;
      case 'dealer_preflight_started': message = 'Проверяем активный каталог продавца после сбора выдачи'; break;
      case 'dealer_catalogue_started': message = `${source(e.source)} · каталог продавца · пауза ${e.wait_seconds} с`; break;
      case 'dealer_catalogue_finished': message = `${source(e.source)} · кандидатов ${e.candidates} · ${e.complete ? 'каталог пройден' : 'каталог просмотрен не полностью'}`; break;
      case 'direct_content_waiting': case 'direct_navigation_waiting': message = `${source(e.source)} · ждём содержимое карточки · объявление не пропускается`; break;
      case 'dealer_link_check': message = `${source(e.source)} · проверка старой ссылки · пауза ${e.wait_seconds} с`; break;
      case 'dealer_preflight_finished': message = 'Сверка ссылок завершена. Неоднозначные связи переданы на подтверждение.'; break;
      case 'link_preflight_started': message = 'Сопоставляем unique_id и актуальные ссылки с сохранённой выдачей'; break;
      case 'link_sync_finished': message = `Ссылок обновлено: ${e.updated || 0}; требуют разбора: ${e.blocked || 0}`; break;
      case 'source_refresh_started': message = 'Обновление реестра из таблицы'; break;
      case 'source_refresh_finished': message = `Реестр обновлён: ${e.rows_valid} из ${e.rows_total} строк`; break;
      case 'source_refresh_failed': case 'cycle_failed': message = `Сбой: ${e.error || ''}`; break;
      case 'filter_started': message = `${source(e.source)} · ${e.stage === 'matching_saved_search' ? 'сопоставляем сохранённую выдачу · ' : ''}${e.filter_name}`; break;
      case 'filter_wait': case 'page_wait': message = `Пауза ${e.wait_seconds} с`; break;
      case 'filter_retry': message = `Вкладка закрыта. Повтор через ${e.retry_seconds} с`; break;
      case 'page_started': message = `${source(e.source)} · загрузка страницы ${e.page}/${e.pages_total}`; break;
      case 'page_finished': message = `Страница ${e.page}: ${e.cards || 0} карточек, ${e.target_cards || 0} снимков своих объявлений`; break;
      case 'page_failed': message = `Страница ${e.page}: ${e.error || 'сбой'}`; break;
      case 'lazy_load_started': message = `${source(e.source)} · подгрузка в текущей вкладке · пауза ${e.wait_seconds} с`; break;
      case 'lazy_load_finished': message = `${source(e.source)} · подгрузка завершена · ${e.cards} карточек`; break;
      case 'navigation_operator_required': case 'navigation_operator_waiting': message = `${source(e.source)} · страница ${e.page} не загружена; цикл ждёт. После восстановления доступа обновите ЭТУ ЖЕ вкладку (Cmd+R)`; break;
      case 'navigation_operator_resolved': message = `${source(e.source)} · доступ восстановлен, продолжаем ту же страницу`; break;
      case 'captcha_refresh': message = e.reason === 'ip_restriction' ? `${source(e.source)} · ограничение IP: обновление ${e.attempt}/${e.limit} в той же вкладке` : `${source(e.source)} · CAPTCHA: одно обычное обновление`; break;
      case 'captcha_operator_required': message = `${source(e.source)} · пройдите CAPTCHA в открытом Chrome ${e.wait_seconds === -1 ? "(без таймера; остановка — Ctrl+C)" : `(${e.wait_seconds} с)`}`; break;
      case 'captcha_operator_resolved': message = e.outcome === 'cleared_by_refresh' ? `${source(e.source)} · ограничение IP снято после обновления, продолжаем` : `${source(e.source)} · CAPTCHA пройдена, продолжаем`; break;
      case 'captcha_operator_waiting': message = `${source(e.source)} · ${captchaWaitReason(e.wait_reason)}`; break;
      case 'captcha_manual_refresh_hint': message = `${source(e.source)} · ${e.instruction || "Обновите ЭТУ ЖЕ вкладку (Cmd+R)"}`; break;
      case 'captcha_operator_unresolved': message = `${source(e.source)} · CAPTCHA не снята, проверка остаётся незавершённой`; break;
      case 'filter_finished': message = e.status === 'technical_error' ? `${e.filter_name}: проверка не удалась · ${e.error || 'технический сбой'}` : e.collection_only ? `${e.filter_name}: выдача сохранена · ${e.cards || 0} карточек` : `${e.filter_name}: найдено ${e.found || 0} из ${e.expected || 0}${e.links_rejected ? ' · ссылок пропущено: ' + e.links_rejected : ''}`; break;
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
      const staleAfter = d.status === 'waiting_captcha' ? Math.max(90, Number(c.wait_seconds || 180) + 30) * 1000 : 180000;
      const stale = ['running','preparing','reconciling','link_sync','waiting_captcha','waiting_navigation'].includes(d.status) && Date.now() - Date.parse(d.updated_at) > staleAfter;
      const total = Number(d.total_filters || 0), done = Number(d.completed_filters || 0);
      const percent = total ? Math.min(100,Math.round(done / total * 100)) : 0;
      document.querySelector('#scan-progress-status').textContent = stale ? 'Нет свежего сигнала' : (label[d.status] || d.status);
      const warning = stale || ['partial','failed','unavailable','waiting_captcha','waiting_navigation'].includes(d.status);
      document.querySelector('#scan-progress-status').className = 'badge ' + (warning ? 'technical_error' : d.status === 'completed' ? 'found' : '');
      document.querySelector('#scan-progress-bar').style.width = percent + '%';
      document.querySelector('#scan-progress-bar').style.backgroundColor = warning ? '#b58b38' : '#168a7c';
      document.querySelector('[role=progressbar]').setAttribute('aria-valuenow', String(percent));
      document.querySelector('#scan-progress-counts').textContent = `${done} из ${total} фильтров обработано · ${percent}%`;
      if(['reconciling','link_sync'].includes(d.status)) document.querySelector('#scan-progress-counts').textContent = 'Предварительный этап: поисковые фильтры ещё не проверяются';
      const summary = d.summary;
      document.querySelector('#scan-progress-result').textContent = summary ? (summary.search_skipped ? 'Поиск не запущен: актуализация ссылок не завершена' : `Обнаружений: ${summary.found || 0} · непоказов: ${(summary.missed_confirmed || 0) + (summary.missed_uncertain || 0)} · технических сбоев: ${summary.technical_errors || 0} · ссылок на подтверждение: ${summary.links_need_review || 0}`) : '';
      let current = d.status === 'preparing' ? 'Получаем актуальные ссылки и состав реестра' : d.status === 'running' ? `${source(c.source)} · ${c.filter_name || 'Подготовка'}${c.page ? ' · страница ' + c.page : ''}` : 'Запустите проверку с локального компьютера';
      if(['completed','partial'].includes(d.status)) current = `Последняя проверка: ${new Date(d.updated_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})} МСК`;
      if(d.status === 'reconciling') current = `${source(c.source)} · сверяем каталог продавца${c.page ? ' · страница ' + c.page : ''}`;
      if(d.status === 'link_sync') current = 'Сверяем ID в карточках и фиде перед поиском';
      if(d.status === 'waiting_navigation') current = 'Страница не загружена; цикл ждёт восстановления доступа. Не закрывайте вкладку; после восстановления обновите её (Cmd+R)';
      if(d.status === 'waiting_captcha') current = c.wait_reason ? captchaWaitReason(c.wait_reason) : 'Пройдите CAPTCHA в открытом приватном окне Chrome; цикл ждёт и не пропускает проверку';
      if(d.error) current = d.error;
      if(stale) current = 'Сигнал не обновлялся более 3 минут. Проверьте терминал и окно Chrome.';
      document.querySelector('#scan-progress-current').textContent = current;
      document.querySelector('#scan-progress-log').textContent = (d.events || []).map(eventLine).filter(Boolean).slice(-18).join('\n') || 'Событий пока нет.';
    } catch(e) { document.querySelector('#scan-progress-status').textContent = 'Нет связи'; document.querySelector('#scan-progress-current').textContent = 'Не удалось получить состояние. Проверьте локальное приложение.'; }
    setTimeout(refresh, document.hidden ? 10000 : 2000);
  }
  refresh();
})();
