'use strict';

// The current project is the scope for every library view. Trial results never
// enter that library; they are a preview of a draft scheme only.
window.ProfileUI = (() => {
  let currentProfile = null, profileList = [], projectList = [];
  let draft = null, isNew = false, selectedRule = -1, editorTab = 'categories';
  let image = null, drag = null, trialTimer = null, trialGeneration = 0, stateSignature = '';
  const copy = value => JSON.parse(JSON.stringify(value));
  const splitLines = text => String(text || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const makeId = prefix => prefix + '_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  const policies = {first:'本项目只保留首次', occurrence:'每次独立出现', all:'全部观察（校准用）'};
  const semantics = {obtained:'获得物品', defeated:'击败敌人', completed:'完成任务／成就', seen:'首次出现／展示'};
  const option = (value, label, selected) => `<option value="${esc(value)}"${String(value)===String(selected)?' selected':''}>${esc(label)}</option>`;

  document.body.insertAdjacentHTML('beforeend', `
  <dialog id="project-dialog" aria-labelledby="project-dialog-title"><form id="project-form">
    <div class="dialog-heading"><h2 id="project-dialog-title">新建项目</h2><button type="button" data-profile-close="project-dialog" class="close-button" aria-label="关闭">×</button></div>
    <label>项目名称<input id="project-name" maxlength="120" required placeholder="例如：游戏名称 · 一周目"></label>
    <label>游戏方案<select id="project-profile" required></select></label>
    <div id="new-game-fields" hidden><label>游戏名称<input id="new-game-name" maxlength="100"></label><label>游戏文字<select id="new-game-language"><option value="zh">中文</option><option value="en">英文</option></select></label></div>
    <p class="hint">不同项目的录像和去重记录相互独立。新周目也可以单独建一个项目。</p>
    <p id="project-error" class="inline-error" hidden></p><div class="dialog-footer"><button type="button" data-profile-close="project-dialog">取消</button><button type="submit" class="primary">创建项目</button></div>
  </form></dialog>
  <dialog id="profile-dialog" class="profile-dialog" aria-labelledby="profile-dialog-title"><form id="profile-form" novalidate>
    <div class="dialog-heading"><h2 id="profile-dialog-title">游戏方案</h2><div class="button-row"><button id="profile-copy" type="button">另存方案</button><button type="button" data-profile-close="profile-dialog" class="close-button" aria-label="关闭">×</button></div></div>
    <div class="profile-top"><label>游戏／方案名称<input id="profile-name" maxlength="100" required></label><label>游戏文字<select id="profile-language"><option value="zh">中文</option><option value="en">英文</option></select></label><div class="profile-version" id="profile-version"></div></div>
    <div class="profile-tabs" role="tablist" aria-label="游戏方案设置"><button type="button" role="tab" data-profile-tab="categories" aria-controls="profile-categories-pane">分类与去重</button><button type="button" role="tab" data-profile-tab="rules" aria-controls="profile-rules-pane">提示规则</button><button type="button" role="tab" data-profile-tab="sample" aria-controls="profile-sample-pane">样例校准</button></div>
    <section id="profile-categories-pane" role="tabpanel"><div id="profile-categories" class="profile-category-table"></div><button id="category-add" type="button">添加分类</button><p class="hint">“每次独立出现”合并同一次连续弹窗，关闭后再次出现会保留；“首次”仅在当前项目内去重。</p></section>
    <section id="profile-rules-pane" role="tabpanel" hidden><p id="profile-adapter-note" class="hint"></p><div id="profile-rule-list" class="profile-rule-list"></div><button id="rule-add" type="button">添加提示规则</button><div class="profile-exclusions"><label>需要排除的界面文字<textarea id="profile-exclude" rows="3" placeholder="每行一条，例如：出售物品、强化所需"></textarea></label><label>名称纠错<textarea id="profile-aliases" rows="3" placeholder="每行一条：识别错字 = 正确名称"></textarea></label></div></section>
    <section id="profile-sample-pane" role="tabpanel" hidden><div class="sample-source"><label>样例录像<select id="sample-video"></select></label><label>画面时间<input id="sample-time" inputmode="decimal" value="00:00:00"></label><button id="sample-load" type="button">读取画面</button></div><button id="sample-import" type="button" hidden>导入样例录像</button>
      <div class="sample-layout"><div class="sample-view"><canvas id="sample-canvas" width="1280" height="720" aria-label="样例画面：拖动框选识别区域"></canvas><p id="sample-empty" class="empty">选择录像并读取画面</p><p id="sample-image-status" class="hint" aria-live="polite"></p></div>
      <div class="sample-tools"><label>校准哪条规则<select id="sample-rule"></select></label><label>框选内容<select id="sample-region"><option value="region">提示／类别标题</option><option value="name_region">物品／任务名称</option></select></label><p class="hint">在左侧画面拖动框选。名称和提示在同一行时，可只框提示区域。</p><p id="sample-region-status" class="hint"></p><button id="sample-clear-region" type="button">清除当前区域</button><div class="two-fields"><label>试识别起点<input id="trial-start" value="00:00:00" inputmode="decimal"></label><label>试识别终点<input id="trial-end" value="00:00:15" inputmode="decimal"></label></div><button id="trial-start-button" type="button">试识别这段录像</button><p class="hint">每次最多 60 秒，结果不写入正式素材库。</p></div></div>
      <p id="trial-status" class="hint" role="status"></p><div id="trial-results" class="trial-results"></div>
    </section>
    <p id="profile-error" class="inline-error" hidden></p><div class="dialog-footer profile-footer"><span id="profile-save-note" class="hint">保存后用于本项目的识别与去重。</span><button type="button" data-profile-close="profile-dialog">关闭</button><button type="submit" class="primary">保存方案</button></div>
  </form></dialog>`);

  function resetProject() {
    S.projectEpoch = (S.projectEpoch || 0) + 1;
    ++S.request; ++S.selectionRequest;
    window.BatchExportUI?.reset();
    S.activeId = null; S.scopeId = null; S.kind = ''; S.videos = []; S.events = []; S.timeline = [];
    S.timelineTotal = 0; S.total = 0; S.selected = null; S.selectedGroup = null;
    S.mediaKey = ''; S.pendingSeek = null; S.pendingPlay = false;
    S.signature = ''; S.eventSignature = ''; S.timelineSignature = '';
    $('player').pause(); $('player').removeAttribute('src'); $('player').load();
    $('event-detail').hidden = true; $('search').value = ''; $('verified-filter').value = '';
    $('clip-start').value = '00:00:00'; $('clip-end').value = '00:00:00'; $('clip-name').value = '';
    renderVideos(); renderEvents(); renderTimeline(); updateVideoHeader();
  }

  async function switchProject(id) {
    if (!id || id === S.projectId) return;
    await api('/api/projects/' + encodeURIComponent(id) + '/activate', {});
    resetProject(); S.projectId = id; stateSignature = '';
    // An older poll may still be finishing. Its epoch guard prevents stale data.
    if (S.polling) {
      await new Promise(resolve => {
        const timer = setInterval(() => { if (!S.polling) { clearInterval(timer); resolve(); } }, 40);
      });
    }
    await poll();
  }

  function categoryOptions(categories, selected) {
    const groups = new Map();
    for (const cat of categories) {
      const group = cat.group || '';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(cat);
    }
    return [...groups].map(([group, cats]) => {
      const options = cats.map(c => option(c.id, c.label, selected)).join('');
      return group ? `<optgroup label="${esc(group)}">${options}</optgroup>` : options;
    }).join('');
  }

  function renderCategories(categories) {
    const enabled = categories.filter(c => c.enabled !== false);
    if (S.kind && !enabled.some(c => c.id === S.kind)) S.kind = '';
    for (const c of categories) names[c.id] = c.label;
    const groups = new Map();
    for (const cat of enabled) {
      const group = cat.group || '';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(cat);
    }
    const button = c => `<button data-kind="${esc(c.id)}" aria-pressed="${S.kind === c.id}" class="${S.kind === c.id ? 'active' : ''}">${esc(c.label)}</button>`;
    document.querySelector('.kind-tabs').innerHTML = `<div class="kind-primary"><button data-kind="" class="${!S.kind?'active':''}" aria-pressed="${!S.kind}">全部</button>${(groups.get('')||[]).map(button).join('')}</div>` + [...groups].filter(([g]) => g).map(([group,cats],i) => `<div class="kind-group" role="group" aria-labelledby="profile-kind-${i}"><h3 id="profile-kind-${i}">${esc(group)}</h3><div class="kind-options">${cats.map(button).join('')}</div></div>`).join('');
    for (const id of ['event-kind', 'manual-kind']) {
      const selected = $(id).value;
      const choices = enabled.some(c => c.id === 'note') ? enabled : [...enabled, {id:'note',label:'备注',group:''}];
      $(id).innerHTML = categoryOptions(choices, selected);
      if (!$(id).value && choices.length) $(id).value = choices[0].id;
    }
  }

  function applyState(state) {
    if (!state.projects || !state.profile) return;
    const newId = state.active_project_id || state.project?.id;
    if (S.projectId && newId && newId !== S.projectId) resetProject();
    S.projectId = newId || S.projectId;
    currentProfile = state.profile;
    profileList = state.profiles || profileList;
    projectList = state.projects;
    const sig = JSON.stringify([projectList, profileList, currentProfile, state.categories, S.projectId]);
    if (sig === stateSignature) return;
    stateSignature = sig;
    $('project-select').innerHTML = projectList.map(p => option(p.id,p.name,S.projectId)).join('');
    $('project-select').disabled = !projectList.length;
    $('project-select').title = projectList.find(p=>p.id===S.projectId)?.name || '';
    $('game-profile').innerHTML = option(currentProfile.id,currentProfile.name,currentProfile.id);
    renderCategories(state.categories || currentProfile.categories || []);
    S.eventSignature = ''; S.timelineSignature = '';
  }

  async function refreshProfiles() {
    const r = await api('/api/profiles');
    profileList = Array.isArray(r) ? r : r.profiles || [];
  }

  function openProject() {
    $('project-name').value = '';
    $('new-game-name').value = '';
    $('project-profile').innerHTML = profileList.map(p=>option(p.id,p.name,currentProfile?.id)).join('') + option('__new__','新建游戏方案…','');
    $('new-game-fields').hidden = true;
    $('new-game-name').required = false;
    showError('project-error','');
    $('project-dialog').showModal(); $('project-name').focus();
  }

  async function baseGeneric(name, language='zh') {
    const response = await api('/api/profiles/generic');
    const profile = copy(response.profile || response);
    delete profile.id;
    profile.name = name;
    profile.language = language;
    profile.version = 1;
    return profile;
  }

  function syncDraft() {
    if (!draft) return;
    draft.name = $('profile-name').value.trim();
    draft.language = $('profile-language').value;
    draft.exclude_text = splitLines($('profile-exclude').value);
    const aliases = {};
    for (const line of splitLines($('profile-aliases').value)) {
      const i = line.indexOf('=');
      if (i < 1 || !line.slice(i+1).trim()) throw Error('名称纠错请按“识别错字 = 正确名称”填写，每行一条');
      aliases[line.slice(0,i).trim()] = line.slice(i+1).trim();
    }
    draft.aliases = aliases;
    for (const el of $('profile-categories').querySelectorAll('[data-cat-index]')) {
      const cat = draft.categories[Number(el.dataset.catIndex)], key = el.dataset.field;
      cat[key] = key === 'enabled' ? el.checked : el.value.trim();
    }
    for (const el of $('profile-rule-list').querySelectorAll('[data-rule-index]')) {
      const rule = draft.rules[Number(el.dataset.ruleIndex)], key = el.dataset.field;
      rule[key] = key === 'exclude' ? splitLines(el.value) : el.value.trim();
    }
  }

  function validateDraft() {
    syncDraft();
    if (!draft.name) throw Error('请填写游戏／方案名称');
    if (!draft.categories.some(c=>c.enabled!==false)) throw Error('请至少启用一个分类');
    if (draft.categories.some(c=>!c.label)) throw Error('每个分类都需要名称');
    if (draft.rules.some(r=>!r.trigger||!r.category)) throw Error('每条提示规则都需要提示文字和分类');
    return copy(draft);
  }

  function renderCategoryEditor() {
    $('profile-categories').innerHTML = `<div class="profile-category-head"><span>启用</span><span>分类名称</span><span>分组</span><span>重复记录</span><span></span></div>` + draft.categories.map((c,i)=>`<div class="profile-category-row"><label class="checkbox-row"><input type="checkbox" data-cat-index="${i}" data-field="enabled" aria-label="启用 ${esc(c.label)}" ${c.enabled!==false?'checked':''}></label><input data-cat-index="${i}" data-field="label" value="${esc(c.label)}" aria-label="分类名称" maxlength="60" required><input data-cat-index="${i}" data-field="group" value="${esc(c.group||'')}" aria-label="分类分组" maxlength="60" placeholder="不分组"><select data-cat-index="${i}" data-field="policy" aria-label="${esc(c.label)}的去重方式">${Object.entries(policies).map(([k,v])=>option(k,v,c.policy||'first')).join('')}</select><button type="button" class="text-button" data-remove-category="${i}" aria-label="移除 ${esc(c.label)} 分类">移除</button></div>`).join('');
  }

  function renderRuleEditor() {
    $('profile-rule-list').innerHTML = draft.rules.length ? draft.rules.map((r,i)=>`<div class="profile-rule-row"><div class="profile-rule-heading"><h3>提示规则 ${i+1}</h3><button type="button" class="text-button" data-remove-rule="${i}">移除</button></div><div class="profile-rule-fields"><label>提示／类别标题<input data-rule-index="${i}" data-field="trigger" value="${esc(r.trigger||'')}" maxlength="80" placeholder="例如：获得物品" required></label><label>归入分类<select data-rule-index="${i}" data-field="category">${draft.categories.map(c=>option(c.id,c.label,r.category)).join('')}</select></label><label>记录含义<select data-rule-index="${i}" data-field="semantic">${Object.entries(semantics).map(([k,v])=>option(k,v,r.semantic||'seen')).join('')}</select></label><label class="rule-exclude">遇到这些文字时排除<textarea data-rule-index="${i}" data-field="exclude" rows="2" placeholder="每行一条">${esc((r.exclude||[]).join('\n'))}</textarea></label></div><button type="button" data-calibrate-rule="${i}">${r.region||r.name_region?'调整识别区域':'框选识别区域'}</button></div>`).join('') : '<p class="empty">尚未添加提示规则</p>';
    $('profile-adapter-note').textContent = draft.adapter === 'phantom_blade_zero_demo' ? '保留影之刃零专用识别。这里可以添加补充提示规则。' : '按提示文字及名称位置识别；未匹配的普通画面文字不会加入目录。';
    renderRulePicker();
  }

  function renderRulePicker() {
    if (!draft.rules.length) selectedRule = -1;
    else selectedRule = Math.max(0,Math.min(selectedRule,draft.rules.length-1));
    $('sample-rule').innerHTML = draft.rules.length ? draft.rules.map((r,i)=>option(i,(draft.categories.find(c=>c.id===r.category)?.label||'分类')+' · '+(r.trigger||'未填写提示'),selectedRule)).join('') : '<option value="">请先添加提示规则</option>';
    $('sample-rule').disabled = !draft.rules.length;
    $('sample-region').disabled = !draft.rules.length;
    $('sample-clear-region').disabled = !draft.rules.length;
    drawCanvas();
  }

  function showTab(tab) {
    syncDraft(); editorTab = tab;
    for (const name of ['categories','rules','sample']) {
      $('profile-'+name+'-pane').hidden = name!==tab;
      const button = document.querySelector(`[data-profile-tab="${name}"]`);
      button.setAttribute('aria-selected',String(name===tab)); button.classList.toggle('active',name===tab);
    }
    if (tab==='rules') renderRuleEditor();
    if (tab==='sample') renderRulePicker();
    showError('profile-error','');
  }

  function openEditor(profile, creating=false) {
    draft=copy(profile); draft.rules ||= []; draft.categories ||= []; isNew=creating; selectedRule=0;
    image=null; drag=null; trialGeneration++; clearTimeout(trialTimer);
    $('profile-name').value=draft.name||''; $('profile-language').value=draft.language||'zh';
    $('profile-exclude').value=(draft.exclude_text||[]).join('\n');
    $('profile-aliases').value=Object.entries(draft.aliases||{}).map(([a,b])=>a+' = '+b).join('\n');
    $('profile-version').textContent=creating?'新方案':'版本 '+(draft.version||1);
    $('profile-save-note').textContent=creating?'保存为独立方案，之后可在新项目中选择。':'保存后用于本项目的识别与去重。';
    $('profile-copy').hidden=creating;
    $('sample-video').innerHTML=S.videos.length?S.videos.map(v=>option(v.id,v.name,S.activeId||S.videos[0].id)).join(''):'<option value="">请先向本项目导入样例录像</option>';
    const time=active()?$('player').currentTime||0:0;
    $('sample-time').value=tc(time,true); $('trial-start').value=tc(time,true);
    $('trial-end').value=tc(Math.min(time+15, Number(active()?.duration)||time+15),true);
    $('sample-load').disabled=!S.videos.length; $('trial-start-button').disabled=!S.videos.length;
    $('sample-import').hidden=Boolean(S.videos.length);
    delete $('trial-start-button').dataset.busy;
    $('trial-results').innerHTML=''; $('trial-status').textContent=''; $('sample-image-status').textContent='';
    renderCategoryEditor(); renderRuleEditor(); showTab('categories'); drawCanvas();
    $('profile-dialog').showModal();
  }

  function canvasPosition(event) {
    const bounds=$('sample-canvas').getBoundingClientRect();
    return {x:Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width)),y:Math.max(0,Math.min(1,(event.clientY-bounds.top)/bounds.height))};
  }
  function dragRect(start,end) { return {x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),w:Math.abs(start.x-end.x),h:Math.abs(start.y-end.y)}; }
  function drawCanvas() {
    const canvas=$('sample-canvas'),ctx=canvas.getContext('2d');
    ctx.clearRect(0,0,canvas.width,canvas.height);
    $('sample-empty').hidden=Boolean(image);
    if (image) ctx.drawImage(image,0,0,canvas.width,canvas.height);
    const rule=draft?.rules?.[selectedRule];
    const render=(rect,label,color)=>{if(!rect)return;const {x,y,w,h}=rect;ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=3;ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);ctx.font='22px "Microsoft YaHei", sans-serif';const labelWidth=ctx.measureText(label).width+16;const labelY=Math.max(0,y*canvas.height-30);ctx.fillStyle='#111e';ctx.fillRect(x*canvas.width,labelY,labelWidth,30);ctx.fillStyle=color;ctx.fillText(label,x*canvas.width+8,labelY+23);};
    if(rule){render(rule.region,'提示','#fff');render(rule.name_region,'名称','#d2ad71');}
    if(drag) render(dragRect(drag.start,drag.end),$('sample-region').value==='region'?'提示':'名称','#fff');
    const rect=rule?.[$('sample-region').value];
    $('sample-region-status').textContent=rect?`已框选：宽 ${(rect.w*100).toFixed(1)}% · 高 ${(rect.h*100).toFixed(1)}%`:'未框选，使用完整画面中的提示位置';
  }

  async function loadSample() {
    const id=$('sample-video').value;
    if(!id) throw Error('请先导入并选择样例录像');
    const time=parseTime($('sample-time').value),video=S.videos.find(v=>v.id===id);
    if(!video||time>=Number(video.duration))throw Error('画面时间必须位于录像内');
    $('sample-image-status').textContent='正在读取画面…';
    try {
      const next=new Image();
      await new Promise((resolve,reject)=>{next.onload=resolve;next.onerror=()=>reject(Error('读取画面失败，请检查原录像是否可用'));next.src=`/api/videos/${encodeURIComponent(id)}/frame?time=${encodeURIComponent(time)}`;});
      image=next; $('sample-canvas').width=next.naturalWidth; $('sample-canvas').height=next.naturalHeight;
      $('sample-canvas').style.aspectRatio=next.naturalWidth+' / '+next.naturalHeight;
      $('sample-image-status').textContent=video.name+' · '+tc(time); drawCanvas();
    } catch(error){$('sample-image-status').textContent='';throw error;}
  }

  async function runTrial() {
    const profile=validateDraft(),videoId=$('sample-video').value;
    const start=parseTime($('trial-start').value),end=parseTime($('trial-end').value);
    const video=S.videos.find(v=>v.id===videoId);
    if(!video)throw Error('请先选择样例录像');
    if(end<=start||end-start>60||end>Number(video.duration)+.001)throw Error('试识别范围须在录像内，时长大于 0 且不超过 60 秒');
    clearTimeout(trialTimer); const generation=++trialGeneration;
    $('trial-results').innerHTML=''; $('trial-status').textContent='正在提交试识别…';
    const request=await api('/api/profiles/preview',{profile,video_id:videoId,start,end,project_id:S.projectId});
    const id=request.id||request.trial_id;
    if(!id)throw Error('服务未返回试识别任务');
    $('trial-start-button').disabled=true;
    const check=async()=>{
      if(generation!==trialGeneration)return;
      try {
        const response=await api('/api/trials/'+encodeURIComponent(id));
        if(generation!==trialGeneration)return;
        if(['failed','error','cancelled'].includes(response.status)) throw Error(response.error||response.message||'试识别未完成');
        if(['done','completed'].includes(response.status)) {
          const rows=response.events||[];
          $('trial-status').textContent=`试识别完成 · ${rows.length} 条结果`;
          $('trial-results').innerHTML=rows.length?rows.map((event,i)=>`<button type="button" data-trial-event="${i}"><span class="timecode">${tc(event.time,true)}</span><span>${esc(event.name)}</span><span class="muted">${esc(profile.categories.find(c=>c.id===event.kind)?.label||event.kind)}</span></button>`).join(''):'<p class="empty">这段样例没有匹配结果。可检查提示文字、框选区域，或换一段包含获得提示的录像。</p>';
          $('trial-results').querySelectorAll('[data-trial-event]').forEach(button=>button.onclick=()=>{
            const event=rows[Number(button.dataset.trialEvent)];
            $('profile-dialog').close(); selectVideo(videoId,{eventSelection:true});seek(event.time,true);
          });
          $('trial-start-button').disabled=false;delete $('trial-start-button').dataset.busy;
        } else {
          $('trial-status').textContent=response.message||'正在本地识别样例…';
          trialTimer=setTimeout(check,900);
        }
      } catch(error){$('trial-status').textContent='';showError('profile-error',error.message);$('trial-start-button').disabled=false;delete $('trial-start-button').dataset.busy;}
    };
    check();
  }

  $('project-select').onchange=e=>guarded(e.currentTarget,()=>switchProject(e.target.value));
  $('project-new').onclick=e=>guarded(e.currentTarget,async()=>{await refreshProfiles();openProject();});
  $('project-profile').onchange=()=>{const isGame=$('project-profile').value==='__new__';$('new-game-fields').hidden=!isGame;$('new-game-name').required=isGame;};
  $('project-form').onsubmit=e=>{e.preventDefault();guarded(e.submitter,async()=>{
    let profileId=$('project-profile').value,createdProfile=null;
    if(profileId==='__new__') {
      if(!$('new-game-name').value.trim())throw Error('请填写游戏名称');
      const result=await api('/api/profiles',await baseGeneric($('new-game-name').value.trim(),$('new-game-language').value));
      createdProfile=result.profile||result;profileId=createdProfile.id;
    }
    const result=await api('/api/projects',{name:$('project-name').value.trim(),profile_id:profileId});
    const project=result.project||result;
    if(!project.id)throw Error('服务未返回项目编号');
    $('project-dialog').close();await switchProject(project.id);
    toast('项目已创建，可导入样例录像');
    if(createdProfile)openEditor(createdProfile);
  },'project-error');};
  $('settings-open').onclick=e=>guarded(e.currentTarget,async()=>{
    const result=await api('/api/settings?project_id='+encodeURIComponent(S.projectId||''));
    S.settings=result.settings||result;
    $('sample-interval').value=S.settings.sample_interval??1;$('max-width').value=String(S.settings.max_width??1600);
    $('game-profile').innerHTML=currentProfile?option(currentProfile.id,currentProfile.name,currentProfile.id):'';
    showError('settings-error','');$('settings-dialog').showModal();
  });
  $('settings-form').onsubmit=e=>{e.preventDefault();guarded(e.submitter,async()=>{
    const result=await api('/api/settings',{project_id:S.projectId,sample_interval:Number($('sample-interval').value),max_width:Number($('max-width').value)});
    S.settings=result.settings||{...S.settings,sample_interval:Number($('sample-interval').value),max_width:Number($('max-width').value)};
    $('settings-dialog').close();toast('识别设置已保存');await poll();
  },'settings-error');};
  $('profile-open').onclick=e=>guarded(e.currentTarget,async()=>{
    if(!currentProfile)throw Error('游戏方案尚未加载');
    $('settings-dialog').close();openEditor(currentProfile);
  });
  $('profile-copy').onclick=()=>{try{syncDraft();isNew=true;delete draft.id;draft.version=1;draft.name+=' · 副本';$('profile-name').value=draft.name;$('profile-version').textContent='新方案';$('profile-copy').hidden=true;$('profile-save-note').textContent='保存为独立方案，之后可在新项目中选择。';}catch(error){showError('profile-error',error.message);}};
  $('profile-form').onsubmit=e=>{e.preventDefault();guarded(e.submitter,async()=>{
    const profile=validateDraft();
    const result=await api(isNew?'/api/profiles':'/api/profiles/'+encodeURIComponent(profile.id),{...profile,project_id:S.projectId},isNew?'POST':'PUT');
    draft=result.profile||result;isNew=false;stateSignature='';
    $('profile-dialog').close();toast('游戏方案已保存');await poll();
  },'profile-error');};
  document.querySelectorAll('[data-profile-close]').forEach(button=>button.onclick=()=>$(button.dataset.profileClose).close());
  document.querySelectorAll('[data-profile-tab]').forEach(button=>button.onclick=()=>{try{showTab(button.dataset.profileTab);}catch(error){showError('profile-error',error.message);}});
  $('profile-dialog').addEventListener('close',()=>{trialGeneration++;clearTimeout(trialTimer);});
  $('category-add').onclick=()=>{try{syncDraft();draft.categories.push({id:makeId('category'),label:'新分类',group:'',enabled:true,policy:'occurrence'});renderCategoryEditor();}catch(error){showError('profile-error',error.message);}};
  $('profile-categories').onclick=e=>{const button=e.target.closest('[data-remove-category]');if(!button)return;try{syncDraft();const i=Number(button.dataset.removeCategory),id=draft.categories[i].id;if(draft.rules.some(r=>r.category===id))throw Error('请先移除或调整使用此分类的提示规则');draft.categories.splice(i,1);renderCategoryEditor();}catch(error){showError('profile-error',error.message);}};
  $('rule-add').onclick=()=>{try{syncDraft();if(!draft.categories.length)throw Error('请先添加分类');draft.rules.push({id:makeId('rule'),category:draft.categories[0].id,trigger:'',semantic:'obtained',region:null,name_region:null,exclude:[]});renderRuleEditor();}catch(error){showError('profile-error',error.message);}};
  $('profile-rule-list').onclick=e=>{try{const remove=e.target.closest('[data-remove-rule]'),calibrate=e.target.closest('[data-calibrate-rule]');if(remove){syncDraft();draft.rules.splice(Number(remove.dataset.removeRule),1);renderRuleEditor();}if(calibrate){syncDraft();selectedRule=Number(calibrate.dataset.calibrateRule);showTab('sample');}}catch(error){showError('profile-error',error.message);}};
  $('sample-load').onclick=e=>guarded(e.currentTarget,loadSample,'profile-error');
  $('sample-import').onclick=()=>{$('profile-dialog').close();$('auto-analyze').checked=false;openImport();};
  $('sample-rule').onchange=()=>{selectedRule=Number($('sample-rule').value);drawCanvas();};
  $('sample-region').onchange=drawCanvas;
  $('sample-clear-region').onclick=()=>{if(draft?.rules[selectedRule])draft.rules[selectedRule][$('sample-region').value]=null;drawCanvas();};
  $('sample-canvas').onpointerdown=e=>{if(!image||selectedRule<0)return;const p=canvasPosition(e);drag={start:p,end:p};e.currentTarget.setPointerCapture(e.pointerId);drawCanvas();};
  $('sample-canvas').onpointermove=e=>{if(drag){drag.end=canvasPosition(e);drawCanvas();}};
  $('sample-canvas').onpointerup=e=>{if(!drag)return;const rect=dragRect(drag.start,canvasPosition(e));drag=null;if(rect.w>.005&&rect.h>.005&&draft.rules[selectedRule]){draft.rules[selectedRule][$('sample-region').value]=Object.fromEntries(Object.entries(rect).map(([key,value])=>[key,Math.round(value*100000)/100000]));}drawCanvas();};
  $('sample-canvas').onpointercancel=()=>{drag=null;drawCanvas();};
  $('trial-start-button').onclick=async e=>{const button=e.currentTarget;if(button.dataset.busy)return;button.dataset.busy='1';button.disabled=true;showError('profile-error','');try{await runTrial();}catch(error){$('trial-status').textContent='';showError('profile-error',error.message);button.disabled=false;delete button.dataset.busy;}};
  $('sample-video').onchange=()=>{image=null;drawCanvas();$('sample-time').value='00:00:00';$('trial-start').value='00:00:00';const v=S.videos.find(v=>v.id===$('sample-video').value);$('trial-end').value=tc(Math.min(Number(v?.duration)||15,15));};
  return {applyState,resetProject,switchProject,openEditor};
})();
