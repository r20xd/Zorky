// Заставка Zorky: минимум 1,8 с, затем плавно уходит, когда приложение отрисовалось
(function () {
  var started = Date.now();
  var MIN_MS = 1800;
  var MAX_MS = 8000;
  function close() {
    var el = document.getElementById("zorky-splash");
    if (!el) return;
    el.classList.add("hide");
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 700);
  }
  function tick() {
    var root = document.getElementById("root");
    var ready = root && root.children.length > 0;
    var age = Date.now() - started;
    if ((ready && age >= MIN_MS) || age >= MAX_MS) close();
    else setTimeout(tick, 100);
  }
  tick();
})();
