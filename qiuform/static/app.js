(function () {
  'use strict';

  /* ---------------------------------------------------------- 主题切换 */

  var root = document.documentElement;

  function isDark() {
    return root.getAttribute('data-theme') === 'dark';
  }

  function paintThemeIcon() {
    var dark = isDark();
    document.querySelectorAll('[data-theme-icon]').forEach(function (node) {
      // 显示的是"点了会切到哪个模式"：亮色时显示月亮，暗色时显示太阳
      node.hidden = (node.getAttribute('data-theme-icon') === 'light') !== dark;
    });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-theme-toggle]');
    if (!button) return;
    var next = isDark() ? 'light' : 'dark';
    if (next === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else {
      root.removeAttribute('data-theme');
    }
    try { localStorage.setItem('qf-theme', next); } catch (err) { /* 忽略 */ }
    paintThemeIcon();
  });

  paintThemeIcon();

  /* ---------------------------------------------------------- 复制 */

  function fallbackCopy(text, done) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); done(); } catch (err) { /* 忽略 */ }
    document.body.removeChild(area);
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy]');
    if (!button) return;

    var target = document.querySelector(button.getAttribute('data-copy'));
    if (!target) return;

    var text = button.getAttribute('data-copy-raw') || target.textContent.trim();
    var label = button.querySelector('[data-copy-label]') || button;
    var original = label.textContent;

    function done() {
      button.classList.add('copied');
      label.textContent = '已复制';
      setTimeout(function () {
        button.classList.remove('copied');
        label.textContent = original;
      }, 1400);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {
        fallbackCopy(text, done);
      });
    } else {
      fallbackCopy(text, done);
    }
  });

  /* ---------------------------------------------------------- 拖放上传 */

  document.querySelectorAll('[data-drop]').forEach(function (zone) {
    var input = zone.querySelector('input[type=file]');
    var counter = zone.querySelector('[data-drop-count]');
    var title = zone.querySelector('[data-drop-title]');
    var defaultTitle = title ? title.textContent : '';
    if (!input) return;

    function refresh() {
      var files = input.files ? Array.prototype.slice.call(input.files) : [];
      if (counter) {
        if (!files.length) {
          counter.textContent = '';
        } else if (files.length === 1) {
          counter.textContent = files[0].name;
        } else {
          counter.textContent = '已选择 ' + files.length + ' 个文件';
        }
      }
      if (title) {
        title.textContent = files.length ? '准备好了，点下面的按钮上传' : defaultTitle;
      }
    }

    zone.addEventListener('click', function (event) {
      if (event.target === input) return;
      input.click();
    });
    input.addEventListener('change', refresh);

    ['dragenter', 'dragover'].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add('over');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        if (name === 'dragleave' && zone.contains(event.relatedTarget)) return;
        zone.classList.remove('over');
      });
    });
    zone.addEventListener('drop', function (event) {
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      input.files = event.dataTransfer.files;
      refresh();
    });
  });

  /* ---------------------------------------------------------- 提示条自动淡出 */

  document.querySelectorAll('.flash-success').forEach(function (node) {
    setTimeout(function () {
      node.style.transition = 'opacity .4s, transform .4s, margin .4s, padding .4s, height .4s';
      node.style.opacity = '0';
      node.style.transform = 'translateY(-6px)';
      setTimeout(function () { node.remove(); }, 420);
    }, 5200);
  });

})();
