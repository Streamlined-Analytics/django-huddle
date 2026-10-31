"use strict";

const HUDDLE_MENTION_PATTERN = /(^|\s)@([^\s@]{0,40})$/;

function huddleComposerKeydown(event, textarea) {
  const results = textarea.form.querySelector(".huddle-mention-results");
  if (event.key === "Escape") {
    huddleHideMentions(textarea.form);
    return;
  }
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }
  event.preventDefault();
  if (results && !results.hidden) {
    const first = results.querySelector(".huddle-mention-option");
    if (first) {
      huddleMentionPick(first);
      return;
    }
  }
  if (textarea.value.trim()) {
    textarea.form.requestSubmit();
  }
}

function huddleMentionInput(textarea) {
  const beforeCaret = textarea.value.slice(0, textarea.selectionStart);
  const match = beforeCaret.match(HUDDLE_MENTION_PATTERN);
  const form = textarea.form;
  if (!match) {
    huddleHideMentions(form);
    return;
  }
  const holder = form.querySelector(".huddle-composer__mentions");
  const results = form.querySelector(".huddle-mention-results");
  const url = `${holder.dataset.mentionUrl}?q=${encodeURIComponent(match[2])}`;
  results.hidden = false;
  htmx.ajax("GET", url, { target: results, swap: "innerHTML" });
}

function huddleMentionPick(option) {
  const form = option.closest("form");
  const textarea = form.querySelector("textarea");
  const caret = textarea.selectionStart;
  const beforeCaret = textarea.value.slice(0, caret).replace(HUDDLE_MENTION_PATTERN, `$1@${option.dataset.name} `);
  textarea.value = beforeCaret + textarea.value.slice(caret);
  textarea.setSelectionRange(beforeCaret.length, beforeCaret.length);
  huddleAddMention(form, option.dataset.pk);
  huddleHideMentions(form);
  textarea.focus();
}

function huddleAddMention(form, pk) {
  const holder = form.querySelector(".huddle-composer__mentions");
  if (holder.querySelector(`input[value="${CSS.escape(pk)}"]`)) {
    return;
  }
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "mentions";
  input.value = pk;
  holder.appendChild(input);
}

function huddleHideMentions(form) {
  const results = form.querySelector(".huddle-mention-results");
  if (results) {
    results.hidden = true;
    results.replaceChildren();
  }
}

function huddleCloseThread() {
  document.getElementById("huddle-thread").replaceChildren();
}

function huddleScrollToEnd(timeline) {
  timeline.scrollTop = timeline.scrollHeight;
}

function huddleIsNearEnd(timeline) {
  return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 120;
}

function huddleReplaceExisting(event) {
  const incoming = event.detail.fragment.firstElementChild;
  if (!incoming || !incoming.id) {
    return;
  }
  const existing = document.getElementById(incoming.id);
  if (existing && event.detail.target !== existing) {
    existing.replaceWith(incoming);
    event.detail.shouldSwap = false;
  }
}

function huddleBeforeOobSwap(event) {
  const target = event.detail.target;
  if (target && target.classList.contains("huddle-timeline")) {
    target.dataset.huddleStick = huddleIsNearEnd(target) ? "1" : "";
  }
  huddleReplaceExisting(event);
}

function huddleAfterOobSwap(event) {
  const target = event.detail.target;
  if (!target || !target.classList.contains("huddle-timeline")) {
    return;
  }
  if (target.dataset.huddleStick) {
    huddleScrollToEnd(target);
  }
  if (document.visibilityState === "visible") {
    htmx.ajax("POST", target.dataset.readUrl, { source: target, swap: "none" });
  }
}

function huddleResubscribe() {
  document.querySelectorAll(".huddle-ws-subscribe").forEach(huddleTriggerResubscribe);
}

function huddleTriggerResubscribe(form) {
  htmx.trigger(form, "huddle:resubscribe");
}

function huddleScrollTimelines() {
  document.querySelectorAll(".huddle-timeline").forEach(huddleScrollToEnd);
}

document.addEventListener("htmx:oobBeforeSwap", huddleBeforeOobSwap);
document.addEventListener("htmx:oobAfterSwap", huddleAfterOobSwap);
document.addEventListener("htmx:wsOpen", huddleResubscribe);
document.addEventListener("DOMContentLoaded", huddleScrollTimelines);
