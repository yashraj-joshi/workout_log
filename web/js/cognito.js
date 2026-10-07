// Sign-in, first password and password reset, straight against Cognito.
//
// Uses the vendored amazon-cognito-identity-js 6.3.20 (web/vendor/, Apache-2.0,
// sha256 32811f694592e437391fe040d836a474d75f54108cfa5123d67e8a4d30283901).
// Sign-in is USER_SRP_AUTH: the app proves it knows the password without
// sending it. Setting or resetting a password does send it, over TLS.
//
// The library is loaded only when someone needs to sign in. Most launches
// start from the refresh cookie and never fetch it.

const LIBRARY = "/vendor/amazon-cognito-identity-6.3.20.min.js";

let loading = null;

function loadLibrary() {
  if (globalThis.AmazonCognitoIdentity) return Promise.resolve(globalThis.AmazonCognitoIdentity);
  loading ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = LIBRARY;
    script.onload = () => resolve(globalThis.AmazonCognitoIdentity);
    script.onerror = () => {
      loading = null;
      script.remove();
      reject(friendly({ code: "NetworkError" }));
    };
    document.head.append(script);
  });
  return loading;
}

// The library saves tokens to the Storage it's given, localStorage by default.
// This one is memory, and it's emptied the moment a sign-in settles.
class MemoryStorage {
  #items = new Map();
  getItem(key) { return this.#items.has(key) ? this.#items.get(key) : null; }
  setItem(key, value) { this.#items.set(key, String(value)); }
  removeItem(key) { this.#items.delete(key); }
  clear() { this.#items.clear(); }
}

const PASSWORD_RULES = "Use at least 12 characters, with an upper-case letter, a lower-case letter and a number.";

// Cognito's messages are written for developers. "Prevent user existence
// errors" is on, so an unknown email already fails exactly like a wrong
// password; these messages keep it that way.
export function friendly(err) {
  const code = (err && (err.code || err.name)) || "";
  const text = (err && err.message) || "";
  let message;
  if (code === "NotAuthorizedException" && /expired/i.test(text)) {
    message = "That temporary password has expired. Ask for a new invite.";
  } else if (code === "NotAuthorizedException" && /attempts exceeded/i.test(text)) {
    message = "Too many attempts. Wait a few minutes, then try again.";
  } else if (code === "NotAuthorizedException" || code === "UserNotFoundException") {
    message = "Incorrect email or password.";
  } else if (code === "PasswordResetRequiredException") {
    message = "This account needs a new password. Use \"Forgot password?\".";
  } else if (code === "InvalidPasswordException") {
    message = PASSWORD_RULES;
  } else if (code === "CodeMismatchException") {
    message = "That code isn't right. Check the latest email and try again.";
  } else if (code === "ExpiredCodeException") {
    message = "That code has expired. Ask for a new one.";
  } else if (/LimitExceeded|TooManyRequests|TooManyFailedAttempts/.test(code)) {
    message = "Too many attempts. Wait a few minutes, then try again.";
  } else if (code === "NetworkError" || err instanceof TypeError) {
    message = "Can't reach sign-in. Check your connection.";
  } else if (code === "InvalidParameterException" && text) {
    message = text;
  } else {
    message = "Sign-in didn't work. Try again.";
  }
  const out = new Error(message);
  out.code = code;
  return out;
}

export { PASSWORD_RULES };

export async function createCognito({ userPoolId, clientId }) {
  const L = await loadLibrary();
  const storage = new MemoryStorage();
  const pool = new L.CognitoUserPool({ UserPoolId: userPoolId, ClientId: clientId, Storage: storage });
  const userFor = (email) => new L.CognitoUser({ Username: email.trim(), Pool: pool, Storage: storage });

  function tokensOf(session) {
    const access = session.getAccessToken();
    return {
      accessToken: access.getJwtToken(),
      idToken: session.getIdToken().getJwtToken(),
      refreshToken: session.getRefreshToken().getToken(),
      // From the token's own lifetime, so a skewed device clock doesn't matter.
      expiresIn: access.getExpiration() - access.getIssuedAt(),
    };
  }

  // Resolves {tokens}, or {newPasswordRequired(password)} on a first sign-in
  // with the temporary password from the invite.
  function settle(user, resolve, reject) {
    const unsupported = () => {
      storage.clear();
      reject(new Error("This account needs a sign-in step the app doesn't support yet."));
    };
    return {
      onSuccess(session) {
        const tokens = tokensOf(session);
        storage.clear();
        resolve({ tokens });
      },
      onFailure(err) {
        storage.clear();
        reject(friendly(err));
      },
      newPasswordRequired() {
        resolve({
          newPasswordRequired: (password) =>
            new Promise((res, rej) => user.completeNewPasswordChallenge(password, {}, settle(user, res, rej))),
        });
      },
      mfaRequired: unsupported,
      totpRequired: unsupported,
      mfaSetup: unsupported,
      selectMFAType: unsupported,
      customChallenge: unsupported,
    };
  }

  return {
    signIn(email, password) {
      const user = userFor(email);
      const details = new L.AuthenticationDetails({ Username: email.trim(), Password: password });
      return new Promise((resolve, reject) => user.authenticateUser(details, settle(user, resolve, reject)));
    },

    // Cognito emails a code. With user-existence errors hidden, an unknown
    // email "succeeds" too, so the screen never reveals who has an account.
    forgotPassword(email) {
      const user = userFor(email);
      return new Promise((resolve, reject) => user.forgotPassword({
        onSuccess: () => resolve(),
        onFailure: (err) => reject(friendly(err)),
        inputVerificationCode: () => resolve(),
      }));
    },

    confirmPassword(email, code, password) {
      const user = userFor(email);
      return new Promise((resolve, reject) => user.confirmPassword(code.trim(), password, {
        onSuccess: () => resolve(),
        onFailure: (err) => reject(friendly(err)),
      }));
    },
  };
}
