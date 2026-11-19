/* Progressive enhancement for the admin console. Every page works without
 * this file; it adds the theme switch, chart read-outs, copy to clipboard
 * and a few form conveniences. No inline handlers anywhere: the
 * Content-Security-Policy only allows scripts from this origin. */
(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");

  function on(type, selector, handler, options) {
    document.addEventListener(
      type,
      function (event) {
        var target = event.target instanceof Element ? event.target.closest(selector) : null;
        if (target) handler(event, target);
      },
      options
    );
  }

  /* ---------------------------------------------------------------- theme */
  function currentTheme() {
    var set = root.getAttribute("data-theme");
    if (set === "light" || set === "dark") return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  on("click", "[data-theme-toggle]", function () {
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = "mo_theme=" + next + "; Path=/; Max-Age=31536000; SameSite=Lax" + secure;
  });

  /* ------------------------------------------------------- chart read-out */
  var tip = null;
  var activeSlot = null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function showTip(hit, x, y) {
    var data;
    try {
      data = JSON.parse(hit.getAttribute("data-tip"));
    } catch (error) {
      return;
    }
    if (!tip) {
      tip = el("div", "tip");
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    tip.replaceChildren(el("div", "tip-title", data.t));
    if (!data.rows.length) tip.appendChild(el("div", "tip-name", "No requests"));
    data.rows.forEach(function (row) {
      var line = el("div", "tip-row");
      line.appendChild(el("span", "tip-key c" + row[0]));
      line.appendChild(el("span", "tip-name", row[1]));
      line.appendChild(el("span", "tip-value", row[2]));
      tip.appendChild(line);
    });
    if (data.total && data.rows.length > 1) {
      var total = el("div", "tip-row tip-total");
      total.appendChild(el("span"));
      total.appendChild(el("span", "tip-name", "Total"));
      total.appendChild(el("span", "tip-value", data.total));
      tip.appendChild(total);
    }
    tip.hidden = false;
    var box = tip.getBoundingClientRect();
    var left = x + 14;
    if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
    var top = y - box.height - 12;
    if (top < 8) top = y + 16;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
    var slot = hit.parentNode;
    if (activeSlot && activeSlot !== slot) activeSlot.classList.remove("is-active");
    activeSlot = slot;
    slot.classList.add("is-active");
  }

  function hideTip() {
    if (tip) tip.hidden = true;
    if (activeSlot) activeSlot.classList.remove("is-active");
    activeSlot = null;
  }

  document.addEventListener("pointermove", function (event) {
    var hit = event.target instanceof Element ? event.target.closest(".plot .hit") : null;
    if (hit) showTip(hit, event.clientX, event.clientY);
    else if (tip && !tip.hidden && event.pointerType === "mouse") hideTip();
  });
  document.addEventListener("pointerdown", function (event) {
    var hit = event.target instanceof Element ? event.target.closest(".plot .hit") : null;
    if (hit) showTip(hit, event.clientX, event.clientY);
    else hideTip();
  });
  document.addEventListener("scroll", hideTip, { passive: true, capture: true });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      hideTip();
      document.querySelectorAll("details.menu[open]").forEach(function (menu) {
        menu.open = false;
      });
    }
  });

  /* ------------------------------------------------------------ user menu */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });

  /* ----------------------------------------------------------------- copy */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = el("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "vh";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (error) {
        ok = false;
      }
      area.remove();
      if (ok) resolve();
      else reject(new Error("copy failed"));
    });
  }
  on("click", "[data-copy]", function (event, button) {
    var source = document.querySelector(button.getAttribute("data-copy"));
    if (!source) return;
    copyText(source.textContent.trim()).then(
      function () {
        button.classList.add("copied");
        var status = document.querySelector(button.getAttribute("data-copy-status") || "#copy-status");
        if (status) status.textContent = "Copied to the clipboard.";
        window.setTimeout(function () {
          button.classList.remove("copied");
        }, 2600);
      },
      function () {
        var status = document.querySelector(button.getAttribute("data-copy-status") || "#copy-status");
        if (status) status.textContent = "Copying is blocked here. Select the secret and copy it by hand.";
        var range = document.createRange();
        range.selectNodeContents(source);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
    );
  });

  /* ---------------------------------------------------------------- forms */
  // Delete confirmation: the button unlocks once the typed name matches.
  function syncConfirm(input) {
    var button = document.querySelector(input.getAttribute("data-confirm-button"));
    if (button) button.disabled = input.value.trim() !== input.getAttribute("data-confirm-match");
  }
  on("input", "[data-confirm-match]", function (event, input) {
    syncConfirm(input);
  });

  // Key form: the route checkboxes only apply to "specific routes".
  function syncAccess() {
    var picks = document.querySelector("[data-route-picks]");
    var some = document.querySelector('input[name="access"][value="some"]');
    if (picks && some) picks.disabled = !some.checked;
  }
  on("change", 'input[name="access"]', syncAccess);

  // Expiry presets fill the date field.
  on("click", "[data-set-value]", function (event, button) {
    var input = document.querySelector(button.getAttribute("data-target"));
    if (!input) return;
    input.value = button.getAttribute("data-set-value");
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.focus();
  });

  // Filters without a submit button apply as soon as they change.
  on("change", "[data-autosubmit]", function (event, control) {
    if (window.htmx && control.form && control.form.hasAttribute("hx-get")) return;
    if (control.form) control.form.requestSubmit ? control.form.requestSubmit() : control.form.submit();
  });

  function init() {
    document.querySelectorAll("[data-confirm-match]").forEach(syncConfirm);
    syncAccess();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  document.addEventListener("htmx:afterSettle", init);

  /* ----------------------------------------------------------------- htmx */
  // A refused or failed partial update must not fail silently.
  function announce(text) {
    var region = document.getElementById("flash");
    if (!region) return;
    var notice = el("div", "notice bad");
    notice.setAttribute("role", "alert");
    notice.appendChild(el("p", null, text));
    region.replaceChildren(notice);
    region.scrollIntoView({ block: "nearest" });
  }
  document.addEventListener("htmx:responseError", function (event) {
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (status === 401) return; // the server sends HX-Redirect to the sign-in page
    if (status === 403) announce("The request was refused. Reload the page and try again.");
    else if (status === 503) announce("The database is not available right now. Nothing was changed.");
    else if (event.detail.requestConfig && event.detail.requestConfig.headers["X-Passive"]) return;
    else announce("The request failed (" + status + "). Nothing was changed.");
  });
  document.addEventListener("htmx:sendError", function (event) {
    if (event.detail.requestConfig && event.detail.requestConfig.headers["X-Passive"]) return;
    announce("The console cannot be reached. Check the connection and try again.");
  });
})();
