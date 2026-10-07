// The only way this app builds DOM. Text always goes in as text nodes, so user
// data can never become markup: there is no innerHTML anywhere in the app.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") {
      el.addEventListener(name.slice(2).toLowerCase(), value);
    } else if (name === "class") {
      el.className = value;
    } else if (name === "dataset") {
      Object.assign(el.dataset, value);
    } else {
      el.setAttribute(name, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function replace(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export const $ = (selector, root = document) => root.querySelector(selector);

let toastTimer;

// Bottom-center, about 2.8 s. One at a time; a new one replaces the old.
export function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}
