# Frontend Component Registry

Read this registry before implementing or changing frontend UI. It is the source of
truth for what should be reused, what may be extended, and what remains
feature-specific.

## Working rules

1. Import reusable UI from `src/component-library`.
2. Use a registered component before writing equivalent markup, state, timers, or
   animation CSS in a feature.
3. Compose primitives for combined behavior. For example, use
   `ValidatedTextField` instead of assembling `TextField`, validation timing, and an
   error message at each call site.
4. Extend an existing primitive when the requested behavior is a general variation
   of it. Create a new primitive only when the semantics or behavior are genuinely
   distinct.
5. Keep workflow and API state in `src/features`; keep reusable presentation and
   interaction behavior in `src/component-library`.
6. Use the shared motion tokens. Do not introduce isolated duration, easing,
   distance, or stagger values without a component-specific reason.
7. When adding, renaming, or materially changing a reusable component, update this
   registry in the same change.

The public import surface is:

```tsx
import {
  AutoGrowingTextArea,
  BlurredContainer,
  BrainLoader,
  Checkbox,
  FadeTransition,
  InlineWarning,
  PageTransition,
  PresenceTransition,
  TextButton,
  TextField,
  ValidatedTextArea,
  ValidatedTextField,
  motionDistances,
  motionDurations,
  motionEasings,
  motionStaggers,
  useFieldValidation,
} from "../component-library";
```

Adjust the relative path as needed, but keep imports on the package barrel.

## Quick decision guide

| Need | Use |
| --- | --- |
| Quiet text action with an underline that draws in on hover/focus | `TextButton` |
| Standard one-line controlled input | `TextField` |
| One-line input with delayed and blur validation | `ValidatedTextField` |
| Expanding, softly scrolling multiline input | `AutoGrowingTextArea` |
| Expanding multiline input with validation | `ValidatedTextArea` |
| A yes/no option in a form | `Checkbox` |
| A warning belonging to custom input UI | `InlineWarning` |
| Reuse validation behavior in a new input primitive | `useFieldValidation` |
| Fade a conditional element in and out | `FadeTransition` |
| Fade or fade-up a conditional/list element | `PresenceTransition` |
| Transition between full views or pages | `PageTransition` |
| Stagger a list | `PresenceTransition` with `motionStaggers` |
| Soften one or more clipped container edges | `BlurredContainer` |
| Show the existing brain-shaped loading treatment | `BrainLoader` |

## Component library

### Inputs

#### `TextField`

A controlled, single-line input with Headspace's shared text-field styling.

Use it for plain text, email, password, date, and other native input types when the
screen does not need inline validation. Pass `value` and `onValueChange`; other native
input attributes and a `ref` pass through. Context-specific selectors (for example the
search field's `input[type="search"]` rules) may restyle it.

Do not build another styled `<input>` for a normal form field. Do not use this when a
warning should appear after the user pauses or leaves the field; use
`ValidatedTextField` instead.

#### `ValidatedTextField`

Composes `TextField`, `useFieldValidation`, and `InlineWarning`.

Use it as the default field for forms that can reject or warn about a value. Its
local validator runs after 500 ms of inactivity by default and immediately on blur.
Use `externalWarning` for server errors and `warningDelayMs` only when the interaction
has a documented reason to differ. Its warnings follow the under-field text rule
in `InlineWarning`: a validator that would only report an empty required value should
return `""`.

#### `AutoGrowingTextArea`

A controlled multiline input that grows up to its maximum visual height, then
scrolls while fading overflowing edges. It also handles soft automatic scrolling and
reduced-motion behavior.

Use it for freeform memory entry, biographies, notes, and other prose. Supply a
stable `textareaRef`, `value`, `onValueChange`, and `ariaLabel`. `emptyPrompt` renders
the large in-field prompt treatment.

Do not recreate its height measurement, scrolling, or edge-fade logic in a feature.
Use a plain native `<textarea>` only when the design explicitly calls for a fixed,
conventional textarea.

#### `ValidatedTextArea`

Composes `AutoGrowingTextArea`, `useFieldValidation`, and `InlineWarning`.

Use it for freeform input that needs the same pause-or-blur warning behavior as a
validated text field. Prefer it over manually placing an `InlineWarning` after an
`AutoGrowingTextArea`. Its warnings follow the same under-field text rule as
`InlineWarning`.

#### `Checkbox`

A controlled native checkbox drawn as a small outlined circle that fills solid
when checked, labelled by its children. Pass `checked` and `onCheckedChange`; other native
input attributes and a `ref` pass through to the input, and `className` goes on the
wrapping label.

Use it for an independent on/off option inside a form, such as "add me to this
headspace" on `NewWorkspace`. Do not use it for an action that takes effect
immediately outside a form, or to pick one of several options.

#### `InlineWarning`

A subtle, accessible warning that fades beneath its owning input.

Use it directly only for a custom input that cannot use one of the validated
wrappers. Pass an empty or missing `message` to hide it. Do not add ad hoc error
paragraphs or custom warning timers beside standard fields.

The text under a field is only for warnings, errors (including server errors), or
context the user could not infer from the field itself. It is never a label, a
placeholder restated, or an obvious prompt such as "Give this a name." For a
required field that is merely empty, return no warning and disable the submit
action until it has a value instead.

### Buttons

#### `TextButton`

A borderless serif text button whose underline draws in from the left on hover and
keyboard focus and retracts on leave. It owns the reset, underline animation, focus
treatment, and disabled state.

Use it for Headspace's quiet text actions: "continue →", "← back", "remember this →",
and similar. Native button attributes and a `ref` pass through. It defaults to
`type="button"`, so pass `type="submit"` explicitly for form submission. Add a
feature class only for placement or tone (for example `auth-entry-action` sets the
softer auth color; `ingestion-submit` sets layout).

Do not re-implement the `::after` underline in feature CSS. It is not for icon-only
controls, dismiss buttons, list/card buttons, or the `graph-back` style links, which
change color on hover rather than drawing an underline.

### Containers

#### `BlurredContainer`

Applies a mask that softly fades selected container edges. It supports `div` and
`header` elements, any combination of `top`, `right`, `bottom`, and `left`, and a
configurable edge size.

Use it when clipped or floating content should dissolve at its bounds, such as
notifications or scroll-adjacent surfaces. It is not a generic card, glassmorphism,
drop-shadow, or backdrop-blur component.

### Feedback

#### `BrainLoader`

The branded brain-shaped loading indicator, announced to assistive technology as a
polite status with a screen-reader `label`. Set `fading` once the content has
arrived to fade it out over `motionDurations.loader`.

Use it for every application loading state instead of creating another spinner.
Pair it with `waitForBrainLoaderLeadIn` and `waitForBrainLoaderFade` from
`src/loading.ts` when a feature needs to sequence content after the loader. It is
not for short inline busy states such as the search icon pulse.

### Motion

#### `PageTransition`

The semantic transition for replacing one full screen, page, or major application
view with another. It fades the old keyed child out before revealing the new child.

Use one wrapper around the switching region and pass the current route/view identity
as `pageKey`. Do not implement page changes with local keyframes or directly use the
internal `FadeSwitch` component.

#### `PresenceTransition`

The general conditional-content primitive. It supports independent `fade` or
`fade-up` entry and exit motion, semantic wrapper elements, delays, custom durations,
delayed unmount, and an `onExited` callback.

Use it for notices, results, cards, panels, buttons, and list items that appear or
disappear. Use `unmountOnExit={false}` when hidden content must preserve layout. For
staggered lists, derive `enterDelay` from `motionStaggers` and cap long sequences when
appropriate.

It does not own auto-height/layout collapse, continuous loading animation, canvas
animation, gesture animation, or hover/focus feedback. Those need a specialized
implementation or future primitive.

#### `FadeTransition`

A constrained `PresenceTransition` that always uses a simple fade for entry and
exit.

Use it when movement would be distracting or semantically unnecessary: inline
warnings, loader visibility, overlays, and similarly quiet state changes. Use
`PresenceTransition` when either direction needs `fade-up` or different motion.

#### Motion tokens

- `motionDurations`: `quick`, `standard`, `page`, and `loader` timing.
- `motionEasings`: `enter` and `exit` curves for movement, plus `fade`, a
  symmetric curve `PresenceTransition` always uses for opacity in both
  directions so fades mirror each other (`easing`/`enterEasing`/`exitEasing`
  only shape fade-up movement).
- `motionDistances`: `subtle` and `standard` translation distances.
- `motionStaggers`: `quick` and `standard` list intervals.

Use these instead of duplicating numeric motion values in JSX. CSS-only interaction
states should follow the same values where practical and must respect
`prefers-reduced-motion`.

### Hooks

#### `useFieldValidation`

Owns the shared validation contract: clear a stale local warning while typing, show
the next warning after a pause, validate immediately on blur, and let a server warning
take precedence.

Use it when creating a genuinely different reusable input primitive. Feature screens
should normally use `ValidatedTextField` or `ValidatedTextArea` instead of calling
the hook themselves.

## Feature components

These live in `src/features` and own application workflows. Reuse or extend them when working in
their area; do not move them into the component library unless they become independent
of Headspace domain state and APIs.

| Component | Responsibility and reuse boundary |
| --- | --- |
| `AuthFlow` | Login and multi-step signup screens. Hands the login response to `useSession().signIn`; it does not store tokens or check sessions. Add authentication/onboarding steps here rather than creating a parallel flow. |
| `SearchInterface` | Search input and submission UI. Extend for search-entry behavior; results belong to `ResultInterface`. |
| `ResultInterface` | Search results, graph result states, and relationship overlay coordination. |
| `NeighborhoodGraph` | Force-directed canvas graph and its graph-specific interaction/measurement behavior. Its physics are not general UI motion. |
| `NodeDetails` | Detailed presentation of a selected memory node. Extend for additional node metadata. |
| `RelationshipPage` | Detailed relationship view and evidence presentation. |
| `SessionMenu` | Fixed top-right hamburger menu on signed-in screens with "all workspaces" (when `onShowWorkspaces` is passed) and "log out". Add further account-level actions here rather than placing more fixed buttons. |
| `SurfacedMemories` | Landing-page surfaced memories, upcoming items, and their coordinated collapse around search. |
| `TextEntryInterface` | Manual memory-entry workflow and submission notifications. Rendered only when the workspace allows writes. `prompt` replaces the random starter prompt (`WorkspaceWelcome` asks what the headspace is for). |
| `WorkspaceSelection` | Signed-in screen listing the user's workspaces as left-aligned landscape cards (name, last edited, role) when none is current or remembered. Selecting one calls `switchWorkspace`, which makes it active and opens its landing page. A trailing outlined "+ new workspace" card swaps the page for `NewWorkspace`. |
| `NewWorkspace` | Full screen for naming a new workspace, with a `Checkbox` for adding the user's self node. Submitting calls `createWorkspace`, which creates and opens it on `WorkspaceWelcome`; "← back" returns to `WorkspaceSelection`. Reuses the auth screens' layout classes. |
| `WorkspaceWelcome` | First page of a just-created workspace, shown by `App` in place of the landing page: the workspace's graph (the user's self node, or nothing), its name, and "add some memories ↓", which scrolls to `TextEntryInterface` below. Viewing a submitted memory leaves it for good. Separate from the explore graph page in `ResultInterface`. |
| `WorkspaceUnavailable` | Signed-in screen for an account whose workspaces failed to load or that has no active membership. |

Small render helpers that are private to one feature, such as memory cards inside
`SurfacedMemories`, may remain local until another feature needs them.

## Session and workspace state

`src/providers` holds the app-wide state every signed-in feature depends on. `App`
nests `SessionProvider` > `WorkspaceProvider` and picks one screen from their state.

| Provider / hook | Responsibility and reuse boundary |
| --- | --- |
| `SessionProvider` / `useSession()` | Whether this browser is signed in: `checking`, `signed-out`, or `signed-in` with the `user`, plus `signIn(loginResponse)` and `signOut()`. The access token in `api.ts` is the source of truth (read with `useSyncExternalStore`); `api.ts` clears it on a 401, which signs the reader out everywhere. Do not store tokens or session flags elsewhere. |
| `WorkspaceProvider` / `useWorkspaceState()` | Loads the signed-in user's memberships and chooses the active workspace (current, then the server's `last_used_workspace_id`, which signup sets to the default workspace); otherwise it reports `selecting` (with `createWorkspace`) so `App` shows `WorkspaceSelection`. `switchWorkspace` records the choice with `markWorkspaceLastUsed`. Re-checks memberships when a workspace request returns 403. Only `App` should need the full state. |
| `useWorkspace()` | The active `workspace`, its `role`, `canWrite`, `newWorkspace` (`{ selfNodeId }` only while in the workspace the user just created; `App` opens `WorkspaceWelcome` from it), `switchWorkspace`, and `showAllWorkspaces` (clears the active choice to return to `WorkspaceSelection` without changing the server's last-used workspace), for features inside the signed-in app. Pass `workspace.id` explicitly to every workspace-scoped `api.ts` function; never cache a workspace id in a module. |

The signed-in app is keyed by workspace id, so switching workspace remounts it and
discards in-flight requests and per-workspace UI state. `ActiveWorkspaceProvider`
pins the workspace to that screen so it stays stable while fading out.

## Internal and specialized implementations

- `FadeSwitch` is an internal implementation detail of `PageTransition`. Do not
  export or call it from features.
- The loading pulse in `SearchInterface` is continuous state feedback and should not
  be replaced with a presence transition.
- The surfaced-memory collapse coordinates grid height, opacity, and translation. It
  remains feature CSS until there is a tested reusable collapse/auto-height primitive.
- `NeighborhoodGraph` uses a canvas force simulation; shared DOM motion components do
  not apply to it.
- Hover, focus, underline, border, and input-height transitions are interaction or
  layout behavior, not presence transitions. The animated text-action underline is
  owned by `TextButton`.

## Adding to the library

Before adding a component, search this registry, `src/component-library`, and current
call sites. If no existing component composes cleanly:

1. Keep the API controlled and explicit.
2. Put inputs in `inputs/`, buttons in `buttons/`, hooks in `hooks/`, and motion
   primitives in `motion/`.
   Place an uncategorized visual container at the library root only when it does not
   fit an existing category.
3. Reuse existing primitives and tokens internally.
4. Export it through the category barrel and the top-level library barrel.
5. Document both when to use it and when not to use it here.
6. Replace the motivating feature's local implementation with the new primitive and
   run the frontend production build.
