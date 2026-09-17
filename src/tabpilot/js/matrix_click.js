/**
 * Answer exactly ONE row of ONE matrix question, then report the resulting state.
 *
 * One row per call is deliberate. Clicking many rows inside a single JavaScript
 * task lets React batch the state updates, and the component commits only the
 * last one — the earlier rows silently stay blank, submit fails validation, and
 * the page appears to loop forever. The caller drives the rows in sequence with a
 * delay between them so each update commits on its own.
 *
 * opts: { selector?, questionIndex, rowIndex, columnIndex, rowSelector?, cellSelector? }
 */
(function (opts) {
  /* MATRIX_COMMON */

  var questions;
  try {
    questions = document.querySelectorAll(opts.selector || MATRIX_SELECTORS);
  } catch (e) {
    return { ok: false, error: 'Invalid selector: ' + e.message };
  }

  // Re-apply the same "has answerable rows" filter matrix_scan uses, so the
  // indices the caller was handed still mean the same questions.
  var usable = [];
  for (var q = 0; q < questions.length; q++) {
    var rows = rowsOf(questions[q], opts.rowSelector);
    if (!rows.length) continue;
    var hasCells = false;
    for (var r = 0; r < rows.length && !hasCells; r++) {
      if (cellsOf(rows[r], opts.cellSelector).length) hasCells = true;
    }
    if (hasCells) usable.push(questions[q]);
  }

  var question = usable[opts.questionIndex || 0];
  if (!question) {
    return { ok: false, error: 'No matrix question at index ' + (opts.questionIndex || 0) +
                              ' (found ' + usable.length + ').' };
  }

  var allRows = rowsOf(question, opts.rowSelector);
  var row = allRows[opts.rowIndex];
  if (!row) {
    return { ok: false, error: 'No row at index ' + opts.rowIndex + ' (question has ' + allRows.length + ').' };
  }

  var cells = cellsOf(row, opts.cellSelector);
  if (!cells.length) return { ok: false, error: 'Row ' + opts.rowIndex + ' has no answerable cells.' };

  var column = opts.columnIndex;
  if (column == null) column = 0;
  if (column < 0) column = cells.length + column;
  var cell = cells[column];
  if (!cell) {
    return { ok: false, error: 'No cell at column ' + opts.columnIndex +
                              ' (row has ' + cells.length + ').' , cellCount: cells.length };
  }

  if (isAnswered(cell)) {
    return { ok: true, alreadyAnswered: true, rowIndex: opts.rowIndex, columnIndex: column,
             label: textOf(row, 90), cellCount: cells.length };
  }

  cell.scrollIntoView({ block: 'center' });

  var target = cell;
  // A styled wrapper usually delegates to a hidden native input; clicking the
  // label-like wrapper is what a human hits, so prefer it, but fall back to the
  // input when the wrapper ignores clicks.
  var rect = target.getBoundingClientRect();
  if (!rect.width && !rect.height) {
    var inner = cell.querySelector('label, [role="radio"], input[type="radio"]');
    if (inner) target = inner;
  }

  try { target.click(); } catch (e) {
    return { ok: false, error: 'Clicking the cell threw: ' + e.message };
  }

  return {
    ok: true,
    clicked: true,
    rowIndex: opts.rowIndex,
    columnIndex: column,
    label: textOf(row, 90),
    cellCount: cells.length,
    // Custom controls repaint on the next frame, so this can still read false
    // right after the click. The caller re-scans after its delay.
    answeredNow: isAnswered(cell)
  };
})
