export const motionDurations = {
  quick: 180,
  standard: 240,
  page: 360,
  loader: 400,
} as const;

export const motionEasings = {
  enter: "cubic-bezier(0.16, 1, 0.3, 1)",
  exit: "cubic-bezier(0.4, 0, 1, 1)",
} as const;

export const motionDistances = {
  subtle: 8,
  standard: 12,
} as const;

export const motionStaggers = {
  quick: 45,
  standard: 60,
} as const;
