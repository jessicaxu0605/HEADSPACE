#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  DATABASE_URL='postgresql://...' prototypes/sql/apply_sql.sh <script>

Examples:
  prototypes/sql/apply_sql.sh setup/extensions_and_helpers.sql
  prototypes/sql/apply_sql.sh canonical_graph/memory_node.sql

The script reads the database connection string from DATABASE_URL, or from
NEON_DATABASE_URL if DATABASE_URL is unset. The script path is resolved from
the prototype SQL directory.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 2
fi

database_url="${DATABASE_URL:-${NEON_DATABASE_URL:-}}"
if [[ -z "$database_url" ]]; then
  echo "Missing DATABASE_URL or NEON_DATABASE_URL." >&2
  exit 2
fi

script_arg="$1"
sql_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$script_arg" = /* ]]; then
  echo "Use a path relative to prototypes/sql, for example: setup/users.sql" >&2
  exit 2
fi

script_path="$sql_root/$script_arg"

if [[ -f "$script_path" ]]; then
  :
else
  echo "SQL script not found: $script_arg" >&2
  exit 1
fi

psql "$database_url" -v ON_ERROR_STOP=1 -f "$script_path"
