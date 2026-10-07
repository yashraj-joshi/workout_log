// The sign-in screen and its three side steps: a first password (after an
// invite), and the two halves of "Forgot password?".

import { h, replace } from "../dom.js";
import { createCognito, PASSWORD_RULES } from "../cognito.js";

// root: where to draw. config: the Cognito IDs from config.js.
// adopt(tokens): hands a fresh sign-in to the session. onDone(): signed in.
// message is news ("Signed out."); problem is something that went wrong.
export function renderSignIn({ root, config, adopt, onDone, message = "", problem = "" }) {
  let cognito = null;
  let email = "";

  const getCognito = async () => (cognito ||= await createCognito(config));

  function field(label, attrs) {
    return h("label", { class: "field" }, h("span", { class: "label" }, label), h("input", attrs));
  }

  function notice(text, kind = "bad") {
    return h("p", { class: `notice ${kind}`, role: kind === "bad" ? "alert" : "status", hidden: !text }, text);
  }

  // Draws one form. submit(values) returns once the step is done; whatever it
  // throws is shown above the button, and the form can be sent again.
  function form({ title, intro, fields, button, submit, links = [], info = "", alert = "" }) {
    const error = notice(alert);
    const send = h("button", { class: "btn primary block", type: "submit" }, button);
    const el = h("form", { class: "card signin", novalidate: true },
      h("h2", { class: "dialog-title" }, title),
      intro && h("p", { class: "meta" }, intro),
      notice(info, "good"),
      fields,
      error,
      send,
      links.length > 0 && h("p", { class: "signin-links" }, links));
    el.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(el));
      error.hidden = true;
      send.disabled = true;
      send.textContent = "Working…";
      try {
        await submit(values);
      } catch (err) {
        error.textContent = err.message || "That didn't work. Try again.";
        error.hidden = false;
        send.disabled = false;
        send.textContent = button;
      }
    });
    replace(root, el);
    el.querySelector("input")?.focus();
  }

  function link(text, onClick) {
    return h("button", { class: "linklike", type: "button", onClick }, text);
  }

  function matching(values) {
    if (values.password !== values.confirm) throw new Error("The two passwords don't match.");
    if (values.password.length < 12) throw new Error(PASSWORD_RULES);
  }

  async function finish(tokens) {
    await adopt(tokens);
    onDone();
  }

  function signIn(info = message, alert = problem) {
    form({
      title: "Sign in",
      info,
      alert,
      fields: [
        field("Email", { name: "email", type: "email", autocomplete: "username", inputmode: "email",
          autocapitalize: "none", spellcheck: "false", required: true, value: email }),
        field("Password", { name: "password", type: "password", autocomplete: "current-password", required: true }),
      ],
      button: "Sign in",
      links: [link("Forgot password?", () => forgot())],
      async submit(values) {
        email = values.email.trim();
        if (!email || !values.password) throw new Error("Enter your email and password.");
        const result = await (await getCognito()).signIn(email, values.password);
        if (result.newPasswordRequired) return firstPassword(result.newPasswordRequired);
        await finish(result.tokens);
      },
    });
  }

  function firstPassword(complete) {
    form({
      title: "Choose a password",
      intro: `Your invite's temporary password worked. Now pick your own. ${PASSWORD_RULES}`,
      fields: [
        field("New password", { name: "password", type: "password", autocomplete: "new-password", required: true }),
        field("Type it again", { name: "confirm", type: "password", autocomplete: "new-password", required: true }),
      ],
      button: "Save and sign in",
      links: [link("Back to sign in", () => signIn("", ""))],
      async submit(values) {
        matching(values);
        const result = await complete(values.password);
        await finish(result.tokens);
      },
    });
  }

  function forgot() {
    form({
      title: "Reset password",
      intro: "We'll email you a code.",
      fields: [
        field("Email", { name: "email", type: "email", autocomplete: "username", inputmode: "email",
          autocapitalize: "none", spellcheck: "false", required: true, value: email }),
      ],
      button: "Email me a code",
      links: [link("Back to sign in", () => signIn("", ""))],
      async submit(values) {
        email = values.email.trim();
        if (!email) throw new Error("Enter your email.");
        await (await getCognito()).forgotPassword(email);
        resetWithCode();
      },
    });
  }

  function resetWithCode() {
    form({
      title: "Reset password",
      info: `If ${email} has an account, a code is on its way.`,
      fields: [
        field("Code from the email", { name: "code", inputmode: "numeric", autocomplete: "one-time-code", required: true }),
        field("New password", { name: "password", type: "password", autocomplete: "new-password", required: true }),
        field("Type it again", { name: "confirm", type: "password", autocomplete: "new-password", required: true }),
      ],
      button: "Set password",
      links: [link("Send a new code", () => forgot()), link("Back to sign in", () => signIn("", ""))],
      async submit(values) {
        matching(values);
        await (await getCognito()).confirmPassword(email, values.code, values.password);
        signIn("Password changed. Sign in with the new one.", "");
      },
    });
  }

  signIn();
}
