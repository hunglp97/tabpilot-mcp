/**
 * Set a form field's value so a modern framework actually notices.
 *
 * Two things go wrong when people do this by hand. First, assigning ``.value``
 * directly is invisible to React: React caches the last value it wrote on the
 * node, sees no difference, and drops the event — so the field looks filled but
 * the component state is empty and submit fails validation. Calling the
 * prototype's native setter defeats that cache. Second, ``input`` alone is not
 * enough for libraries that listen on ``change`` (and vice versa), so both fire.
 *
 * opts: { selector, value, clear, nth }
 */
(function (opts) {
  var nodes;
  try {
    nodes = document.querySelectorAll(opts.selector);
  } catch (e) {
    return { ok: false, error: 'Invalid selector ' + JSON.stringify(opts.selector) + ': ' + e.message };
  }
  var el = nodes[opts.nth || 0];
  if (!el) {
    return { ok: false, error: 'No element matches ' + JSON.stringify(opts.selector) +
                              (opts.nth ? ' at nth=' + opts.nth : '') + '.' };
  }
  if (el.disabled) return { ok: false, error: 'That field is disabled.' };
  if (el.readOnly) return { ok: false, error: 'That field is read-only.' };

  el.scrollIntoView({ block: 'center' });
  try { el.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }

  var tag = el.tagName.toLowerCase();
  var value = opts.value == null ? '' : String(opts.value);

  if (el.isContentEditable) {
    if (opts.clear !== false) el.textContent = '';
    el.textContent = value;
    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, tag: tag, kind: 'contenteditable', value: el.textContent };
  }

  if (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) {
    var wanted = value === 'true' || value === '1' || value === 'on' || value === 'checked';
    if (el.checked !== wanted) el.click();
    return { ok: true, tag: tag, kind: el.type, checked: el.checked };
  }

  if (tag !== 'input' && tag !== 'textarea') {
    return { ok: false, error: 'Cannot fill a <' + tag + '>. Use click or select_option instead.' };
  }

  var prototype = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  var descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
  var setValue = descriptor && descriptor.set
    ? function (v) { descriptor.set.call(el, v); }
    : function (v) { el.value = v; };

  if (opts.clear !== false) {
    setValue('');
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  setValue(value);

  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  if (window.jQuery) {
    try { window.jQuery(el).trigger('input').trigger('change'); } catch (e) { /* jQuery-free page */ }
  }

  return { ok: true, tag: tag, kind: el.type || 'text', value: el.value,
            matchedValue: el.value === value };
})
