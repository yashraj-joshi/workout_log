# 01 - Prerequisites

Get every tool installed and proven working before you touch AWS. Each section
is one task: what it is for, the command, what you should see, and what to do
when you see something else.

Everything here is free. The accounts in section 1 are where money can be
spent; docs 02 and 03 set spending limits on both.

**Time:** about 30 minutes, most of it downloads.

---

## 1. Accounts you need

| Account | What for | Cost to create |
| --- | --- | --- |
| AWS | Hosting, database, sign-in, the API | $0. Pay per use; expect under $1/month for one user (docs/09) |
| OpenAI (API, not ChatGPT) | Transcription and the assistant | $0 to create; prepaid credit, $5 minimum |
| Apple ID | Only to install the app on your iPhone; you already have one | $0 |

You do **not** need an Apple Developer account. This is a web app installed
through Safari.

---

## 2. Homebrew

The package manager everything else installs through.

**Check whether you already have it:**

```bash
brew --version
```

Expected: `Homebrew 4.x.x` or newer. If you see that, skip to section 3.

**Install it:**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

On Apple silicon, Homebrew installs to `/opt/homebrew` and the installer prints
two `echo` commands to add it to your `PATH`. **Run them**, then:

```bash
brew --version
```

**Common errors**

| What you see | Fix |
| --- | --- |
| `command not found: brew` after installing | You skipped the PATH commands. Run: `echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile && eval "$(/opt/homebrew/bin/brew shellenv)"` |
| `xcrun: error: invalid active developer path` | `xcode-select --install`, accept the dialog, then retry |

---

## 3. AWS CLI v2

Talks to AWS from the terminal. **v2 specifically** - v1 does not support
`aws configure sso`, which doc 02 uses.

```bash
brew install awscli
aws --version
```

Expected, with the version at v2 or higher:

```
aws-cli/2.32.21 Python/3.13.11 Darwin/25.6.0 source/arm64
```

**Common errors**

| What you see | Fix |
| --- | --- |
| `aws-cli/1.x` | An old pip install is shadowing it. Run `which aws`; if it is not under `/opt/homebrew`, run `pip3 uninstall awscli` and open a new terminal |
| `command not found: aws` | Open a new terminal so `PATH` reloads |

You will not configure credentials yet. That is doc 02.

---

## 4. AWS SAM CLI

Builds and deploys the backend stack.

```bash
brew install aws-sam-cli
sam --version
```

Expected: `SAM CLI, version 1.149.0` or newer.

**Common errors**

| What you see | Fix |
| --- | --- |
| `brew: No available formula` | `brew tap aws/tap` then retry |
| `sam` runs but `sam build` later fails on Python version | Section 5 - SAM uses your `python3` |

---

## 5. Python 3.13 and the project venv

The Lambda functions run Python 3.13 on arm64. Matching it locally means your
tests run against what will actually be deployed.

```bash
python3 --version
```

Expected: `Python 3.13.x`. If it is older:

```bash
brew install python@3.13
```

**Create the project's virtual environment.** From the repo root:

```bash
make venv
```

What this does: creates `.venv/`, then installs `fastapi`, `mangum`,
`pydantic`, `openai`, `boto3`, the test tools and `cfn-lint` from
`backend/requirements-dev.txt`. `.venv/` is gitignored - it never gets
committed.

Expected last line:

```
venv ready: .venv
```

**Check it worked** by running the whole backend test suite:

```bash
make test
```

Expected:

```
198 passed
  no frontend tests yet (phase 4)
```

**Common errors**

| What you see | Fix |
| --- | --- |
| `No module named venv` | `brew install python@3.13`, open a new terminal |
| `error: externally-managed-environment` | You are installing outside the venv. Use `make venv`, or prefix commands with `.venv/bin/` |
| `ModuleNotFoundError: No module named 'workoutlog'` | Run pytest through `make test-py`, not bare `pytest`. `backend/pytest.ini` sets the path |

---

## 6. Node.js

Only used to run the frontend tests (`node --test`). The app itself has no npm
dependencies and no build step.

```bash
brew install node
node --version
```

Expected: `v22.x.x` or newer. Node 18+ is enough for the built-in test runner.

**Check it worked:**

```bash
make test-js 2>&1 | grep -E '^ℹ (pass|fail)'
```

Expected: a `pass` count and `fail 0`.

---

## 7. Docker Desktop

Only needed if `sam build` fails on a native wheel (`pydantic-core` is the
usual one). `sam build --use-container` then compiles inside an
Amazon Linux image that matches Lambda exactly.

```bash
brew install --cask docker
```

Then **open Docker Desktop from Applications once** and let it finish starting -
the CLI does not work until the daemon is running.

```bash
docker --version
docker run --rm hello-world
```

Expected: a version line, then `Hello from Docker!`.

**Common errors**

| What you see | Fix |
| --- | --- |
| `Cannot connect to the Docker daemon` | Docker Desktop is not running. Open it and wait for the whale icon to stop animating |
| `no matching manifest for linux/arm64` | Expected for some images on Apple silicon; SAM's images support arm64, so this does not affect `sam build` |

---

## 8. Git

```bash
git --version
```

Expected: `git version 2.x.x`. macOS ships it; if it prompts to install the
command line tools, accept.

Set your identity if you have not already:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

---

## 9. Final check

Run all of these from the repo root. Every line should print a version, and the
tests should pass.

```bash
brew --version && \
aws --version && \
sam --version && \
python3 --version && \
node --version && \
docker --version && \
git --version && \
make test && \
make check-secrets
```

Expected last two lines:

```
198 passed
check-secrets: OK
```

If that all works, go to [02-aws-account.md](02-aws-account.md).
