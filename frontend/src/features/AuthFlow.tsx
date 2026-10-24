import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  createUser,
  login,
  type CreateUserInput,
  type LoginResponse,
} from "../api";
import {
  PageTransition,
  PresenceTransition,
  TextButton,
  ValidatedTextArea,
  ValidatedTextField,
} from "../component-library";

const ABOUT_PROMPTS = [
  "What do you love doing when you have a free afternoon?",
  "What topics can you talk about for hours?",
  "Where do you live, and what places feel like home?",
  "What are you learning or hoping to get better at?",
  "Who and what matter most in your life?",
  "What routines, tastes, or little preferences make you you?",
];

type AuthPage = "welcome" | "login" | "credentials" | "profile" | "about";
type AuthStatusTarget =
  | "login-email"
  | "login-password"
  | "signup-email"
  | "signup-password"
  | "first-name"
  | "last-name"
  | "about"
  | null;

type AuthFlowProps = {
  onAuthenticated: (
    login: LoginResponse,
    options?: { newAccount?: boolean },
  ) => void;
};

const emptySignup: CreateUserInput = {
  email: "",
  password: "",
  firstName: "",
  lastName: "",
  about: "",
};

const isValidEmail = (value: string): boolean =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());

const emailWarning = (value: string): string =>
  isValidEmail(value) ? "" : "Enter a valid email address.";

const passwordWarning = (value: string): string =>
  value.length >= 12 ? "" : "Use at least 12 characters for your password.";

const requiredWarning = (value: string, label: string): string =>
  value.trim() ? "" : `Enter your ${label}.`;

export default function AuthFlow({ onAuthenticated }: AuthFlowProps) {
  const [page, setPage] = useState<AuthPage>("welcome");
  const [signup, setSignup] = useState<CreateUserInput>(emptySignup);
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [promptIndex, setPromptIndex] = useState(0);
  const [status, setStatus] = useState("");
  const [statusTarget, setStatusTarget] = useState<AuthStatusTarget>(null);
  const [submitting, setSubmitting] = useState(false);
  const aboutTextareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (page !== "about") return;
    const interval = window.setInterval(
      () => setPromptIndex((index) => (index + 1) % ABOUT_PROMPTS.length),
      4200,
    );
    return () => window.clearInterval(interval);
  }, [page]);

  const clearStatus = (): void => {
    setStatus("");
    setStatusTarget(null);
  };

  const showStatus = (
    message: string,
    target: Exclude<AuthStatusTarget, null>,
  ): void => {
    clearStatus();
    setStatusTarget(target);
    setStatus(message);
  };

  const goTo = (nextPage: AuthPage): void => {
    clearStatus();
    setPage(nextPage);
  };

  const updateSignup = (values: Partial<CreateUserInput>): void => {
    setSignup((current) => ({ ...current, ...values }));
    clearStatus();
  };

  const continueFromCredentials = (event: FormEvent): void => {
    event.preventDefault();
    if (!isValidEmail(signup.email)) {
      showStatus(emailWarning(signup.email), "signup-email");
      return;
    }
    if (signup.password.length < 12) {
      showStatus(passwordWarning(signup.password), "signup-password");
      return;
    }
    goTo("profile");
  };

  const continueFromProfile = (event: FormEvent): void => {
    event.preventDefault();
    if (!signup.firstName.trim()) {
      showStatus(requiredWarning(signup.firstName, "first name"), "first-name");
      return;
    }
    if (!signup.lastName.trim()) {
      showStatus(requiredWarning(signup.lastName, "last name"), "last-name");
      return;
    }
    goTo("about");
  };

  const finishSignup = async (
    event?: FormEvent,
    skipAbout = false,
  ): Promise<void> => {
    event?.preventDefault();
    setSubmitting(true);
    clearStatus();
    try {
      // Signup also creates the account's default workspace; marking the
      // session as new lets the workspace provider open it for the tutorial.
      await createUser(skipAbout ? { ...signup, about: "" } : signup);
      onAuthenticated(await login(signup.email, signup.password), {
        newAccount: true,
      });
    } catch (error) {
      showStatus(
        error instanceof Error
          ? error.message
          : "Couldn’t create your account.",
        "about",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const submitLogin = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    if (!isValidEmail(loginEmail)) {
      showStatus(emailWarning(loginEmail), "login-email");
      return;
    }
    if (!loginPassword) {
      showStatus("Enter your password.", "login-password");
      return;
    }
    setSubmitting(true);
    clearStatus();
    try {
      onAuthenticated(await login(loginEmail, loginPassword));
    } catch (error) {
      showStatus(
        error instanceof Error ? error.message : "Couldn’t log you in.",
        "login-password",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-shell">
      <PageTransition
        appear={false}
        className="auth-page-transition"
        pageKey={page}
      >
        <>
          {page === "welcome" && (
            <section
              className="auth-card auth-welcome"
              aria-labelledby="auth-title"
            >
              <div className="auth-welcome-kicker">get in the right</div>
              <h1 id="auth-title">HEADSPACE</h1>
              <div className="auth-entry-actions">
                <TextButton
                  className="auth-entry-action"
                  onClick={() => goTo("credentials")}
                >
                  get started →
                </TextButton>
                <TextButton
                  className="auth-entry-action"
                  onClick={() => goTo("login")}
                >
                  returning →
                </TextButton>
              </div>
            </section>
          )}

          {page === "login" && (
            <section
              className="auth-card auth-login"
              aria-labelledby="login-title"
            >
              <h1 id="login-title">Welcome back</h1>
              <form
                className="auth-login-form"
                noValidate
                onSubmit={(event) => void submitLogin(event)}
              >
                <div className="auth-login-fields">
                  <ValidatedTextField
                    aria-label="Email"
                    autoComplete="email"
                    inputMode="email"
                    placeholder="email"
                    type="email"
                    value={loginEmail}
                    onValueChange={(value) => {
                      setLoginEmail(value);
                      clearStatus();
                    }}
                    validate={emailWarning}
                    externalWarning={
                      statusTarget === "login-email" ? status : ""
                    }
                    required
                    autoFocus
                  />
                  <ValidatedTextField
                    aria-label="Password"
                    autoComplete="current-password"
                    placeholder="password"
                    type="password"
                    value={loginPassword}
                    onValueChange={(value) => {
                      setLoginPassword(value);
                      clearStatus();
                    }}
                    validate={(value) => requiredWarning(value, "password")}
                    externalWarning={
                      statusTarget === "login-password" ? status : ""
                    }
                    required
                  />
                </div>
                <div className="auth-login-actions">
                  <TextButton
                    className="auth-entry-action"
                    onClick={() => goTo("welcome")}
                  >
                    ← back
                  </TextButton>
                  <TextButton
                    className="auth-entry-action"
                    type="submit"
                    disabled={submitting}
                  >
                    {submitting ? "logging in…" : "log in →"}
                  </TextButton>
                </div>
              </form>
            </section>
          )}

          {page === "credentials" && (
            <section
              className="auth-card auth-signup"
              aria-labelledby="credentials-title"
            >
              <h1 id="credentials-title">Start with the basics</h1>
              <form
                className="auth-signup-form"
                noValidate
                onSubmit={continueFromCredentials}
              >
                <div className="auth-signup-fields">
                  <ValidatedTextField
                    aria-label="Email"
                    autoComplete="email"
                    inputMode="email"
                    placeholder="email"
                    type="email"
                    value={signup.email}
                    onValueChange={(email) => updateSignup({ email })}
                    validate={emailWarning}
                    externalWarning={
                      statusTarget === "signup-email" ? status : ""
                    }
                    required
                    autoFocus
                  />
                  <ValidatedTextField
                    aria-label="Password"
                    autoComplete="new-password"
                    minLength={12}
                    placeholder="password (12+ characters)"
                    type="password"
                    value={signup.password}
                    onValueChange={(password) => updateSignup({ password })}
                    validate={passwordWarning}
                    externalWarning={
                      statusTarget === "signup-password" ? status : ""
                    }
                    required
                  />
                </div>
                <div className="auth-signup-actions">
                  <TextButton
                    className="auth-entry-action"
                    onClick={() => goTo("welcome")}
                  >
                    ← back
                  </TextButton>
                  <TextButton className="auth-entry-action" type="submit">
                    continue →
                  </TextButton>
                </div>
              </form>
            </section>
          )}

          {page === "profile" && (
            <section
              className="auth-card auth-signup"
              aria-labelledby="profile-title"
            >
              <h1 id="profile-title">Tell me about yourself</h1>
              <form
                className="auth-signup-form"
                noValidate
                onSubmit={continueFromProfile}
              >
                <div className="auth-signup-fields">
                  <div className="auth-field-row">
                    <ValidatedTextField
                      aria-label="First name"
                      autoComplete="given-name"
                      placeholder="first name"
                      value={signup.firstName}
                      onValueChange={(firstName) => updateSignup({ firstName })}
                      validate={(value) => requiredWarning(value, "first name")}
                      externalWarning={
                        statusTarget === "first-name" ? status : ""
                      }
                      required
                      autoFocus
                    />
                    <ValidatedTextField
                      aria-label="Last name"
                      autoComplete="family-name"
                      placeholder="last name"
                      value={signup.lastName}
                      onValueChange={(lastName) => updateSignup({ lastName })}
                      validate={(value) => requiredWarning(value, "last name")}
                      externalWarning={
                        statusTarget === "last-name" ? status : ""
                      }
                      required
                    />
                  </div>
                </div>
                <div className="auth-signup-actions">
                  <TextButton
                    className="auth-entry-action"
                    onClick={() => goTo("credentials")}
                  >
                    ← back
                  </TextButton>
                  <TextButton className="auth-entry-action" type="submit">
                    continue →
                  </TextButton>
                </div>
              </form>
            </section>
          )}

          {page === "about" && (
            <section
              className="auth-card auth-signup auth-about"
              aria-labelledby="about-title"
            >
              <h1 id="about-title">What else is worth knowing?</h1>
              <PresenceTransition
                as="p"
                className="auth-prompt"
                aria-live="polite"
                enter="fade-up"
                exit="fade-up"
                key={promptIndex}
                visible
              >
                {ABOUT_PROMPTS[promptIndex]}
              </PresenceTransition>
              <form
                className="auth-signup-form"
                noValidate
                onSubmit={(event) => void finishSignup(event)}
              >
                <ValidatedTextArea
                  textareaRef={aboutTextareaRef}
                  value={signup.about ?? ""}
                  onValueChange={(about) => updateSignup({ about })}
                  validate={() => ""}
                  externalWarning={statusTarget === "about" ? status : ""}
                  ariaLabel="About you"
                  emptyPrompt="about you"
                  maxLength={2000}
                  autoFocus
                  className="auth-about-entry"
                />
                <div className="auth-signup-actions auth-signup-actions-wide">
                  <TextButton
                    className="auth-entry-action"
                    onClick={() => goTo("profile")}
                  >
                    ← back
                  </TextButton>
                  <TextButton
                    className="auth-entry-action"
                    type="submit"
                    disabled={submitting}
                  >
                    {submitting ? "creating your space…" : "enter →"}
                  </TextButton>
                  <TextButton
                    className="auth-entry-action"
                    disabled={submitting}
                    onClick={() => void finishSignup(undefined, true)}
                  >
                    skip
                  </TextButton>
                </div>
              </form>
            </section>
          )}

          {(["credentials", "profile", "about"] as AuthPage[]).includes(
            page,
          ) && (
            <div className="auth-progress" aria-hidden="true">
              {["credentials", "profile", "about"].map((step) => (
                <span key={step} className={step === page ? "is-active" : ""} />
              ))}
            </div>
          )}
        </>
      </PageTransition>
    </main>
  );
}
