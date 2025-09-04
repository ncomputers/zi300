(() => {
  const metricOptions = document.getElementById('metricOptionsTemplate').innerHTML;
  const defaultRecipients = localStorage.getItem('alertRecipients') || '';

  function rowTemplate() {
    return `<tr>
    <td><select class="form-select metric">${metricOptions}</select></td>
    <td><select class="form-select type"><option value="event">Event</option><option value="threshold">Threshold</option></select></td>
    <td><input type="number" class="form-control value" value="1" min="1"><div class="form-text">Count threshold</div></td>
    <td><select class="form-select window d-none"><option value="1">1 min</option><option value="5">5 min</option><option value="15">15 min</option><option value="60">60 min</option></select></td>
    <td><input class="form-control recipients" value="${defaultRecipients}"></td>
    <td class="text-center"><input type="checkbox" class="form-check-input attach"></td>
    <td><button class="btn btn-danger btn-sm del">Delete</button></td>
  </tr>`;
  }

  document.getElementById('addRule').onclick = function () {
    document.querySelector('#rulesTable tbody').insertAdjacentHTML('beforeend', rowTemplate());
  };

  document.querySelector('#rulesTable').addEventListener('click', e => {
    if (e.target.classList.contains('del')) e.target.closest('tr').remove();
  });
  document.querySelector('#rulesTable').addEventListener('change', e => {
    if (e.target.classList.contains('type')) {
      const win = e.target.closest('tr').querySelector('.window');
      if (e.target.value === 'threshold') win.classList.remove('d-none');
      else win.classList.add('d-none');
    }
  });

  document.getElementById('save').onclick = async () => {
    const rows = document.querySelectorAll('#rulesTable tbody tr');
    const rules = [];
    rows.forEach(r => {
      rules.push({
        metric: r.querySelector('.metric').value,
        type: r.querySelector('.type').value,
        value: parseInt(r.querySelector('.value').value || 0),
        window: parseInt(r.querySelector('.window').value || 1),
        recipients: r.querySelector('.recipients').value,
        attach: r.querySelector('.attach').checked
      });
    });
    if (rules.length) {
      localStorage.setItem('alertRecipients', rules[0].recipients);
    }
    const r = await fetch('/alerts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules })
    });
    const d = await r.json();
    document.getElementById('msg').innerHTML = d.saved ? '<div class="alert alert-success">Saved</div>' : '<div class="alert alert-danger">Error</div>';
  };
})();
