import { useEffect, useRef, useState } from "react";
import { getEdgeDetail } from "../api";
import type {
  MemoryEdgeDetail,
  MemoryNodeReference,
} from "../memory_graph";
import {
  waitForBrainLoaderFade,
  waitForBrainLoaderLeadIn,
} from "../loading";
import { useWorkspace } from "../providers/WorkspaceProvider";
import {
  BlurredContainer,
  BrainLoader,
  PresenceTransition,
} from "../component-library";

type RelationshipPageProps = {
  edgeId: string;
  onBack: () => void;
  onOpenNode: (selection: MemoryNodeReference) => void;
};

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

const formatDateTime = (value: string): string =>
  dateTimeFormat.format(new Date(value));
const formatDate = (value: string): string =>
  dateFormat.format(new Date(value));
const percent = (value: number): string => `${Math.round(value * 100)}%`;
const humanize = (value: string): string => value.replaceAll("_", " ");

export default function RelationshipPage({
  edgeId,
  onBack,
  onOpenNode,
}: RelationshipPageProps) {
  const { workspace } = useWorkspace();
  const [edge, setEdge] = useState<MemoryEdgeDetail | null>(null);
  const [status, setStatus] = useState("");
  const [statusIsError, setStatusIsError] = useState(false);
  const backButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    backButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onBack();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onBack]);

  useEffect(() => {
    const loaderStartedAt = performance.now();
    const request = new AbortController();
    setEdge(null);
    setStatus("gathering this relationship…");
    setStatusIsError(false);

    getEdgeDetail(workspace.id, edgeId, { signal: request.signal })
      .then(async (payload) => {
        await waitForBrainLoaderLeadIn(loaderStartedAt);
        if (request.signal.aborted) return;
        setEdge(payload);
        await waitForBrainLoaderFade();
        if (request.signal.aborted) return;
        setStatus("");
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        const message =
          error instanceof Error ? error.message : "Something went wrong.";
        setStatus(`Couldn’t load this relationship. ${message}`);
        setStatusIsError(true);
      });

    return () => request.abort();
  }, [edgeId, workspace.id]);

  const relationLabel = edge?.connector ?? "";

  return (
    <section
      className="relationship-page"
      role="dialog"
      aria-modal="true"
      aria-labelledby="relationship-title"
    >
      <div className="relationship-inner">
        <BlurredContainer
          blurredEdges={["bottom"]}
          edgeBlurSize={20}
          className="relationship-topbar"
        >
          <button
            ref={backButtonRef}
            className="graph-back"
            type="button"
            onClick={onBack}
          >
            ← back to graph
          </button>
        </BlurredContainer>

        {status && !statusIsError && (
          <BrainLoader
            fading={edge !== null}
            label={status}
          />
        )}
        <PresenceTransition
          as="p"
          className="graph-status error-message"
          enter="fade-up"
          exit="fade-up"
          role="alert"
          visible={statusIsError}
        >
          {status}
        </PresenceTransition>

        {!edge && (
          <h2 id="relationship-title" className="sr-only">
            Relationship details
          </h2>
        )}

        <PresenceTransition
          enter="fade-up"
          exit="fade-up"
          visible={edge !== null}
        >
          {edge && (
          <article className="relationship-body">
            <header className="relationship-header">
              <h2 id="relationship-title" className="graph-title">
                <button
                  className="relationship-entity"
                  type="button"
                  onClick={() => onOpenNode(edge.from_node)}
                >
                  {edge.from_node.title}
                </button>{" "}
                <span>{relationLabel}</span>{" "}
                <button
                  className="relationship-entity"
                  type="button"
                  onClick={() => onOpenNode(edge.to_node)}
                >
                  {edge.to_node.title}
                </button>
              </h2>
              {edge.details && (
                <p className="relationship-details">{edge.details}</p>
              )}

              <dl className="relationship-stats">
                <div>
                  <dt>confidence</dt>
                  <dd>{percent(edge.confidence)}</dd>
                </div>
                <div>
                  <dt>strength</dt>
                  <dd>{percent(edge.strength)}</dd>
                </div>
                <div>
                  <dt>first noted</dt>
                  <dd>{formatDate(edge.created_at)}</dd>
                </div>
                <div>
                  <dt>last updated</dt>
                  <dd>{formatDate(edge.updated_at)}</dd>
                </div>
                {edge.status !== "active" && (
                  <div>
                    <dt>status</dt>
                    <dd>{humanize(edge.status)}</dd>
                  </div>
                )}
                {edge.valid_from && (
                  <div>
                    <dt>valid from</dt>
                    <dd>{formatDate(edge.valid_from)}</dd>
                  </div>
                )}
                {edge.valid_to && (
                  <div>
                    <dt>valid until</dt>
                    <dd>{formatDate(edge.valid_to)}</dd>
                  </div>
                )}
                {edge.deleted_at && (
                  <div>
                    <dt>archived</dt>
                    <dd>
                      {formatDate(edge.deleted_at)}
                      {edge.deleted_reason &&
                        ` · ${humanize(edge.deleted_reason)}`}
                    </dd>
                  </div>
                )}
              </dl>
            </header>

            <section
              className="relationship-evidence"
              aria-labelledby="relationship-evidence-title"
            >
              <h3
                id="relationship-evidence-title"
                className="relationship-section-title"
              >
                evidence
                <span className="relationship-count">
                  {edge.evidence.length}
                </span>
              </h3>
              {edge.evidence.length === 0 ? (
                <p className="graph-empty">no evidence recorded.</p>
              ) : (
                <ol className="relationship-evidence-list">
                  {edge.evidence.map((item) => (
                    <li key={item.evidence_id}>
                      <blockquote className="relationship-quote">
                        “{item.raw_content}”
                      </blockquote>
                      <p className="relationship-evidence-meta">
                        ingested{" "}
                        <time dateTime={item.source.created_at}>
                          {formatDateTime(item.source.created_at)}
                        </time>{" "}
                        · {humanize(item.source.source_type)} ·{" "}
                        {percent(item.confidence)} confidence
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </article>
          )}
        </PresenceTransition>
      </div>
    </section>
  );
}
