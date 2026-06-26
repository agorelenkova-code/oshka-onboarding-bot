/**
 * Apps Script для Google-таблицы прогресса онбординга.
 *
 * Установка:
 *   1) Открой свою таблицу → меню «Расширения» → «Apps Script».
 *   2) Удали пустой код, вставь весь этот файл.
 *   3) Вверху «Сохранить» (значок дискеты).
 *   4) Кнопка «Развернуть» (Deploy) → «Новое развёртывание» → тип «Веб-приложение»:
 *        • «Запуск от имени»: Я (твой аккаунт)
 *        • «У кого есть доступ»: Все (Anyone)
 *      → «Развернуть». Скопируй URL веб-приложения (.../exec) и пришли его мне.
 *   5) При первом деплое Google попросит «Разрешить доступ» — подтверди своим аккаунтом.
 *
 * Секрет ниже должен совпадать с APPS_SCRIPT_SECRET в .env бота.
 */

var SECRET = "7BTVjRw2pphSyxfpHexWwe3qIlg5tcVb";
var TASKS = ["1","2","3","4","5","6","7","8","9","13","14","15","16"];

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

function header() {
  var h = ["user_id", "имя", "username", "старт", "обновлено"];
  for (var i = 0; i < TASKS.length; i++) h.push("задание " + TASKS[i]);
  h.push("вопросы");
  return h;
}

function handle(d) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  var H = header();
  if (sh.getLastRow() === 0) sh.appendRow(H);

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
    newRow.push("");
    sh.appendRow(newRow);
    row = sh.getLastRow();
  }

  sh.getRange(row, H.indexOf("обновлено") + 1).setValue(d.ts);

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
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
