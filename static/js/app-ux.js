/* UX layer: behaviour only, no change to routes, forms or business rules.
   - unsaved changes: indicator, warning before leaving, warning when another form on the page would lose input
   - save: one click only ("Записване…"), scroll position kept after the page reloads
   - keyboard: Enter moves to the next field in measurement tables, Ctrl+S saves the changed form
   - data age: values older than 2 days are marked */
(() => {
  "use strict";

  const SCROLL_KEY = "app-ux-scroll";
  const dirty = new Set();
  let indicator = null;

  const isTracked = form =>
    (form.getAttribute("method") || "get").toLowerCase() === "post" && !form.hasAttribute("data-ux-skip");

  function formName(form) {
    for (let el = form, i = 0; el && el !== document.body && i < 6; el = el.parentElement, i++) {
      const head = el.querySelector("h2, h3, h4, legend");
      if (head && head.textContent.trim()) return head.textContent.replace(/\s+/g, " ").trim();
    }
    return "формата";
  }

  function renderIndicator() {
    if (!indicator) {
      indicator = document.createElement("div");
      indicator.className = "ux-unsaved";
      indicator.setAttribute("role", "status");
      indicator.hidden = true;
      document.body.appendChild(indicator);
    }
    const names = [...dirty].map(formName);
    indicator.hidden = names.length === 0;
    indicator.textContent = names.length ? "Незаписани промени: " + names.join(", ") : "";
    document.querySelectorAll("form").forEach(f => {
      f.querySelectorAll("button[type='submit'], button:not([type]), input[type='submit']")
        .forEach(b => b.classList.toggle("ux-attn", dirty.has(f)));
    });
  }

  function markDirty(event) {
    const form = event.target.form || event.target.closest?.("form");
    if (!form || !isTracked(form) || event.target.type === "hidden") return;
    if (!dirty.has(form)) { dirty.add(form); renderIndicator(); }
  }
  document.addEventListener("input", markDirty, true);
  document.addEventListener("change", markDirty, true);

  /* Warn before a submit throws away input typed into a different form on the same page. */
  document.addEventListener("submit", event => {
    const form = event.target;
    const others = [...dirty].filter(f => f !== form && document.contains(f));
    if (others.length && !window.confirm(
      "Имате незаписани промени в: " + others.map(formName).join(", ") +
      ".\nАко продължите, те ще бъдат загубени. Продължаване?")) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  /* Runs after the page's own submit handlers, so validation, confirm dialogs and fetch saves are respected. */
  document.addEventListener("submit", event => {
    const form = event.target;
    dirty.delete(form);
    renderIndicator();
    if (event.defaultPrevented) return;
    dirty.clear();
    try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ path: location.pathname, y: scrollY, t: Date.now() })); } catch (e) {}
    const button = event.submitter || form.querySelector("button[type='submit'], button:not([type]), input[type='submit']");
    if (button && !button.disabled) {
      setTimeout(() => {
        button.dataset.uxLabel = button.tagName === "INPUT" ? button.value : button.innerHTML;
        if (button.tagName === "INPUT") button.value = "Записване…"; else button.textContent = "Записване…";
        button.disabled = true;
        button.classList.add("ux-busy");
      }, 0);
    }
  });

  window.addEventListener("beforeunload", event => {
    if ([...dirty].some(f => document.contains(f))) { event.preventDefault(); event.returnValue = ""; }
  });

  /* Back button restores the page from cache: give buttons back. */
  window.addEventListener("pageshow", () => {
    document.querySelectorAll(".ux-busy").forEach(b => {
      if (b.tagName === "INPUT") b.value = b.dataset.uxLabel; else b.innerHTML = b.dataset.uxLabel;
      b.disabled = false;
      b.classList.remove("ux-busy");
    });
  });

  function restoreScroll() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || "null");
      sessionStorage.removeItem(SCROLL_KEY);
      if (saved && saved.path === location.pathname && Date.now() - saved.t < 30000 && !location.hash) {
        requestAnimationFrame(() => scrollTo(0, saved.y));
      }
    } catch (e) {}
  }

  /* Enter in a measurement table goes to the next field instead of sending a half-filled table. */
  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.defaultPrevented || event.isComposing) return;
    const field = event.target;
    if (!(field instanceof HTMLInputElement) || !field.form || !field.closest("table")) return;
    if (["checkbox", "radio", "submit", "button", "file"].includes(field.type)) return;
    const fields = [...field.form.querySelectorAll("input, select, textarea")].filter(f =>
      f.type !== "hidden" && !f.disabled && !f.readOnly && f.offsetParent !== null &&
      !["submit", "button", "checkbox", "radio", "file"].includes(f.type));
    const next = fields[fields.indexOf(field) + (event.shiftKey ? -1 : 1)];
    if (!next) return;
    event.preventDefault();
    next.focus();
    if (typeof next.select === "function") next.select();
  });

  /* Ctrl+S saves when exactly one form has changes. */
  document.addEventListener("keydown", event => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "s") return;
    const forms = [...dirty].filter(f => document.contains(f));
    if (!forms.length) return;
    event.preventDefault();
    if (forms.length === 1) {
      const f = forms[0];
      if (typeof f.requestSubmit === "function") f.requestSubmit(); else f.submit();
    } else if (indicator) {
      indicator.classList.remove("ux-flash"); void indicator.offsetWidth; indicator.classList.add("ux-flash");
    }
  });

  /* Overwriting a value in a table: select it on focus. */
  document.addEventListener("focusin", event => {
    const f = event.target;
    if (f instanceof HTMLInputElement && f.closest("table") && (f.type === "text" || f.type === "number") && !f.readOnly) {
      setTimeout(() => { try { f.select(); } catch (e) {} }, 0);
    }
  });

  const pad = n => String(n).padStart(2, "0");
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const parseIso = s => {
    let m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    m = /^(\d{2})\.(\d{2})\.(\d{4})/.exec(s || "");
    return m ? new Date(+m[3], +m[2] - 1, +m[1]) : null;
  };

  /* Data age: <el data-ux-age="2026-06-16"> */
  function markAge(root) {
    root.querySelectorAll("[data-ux-age]").forEach(el => {
      const d = parseIso(el.dataset.uxAge);
      if (!d) return;
      const days = Math.round((parseIso(todayIso()) - d) / 86400000);
      const limit = Number(el.dataset.uxAgeLimit || 2);
      if (days > limit) {
        el.classList.add("ux-stale");
        if (el.dataset.uxStaleText) el.textContent = el.dataset.uxStaleText;
        el.title = `Последна стойност преди ${days} дни`;
        const note = el.parentElement && el.parentElement.querySelector("[data-ux-age-note]");
        if (note) note.textContent += ` · преди ${days} дни`;
      }
    });
  }

  function init() {
    restoreScroll();
    markAge(document);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
