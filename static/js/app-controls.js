(() => {
  'use strict';

  function localizeFileInputs(root = document) {
    root.querySelectorAll('input[type="file"]:not([data-app-file-ready])').forEach((input) => {
      input.dataset.appFileReady = '1';
      const wrapper = document.createElement('span');
      wrapper.className = 'app-file-control';
      input.parentNode.insertBefore(wrapper, input);
      wrapper.appendChild(input);

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'app-file-button';
      button.textContent = 'Избери файл';

      const name = document.createElement('span');
      name.className = 'app-file-name';
      name.textContent = 'Няма избран файл';

      wrapper.append(button, name);
      button.addEventListener('click', () => { if (!input.disabled) input.click(); });
      input.addEventListener('change', () => {
        const files = Array.from(input.files || []);
        name.textContent = files.length ? files.map((file) => file.name).join(', ') : 'Няма избран файл';
        name.title = name.textContent;
      });
      if (input.disabled) wrapper.classList.add('is-disabled');
    });
  }

  const months = ['януари','февруари','март','април','май','юни','юли','август','септември','октомври','ноември','декември'];
  const weekdays = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд'];
  const parseIso = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
    if (!match) return null;
    const date = new Date(+match[1], +match[2] - 1, +match[3]);
    return date.getFullYear() === +match[1] && date.getMonth() === +match[2] - 1 && date.getDate() === +match[3] ? date : null;
  };
  const parseBg = (value) => {
    const match = /^(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})$/.exec(String(value || '').trim());
    if (!match) return null;
    const date = new Date(+match[3], +match[2] - 1, +match[1]);
    return date.getFullYear() === +match[3] && date.getMonth() === +match[2] - 1 && date.getDate() === +match[1] ? date : null;
  };
  const toIso = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const toBg = (d) => `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}.${d.getFullYear()}`;
  const sameDay = (a,b) => !!a && !!b && a.getFullYear()===b.getFullYear() && a.getMonth()===b.getMonth() && a.getDate()===b.getDate();

  function initDateControl(control) {
    if (control.dataset.appDateReady) return;
    control.dataset.appDateReady = '1';
    const display = control.querySelector('[data-app-date-display]');
    const value = control.querySelector('[data-app-date-value]');
    const trigger = control.querySelector('[data-app-date-trigger]');
    if (!display || !value || !trigger) return;
    const output = control.dataset.dateOutput === 'bg' ? 'bg' : 'iso';
    const lockedMonthMatch = /^(\d{4})-(\d{2})$/.exec(control.dataset.dateLockMonth || '');
    const lockedMonth = lockedMonthMatch ? new Date(+lockedMonthMatch[1], +lockedMonthMatch[2] - 1, 1) : null;
    let selected = parseIso(value.value) || parseBg(value.value) || parseBg(display.value) || null;
    let view = selected
      ? new Date(selected.getFullYear(), selected.getMonth(), 1)
      : lockedMonth
        ? new Date(lockedMonth.getFullYear(), lockedMonth.getMonth(), 1)
        : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    if (selected) {
      display.value = toBg(selected);
      value.value = output === 'bg' ? toBg(selected) : toIso(selected);
    } else {
      display.value = '';
      value.value = '';
    }

    const picker = document.createElement('div');
    picker.className = 'app-bg-datepicker';
    picker.hidden = true;
    picker.innerHTML = `<div class="app-bg-datepicker-head"><button type="button" data-prev aria-label="Предишен месец">‹</button><div class="app-bg-datepicker-title" data-title></div><button type="button" data-next aria-label="Следващ месец">›</button></div><div class="app-bg-datepicker-weekdays">${weekdays.map(d=>`<span>${d}</span>`).join('')}</div><div class="app-bg-datepicker-days" data-days></div><div class="app-bg-datepicker-actions"><button type="button" data-today>Днес</button><button type="button" data-clear>Изчисти</button></div>`;
    document.body.appendChild(picker);
    const title = picker.querySelector('[data-title]');
    const days = picker.querySelector('[data-days]');
    const prevButton = picker.querySelector('[data-prev]');
    const nextButton = picker.querySelector('[data-next]');
    const todayButton = picker.querySelector('[data-today]');

    if (lockedMonth) {
      prevButton.hidden = true;
      nextButton.hidden = true;
      const today = new Date();
      todayButton.hidden = today.getFullYear() !== lockedMonth.getFullYear() || today.getMonth() !== lockedMonth.getMonth();
    }

    const sync = (date) => {
      selected = date;
      display.value = date ? toBg(date) : '';
      value.value = date ? (output === 'bg' ? toBg(date) : toIso(date)) : '';
      display.setCustomValidity('');
      value.dispatchEvent(new Event('change', { bubbles:true }));
    };
    const render = () => {
      if (lockedMonth) view = new Date(lockedMonth.getFullYear(), lockedMonth.getMonth(), 1);
      title.textContent = `${months[view.getMonth()]} ${view.getFullYear()}`;
      days.innerHTML = '';
      const first = new Date(view.getFullYear(), view.getMonth(), 1);
      const start = new Date(first); start.setDate(1 - ((first.getDay()+6)%7));
      const today = new Date();
      for (let i=0;i<42;i++) {
        const d = new Date(start); d.setDate(start.getDate()+i);
        const b = document.createElement('button'); b.type='button'; b.className='app-bg-datepicker-day'; b.textContent=String(d.getDate());
        const outside = d.getMonth()!==view.getMonth();
        if (outside) b.classList.add('is-outside');
        if (lockedMonth && outside) { b.disabled = true; b.setAttribute('aria-disabled','true'); }
        if (sameDay(d,today)) b.classList.add('is-today');
        if (sameDay(d,selected)) b.classList.add('is-selected');
        if (!b.disabled) b.addEventListener('click',()=>{ sync(d); close(); });
        days.appendChild(b);
      }
    };
    const position = () => {
      picker.hidden=false; picker.style.visibility='hidden';
      const rect=control.getBoundingClientRect(), pr=picker.getBoundingClientRect(), m=8;
      let left=Math.max(m,Math.min(rect.left,window.innerWidth-pr.width-m));
      let top=rect.bottom+6; if(top+pr.height>window.innerHeight-m) top=rect.top-pr.height-6;
      picker.style.left=`${Math.round(left)}px`; picker.style.top=`${Math.round(Math.max(m,top))}px`; picker.style.visibility='visible';
    };
    const open = () => {
      const d=parseBg(display.value)||parseIso(value.value)||null;
      selected=d;
      view=d ? new Date(d.getFullYear(),d.getMonth(),1) : lockedMonth ? new Date(lockedMonth) : new Date(new Date().getFullYear(),new Date().getMonth(),1);
      render(); position();
    };
    const close = () => { picker.hidden=true; };

    trigger.addEventListener('click',(e)=>{e.stopPropagation(); picker.hidden?open():close();});
    display.addEventListener('click',(e)=>{e.stopPropagation(); open();});
    display.addEventListener('input',()=>{ const digits=display.value.replace(/\D/g,'').slice(0,8); display.value=digits.length>4?`${digits.slice(0,2)}.${digits.slice(2,4)}.${digits.slice(4)}`:digits.length>2?`${digits.slice(0,2)}.${digits.slice(2)}`:digits; });
    display.addEventListener('blur',()=>{
      if(!display.value.trim()) return;
      const d=parseBg(display.value);
      const inLockedMonth = !lockedMonth || (d && d.getFullYear()===lockedMonth.getFullYear() && d.getMonth()===lockedMonth.getMonth());
      if(d && inLockedMonth) sync(d);
      else display.setCustomValidity(lockedMonth ? 'Избери дата от показания месец.' : 'Въведи валидна дата във формат дд.мм.гггг.');
    });
    prevButton.addEventListener('click',()=>{if(!lockedMonth){view.setMonth(view.getMonth()-1);render();}});
    nextButton.addEventListener('click',()=>{if(!lockedMonth){view.setMonth(view.getMonth()+1);render();}});
    todayButton.addEventListener('click',()=>{sync(new Date());close();});
    picker.querySelector('[data-clear]').addEventListener('click',()=>{sync(null);close();});
    picker.addEventListener('click',(e)=>e.stopPropagation());
    document.addEventListener('click',close);
    window.addEventListener('resize',close);
    control.closest('form')?.addEventListener('submit',(e)=>{
      const d=parseBg(display.value);
      const inLockedMonth = !lockedMonth || (d && d.getFullYear()===lockedMonth.getFullYear() && d.getMonth()===lockedMonth.getMonth());
      if(!d || !inLockedMonth){
        e.preventDefault();
        display.setCustomValidity(lockedMonth ? 'Избери дата от показания месец.' : 'Избери валидна дата.');
        display.reportValidity();
      } else sync(d);
    });
  }

  function init(root=document){ localizeFileInputs(root); root.querySelectorAll('[data-app-date-control]').forEach(initDateControl); }
  window.AppControls = Object.assign(window.AppControls || {}, { init });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',()=>init()); else init();
})();
