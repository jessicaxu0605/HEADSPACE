import type { FormEvent, RefObject } from "react";
import { TextField } from "../component-library";

type SearchInterfaceProps = {
  inputRef: RefObject<HTMLInputElement | null>;
  query: string;
  searching: boolean;
  onQueryChange: (query: string) => void;
  onFocusChange: (focused: boolean) => void;
  onSearch: () => void;
};

export default function SearchInterface({
  inputRef,
  query,
  searching,
  onQueryChange,
  onFocusChange,
  onSearch,
}: SearchInterfaceProps) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    onSearch();
  };

  return (
    <form
      className={`search-form${searching ? " is-loading" : ""}`}
      role="search"
      onSubmit={handleSubmit}
    >
      <svg className="search-icon" aria-hidden="true" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="6.5" />
        <path d="m16 16 4 4" />
      </svg>
      <TextField
        ref={inputRef}
        type="search"
        name="query"
        aria-label="Search your headspace"
        aria-busy={searching}
        placeholder="pick your brain"
        autoComplete="off"
        value={query}
        onValueChange={onQueryChange}
        onFocus={() => onFocusChange(true)}
        onBlur={() => onFocusChange(false)}
      />
    </form>
  );
}
