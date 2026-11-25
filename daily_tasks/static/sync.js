/*
 * Синхронизация с сервером бота.
 * Приложение работает как раньше, но вместо памяти браузера данные хранятся на
 * сервере: одинаковые на телефоне, ноутбуке и в сообщениях боту.
 */
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = (tg && tg.initData) || '';
  var version = 0;
  var lastSent = null; // последний JSON, который точно есть на сервере
  var pending = null; // JSON, который ещё надо отправить
  var sending = false;
  var started = false;

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor('#f4f1ec');
      tg.setBackgroundColor('#f4f1ec');
    } catch (e) {}
  }

  // ---------- Трёхстороннее объединение: base (было), local (наше), remote (сервер) ----------
  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }
  function isObj(x) {
    return x !== null && typeof x === 'object' && !Array.isArray(x);
  }
  function byId(list) {
    return (
      Array.isArray(list) &&
      list.every(function (x) {
        return isObj(x) && x.id !== undefined;
      })
    );
  }
  function merge3(b, l, r) {
    if (same(l, b)) return r;
    if (same(r, b) || same(l, r)) return l;
    if (isObj(l) && isObj(r)) {
      var out = {};
      var bb = isObj(b) ? b : {};
      Object.keys(Object.assign({}, bb, r, l)).forEach(function (k) {
        var v = merge3(bb[k], l[k], r[k]);
        if (v !== undefined) out[k] = v;
      });
      return out;
    }
    if (byId(l) && byId(r) && (b === undefined || byId(b))) {
      var index = function (list) {
        var m = {};
        (list || []).forEach(function (x) {
          m[x.id] = x;
        });
        return m;
      };
      var bi = index(b), li = index(l), ri = index(r);
      var ids = r.map((x) => x.id).concat(l.map((x) => x.id).filter((id) => !(id in ri)));
      var res = [];
      ids.forEach(function (id) {
        var v = merge3(bi[id], li[id], ri[id]);
        if (v !== undefined) res.push(v);
      });
      return res;
    }
    return l; // настоящее противоречие — побеждает последнее действие на этом устройстве
  }
  function mergeJson(baseRaw, localRaw, remoteRaw) {
    try {
      var base = baseRaw ? JSON.parse(baseRaw) : undefined;
      var merged = merge3(base, JSON.parse(localRaw), JSON.parse(remoteRaw));
      var out = JSON.stringify(merged);
      return same(merged, JSON.parse(remoteRaw)) ? remoteRaw : out;
    } catch (e) {
      return localRaw;
    }
  }
  window.__mergeJson = mergeJson;

  function api(method, body) {
    return fetch('api/state', {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-Init-Data': initData },
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
    });
  }

  function notice(text) {
    var t = document.getElementById('toast');
    var tt = document.getElementById('toast-text');
    var undo = document.getElementById('undo');
    if (!t || !tt) return;
    tt.textContent = text;
    if (undo) undo.hidden = true;
    t.hidden = false;
    setTimeout(function () {
      t.hidden = true;
    }, 3500);
  }

  function fatal(text) {
    document.querySelector('main.app').innerHTML =
      '<div style="padding:48px 20px;text-align:center;font-size:17px;line-height:1.5">' +
      text +
      '</div>';
  }

  function flush() {
    if (sending || pending === null) return;
    if (pending === lastSent) {
      pending = null;
      return;
    }
    var json = pending;
    pending = null;
    sending = true;
    api('PUT', { data: json, version: version })
      .then(function (r) {
        if (r.status === 409) {
          // Пока мы меняли, данные изменились (бот или другое устройство):
          // объединяем обе стороны и отправляем ещё раз.
          return r.json().then(function (cur) {
            var merged = mergeJson(lastSent, json, cur.data);
            version = cur.version;
            lastSent = cur.data;
            window.__applyRemote(merged);
            if (merged !== cur.data) pending = merged;
          });
        }
        if (!r.ok) throw new Error(r.status);
        return r.json().then(function (res) {
          version = res.version;
          lastSent = json;
        });
      })
      .catch(function () {
        pending = pending === null ? json : pending;
        notice('Нет связи с сервером. Изменения сохранятся, когда связь появится.');
        setTimeout(flush, 5000);
      })
      .then(function () {
        sending = false;
        if (pending !== null) flush();
      });
  }

  window.__sync = function (json) {
    if (!started) return;
    pending = json;
    setTimeout(flush, 0);
  };

  // Подтягиваем изменения с других устройств и из бота.
  function refresh() {
    if (!started || sending || pending !== null) return;
    // Не перерисовываем, пока человек что-то печатает в списке или заметке.
    var el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && !el.closest('#add')) return;
    api('GET')
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (cur) {
        if (!cur || cur.version === version || sending || pending !== null) return;
        version = cur.version;
        lastSent = cur.data;
        window.__applyRemote(cur.data);
      })
      .catch(function () {});
  }
  setInterval(function () {
    if (!document.hidden) refresh();
  }, 15000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refresh();
  });

  if (!initData) {
    fatal('Откройте это приложение через бота в Telegram — кнопка «Задачи» рядом с полем ввода.');
    return;
  }
  api('GET')
    .then(function (r) {
      if (r.status === 401 || r.status === 403) {
        return r.text().then(function (t) {
          fatal(t || 'Доступ закрыт.');
          return null;
        });
      }
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then(function (cur) {
      if (!cur) return;
      version = cur.version;
      lastSent = cur.data;
      window.__SERVER_DATA__ = cur.data;
      started = true;
      window.startApp();
    })
    .catch(function () {
      fatal('Не удалось загрузить данные. Проверьте интернет и откройте приложение ещё раз.');
    });
})();
