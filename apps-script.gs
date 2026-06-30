/**
 * Apps Script для Google-таблицы прогресса онбординга.
 * При обновлении кода: Развернуть → Управление развёртываниями → ✏️ →
 * Версия: Новая версия → Развернуть.
 */

// ВАЖНО: впиши сюда тот же секрет, что в APPS_SCRIPT_SECRET у бота.
var SECRET = "ВПИШИ_СЕКРЕТ_КАК_В_БОТЕ";
var TASKS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "13", "14", "15", "16"];

function header() {
  var h = ["user_id", "имя", "username", "старт", "обновлено"];
  for (var i = 0; i < TASKS.length; i++) h.push("задание " + TASKS[i]);
  h.push("вопросы");
  h.push("напоминания");
  h.push("ФИО");
  h.push("класс");
  return h;
}

function sheet_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var H = header();
  if (sh.getLastRow() === 0) {
    sh.appendRow(H);
  } else {
    var cur = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), H.length)).getValues()[0];
    for (var i = 0; i < H.length; i++) {
      if (cur[i] !== H[i]) sh.getRange(1, i + 1).setValue(H[i]);
    }
  }
  return sh;
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return json({ ok: false, error: "bad secret" });
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try { handle(data); } finally { lock.releaseLock(); }
    return json({ ok: true });
  } catch (err) {
    return json({ ok: false, error: String(err) });
  }
}

function handle(d) {
  var sh = sheet_();
  var H = header();
  var row = -1;
  if (sh.getLastRow() >= 2) {
    var ids = sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues();
    for (var i = 0; i < ids.length; i++) {
      if (String(ids[i][0]) === String(d.user_id)) { row = i + 2; break; }
    }
  }
  if (row === -1) {
    var newRow = [d.user_id, d.name, d.username, d.ts, d.ts];
    for (var t = 0; t < TASKS.length; t++) newRow.push("");
    newRow.push(""); // вопросы
    newRow.push(""); // напоминания
    newRow.push(""); // ФИО
    newRow.push(""); // класс
    sh.appendRow(newRow);
    row = sh.getLastRow();
  }

  sh.getRange(row, H.indexOf("обновлено") + 1).setValue(d.ts);

  if (d.status === "register") {
    if (d.fio) sh.getRange(row, H.indexOf("ФИО") + 1).setValue(d.fio);
    if (d.group) sh.getRange(row, H.indexOf("класс") + 1).setValue(d.group);
  }
  if (d.status === "done" && !d.step && d.task) {
    var col = H.indexOf("задание " + d.task) + 1;
    if (col > 0) sh.getRange(row, col).setValue("✅");
  }
  if (d.status === "question") {
    var qcol = H.indexOf("вопросы") + 1;
    var prev = sh.getRange(row, qcol).getValue();
    var note = "[задание " + d.task + (d.stepTitle ? " / " + d.stepTitle : "") + "] " + (d.comment || "");
    sh.getRange(row, qcol).setValue((prev ? prev + "\n" : "") + note);
  }
  if (d.status === "remind" && d.remind_key) {
    var rcol = H.indexOf("напоминания") + 1;
    var p = sh.getRange(row, rcol).getValue();
    sh.getRange(row, rcol).setValue((p ? p + " " : "") + d.remind_key);
  }
}

function doGet(e) {
  if (!e || !e.parameter || e.parameter.secret !== SECRET) {
    return json({ ok: false, error: "bad secret" });
  }
  var sh = sheet_();
  var H = header();
  var idxStart = H.indexOf("старт");
  var idxRem = H.indexOf("напоминания");
  var idxFio = H.indexOf("ФИО");
  var idxClass = H.indexOf("класс");
  var taskIdx = {};
  for (var t = 0; t < TASKS.length; t++) taskIdx[TASKS[t]] = H.indexOf("задание " + TASKS[t]);

  var users = [];
  if (sh.getLastRow() >= 2) {
    var data = sh.getRange(2, 1, sh.getLastRow() - 1, H.length).getValues();
    for (var r = 0; r < data.length; r++) {
      var row = data[r];
      if (!row[0]) continue;
      var done = [];
      for (var k = 0; k < TASKS.length; k++) {
        if (row[taskIdx[TASKS[k]]]) done.push(TASKS[k]);
      }
      var start = row[idxStart];
      if (start instanceof Date) start = Utilities.formatDate(start, "Etc/GMT", "yyyy-MM-dd");
      users.push({
        user_id: String(row[0]),
        start: String(start || ""),
        reminders: String(row[idxRem] || ""),
        fio: String(row[idxFio] || ""),
        group: String(row[idxClass] || ""),
        done: done
      });
    }
  }
  return json({ ok: true, users: users });
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
