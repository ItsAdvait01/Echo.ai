"""In-page instrumentation script. U1 owns this module.

Playwright does not expose click/input/scroll events, so the observer injects
one small, defensive script into every document of its controlled context and
receives payloads through an exposed binding (``window.__echoEmit``).

Three deliberate properties:

* The script never reads or transmits field values. For text-like controls only
  ``filled: true|false`` leaves the page; sensitive controls emit a bare
  ``sensitive_interaction`` marker so the observer can count suppressions
  without ever seeing the value.
* Injection is idempotent *by re-attachment*: every run first removes the
  listeners registered by the previous run, then attaches a fresh set. A boolean
  "already installed" guard is not enough, because ``document.open()`` (used by
  ``document.write`` and Playwright's ``set_content``) silently drops listeners
  while window-level state survives. The observer re-runs this script on every
  load, so capture cannot end up detached, and cannot be duplicated either.
* Everything is wrapped in try/catch, so a page with unusual DOM shapes cannot
  break itself by being observed.

Python treats these payloads as untrusted: field names are whitelisted and all
strings are re-sanitized in :mod:`app.browser.sanitizer`.
"""

from __future__ import annotations

BINDING_NAME = "__echoEmit"

INIT_SCRIPT = r"""
(() => {
  "use strict";

  const HANDLERS_KEY = "__echoObserverHandlers";

  const clean = (value, limit) => {
    if (value === null || value === undefined) { return ""; }
    let text = String(value).replace(/\s+/g, " ").trim();
    if (text.length > limit) { text = text.slice(0, limit).trim(); }
    return text;
  };

  const emit = (payload) => {
    try {
      if (typeof window.__echoEmit === "function") { window.__echoEmit(payload); }
    } catch (e) { /* observation must never break the page */ }
  };

  try {
    if (!window.__echoObserverInstalled) {
      Object.defineProperty(window, "__echoObserverInstalled", { value: true, enumerable: false });
    }
  } catch (e) { /* non-fatal marker */ }

  try {
    const previous = window[HANDLERS_KEY];
    if (previous && previous.length) {
      for (let i = 0; i < previous.length; i += 1) {
        const entry = previous[i];
        try { window.removeEventListener(entry[0], entry[1], entry[2]); } catch (e) { /* ignore */ }
      }
    }
  } catch (e) { /* ignore */ }

  const registered = [];
  const listen = (type, handler, options) => {
    try {
      window.addEventListener(type, handler, options);
      registered.push([type, handler, options]);
    } catch (e) { /* ignore */ }
  };
  try { window[HANDLERS_KEY] = registered; } catch (e) { /* ignore */ }

  const MAX_TEXT = 80;
  const MAX_LOCATOR = 160;
  const SENSITIVE_HINT = /(?<![A-Za-z])(pass(word|wd|phrase)?|pwd|secret|token|api[-_]?key|auth(orization)?|bearer|otp|one[-_]?time|pin|cvv|cvc|csc|card(num(ber)?)?|credit|iban|routing|account[-_]?number|security[-_]?code|ssn|social[-_]?security|seed[-_]?phrase|session[-_]?id|recovery[-_]?code)(?![A-Za-z])/i;
  const SENSITIVE_AUTOCOMPLETE = {
    "current-password": 1, "new-password": 1, "one-time-code": 1,
    "cc-number": 1, "cc-csc": 1, "cc-exp": 1, "cc-exp-month": 1, "cc-exp-year": 1,
    "cc-name": 1, "cc-given-name": 1, "cc-family-name": 1, "cc-type": 1
  };
  const TEXT_ROLES = { button: 1, link: 1, tab: 1, menuitem: 1, summary: 1 };
  const TEST_ID_ATTRS = ["data-testid", "data-test-id", "data-test", "data-qa", "data-cy"];

  const attr = (el, name) => {
    try { return el && el.getAttribute ? el.getAttribute(name) : null; } catch (e) { return null; }
  };

  const tagOf = (el) => {
    try { return el && el.tagName ? String(el.tagName).toLowerCase() : ""; } catch (e) { return ""; }
  };

  const roleOf = (el) => {
    const explicit = clean(attr(el, "role"), 32).toLowerCase();
    if (explicit) { return explicit; }
    const tag = tagOf(el);
    const type = clean(attr(el, "type"), 32).toLowerCase();
    if (tag === "a" || tag === "area") { return attr(el, "href") ? "link" : null; }
    if (tag === "button") { return "button"; }
    if (tag === "select") { return attr(el, "multiple") !== null ? "listbox" : "combobox"; }
    if (tag === "textarea") { return "textbox"; }
    if (tag === "input") {
      if (type === "submit" || type === "button" || type === "reset" || type === "image") { return "button"; }
      if (type === "checkbox") { return "checkbox"; }
      if (type === "radio") { return "radio"; }
      if (type === "search") { return "searchbox"; }
      if (type === "range") { return "slider"; }
      if (type === "hidden" || type === "file") { return null; }
      return "textbox";
    }
    if (tag === "form") { return "form"; }
    if (tag === "img") { return "img"; }
    if (/^h[1-6]$/.test(tag)) { return "heading"; }
    if (tag === "li") { return "listitem"; }
    return null;
  };

  const labelFor = (el) => {
    try {
      const labelledBy = clean(attr(el, "aria-labelledby"), 200);
      if (labelledBy) {
        const parts = labelledBy.split(" ").map((id) => {
          const node = id ? document.getElementById(id) : null;
          return node ? node.textContent : "";
        });
        const text = clean(parts.join(" "), MAX_TEXT);
        if (text) { return text; }
      }
    } catch (e) { /* ignore malformed aria */ }
    const aria = clean(attr(el, "aria-label"), MAX_TEXT);
    if (aria) { return aria; }
    try {
      if (el.labels && el.labels.length) {
        const text = clean(Array.prototype.map.call(el.labels, (node) => node.textContent).join(" "), MAX_TEXT);
        if (text) { return text; }
      }
    } catch (e) { /* not a labelable element */ }
    try {
      const wrapper = el.closest ? el.closest("label") : null;
      if (wrapper) {
        const text = clean(wrapper.textContent, MAX_TEXT);
        if (text) { return text; }
      }
    } catch (e) { /* ignore */ }
    return "";
  };

  const accessibleName = (el) => {
    const fromLabel = labelFor(el);
    if (fromLabel) { return fromLabel; }
    const tag = tagOf(el);
    const type = clean(attr(el, "type"), 32).toLowerCase();
    if (tag === "img" || (tag === "input" && type === "image")) {
      const alt = clean(attr(el, "alt"), MAX_TEXT);
      if (alt) { return alt; }
    }
    if (tag === "input" && (type === "submit" || type === "button" || type === "reset")) {
      const value = clean(attr(el, "value"), MAX_TEXT);
      if (value) { return value; }
    }
    if (tag === "select") { return clean(attr(el, "title"), MAX_TEXT); }
    const role = roleOf(el);
    if (TEXT_ROLES[role] || tag === "button" || tag === "a" || tag === "summary") {
      const text = clean(el.innerText || el.textContent, MAX_TEXT);
      if (text) { return text; }
    }
    if (tag === "input" || tag === "textarea") {
      const placeholder = clean(attr(el, "placeholder"), MAX_TEXT);
      if (placeholder) { return placeholder; }
    }
    return clean(attr(el, "title"), MAX_TEXT);
  };

  const visibleTextOf = (el) => {
    const tag = tagOf(el);
    if (tag === "select" || tag === "form" || tag === "input" || tag === "textarea" ||
        tag === "body" || tag === "html" || tag === "option") {
      return "";
    }
    const role = roleOf(el);
    if (!TEXT_ROLES[role] && tag !== "button" && tag !== "a" && tag !== "label") {
      return "";
    }
    return clean(el.innerText || el.textContent, MAX_TEXT);
  };

  const testIdOf = (el) => {
    for (let i = 0; i < TEST_ID_ATTRS.length; i += 1) {
      const value = clean(attr(el, TEST_ID_ATTRS[i]), 64);
      if (value) { return value; }
    }
    return "";
  };

  const locatorHint = (el, name) => {
    const role = roleOf(el);
    if (role && name) {
      return clean("role=" + role + '[name="' + name.replace(/"/g, '\\"') + '"]', MAX_LOCATOR);
    }
    const testId = testIdOf(el);
    if (testId) { return '[data-testid="' + testId + '"]'; }
    const id = clean(attr(el, "id"), 40);
    if (id && /^[A-Za-z][\w\-.]*$/.test(id)) { return "#" + id; }
    const tag = tagOf(el);
    const className = clean(attr(el, "class"), 40).split(" ")[0] || "";
    if (tag && className && /^[A-Za-z][\w\-]*$/.test(className)) { return tag + "." + className; }
    return tag;
  };

  const fieldCategoryOf = (el) => {
    const tag = tagOf(el);
    if (tag === "textarea") { return "textarea"; }
    if (tag === "select") { return "select"; }
    if (tag !== "input") { return ""; }
    const type = clean(attr(el, "type"), 32).toLowerCase() || "text";
    if (type === "text") { return "text"; }
    if (type === "search" || type === "email" || type === "tel" || type === "url" ||
        type === "number" || type === "date") {
      return type;
    }
    if (type === "checkbox") { return "checkbox"; }
    if (type === "radio") { return "radio"; }
    return "other";
  };

  const sensitiveField = (el) => {
    const type = clean(attr(el, "type"), 32).toLowerCase();
    if (type === "password") { return true; }
    const autocomplete = clean(attr(el, "autocomplete"), 64).toLowerCase();
    if (autocomplete && SENSITIVE_AUTOCOMPLETE[autocomplete]) { return true; }
    const hints = [attr(el, "name"), attr(el, "id"), attr(el, "aria-label"), attr(el, "placeholder"), labelFor(el)]
      .filter((part) => part).join(" ");
    return SENSITIVE_HINT.test(hints);
  };

  const describe = (el) => {
    const descriptor = {
      tag: tagOf(el),
      role: roleOf(el) || "",
      accessible_name: accessibleName(el),
      visible_text: visibleTextOf(el),
      test_id: testIdOf(el),
      locator_hint: "",
      field_category: fieldCategoryOf(el)
    };
    descriptor.locator_hint = locatorHint(el, descriptor.accessible_name);
    return descriptor;
  };

  const reportedFields = new WeakSet();

  const onClick = (event) => {
    try {
      const el = event.target;
      if (!el || !el.tagName) { return; }
      emit({ type: "click", target: describe(el), url: location.href, title: document.title });
    } catch (e) { /* ignore */ }
  };

  const onInput = (event) => {
    try {
      const el = event.target;
      const tag = tagOf(el);
      if (tag !== "input" && tag !== "textarea") { return; }
      if (reportedFields.has(el)) { return; }
      reportedFields.add(el);
      if (sensitiveField(el)) {
        emit({ type: "sensitive_interaction", url: location.href, title: document.title });
        return;
      }
      const raw = el.value === undefined || el.value === null ? "" : String(el.value);
      emit({
        type: "text_input",
        target: describe(el),
        filled: raw.length > 0,
        url: location.href,
        title: document.title
      });
    } catch (e) { /* ignore */ }
  };

  const onChange = (event) => {
    try {
      const el = event.target;
      if (tagOf(el) !== "select") { return; }
      if (sensitiveField(el)) {
        emit({ type: "sensitive_interaction", url: location.href, title: document.title });
        return;
      }
      let label = "";
      try {
        const option = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
        label = option ? clean(option.textContent, MAX_TEXT) : "";
      } catch (e) { /* ignore */ }
      emit({
        type: "select_change",
        target: describe(el),
        option_label: label,
        option_count: el.options ? el.options.length : 0,
        url: location.href,
        title: document.title
      });
    } catch (e) { /* ignore */ }
  };

  const onSubmit = (event) => {
    try {
      const form = event.target;
      if (tagOf(form) !== "form") { return; }
      emit({
        type: "form_submit",
        target: describe(form),
        action: attr(form, "action") || "",
        method: attr(form, "method") || "",
        url: location.href,
        title: document.title
      });
    } catch (e) { /* ignore */ }
  };

  const onKeyDown = (event) => {
    try {
      const key = event.key;
      if (key !== "Enter" && key !== "Tab" && key !== "Escape") { return; }
      if (event.isComposing) { return; }
      const el = event.target;
      if (el && el.tagName && sensitiveField(el)) { return; }
      emit({
        type: "key_press",
        key: key,
        target: el && el.tagName ? describe(el) : {},
        url: location.href,
        title: document.title
      });
    } catch (e) { /* ignore */ }
  };

  let lastScrollAt = 0;
  let lastScrollY = window.scrollY || 0;
  const SCROLL_MIN_DELTA = 250;
  const SCROLL_MIN_INTERVAL = 750;

  const onScroll = () => {
    try {
      const now = Date.now();
      const y = window.scrollY || 0;
      const delta = y - lastScrollY;
      if (now - lastScrollAt < SCROLL_MIN_INTERVAL) { return; }
      if (Math.abs(delta) < SCROLL_MIN_DELTA) { return; }
      lastScrollAt = now;
      lastScrollY = y;
      emit({
        type: "scroll",
        direction: delta > 0 ? "down" : "up",
        scroll_y: Math.round(y),
        url: location.href,
        title: document.title
      });
    } catch (e) { /* ignore */ }
  };

  listen("click", onClick, true);
  listen("input", onInput, true);
  listen("change", onChange, true);
  listen("submit", onSubmit, true);
  listen("keydown", onKeyDown, true);
  listen("scroll", onScroll, { passive: true });
})();
"""

__all__ = ["BINDING_NAME", "INIT_SCRIPT"]
