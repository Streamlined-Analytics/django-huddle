"use strict";

const HUDDLE_MENTION_PATTERN = /(^|\s)@((?:[^\s@]{1,40}(?: [^\s@]{0,40}){0,2})?)$/;
const HUDDLE_REFERENCE_PATTERN = /(^|\s)#([^\s#]{0,40})$/;

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
      first.click();
      return;
    }
  }
  if (textarea.value.trim()) {
    textarea.form.requestSubmit();
  }
}

function huddleMentionInput(textarea) {
  const beforeCaret = textarea.value.slice(0, textarea.selectionStart);
  const form = textarea.form;
  const mention = beforeCaret.match(HUDDLE_MENTION_PATTERN);
  if (mention) {
    huddleShowResults(form, form.querySelector(".huddle-composer__mentions").dataset.mentionUrl, mention[2]);
    return;
  }
  const reference = beforeCaret.match(HUDDLE_REFERENCE_PATTERN);
  const references = form.querySelector(".huddle-composer__references");
  if (reference && references) {
    huddleShowResults(form, references.dataset.referenceUrl, reference[2]);
    return;
  }
  huddleHideMentions(form);
}

function huddleShowResults(form, url, query) {
  const results = form.querySelector(".huddle-mention-results");
  results.hidden = false;
  htmx.ajax("GET", `${url}?q=${encodeURIComponent(query)}`, { target: results, swap: "innerHTML" });
}

function huddleMentionPick(option) {
  const form = option.closest("form");
  huddleInsertPick(form, HUDDLE_MENTION_PATTERN, `@${option.dataset.name}`);
  huddleAddHidden(form.querySelector(".huddle-composer__mentions"), "mentions", option.dataset.pk);
}

function huddleReferencePick(option) {
  const form = option.closest("form");
  huddleInsertPick(form, HUDDLE_REFERENCE_PATTERN, `#${option.dataset.code}`);
  huddleAddHidden(form.querySelector(".huddle-composer__references"), "references", option.dataset.token);
}

function huddleInsertPick(form, pattern, text) {
  const textarea = form.querySelector("textarea");
  const caret = textarea.selectionStart;
  const beforeCaret = textarea.value.slice(0, caret).replace(pattern, (typed, lead) => `${lead}${text} `);
  textarea.value = beforeCaret + textarea.value.slice(caret);
  textarea.setSelectionRange(beforeCaret.length, beforeCaret.length);
  huddleHideMentions(form);
  textarea.focus();
}

function huddleAddHidden(holder, name, value) {
  if (holder.querySelector(`input[value="${CSS.escape(value)}"]`)) {
    return;
  }
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = name;
  input.value = value;
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
  huddleClearEmptyState(target);
  if (target.dataset.huddleStick) {
    huddleScrollToEnd(target);
  }
  if (document.visibilityState === "visible") {
    htmx.ajax("POST", target.dataset.readUrl, { source: target, swap: "none" });
  }
}

function huddleClearEmptyState(timeline) {
  timeline.querySelectorAll(".huddle-empty").forEach(huddleRemoveElement);
}

function huddleRemoveElement(element) {
  element.remove();
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
