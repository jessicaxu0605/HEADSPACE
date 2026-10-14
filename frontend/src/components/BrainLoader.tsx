type BrainLoaderProps = {
  fading?: boolean;
  label: string;
};

export default function BrainLoader({
  fading = false,
  label,
}: BrainLoaderProps) {
  return (
    <div
      className={`brain-loader${fading ? " is-fading" : ""}`}
      role="status"
      aria-live="polite"
    >
      <img src="/brain_loader.svg" alt="" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
