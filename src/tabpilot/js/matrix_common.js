/* Shared matrix helpers, spliced into matrix_scan.js and matrix_click.js. */

var MATRIX_SELECTORS = '.matrix_question, .display_table, [class*="matrix"], [class*="Matrix"], ' +
                       'table[role="presentation"] , [role="grid"], [role="radiogroup"][class*="grid"]';
var ROW_SELECTORS = '[class*="RowWrapper"], [class*="rowWrapper"], .grid_row, [class*="row_wrapper"], ' +
                    '[role="row"], tbody > tr';
var CELL_SELECTORS = '.radio_button, input[type="radio"], input[type="checkbox"], ' +
                     '[role="radio"], [class*="radio"], [class*="Radio"], td';

function textOf(el, max) {
  var t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  max = max || 120;
  return t.length > max ? t.slice(0, max) + '…' : t;
}

/**
 * Whether a matrix cell is answered.
 *
 * Survey platforms rarely use a native radio — the visible control is a styled
 * div whose "on" state lives in a class name or an aria attribute. Checking only
 * ``input.checked`` reports every row as unanswered on those pages and sends the
 * caller into an endless re-answer loop, so several signals are tried in order
 * of trustworthiness.
 */
function isAnswered(cell) {
  if (!cell) return false;

  if (cell.tagName === 'INPUT' && (cell.type === 'radio' || cell.type === 'checkbox')) {
    return cell.checked;
  }
  var native = cell.querySelector && cell.querySelector('input[type="radio"], input[type="checkbox"]');
  if (native) return native.checked;

  var ariaHost = cell.getAttribute && cell.getAttribute('aria-checked') !== null
    ? cell
    : (cell.querySelector && cell.querySelector('[aria-checked]'));
  if (ariaHost) {
    var aria = ariaHost.getAttribute('aria-checked');
    if (aria !== null) return aria === 'true';
  }

  var className = typeof cell.className === 'string' ? cell.className : '';
  if (/(^|[\s_-])(selected|checked|active|is-on|is-checked)([\s_-]|$)/i.test(className)) return true;
  if (cell.querySelector && cell.querySelector(
      '[class*="selected"], [class*="Selected"], [class*="checked"], [class*="Checked"], svg[class*="check"]')) {
    return true;
  }
  return false;
}

function rowsOf(question, rowSelector) {
  var rows = question.querySelectorAll(rowSelector || ROW_SELECTORS);
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    // Keep only leaf rows; nested wrappers would double-count every row.
    if (!rows[i].querySelector(rowSelector || ROW_SELECTORS)) out.push(rows[i]);
  }
  return out;
}

function cellsOf(row, cellSelector) {
  var cells = row.querySelectorAll(cellSelector || CELL_SELECTORS);
  var out = [];
  for (var i = 0; i < cells.length; i++) {
    var cell = cells[i];
    // A <td> wrapping a radio is the same control counted twice.
    if (cell.tagName === 'TD' && cell.querySelector(
        'input[type="radio"], [role="radio"], [class*="radio"]')) continue;
    out.push(cell);
  }
  return out;
}
