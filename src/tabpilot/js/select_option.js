/**
 * Choose option(s) in a dropdown, detecting which kind of dropdown it is.
 *
 * A bare <select> and a Select2 widget need the same underlying node driven, but
 * Select2 keeps its own state in jQuery and only refreshes on a jQuery-dispatched
 * ``change`` — a native event updates the value while the visible pills stay
 * stale. React-Select has no <select> at all and cannot be driven this way; it
 * is detected and reported so the caller can drive it through the UI instead.
 *
 * opts: { selector, values: string[], by: 'value'|'label'|'auto', nth }
 */
(function (opts) {
  var nodes;
  try {
    nodes = document.querySelectorAll(opts.selector);
  } catch (e) {
    return { ok: false, error: 'Invalid selector ' + JSON.stringify(opts.selector) + ': ' + e.message };
  }
  var el = nodes[opts.nth || 0];
  if (!el) return { ok: false, error: 'No element matches ' + JSON.stringify(opts.selector) + '.' };

  var wanted = (opts.values || []).map(function (v) { return String(v); });
  var by = opts.by || 'auto';

  /* --- React-Select / Headless UI: no native <select> to drive ----------- */
  if (el.tagName.toLowerCase() !== 'select') {
    var looksReactSelect =
      /(^|[\s-])(select__control|select__value-container|css-.*-control)/.test(el.className || '') ||
      el.querySelector('[class*="-control"], [class*="__control"], [role="combobox"], [role="listbox"]') ||
      el.getAttribute('role') === 'combobox';
    return {
      ok: false,
      kind: looksReactSelect ? 'react-select' : 'unknown',
      error: looksReactSelect
        ? 'This is a React-Select style widget with no underlying <select>. It must be driven ' +
          'through the UI: open it, then click the option.'
        : 'Element <' + el.tagName.toLowerCase() + '> is not a <select>.',
      needsUiInteraction: !!looksReactSelect
    };
  }

  el.scrollIntoView({ block: 'center' });

  function labelOf(option) {
    return (option.label || option.text || '').replace(/\s+/g, ' ').trim();
  }

  function matches(option, needle) {
    if (by === 'value') return option.value === needle;
    if (by === 'label') return labelOf(option) === needle;
    return option.value === needle || labelOf(option) === needle;
  }

  var options = Array.prototype.slice.call(el.options);
  var selected = [], missing = [];

  for (var w = 0; w < wanted.length; w++) {
    var hit = null;
    for (var o = 0; o < options.length; o++) {
      if (matches(options[o], wanted[w])) { hit = options[o]; break; }
    }
    if (hit) selected.push(hit); else missing.push(wanted[w]);
  }

  if (missing.length) {
    return {
      ok: false,
      kind: el.multiple ? 'select-multiple' : 'select',
      error: 'No option for: ' + missing.join(', '),
      available: options.slice(0, 40).map(function (o) { return { value: o.value, label: labelOf(o) }; }),
      optionCount: options.length
    };
  }

  if (!el.multiple && selected.length > 1) {
    return { ok: false, error: 'This <select> is single-choice but ' + selected.length + ' values were given.' };
  }

  for (var i = 0; i < options.length; i++) {
    options[i].selected = selected.indexOf(options[i]) !== -1;
  }

  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));

  var select2Refreshed = false;
  if (window.jQuery) {
    try {
      window.jQuery(el).trigger('change');
      // Select2 v4 listens for this to repaint its pills.
      window.jQuery(el).trigger('change.select2');
      select2Refreshed = true;
    } catch (e) { /* jQuery present but not Select2 */ }
  }

  var isSelect2 = !!(el.className && /select2/.test(el.className)) ||
                  !!(el.nextElementSibling && /select2/.test(el.nextElementSibling.className || ''));

  return {
    ok: true,
    kind: isSelect2 ? 'select2' : (el.multiple ? 'select-multiple' : 'select'),
    selected: selected.map(function (o) { return { value: o.value, label: labelOf(o) }; }),
    select2Refreshed: select2Refreshed && isSelect2,
    optionCount: options.length
  };
})
