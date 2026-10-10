#!/bin/sh
# Link only this product's Skills into a native runtime home.
# Full product installers retain authority over agents, workflows, and link state.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
skills_repo_dir="$repo_root/skills"
[ "$#" -le 1 ] || { printf 'usage: %s [claude|codex|opencode|omp]\n' "$0" >&2; exit 2; }
runtime=${1:-claude}
case "$runtime" in
  claude) runtime_root=${CLAUDE_CONFIG_DIR:-"$HOME/.claude"} ;;
  codex) runtime_root=${CODEX_HOME:-"$HOME/.codex"} ;;
  opencode) runtime_root="${XDG_CONFIG_HOME:-$HOME/.config}/opencode" ;;
  omp) runtime_root=${PI_CODING_AGENT_DIR:-"$HOME/.omp/agent"} ;;
  *) printf 'unknown runtime: %s\n' "$runtime" >&2; exit 2 ;;
esac
runtime_skills_dir="$runtime_root/skills"

# Git Bash/MSYS can turn `ln -s` into a directory copy. Use the existing
# Windows linker so updates stay live and junction ownership stays explicit.
# WSL reports Linux and keeps using the POSIX implementation below.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    command -v pwsh >/dev/null 2>&1 || {
      printf 'PowerShell 7 (pwsh) is required to link Skills on Windows.\n' >&2
      exit 1
    }
    command -v cygpath >/dev/null 2>&1 || {
      printf 'cygpath is required to pass native Windows paths to PowerShell.\n' >&2
      exit 1
    }
    native_script=$(cygpath -aw "$script_dir/link-skills.ps1")
    native_runtime_root=$(cygpath -aw "$runtime_root")
    MSYS2_ARG_CONV_EXCL='*' exec pwsh -NoLogo -NoProfile -File "$native_script" \
      -Runtime "$runtime" -RuntimeHome "$native_runtime_root"
    ;;
esac

[ -d "$skills_repo_dir" ] || {
  printf 'missing product skills directory: %s\n' "$skills_repo_dir" >&2
  exit 1
}
if [ -L "$runtime_skills_dir" ]; then
  printf 'conflict: %s must be a runtime-owned real directory, not a link\n' \
    "$runtime_skills_dir" >&2
  exit 1
fi
mkdir -p "$runtime_skills_dir"

# Check every wanted destination before adding or removing a link.
for source_dir in "$skills_repo_dir"/*/; do
  source_dir=${source_dir%/}
  [ -f "$source_dir/SKILL.md" ] || continue
  destination="$runtime_skills_dir/$(basename "$source_dir")"
  if [ -L "$destination" ]; then
    [ "$(realpath "$destination" 2>/dev/null)" = "$(realpath "$source_dir")" ] || {
      printf 'conflict: %s points to %s\n' "$destination" "$(readlink "$destination")" >&2
      exit 1
    }
  elif [ -e "$destination" ]; then
    printf 'conflict: existing real file or directory is never replaced: %s\n' "$destination" >&2
    exit 1
  fi
done

# Remove links this checkout owns that are stale or renamed. Links owned by
# other checkouts are never touched.
for existing in "$runtime_skills_dir"/*; do
  [ -L "$existing" ] || continue
  link_target=$(readlink "$existing")
  case "$link_target" in
    "$skills_repo_dir"/*) ;;
    *) continue ;;
  esac
  if [ -f "$link_target/SKILL.md" ] &&
    [ "$(basename "$link_target")" = "$(basename "$existing")" ]; then
    continue
  fi
  unlink "$existing"
  printf 'removed stale kgdistiller Skill link: %s\n' "$existing"
done

linked=0
for source_dir in "$skills_repo_dir"/*/; do
  source_dir=${source_dir%/}
  [ -f "$source_dir/SKILL.md" ] || continue
  entry_name=$(basename "$source_dir")
  destination="$runtime_skills_dir/$entry_name"
  if [ -L "$destination" ]; then
    [ "$(realpath "$destination")" = "$(realpath "$source_dir")" ] || {
      printf 'conflict: %s points to %s\n' "$destination" "$(readlink "$destination")" >&2
      exit 1
    }
  elif [ -e "$destination" ]; then
    printf 'conflict: existing real file or directory is never replaced: %s\n' \
      "$destination" >&2
    exit 1
  else
    ln -s "$source_dir" "$destination"
  fi
  linked=$((linked + 1))
done

printf 'ok: %s linked %s kgdistiller Skills into %s (skills-only)\n' \
  "$runtime" "$linked" "$runtime_skills_dir"
