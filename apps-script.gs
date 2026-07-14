/**
 * Apps Script для Google-таблицы прогресса онбординга.
 * При обновлении кода: Развернуть → Управление развёртываниями → ✏️ →
 * Версия: Новая версия → Развернуть.
 */

// ВАЖНО: впиши сюда тот же секрет, что в APPS_SCRIPT_SECRET у бота.
var SECRET = "ВПИШИ_СЕКРЕТ_КАК_В_БОТЕ";
// Токен для веб-прогресса (пользователи без Telegram). Такой же, как window.TG_WEB_TOKEN в config.js.
// Разрешает ТОЛЬКО дозапись прогресса (doPost), чтение (doGet) по-прежнему требует SECRET.
var WEB_TOKEN = "qR6Fg9Q32QfoMAmLO4LsggcI";
// Куда бот принимает веб-вопросы, чтобы переслать их кураторам в Telegram.
var BOT_NOTIFY_URL = "https://oshka-onboarding-bot.onrender.com/web-question";
var TASKS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "13", "14", "15", "16", "18", "19", "20", "21"];

function header() {
  var h = ["user_id", "имя", "username", "старт", "обновлено"];
  for (var i = 0; i < TASKS.length; i++) h.push("задание " + TASKS[i]);
  h.push("вопросы");
  h.push("напоминания");
  h.push("ФИО");
  h.push("класс");
  h.push("тариф");
  h.push("роль");
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
    var okAuth = (data.secret === SECRET) || (WEB_TOKEN && data.webtoken === WEB_TOKEN);
    if (!okAuth) return json({ ok: false, error: "bad auth" });
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
    newRow.push(""); // тариф
    newRow.push(""); // роль
    sh.appendRow(newRow);
    row = sh.getLastRow();
  }

  sh.getRange(row, H.indexOf("обновлено") + 1).setValue(d.ts);

  if (d.status === "register") {
    if (d.fio) sh.getRange(row, H.indexOf("ФИО") + 1).setValue(d.fio);
    if (d.group) sh.getRange(row, H.indexOf("класс") + 1).setValue(d.group);
    if (d.tariff) sh.getRange(row, H.indexOf("тариф") + 1).setValue(d.tariff);
    if (d.role) sh.getRange(row, H.indexOf("роль") + 1).setValue(d.role);
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
    // Веб-вопрос (без Telegram) — просим бота переслать его в чат кураторов.
    if (BOT_NOTIFY_URL && String(d.user_id || "").indexOf("web:") === 0) {
      var fio = sh.getRange(row, H.indexOf("ФИО") + 1).getValue();
      var klass = sh.getRange(row, H.indexOf("класс") + 1).getValue();
      try {
        UrlFetchApp.fetch(BOT_NOTIFY_URL, {
          method: "post", contentType: "application/json",
          payload: JSON.stringify({
            secret: SECRET,
            name: fio || d.name || "",
            email: String(d.user_id).replace(/^web:/, ""),
            group: klass || "",
            task: d.task, stepTitle: d.stepTitle || "", comment: d.comment || ""
          }),
          muteHttpExceptions: true
        });
      } catch (e2) { /* вопрос уже в таблице — пинг best-effort */ }
    }
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
  var action = e.parameter.action || "users";

  // Чтение вкладок. Необязательный ?file=<id> — читать другой файл (по умолчанию текущий).
  // ?action=sheets[&file=…]        — список вкладок
  // ?action=sheet&name=…[&file=…]  — содержимое вкладки по имени
  if (action === "sheets" || action === "sheet") {
    var ss;
    try {
      ss = e.parameter.file ? SpreadsheetApp.openById(e.parameter.file)
                            : SpreadsheetApp.getActiveSpreadsheet();
    } catch (err) {
      return json({ ok: false, error: "no access to file: " + String(err) });
    }
    if (action === "sheets") {
      return json({ ok: true, sheets: ss.getSheets().map(function (s) { return s.getName(); }) });
    }
    var target = ss.getSheetByName(e.parameter.name || "");
    if (!target) return json({ ok: false, error: "no sheet: " + (e.parameter.name || "") });
    return json({ ok: true, name: e.parameter.name, rows: target.getDataRange().getValues() });
  }

  // Картинки из Google-дока с учётом ВКЛАДОК (tabs):
  //  ?action=doctabs&file=<docId>                — список вкладок + число картинок
  //  ?action=docimg&file=<docId>&tab=<t>&i=<n>   — картинка n из вкладки t (base64)
  if (action === "doctabs" || action === "docimg") {
    var doc;
    try { doc = DocumentApp.openById(e.parameter.file); }
    catch (err) { return json({ ok: false, error: "no doc: " + String(err) }); }
    var tabs = [];
    function walk(tb) {
      tabs.push(tb);
      var ch = tb.getChildTabs ? tb.getChildTabs() : [];
      for (var j = 0; j < ch.length; j++) walk(ch[j]);
    }
    var top = doc.getTabs ? doc.getTabs() : [];
    for (var i = 0; i < top.length; i++) walk(top[i]);
    function bodyOf(ti) {
      return tabs.length ? tabs[ti].asDocumentTab().getBody() : doc.getBody();
    }
    if (action === "doctabs") {
      if (!tabs.length) return json({ ok: true, tabs: [{ title: "(без вкладок)", images: doc.getBody().getImages().length }] });
      return json({ ok: true, tabs: tabs.map(function (t) {
        return { title: t.getTitle(), images: t.asDocumentTab().getBody().getImages().length };
      }) });
    }
    var ti = parseInt(e.parameter.tab || "0", 10);
    var imgs = bodyOf(ti).getImages();
    var k = parseInt(e.parameter.i, 10);
    if (isNaN(k) || k < 0 || k >= imgs.length) return json({ ok: false, error: "bad index" });
    var blob = imgs[k].getBlob();
    return json({ ok: true, mime: blob.getContentType(), b64: Utilities.base64Encode(blob.getBytes()) });
  }

  var sh = sheet_();
  var H = header();
  var idxStart = H.indexOf("старт");
  var idxRem = H.indexOf("напоминания");
  var idxFio = H.indexOf("ФИО");
  var idxClass = H.indexOf("класс");
  var idxTariff = H.indexOf("тариф");
  var idxRole = H.indexOf("роль");
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
        tariff: String(row[idxTariff] || ""),
        role: String(row[idxRole] || ""),
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
