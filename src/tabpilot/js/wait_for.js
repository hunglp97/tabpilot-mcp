/**
 * Evaluate a wait condition once. The caller polls; the page only answers.
 *
 * opts: { selector?, state, text?, predicate? }
 * state: present | absent | visible | hidden | enabled | text
 */
(function (opts) {
  if (opts.predicate) {
    var value;
    try {
      value = eval(opts.predicate);
    } catch (e) {
      return { ok: false, error: 'Predicate threw: ' + e.message };
    }
    return { ok: true, satisfied: !!value, detail: 'predicate -> ' + String(value) };
  }

  var nodes;
  try {
    nodes = document.querySelectorAll(opts.selector);
  } catch (e) {
    return { ok: false, error: 'Invalid selector ' + JSON.stringify(opts.selector) + ': ' + e.message };
  }

  function visible(el) {
    var rect = el.getBoundingClientRect();
    if (!rect.width && !rect.height) return false;
    var style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
  }

  var state = opts.state || 'visible';
  var el = nodes[0];
  var satisfied = false, detail = '';

  if (state === 'present') {
    satisfied = nodes.length > 0;
    detail = nodes.length + ' match(es)';
  } else if (state === 'absent') {
    satisfied = nodes.length === 0;
    detail = nodes.length + ' match(es)';
  } else if (state === 'visible') {
    for (var i = 0; i < nodes.length && !satisfied; i++) satisfied = visible(nodes[i]);
    detail = satisfied ? 'visible' : (nodes.length ? 'present but not visible' : 'not in the DOM');
  } else if (state === 'hidden') {
    satisfied = nodes.length === 0 || !visible(nodes[0]);
    detail = satisfied ? 'hidden or gone' : 'still visible';
  } else if (state === 'enabled') {
    satisfied = !!el && !el.disabled && visible(el);
    detail = !el ? 'not in the DOM' : (el.disabled ? 'disabled' : (visible(el) ? 'enabled' : 'not visible'));
  } else if (state === 'text') {
    var needle = (opts.text || '').trim().toLowerCase();
    var haystack = el ? (el.innerText || el.textContent || '') : document.body.innerText || '';
    satisfied = haystack.replace(/\s+/g, ' ').toLowerCase().indexOf(needle) !== -1;
    detail = satisfied ? 'text found' : 'text not found';
  } else {
    return { ok: false, error: 'Unknown state ' + JSON.stringify(state) + '.' };
  }

  return { ok: true, satisfied: satisfied, detail: detail, matched: nodes.length,
           readyState: document.readyState };
})
