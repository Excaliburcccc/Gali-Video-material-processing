'use strict';

window.BatchExportUI = (() => {
  const selected = new Map();
  let revision = 0, previewRequest = 0, previewTimer, selecting = false, submitting = false;
  const defaultSeconds = () => ({before: $('batch-before').value, after: $('batch-after').value});
  const has = id => selected.has(String(id));
  const key = () => String(revision);
  const order = () => new Map(S.videos.map((v, i) => [String(v.id), i]));
  function sorted() {
    const positions = order();
    return [...selected.values()].sort((a, b) =>
      (positions.get(String(a.event.video_id)) ?? Infinity) - (positions.get(String(b.event.video_id)) ?? Infinity)
      || Number(a.event.time) - Number(b.event.time) || String(a.event.id).localeCompare(String(b.event.id)));
  }
  function seconds(value, label) {
    const n = Number(value);
    if (String(value).trim() === '' || !Number.isFinite(n) || n < 0 || n > 86400) throw Error(label + '请输入 0 至 86400 的秒数');
    return n;
  }
  function range(item) {
    const before = seconds(item.before, '前留时间：'), after = seconds(item.after, '后留时间：');
    if (before + after <= 0) throw Error('每个片段前后保留时间不能都为 0');
    const video = S.videos.find(v => String(v.id) === String(item.event.video_id));
    const start = Math.max(0, Number(item.event.time) - before);
    const end = Math.min(Number(video?.duration) || Infinity, Number(item.event.time) + after);
    if (!(end > start)) throw Error('片段范围超出了录像长度');
    return {start, end, duration: end - start, clipped: start !== Number(item.event.time) - before || end !== Number(item.event.time) + after};
  }
  function payload() {
    if (!selected.size) throw Error('请先勾选需要导出的事件');
    return {
      project_id: S.projectId, mode: $('batch-mode').value, name: $('batch-name').value.trim(),
      items: sorted().map(item => {
        range(item);
        return {event_id: item.event.id, before: seconds(item.before, '前留时间：'), after: seconds(item.after, '后留时间：')};
      })
    };
  }
  function controls() {
    $('batch-selection-count').textContent = `已选 ${selected.size} 条`;
    $('batch-count').textContent = `${selected.size} 个片段`;
    $('batch-open').disabled = !selected.size;
    $('batch-clear').disabled = !selected.size || selecting;
    $('batch-select-all').disabled = selecting || !(S.total ?? S.events.length);
    $('batch-select-all').textContent = selecting ? '正在全选…' : '全选结果';
    $('batch-submit').disabled = !selected.size || submitting || selecting;
    $('batch-apply').disabled = !selected.size || submitting;
    $('batch-empty').hidden = Boolean(selected.size);
    $('batch-rows-wrap').hidden = !selected.size;
    document.querySelectorAll('[data-select-event]').forEach(input => {input.checked = has(input.dataset.selectEvent);});
  }
  function updateRanges() {
    let duration = 0, valid = true;
    for (const row of $('batch-rows').children) {
      const item = selected.get(row.dataset.batchEvent);
      if (!item) continue;
      const target = row.querySelector('[data-batch-range]');
      try {
        const r = range(item); duration += r.duration;
        target.textContent = `${tc(r.start, true)} – ${tc(r.end, true)}${r.clipped ? ' · 已到录像边界' : ''}`;
        target.classList.remove('inline-error');
      } catch (error) { target.textContent = error.message; target.classList.add('inline-error'); valid = false; }
    }
    $('batch-duration').textContent = selected.size ? (valid ? `预计时长 ${tc(duration, true)}` : '请检查片段时间') : '';
  }
  function render() {
    $('batch-rows').innerHTML = sorted().map((item, i) => {
      const e = item.event, video = S.videos.find(v => String(v.id) === String(e.video_id));
      return `<div class="batch-row" data-batch-event="${esc(e.id)}"><div class="batch-identity"><span class="batch-number">${i + 1}</span><div><span class="batch-event-name">${esc(e.name || '未命名标记')}</span><span class="batch-source">${esc(e.video_name || video?.name || '')} · ${tc(e.time, true)}</span><span class="batch-range" data-batch-range></span></div></div><label><span class="sr-only">${esc(e.name)} 前留秒数</span><input type="number" min="0" max="86400" step="any" inputmode="decimal" value="${esc(item.before)}" data-batch-padding="before" aria-label="${esc(e.name)} 前留秒数"></label><label><span class="sr-only">${esc(e.name)} 后留秒数</span><input type="number" min="0" max="86400" step="any" inputmode="decimal" value="${esc(item.after)}" data-batch-padding="after" aria-label="${esc(e.name)} 后留秒数"></label><button type="button" class="text-button" data-batch-remove="${esc(e.id)}" aria-label="移除 ${esc(e.name)}">移除</button></div>`;
    }).join('');
    controls(); updateRanges();
  }
  function changed() {
    ++revision; render(); S.eventSignature = ''; renderEvents();
    showError('batch-error', ''); showError('batch-message', ''); schedulePreview();
  }
  function toggle(event, checked) {
    if (checked) {
      if (!has(event.id) && selected.size >= 5000) {
        showError('batch-selection-error', '每次最多选择 5000 个片段，请分批导出'); controls(); return;
      }
      if (!has(event.id)) selected.set(String(event.id), {event: {...event}, ...defaultSeconds()});
    }
    else selected.delete(String(event.id));
    changed();
  }
  function reset() {
    selected.clear(); ++previewRequest; clearTimeout(previewTimer); selecting = false;
    ++revision; render(); showError('batch-error', ''); showError('batch-message', ''); showError('batch-selection-error', '');
  }
  function syncRows() {
    const rows = new Map([...$('batch-rows').children].map(row => [row.dataset.batchEvent, row]));
    const items = sorted(), oldOrder = [...rows.keys()], focused = document.activeElement;
    const reordered = items.some((item, i) => String(item.event.id) !== oldOrder[i]);
    items.forEach((item, index) => {
      const event = item.event, row = rows.get(String(event.id));
      if (!row) return;
      row.querySelector('.batch-number').textContent = String(index + 1);
      row.querySelector('.batch-event-name').textContent = event.name || '未命名标记';
      row.querySelector('.batch-source').textContent = `${event.video_name || S.videos.find(v => String(v.id) === String(event.video_id))?.name || ''} · ${tc(event.time, true)}`;
      if (reordered) $('batch-rows').append(row);
    });
    if (reordered && focused?.isConnected && focused.closest('#batch-rows')) focused.focus({preventScroll: true});
    updateRanges();
  }
  function refresh(events) {
    // Polls update event names/times without replacing focused padding inputs.
    for (const event of events) {
      const item = selected.get(String(event.id));
      if (!item) continue;
      item.event = {...event};
    }
    controls(); syncRows();
  }
  function remove(id) { if (selected.delete(String(id))) changed(); }
  function currentFilter() {
    const params = query();
    if (S.scopeId) { params.set('video_id', S.scopeId); params.set('scope', 'video'); }
    return params;
  }
  async function selectAll() {
    if (selecting) return;
    const epoch = S.projectEpoch, params = currentFilter(), filter = params.toString(), rows = [];
    selecting = true; controls(); showError('batch-selection-error', '');
    try {
      for (let offset = 0; ; offset += 5000) {
        params.set('limit', '5000'); params.set('offset', String(offset));
        const result = await api('/api/events?' + params);
        if (epoch !== S.projectEpoch) return;
        if (filter !== currentFilter().toString()) throw Error('筛选条件已改变，请重新全选');
        const events = Array.isArray(result) ? result : result.events || [];
        rows.push(...events);
        if (!events.length || rows.length >= (result.total ?? rows.length)) break;
      }
      const combined = new Set([...selected.keys(), ...rows.map(event => String(event.id))]);
      if (combined.size > 5000) throw Error('每次最多选择 5000 个片段，请缩小筛选范围后分批导出');
      for (const event of rows) if (!has(event.id)) selected.set(String(event.id), {event: {...event}, ...defaultSeconds()});
      changed();
    } catch (error) { if (epoch === S.projectEpoch) showError('batch-selection-error', error.message); }
    finally { if (epoch === S.projectEpoch) {selecting = false; controls();} }
  }
  function schedulePreview() {
    clearTimeout(previewTimer); ++previewRequest;
    if (selected.size) previewTimer = setTimeout(preview, 350);
  }
  async function preview() {
    const request = ++previewRequest, epoch = S.projectEpoch;
    let body;
    try { body = payload(); } catch { return; }
    try {
      const result = await api('/api/export/batch/preview', body);
      if (request !== previewRequest || epoch !== S.projectEpoch) return;
      for (const clip of result.clips || []) {
        const item = selected.get(String(clip.event_id));
        if (item) Object.assign(item.event, {name: clip.name, time: clip.time, video_id: clip.video_id, video_name: clip.video_name});
      }
      syncRows();
      $('batch-duration').textContent = `预计时长 ${tc(result.duration, true)}`;
      showError('batch-error', '');
    } catch (error) { if (request === previewRequest && epoch === S.projectEpoch) showError('batch-error', error.message); }
  }
  $('batch-select-all').onclick = selectAll;
  $('batch-clear').onclick = () => {selected.clear(); changed();};
  $('batch-open').onclick = () => {
    const panel = $('batch-export-panel'); panel.open = true;
    if (innerWidth > 1050) {
      window.scrollTo({top: 0, left: 0, behavior: 'instant'});
      const viewer = document.querySelector('.viewer');
      viewer.scrollTo({top: viewer.scrollTop + panel.getBoundingClientRect().top - viewer.getBoundingClientRect().top, behavior: 'smooth'});
    } else panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  $('batch-apply').onclick = () => {
    try {
      const values = defaultSeconds();
      seconds(values.before, '前留时间：'); seconds(values.after, '后留时间：');
      if (Number(values.before) + Number(values.after) <= 0) throw Error('前后保留时间不能都为 0');
      for (const item of selected.values()) Object.assign(item, values);
      changed();
    } catch (error) { showError('batch-error', error.message); }
  };
  $('batch-rows').oninput = event => {
    const input = event.target.closest('[data-batch-padding]');
    if (!input) return;
    const item = selected.get(input.closest('[data-batch-event]').dataset.batchEvent);
    item[input.dataset.batchPadding] = input.value;
    ++revision; updateRanges(); showError('batch-error', ''); showError('batch-message', ''); schedulePreview();
  };
  $('batch-rows').onclick = event => {const button = event.target.closest('[data-batch-remove]'); if (button) remove(button.dataset.batchRemove);};
  $('batch-mode').onchange = schedulePreview;
  $('batch-export-form').onsubmit = async event => {
    event.preventDefault(); if (submitting) return;
    const epoch = S.projectEpoch;
    try {
      const body = payload(); submitting = true; controls();
      showError('batch-error', ''); showError('batch-message', '');
      const result = await api('/api/export/batch', body);
      if (epoch !== S.projectEpoch) return;
      $('batch-message').textContent = `已将 ${result.count} 个片段加入导出队列，可在左侧“处理任务”打开成品文件夹。`;
      $('batch-message').hidden = false;
      $('jobs-section').open = true;
      toast('批量导出已加入队列'); await poll();
    } catch (error) { if (epoch === S.projectEpoch) showError('batch-error', error.message); }
    finally { submitting = false; controls(); }
  };
  render();
  return {has, key, toggle, refresh, reset, remove, updateEvent(event) {refresh([event]); schedulePreview();}};
})();
