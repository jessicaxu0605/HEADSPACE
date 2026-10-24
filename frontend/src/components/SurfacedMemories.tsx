import { useEffect, useState } from "react";
import { getLandingMemories } from "../api";
import {
  PresenceTransition,
  motionStaggers,
} from "../component-library";
import type {
  MemoryLanding,
  MemoryLandingCard,
  MemoryLandingEdge,
  MemoryNodeReference,
} from "../memory_graph";

type SurfacedMemoriesProps = {
  hidden: boolean;
  onOpenNode: (selection: MemoryNodeReference) => void;
};

// Anchors shown on recent loads are sent back as exclusions so each visit
// surfaces something new. Upcoming items are a separate list and never excluded.
const RECENT_ANCHORS_KEY = "headspace:recent-landing-anchors";
const RECENT_ANCHOR_LIMIT = 12;

type RecentAnchor = { kind: "node" | "edge"; id: string };

const readRecentAnchors = (): RecentAnchor[] => {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(RECENT_ANCHORS_KEY) ?? "[]",
    ) as unknown;
    return Array.isArray(stored) ? (stored as RecentAnchor[]) : [];
  } catch {
    return [];
  }
};

const rememberAnchors = (cards: MemoryLandingCard[]): void => {
  const shown = cards.map(({ anchor }) => ({ kind: anchor.kind, id: anchor.id }));
  const recent = [...shown, ...readRecentAnchors()].slice(
    0,
    RECENT_ANCHOR_LIMIT,
  );
  try {
    window.localStorage.setItem(RECENT_ANCHORS_KEY, JSON.stringify(recent));
  } catch {
    // Storage is a convenience; without it the draw just repeats more often.
  }
};

const REASON_LABELS: Record<string, string> = {
  on_this_day: "on this day",
  source_anniversary: "on this day",
  starting_or_ending: "this week",
  recent: "recently added",
  random_edge: "from your headspace",
  random_node: "from your headspace",
};

// Notes like "added today" already say why the card is here.
const kicker = (card: MemoryLandingCard): string =>
  card.anchor.note ??
  REASON_LABELS[card.anchor.reason] ??
  "from your headspace";

const oneLine = (text: string): string => text.replace(/\s+/g, " ").trim();

const anchorEdge = (card: MemoryLandingCard): MemoryLandingEdge | undefined =>
  card.edges.find((edge) => edge.role === "anchor");

// The node a card opens: the anchor node, or the anchor edge's endpoint that
// is not a bare date.
const focusNode = (card: MemoryLandingCard): MemoryNodeReference | undefined =>
  card.nodes.find((node) => node.node_type !== "date") ?? card.nodes[0];

function MemoryCard({
  card,
  index,
  onOpenNode,
}: {
  card: MemoryLandingCard;
  index: number;
  onOpenNode: (selection: MemoryNodeReference) => void;
}) {
  const edge = anchorEdge(card);
  const anchorNode = card.anchor.kind === "node" ? card.nodes[0] : undefined;
  const title = edge?.sentence ?? anchorNode?.title ?? "";
  const summary = anchorNode?.summary;
  const quote =
    (edge?.evidence ?? card.edges.find((item) => item.evidence)?.evidence)
      ?.raw_content ?? null;
  const context = card.edges
    .filter((item) => item.role === "context")
    .slice(0, summary ? 1 : 2);
  const related = card.edges.find((item) => item.role === "related");
  const target = focusNode(card);

  return (
    <PresenceTransition
      as="li"
      className="memory-card"
      enter="fade-up"
      enterDelay={Math.min(index, 3) * motionStaggers.standard}
      exit="fade-up"
      visible
    >
      <button
        className="memory-card-button"
        type="button"
        disabled={!target}
        onClick={() => target && onOpenNode(target)}
      >
        <span className="memory-kicker">{kicker(card)}</span>
        <span className="memory-title">{title}</span>
        {summary && (
          <span className="memory-text">{oneLine(summary)}</span>
        )}
        {quote && (
          <span className="memory-quote">“{oneLine(quote)}”</span>
        )}
        {context.map((item) => (
            <span className="memory-context" key={item.edge_id}>
              {item.sentence}
            </span>
          ))}
        {related && (
          <span className="memory-related">
            reminds you of: {related.sentence}
          </span>
        )}
      </button>
    </PresenceTransition>
  );
}

// An item's neighbors, without its date (the note already says it).
const upcomingNeighbors = (item: MemoryLandingCard): string[] =>
  item.nodes
    .filter(
      (node) => node.node_id !== item.anchor.id && node.node_type !== "date",
    )
    .map((node) => node.title);

// "Thu, Oct 29" for an ISO date, read as a local calendar day.
const formatWhen = (when: string): string => {
  const [year, month, day] = when.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
};

const upcomingNote = (item: MemoryLandingCard): string | null => {
  const parts = [
    item.anchor.reason === "upcoming_events" && item.anchor.when
      ? formatWhen(item.anchor.when)
      : null,
    item.anchor.note,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
};

function UpcomingCard({
  items,
  onOpenNode,
}: {
  items: MemoryLandingCard[];
  onOpenNode: (selection: MemoryNodeReference) => void;
}) {
  return (
    <PresenceTransition
      as="section"
      className="upcoming-card"
      aria-labelledby="upcoming-card-title"
      enter="fade-up"
      exit="fade-up"
      visible
    >
      <h2 id="upcoming-card-title" className="memory-kicker upcoming-card-title">
        upcoming
      </h2>
      <ul className="upcoming-list">
        {items.map((item) => {
          const node = item.nodes[0];
          const note = upcomingNote(item);
          const neighbors = upcomingNeighbors(item);
          return (
            <li className="upcoming-item" key={item.anchor.id}>
              <button
                className="upcoming-button"
                type="button"
                disabled={!node}
                onClick={() => node && onOpenNode(node)}
              >
                <span
                  className={`upcoming-marker is-${node?.node_type ?? "task"}`}
                  aria-hidden="true"
                />
                <span className="upcoming-title">{node?.title}</span>
                {note && (
                  <span
                    className={`upcoming-note${item.anchor.note?.startsWith("overdue") ? " is-overdue" : ""}`}
                  >
                    {note}
                  </span>
                )}
                {neighbors.length > 0 && (
                  <span className="upcoming-neighbors">
                    {neighbors.join(" · ")}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </PresenceTransition>
  );
}

export default function SurfacedMemories({
  hidden,
  onOpenNode,
}: SurfacedMemoriesProps) {
  const [landing, setLanding] = useState<MemoryLanding | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const request = new AbortController();
    const recent = readRecentAnchors();
    getLandingMemories({
      excludeNodeIds: recent.filter((a) => a.kind === "node").map((a) => a.id),
      excludeEdgeIds: recent.filter((a) => a.kind === "edge").map((a) => a.id),
      signal: request.signal,
    })
      .then((landing) => {
        setLanding(landing);
        rememberAnchors(landing.memories);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setFailed(true);
        }
      });
    return () => request.abort();
  }, []);

  const upcoming = landing?.upcoming ?? [];
  const memories = landing?.memories ?? [];
  const empty =
    landing !== null && upcoming.length === 0 && memories.length === 0;

  return (
    <section
      className={`surfaced-memories${hidden ? " is-hidden" : ""}`}
      aria-label="Surfaced memories"
      aria-hidden={hidden || undefined}
      inert={hidden}
    >
      <div className="surfaced-memories-inner">
        <PresenceTransition
          as="p"
          className="surfaced-status"
          enter="fade-up"
          exit="fade-up"
          visible={failed}
        >
          couldn’t surface any memories.
        </PresenceTransition>
        <PresenceTransition
          as="p"
          className="surfaced-status"
          enter="fade-up"
          exit="fade-up"
          visible={empty}
        >
          nothing to surface yet. add a thought below.
        </PresenceTransition>
        {landing !== null && !empty && (
          <div className="surfaced-memories-content">
            {upcoming.length > 0 && (
              <UpcomingCard items={upcoming} onOpenNode={onOpenNode} />
            )}
            {memories.length > 0 && (
              <ul className="memory-cards">
                {memories.map((card, index) => (
                  <MemoryCard
                    key={card.anchor.id}
                    card={card}
                    index={index}
                    onOpenNode={onOpenNode}
                  />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
