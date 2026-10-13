import "./style.css";

type IngestionRun = {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
};

type ApiError = {
  detail?: string | Array<{ msg?: string }>;
};

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL;

if (!configuredApiBaseUrl) {
  throw new Error("VITE_API_BASE_URL is not configured.");
}

const apiBaseUrl = configuredApiBaseUrl.replace(/\/$/, "");

document.querySelector<HTMLDivElement>("#app")!.innerHTML = `
  <main>
    <section class="entry" aria-labelledby="page-title">
      <h1 id="page-title">Tell me about your day</h1>

      <form id="ingestion-form">
        <div class="text-entry">
          <span class="empty-prompt" aria-hidden="true">type here</span>
          <textarea
            id="content"
            name="content"
            rows="1"
            aria-label="Your thought"
            required
            autofocus
          ></textarea>
        </div>
        <button
          id="submit-button"
          type="submit"
          aria-hidden="true"
          tabindex="-1"
        >remember this thought</button>
      </form>

      <div id="status" class="sr-only" role="status" aria-live="polite"></div>
    </section>
  </main>
`;

const form = document.querySelector<HTMLFormElement>("#ingestion-form")!;
const textEntry = document.querySelector<HTMLDivElement>(".text-entry")!;
const textarea = document.querySelector<HTMLTextAreaElement>("#content")!;
const submitButton =
  document.querySelector<HTMLButtonElement>("#submit-button")!;
const status = document.querySelector<HTMLDivElement>("#status")!;

let retryRequest: { content: string; id: string } | null = null;
const textareaMaxHeight = 240;
let keepBottomFadeHidden = false;
let autoScrollFrame: number | undefined;
let revealButtonTimeout: number | undefined;

const updateScrollFades = (): void => {
  const threshold = 2;
  const canScroll = textarea.scrollHeight > textareaMaxHeight + threshold;
  const isAtBottom =
    textarea.scrollTop + textarea.clientHeight >= textarea.scrollHeight - threshold;

  textEntry.classList.toggle("can-scroll-up", canScroll && textarea.scrollTop > threshold);
  textEntry.classList.toggle(
    "can-scroll-down",
    canScroll && !isAtBottom && !keepBottomFadeHidden,
  );
};

const releaseAutoScroll = (): void => {
  if (autoScrollFrame !== undefined) {
    window.cancelAnimationFrame(autoScrollFrame);
    autoScrollFrame = undefined;
  }

  keepBottomFadeHidden = false;
  updateScrollFades();
};

const scrollToBottomSoftly = (): void => {
  if (autoScrollFrame !== undefined) {
    window.cancelAnimationFrame(autoScrollFrame);
  }

  const start = textarea.scrollTop;
  const target = Math.max(0, textarea.scrollHeight - textarea.clientHeight);

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    textarea.scrollTop = target;
    releaseAutoScroll();
    return;
  }

  const duration = 850;
  const startedAt = performance.now();

  const step = (now: number): void => {
    const progress = Math.min((now - startedAt) / duration, 1);
    const easedProgress = 1 - Math.pow(1 - progress, 3);
    textarea.scrollTop = start + (target - start) * easedProgress;

    if (progress < 1) {
      autoScrollFrame = window.requestAnimationFrame(step);
      return;
    }

    autoScrollFrame = undefined;
    keepBottomFadeHidden = false;
    updateScrollFades();
  };

  autoScrollFrame = window.requestAnimationFrame(step);
};

const newRequestId = (): string => {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const errorMessage = (
  payload: ApiError | IngestionRun | null,
  response: Response,
): string => {
  if (payload && "detail" in payload && typeof payload.detail === "string") {
    return payload.detail;
  }

  if (payload && "detail" in payload && Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => item.msg)
      .filter(Boolean)
      .join(", ");
  }

  if (payload && "error" in payload && payload.error) {
    return JSON.stringify(payload.error);
  }

  return `The API returned ${response.status} ${response.statusText}.`;
};

const resizeTextarea = (): void => {
  const currentHeight = textarea.getBoundingClientRect().height;
  textarea.style.height = "auto";
  const nextHeight = Math.min(textarea.scrollHeight, textareaMaxHeight);

  textarea.style.height = `${currentHeight}px`;
  void textarea.offsetHeight;
  textarea.style.height = `${nextHeight}px`;
  textarea.style.overflowY =
    textarea.scrollHeight > textareaMaxHeight ? "auto" : "hidden";
};

textarea.addEventListener("input", () => {
  const hasText = Boolean(textarea.value.trim());
  const wasScrolledUp = textEntry.classList.contains("can-scroll-down");

  textEntry.classList.toggle("has-text", hasText);

  if (!hasText) {
    window.clearTimeout(revealButtonTimeout);
    revealButtonTimeout = undefined;
    submitButton.classList.remove("is-visible");
    submitButton.setAttribute("aria-hidden", "true");
    submitButton.tabIndex = -1;
  } else if (
    !submitButton.classList.contains("is-visible") &&
    revealButtonTimeout === undefined
  ) {
    revealButtonTimeout = window.setTimeout(() => {
      revealButtonTimeout = undefined;
      submitButton.classList.add("is-visible");
      submitButton.setAttribute("aria-hidden", "false");
      submitButton.tabIndex = 0;
    }, 1000);
  }

  resizeTextarea();

  if (!wasScrolledUp) {
    keepBottomFadeHidden = true;
    scrollToBottomSoftly();
  }

  updateScrollFades();

  if (retryRequest && retryRequest.content !== textarea.value) {
    retryRequest = null;
  }
});

textarea.addEventListener("scroll", updateScrollFades);
textarea.addEventListener("transitionend", updateScrollFades);
textarea.addEventListener("wheel", releaseAutoScroll, { passive: true });
textarea.addEventListener("touchstart", releaseAutoScroll, { passive: true });

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const content = textarea.value;
  if (!content.trim()) {
    textarea.focus();
    return;
  }

  if (!retryRequest || retryRequest.content !== content) {
    retryRequest = { content, id: newRequestId() };
  }

  submitButton.disabled = true;
  textarea.disabled = true;
  status.textContent = "Sending your thought to Headspace.";

  try {
    const response = await fetch(`${apiBaseUrl}/api/v1/ingestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_type: "manual_entry",
        content,
        source_metadata: {},
        client_request_id: retryRequest.id,
      }),
    });

    const payload = (await response.json().catch(() => null)) as
      | IngestionRun
      | ApiError
      | null;

    if (!response.ok) {
      throw new Error(errorMessage(payload, response));
    }

    const run = payload as IngestionRun;
    status.textContent = `Ingestion run ${run.id} is ${run.status}.`;
    form.reset();
    textEntry.classList.remove("has-text");
    textarea.style.height = "auto";
    textarea.style.overflowY = "hidden";
    updateScrollFades();
    window.clearTimeout(revealButtonTimeout);
    revealButtonTimeout = undefined;
    submitButton.classList.remove("is-visible");
    submitButton.setAttribute("aria-hidden", "true");
    submitButton.tabIndex = -1;
    retryRequest = null;
    textarea.focus();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Something went wrong.";
    status.textContent = `Could not remember your thought. ${message}`;
  } finally {
    submitButton.disabled = false;
    textarea.disabled = false;
  }
});
