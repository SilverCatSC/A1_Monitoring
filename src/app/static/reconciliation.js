document.querySelectorAll('.confirmation-form').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const out = form.querySelector('.confirmation-result'), button = form.querySelector('button');
  const fields = new FormData(form);
  button.disabled = true; out.textContent = 'Сохранение…';
  try {
    const response = await fetch('/api/v1/dealer/reconciliation/' + form.dataset.checkId + '/confirm', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:fields.get('url'),actor:fields.get('actor'),reason:fields.get('reason')})
    });
    const payload = await response.json();
    if(!response.ok) throw new Error(payload.detail || 'Не удалось сохранить');
    out.textContent = 'Связь сохранена. Она будет проверена при следующем запуске.';
    location.reload();
  } catch(error) { out.textContent = error.message; button.disabled = false; }
}));
