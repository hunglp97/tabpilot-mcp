/**
 * Inventory the matrix/grid questions on the page and which rows are answered.
 *
 * opts: { selector?, rowSelector?, cellSelector?, limit? }
 */
(function (opts) {
  /* MATRIX_COMMON */

  var questions;
  try {
    questions = document.querySelectorAll(opts.selector || MATRIX_SELECTORS);
  } catch (e) {
    return { ok: false, error: 'Invalid selector: ' + e.message };
  }

  var limit = opts.limit || 20;
  var out = [];

  for (var q = 0; q < questions.length && out.length < limit; q++) {
    var question = questions[q];
    var rows = rowsOf(question, opts.rowSelector);
    if (!rows.length) continue;

    var rowReport = [], unanswered = [];
    var sawAnyCell = false;

    for (var r = 0; r < rows.length; r++) {
      var cells = cellsOf(rows[r], opts.cellSelector);
      if (cells.length) sawAnyCell = true;
      var answered = false, answeredAt = -1;
      for (var c = 0; c < cells.length; c++) {
        if (isAnswered(cells[c])) { answered = true; answeredAt = c; break; }
      }
      if (!answered && cells.length) unanswered.push(r);
      rowReport.push({
        index: r,
        label: textOf(rows[r], 90),
        cellCount: cells.length,
        answered: answered,
        answeredAt: answeredAt
      });
    }

    if (!sawAnyCell) continue;

    out.push({
      index: q,
      id: question.id || null,
      className: typeof question.className === 'string' ? question.className.slice(0, 120) : null,
      title: textOf(question, 140),
      rowCount: rows.length,
      unansweredCount: unanswered.length,
      unansweredRows: unanswered,
      rows: rowReport
    });
  }

  return { ok: true, questionCount: out.length, questions: out };
})
