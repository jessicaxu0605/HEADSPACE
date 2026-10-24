import type { ReactNode } from "react";
import type { MemoryExternalId, MemoryNodeDetail } from "../memory_graph";

type NodeDetailsProps = {
  aliases: string[];
  externalIds: MemoryExternalId[];
  details: MemoryNodeDetail[];
};

type Row = { key: string; label: string; value: ReactNode };

const externalLink = (href: string, text: string) => (
  <a href={href} target="_blank" rel="noreferrer">
    {text}
  </a>
);

const formatExternalId = ({ provider, external_id }: MemoryExternalId): Row => {
  const key = `${provider}:${external_id}`;
  switch (provider.toLowerCase()) {
    case "email":
      return {
        key,
        label: "email",
        value: <a href={`mailto:${external_id}`}>{external_id}</a>,
      };
    case "phone":
      return {
        key,
        label: "phone",
        value: <a href={`tel:${external_id}`}>{external_id}</a>,
      };
    case "instagram":
      return {
        key,
        label: "instagram",
        value: externalLink(
          `https://www.instagram.com/${encodeURIComponent(external_id)}/`,
          `@${external_id}`,
        ),
      };
    default:
      return { key, label: provider.replaceAll("_", " "), value: external_id };
  }
};

// "2026-03-03" is a calendar date, not an instant; parsing it with Date would
// read it as UTC midnight and can show the previous day.
const formatDate = (value: string) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  return new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  ).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
};

const formatDateTime = (value: string) => {
  const dateOnly = /^(\d{4}-\d{2}-\d{2})(?:T00:00(?::00)?)?$/.exec(value);
  if (dateOnly) return formatDate(dateOnly[1]);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

const formatValue = ({ name, kind, value }: MemoryNodeDetail): ReactNode => {
  const text = String(value);
  if (kind === "date") return formatDate(text);
  if (kind === "datetime") return formatDateTime(text);
  if (kind === "enum") return text.replaceAll("_", " ");
  if (name === "address") {
    return externalLink(
      `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(text)}`,
      text,
    );
  }
  return text;
};

export default function NodeDetails({
  aliases,
  externalIds,
  details,
}: NodeDetailsProps) {
  const rows: Row[] = [
    ...(aliases.length > 0
      ? [{ key: "aliases", label: "also known as", value: aliases.join(", ") }]
      : []),
    ...externalIds.map(formatExternalId),
    ...details.map((detail) => ({
      key: detail.name,
      label: detail.label,
      value: formatValue(detail),
    })),
  ];
  if (rows.length === 0) return null;

  return (
    <dl className="node-details">
      {rows.map((row) => (
        <div className="node-detail" key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
