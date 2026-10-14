const BRAIN_LOADER_LEAD_IN_MS = 2_000;
const BRAIN_LOADER_FADE_MS = 400;

const wait = async (durationMs: number): Promise<void> => {
  if (durationMs <= 0) return;
  await new Promise<void>((resolve) => window.setTimeout(resolve, durationMs));
};

export const waitForBrainLoaderLeadIn = async (
  startedAt: number,
): Promise<void> => wait(BRAIN_LOADER_LEAD_IN_MS - (performance.now() - startedAt));

export const waitForBrainLoaderFade = async (): Promise<void> =>
  wait(BRAIN_LOADER_FADE_MS);
