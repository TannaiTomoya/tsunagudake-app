// 位置を使うのは、本人がボタンを押したその1回だけ。端末にも、どこにも残さない。
(function () {
  "use strict";

  function locate(done, fail) {
    if (!navigator.geolocation) {
      fail("この端末では、位置を使えません。");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      function (position) {
        done(position.coords.latitude, position.coords.longitude);
      },
      function () {
        fail("位置を取れませんでした。端末の設定で、位置の利用を許可してください。");
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  }

  // 「着きました」：位置を取ってから送る。
  document.querySelectorAll("form[data-checkin-arrive]").forEach(function (form) {
    var status = form.querySelector("[data-checkin-status]");
    var sending = false;
    form.addEventListener("submit", function (event) {
      if (sending) {
        return;
      }
      event.preventDefault();
      if (status) {
        status.hidden = false;
        status.textContent = "位置を確かめています。";
      }
      locate(
        function (lat, lon) {
          form.elements.lat.value = lat.toFixed(6);
          form.elements.lon.value = lon.toFixed(6);
          sending = true;
          form.submit();
        },
        function (message) {
          if (status) {
            status.textContent = message;
          }
        }
      );
    });
  });

  // 事務所の代表：今いる場所を、入力欄に入れる（保存は、ボタンを押したときだけ）。
  document.querySelectorAll("button[data-fill-location]").forEach(function (button) {
    var form = button.form;
    var status = form.querySelector("[data-location-status]");
    button.hidden = false;
    button.addEventListener("click", function () {
      locate(
        function (lat, lon) {
          form.elements.lat.value = lat.toFixed(6);
          form.elements.lon.value = lon.toFixed(6);
          if (status) {
            status.hidden = false;
            status.textContent = "今いる場所を入れました。保存するまで、登録されません。";
          }
        },
        function (message) {
          if (status) {
            status.hidden = false;
            status.textContent = message;
          }
        }
      );
    });
  });
})();
