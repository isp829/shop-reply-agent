// 문의 입력 화면: 예시 채우기, 주문 미리보기, 처리 과정 실시간 표시(SSE).
// 자바스크립트가 없거나 스트리밍이 실패하면 일반 폼 제출로 동작한다.
(function () {
  "use strict";

  var form = document.getElementById("inquiry-form");
  if (!form) return;

  var orderInput = document.getElementById("order-number");
  var textInput = document.getElementById("inquiry-text");
  var preview = document.getElementById("order-preview");
  var button = document.getElementById("submit-button");
  var busyText = document.getElementById("busy-text");
  var steps = document.getElementById("steps");
  var tools = document.getElementById("tools");
  var warnings = document.getElementById("warnings");
  var draft = document.getElementById("draft");
  var errorBox = document.getElementById("stream-error");
  var STATE_TEXT = { waiting: "대기", running: "진행 중", done: "완료", error: "실패" };
  var ORDER_RE = /^ORD-\d{4}$/i;
  var previewTimer = null;
  var writerText = "";

  function el(tag, text, className) {
    var node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function setStep(step, state) {
    var item = steps.querySelector('[data-step="' + step + '"]');
    if (!item) return;
    item.setAttribute("data-state", state);
    var label = item.querySelector(".step-state");
    if (label) label.textContent = STATE_TEXT[state] || state;
    if (state === "running") item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  }

  function resetProgress() {
    Array.prototype.forEach.call(steps.querySelectorAll("li"), function (li) {
      setStep(li.getAttribute("data-step"), "waiting");
    });
    tools.textContent = "";
    warnings.textContent = "";
    writerText = "";
    draft.textContent = "에이전트가 문의를 확인하고 있습니다…";
    errorBox.hidden = true;
    errorBox.textContent = "";
  }

  function setBusy(busy) {
    button.disabled = busy;
    button.textContent = busy ? "처리 중…" : "답장 초안 만들기";
    busyText.hidden = !busy;
    form.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    var running = steps.querySelector('[data-state="running"]');
    if (running) setStep(running.getAttribute("data-step"), "error");
    setBusy(false);
  }

  function visibleDraft(text) {
    var cut = text.indexOf("```");
    return (cut >= 0 ? text.slice(0, cut) : text).trim();
  }

  function handle(event) {
    switch (event.type) {
      case "step":
        setStep(event.step, event.state === "done" ? "done" : "running");
        if (event.state !== "done") {
          // 이전 단계는 완료로 표시
          var found = false;
          Array.prototype.forEach.call(steps.querySelectorAll("li"), function (li) {
            if (li.getAttribute("data-step") === event.step) found = true;
            else if (!found && li.getAttribute("data-state") === "running") setStep(li.getAttribute("data-step"), "done");
          });
        }
        if (event.step === "writer" && event.state !== "done") draft.textContent = "";
        break;
      case "tool":
        tools.appendChild(el("li", event.label + " (" + event.name + ")"));
        break;
      case "warning":
        warnings.appendChild(el("li", event.message, "alert warning"));
        break;
      case "token":
        if (event.step === "writer") {
          writerText += event.text;
          draft.textContent = visibleDraft(writerText) || "답장을 작성하고 있습니다…";
        } else if (!writerText) {
          draft.textContent = event.step === "triage" ? "문의를 분류하고 있습니다…" : "주문과 정책을 확인하고 있습니다…";
        }
        break;
      case "done":
        setStep("verify", "done");
        draft.textContent = visibleDraft(writerText) + "\n\n결과 화면으로 이동합니다…";
        window.location.href = event.url;
        return true;
      case "error":
        showError(event.message);
        return true;
    }
    return false;
  }

  function fallbackSubmit() {
    HTMLFormElement.prototype.submit.call(form);
  }

  async function submitWithStream(evt) {
    if (!window.fetch || !window.ReadableStream || !window.TextDecoder) return;
    evt.preventDefault();
    if (!form.reportValidity()) return;
    resetProgress();
    setBusy(true);
    var body = new URLSearchParams(new FormData(form));
    var finished = false;
    try {
      var response = await fetch("/api/inquiries/stream", {
        method: "POST",
        body: body,
        headers: { "Accept": "text/event-stream" },
        credentials: "same-origin"
      });
      var type = response.headers.get("content-type") || "";
      if (!response.ok || type.indexOf("text/event-stream") < 0) {
        var data = type.indexOf("json") >= 0 ? await response.json() : {};
        showError(data.error || "요청을 처리하지 못했습니다. 다시 시도해 주세요.");
        return;
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      while (!finished) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        var parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (var i = 0; i < parts.length && !finished; i++) {
          var line = parts[i].trim();
          if (line.indexOf("data: ") !== 0) continue;
          try {
            finished = handle(JSON.parse(line.slice(6)));
          } catch (e) {
            /* 해석할 수 없는 줄은 건너뛴다 */
          }
        }
      }
      if (!finished) showError("연결이 끊겼습니다. 잠시 후 다시 시도해 주세요.");
    } catch (e) {
      if (!finished) {
        draft.textContent = "실시간 표시를 쓸 수 없어 일반 방식으로 처리합니다…";
        fallbackSubmit();
      }
    }
  }

  async function updatePreview() {
    var value = orderInput.value.trim();
    preview.textContent = "";
    if (!ORDER_RE.test(value)) return;
    try {
      var response = await fetch("/api/orders/" + encodeURIComponent(value.toUpperCase()), { credentials: "same-origin" });
      if (!response.ok) {
        preview.appendChild(el("p", "주문을 찾을 수 없습니다.", "hint"));
        return;
      }
      var o = await response.json();
      var dl = el("dl", null, "order-summary compact");
      [["상품", o.product], ["금액", o.amount], ["주문일", o.order_date],
       ["배송", o.shipping_status + (o.delivered_date ? " (" + o.delivered_date + " 완료)" : "")],
       ["환불", o.refund_status], ["고객", o.customer_name]].forEach(function (pair) {
        dl.appendChild(el("dt", pair[0]));
        dl.appendChild(el("dd", pair[1]));
      });
      preview.appendChild(dl);
    } catch (e) {
      /* 미리보기는 보조 기능이라 실패해도 무시한다 */
    }
  }

  orderInput.addEventListener("input", function () {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 250);
  });

  Array.prototype.forEach.call(document.querySelectorAll(".example"), function (btn) {
    btn.addEventListener("click", function () {
      orderInput.value = btn.getAttribute("data-order");
      textInput.value = btn.getAttribute("data-text");
      updatePreview();
      textInput.focus();
    });
  });

  form.addEventListener("submit", submitWithStream);
  if (orderInput.value) updatePreview();
})();
