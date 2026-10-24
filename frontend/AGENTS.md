# Headspace Frontend Agent Guide

Before changing frontend code, read
[`COMPONENT_REGISTRY.md`](./COMPONENT_REGISTRY.md) in full.

Use the registered components, hooks, and motion tokens before creating new UI
implementations. Prefer composing or extending an existing library component when it
already owns the relevant behavior. Do not recreate validation timing, inline
warnings, auto-growing text areas, blurred edges, underlined text buttons, loaders,
or presence/page transitions inside a feature component.

Reuse existing styles wherever possible. Do not add new visual styling (colors,
backgrounds, shadows, borders, radii, font sizes, spacing tweaks, transitions)
without confirming with the user first. Prefer a registered component's built-in
look, then existing classes and values in `src/style.css`. When something new
seems necessary, say what you want to add and why, and wait for approval. Layout
needed only to place a component is fine without asking.

Import reusable UI through `src/component-library` rather than deep-importing its
implementation files. `FadeSwitch` is internal to the motion package and is not a
public component.

When a genuinely new reusable component is needed:

1. Put it in `src/component-library` under the appropriate category.
2. Export it from the category barrel and `src/component-library/index.ts`.
3. Add its intended use and boundaries to `COMPONENT_REGISTRY.md` in the same change.

Feature-specific components stay in `src/features`. Check the feature component
inventory in the registry before adding another one, and extend an existing feature
component when it already owns the workflow.
